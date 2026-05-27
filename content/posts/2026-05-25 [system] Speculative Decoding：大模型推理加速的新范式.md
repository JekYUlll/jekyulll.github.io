+++
date = '2026-05-25T23:54:00+08:00'
draft = false
title = 'Speculative Decoding：大模型推理加速的新范式'
author = 'JekYUlll'
lastmod = '2026-05-25T23:54:00+08:00'
tags = ['llm-inference', 'speculative-decoding']
categories = ['infra']
+++

## 背景

用过 ChatGPT 的人都有这种体验：打字过程中模型已经开始回复，似乎不用等太久。但如果你跑过本地 70B 模型，感受就完全不同了。每生成一个 token（≈0.75 个汉字）就要等 14ms 左右，生成 500 字的短文得等将近 10 秒。

为什么这么慢？答案是**自回归解码**的串行瓶颈。

大语言模型的标准推理是逐 token 生成的。每个 token 的生成都必须完整执行一次模型前向传播。对于 70B 的模型，每次要把约 140GB 的权重从 HBM 搬到计算单元，只产出 **1 个 token**。单序列推理时 GPU 利用率常常不到 10%——绝大多数时间花在搬运上，不是在算。

传统优化各有各的代价：INT4/INT8 量化是有损的，Flash Attention 需要硬件支持，batching 提升的是吞吐不是单请求延迟。

**Speculative Decoding（推测解码）** 换了个思路：用小模型快速起草、大模型并行验证。不仅能实现 2–3× 的加速，而且数学上保证输出分布与原始模型完全一致。

## 核心原理

### Transformer 的关键特性：并行验证是"免费"的

Speculative Decoding 的核心前提来自 Transformer 架构的一个特点：**当 batch=1 时，单次前向传播处理 k 个 token 的延迟 ≈ 处理 1 个 token 的延迟**。

这是内存带宽瓶颈的直接结果。单序列推理时前向传播的时间主要花在从 HBM 搬运权重上，而不是花在序列维度的计算上。所以处理 1 个 token 还是 5 个 token，搬运开销是一样的。

这个洞察意味着：如果能提前猜出接下来几个 token，让大模型一次性并行验证，就能在一次前向传播中产出多个 token。

### Draft-then-Verify 两阶段框架

Speculative Decoding 用两个模型协同工作：

每轮迭代分三步：

1. **Draft 阶段**：Draft Model 自回归生成 $\gamma$ 个候选 token（通常 $\gamma=4\sim8$）。小模型，快。
2. **Verify 阶段**：把 prefix + 全部 $\gamma$ 个 draft token 拼起来，一次性喂给 Target Model 做一次前向传播，得到每个位置的真实分布。
3. **Accept/Reject 阶段**：逐个检查是否接受。如果随机数小于接受概率就收下，否则从差值分布重新采样并停在这个位置。
   - 如果随机数 $r < \alpha_i$，接受 $\tilde{x}_i$
   - 否则从分布 $p'(x) = \text{norm}(\max(0, p_i(x) - q_i(x)))$ 中采样并停止

这个 **Modified Rejection Sampling** 保证采样分布与直接用 Target Model 自回归生成完全一致。

### 加速比分析

加速比取决于 draft model 的**接受率**。draft model 和 target model 分布越接近（比如用 target model 的浅层做 draft），接受率越高。实践中实测可达 2–3×，最佳情况（EAGLE-3）可达 4–6.5×。

关键约束：draft model 必须**足够快**。如果 draft 时间 + verify 时间 > 直接生成时间，反而更慢。

## 代码实战

### 用 Hugging Face Transformers 体验 Assisted Generation

Hugging Face 的 `transformers` 库从 4.23.0 开始内置了 Assisted Generation（与 Assisted Decoding 等价，Hugging Face 的实现名称）。

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

# 加载 target model（大模型）
model_name = "meta-llama/Llama-2-7b-chat-hf"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype=torch.float16,
    device_map="auto"
)

# 加载 draft model（小模型）
draft_name = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
draft_model = AutoModelForCausalLM.from_pretrained(
    draft_name,
    torch_dtype=torch.float16,
    device_map="auto"
)

prompt = "Explain the concept of speculative decoding in three sentences."
inputs = tokenizer(prompt, return_tensors="pt").to("cuda")

# 普通生成（baseline）
output_normal = model.generate(
    **inputs,
    max_new_tokens=100,
    do_sample=True,
    temperature=0.7,
)

# Assisted Generation
output_assisted = model.generate(
    **inputs,
    max_new_tokens=100,
    do_sample=True,
    temperature=0.7,
    assistant_model=draft_model,  # 关键参数
)

print("Normal:", tokenizer.decode(output_normal[0], skip_special_tokens=True))
print("Assisted:", tokenizer.decode(output_assisted[0], skip_special_tokens=True))
```

只需传入 `assistant_model=draft_model`，Hugging Face 会自动执行 speculative decoding 的全部逻辑。实测在 A100 上，TinyLlama(1.1B) 辅助 Llama-2-7B，约可获得 1.8–2.2× 的加速。

### 实现一个最小化的 Speculative Decoding

以下是一个简化但完整的独立实现，展示核心逻辑：

```python
import torch
import torch.nn.functional as F

def speculative_decoding_step(
    draft_model,
    target_model,
    prefix_ids,
    gamma=5,
    temperature=1.0
):
    """
    一次 speculative decoding 迭代。
    prefix_ids: shape [1, seq_len]
    返回: accepted token IDs list
    """
    device = prefix_ids.device
    
    # Phase 1: Draft — 小模型自回归生成 γ 个候选
    draft_ids = prefix_ids.clone()
    for _ in range(gamma):
        with torch.no_grad():
            logits = draft_model(draft_ids).logits[:, -1, :] / temperature
        probs = F.softmax(logits, dim=-1)
        next_token = torch.multinomial(probs, num_samples=1)
        draft_ids = torch.cat([draft_ids, next_token], dim=-1)
    
    draft_tokens = draft_ids[:, prefix_ids.shape[1]:]  # [1, gamma]
    
    # Phase 2: Verify — 大模型一次并行前向传播
    with torch.no_grad():
        target_logits = target_model(draft_ids).logits / temperature
    # 获取每个位置的概率分布
    # target_logits shape: [1, prefix_len + gamma, vocab_size]
    target_probs = F.softmax(target_logits[:, prefix_ids.shape[1]-1:, :], dim=-1)
    draft_probs = F.softmax(
        draft_model(draft_ids).logits[:, prefix_ids.shape[1]-1:, :] / temperature,
        dim=-1
    )
    
    # Phase 3: Accept/Reject via Modified Rejection Sampling
    accepted = []
    for i in range(gamma):
        token_id = draft_tokens[0, i].item()
        p_i = target_probs[0, i, token_id].item()
        q_i = draft_probs[0, i, token_id].item()
        
        accept_prob = min(1.0, p_i / (q_i + 1e-10))
        
        if torch.rand(1).item() < accept_prob:
            accepted.append(token_id)
        else:
            # Reject: resample from (p_i - q_i)_+
            adjusted = target_probs[0, i] - draft_probs[0, i]
            adjusted = torch.clamp(adjusted, min=0)
            if adjusted.sum() > 0:
                adjusted /= adjusted.sum()
                resampled = torch.multinomial(adjusted, 1).item()
                accepted.append(resampled)
            break
    
    return accepted
```

这个实现虽然生产环境不够用（缺少 KV cache 共享、不需要每步重算 prefix），但完整展示了 draft → verify → accept/reject 的三段式流程。

## 生态现状

Speculative Decoding 已在工业界和学术界广泛应用：

| 项目/框架 | 核心机制 | 典型加速比 | 特点 |
|-----------|---------|-----------|------|
| Hugging Face Assisted Generation | 任意小模型做 draft | 1.5–3× | 开箱即用，支持任意 pair |
| vLLM (v0.6+) | 内置 speculative decoding | 2–3× | 生产级推理引擎 |
| Medusa (CMU) | 多个独立 head 并行 draft | 2–3.5× | 无需额外 draft model |
| EAGLE-3 (NeurIPS 2025) | 特征层投机 + 动态树搜索 | 4–6.5× | 当前 SOTA，基于 draft head |
| TensorRT-LLM | 集成 speculative decoding | 2–3× | NVIDIA 官方优化 |
| Self-Speculative Decoding | 用自身浅层做 draft | 1.5–2× | 无需额外模型 |

其中 EAGLE-3（2025）用 target model 中间层的特征向量做 draft head 输入，大幅提高 draft 质量，同时 draft head 极小（~68M 参数），是目前公开方案里最快的。

## 今日可执行动作

1. **用 HF Assisted Generation 跑一次对比**：选一个大模型（如 Qwen2.5-7B）和一个小模型（如 Qwen2.5-0.5B），用上面代码实测加速比。你会发现在 batch=1 的场景下，辅助生成明显快于普通生成，且输出分布完全一致。

2. **阅读 EAGLE-3 论文**：搜索 "EAGLE-3 EAGLE-3: Efficient and Accurate Speculative Decoding"（NeurIPS 2025），理解特征层投机和动态树搜索如何进一步提升接受率。

3. **在自己的推理服务中启用**：如果你用 vLLM 部署模型，v0.6.0+ 版本支持 `--speculative-model` 参数。尝试配置一个 draft model（如 Llama-68M），对比开启前后的 TTFT（首 token 延迟）和 ITL（逐 token 延迟）指标。

## 参考

- Leviathan et al., "Fast Inference from Transformers via Speculative Decoding", ICML 2023. arXiv:2211.17192
- Chen et al., "Accelerating LLM Inference with Speculative Sampling", DeepMind 2023. arXiv:2302.01318
- Hugging Face Blog, "Assisted Generation: a new direction toward low-latency text generation", 2023. https://huggingface.co/blog/assisted-generation
- EAGLE-3, NeurIPS 2025. arXiv:2603.03251
- "Speculative Decoding深度全景解析", 博客园. https://www.cnblogs.com/SCCQ/p/19837997
