+++
date = '2026-05-27T10:04:19+08:00'
draft = false
title = 'vLLM PagedAttention：大模型推理的显存管理革命'
author = 'JekYUlll'
lastmod = '2026-05-27T10:04:19+08:00'
tags = ['vllm', 'pagedattention', 'llm-inference']
categories = ['infra']
+++

## 背景

如果你部署过 LLM 服务，大概见过这个场景：GPU 显存明明还有空，但新请求就是接不进来。NVIDIA 的监控面板上显示显存用了 85%，但实际有效利用率不到 40%。剩下那 45% 去哪了？被 KV cache 的碎片吃掉了。

LLM 推理有个独特的矛盾：**算力不是瓶颈，显存带宽才是**。每次 decode 只需要做一次矩阵乘法，但得把模型权重和 KV cache 从 HBM 搬到 SM。KV cache 的大小随序列长度增长——LLaMA-13B 在 4096 context 下，每条序列要吃掉约 3.1 GiB 显存。7 条并发，光 cache 就 21.7 GiB，还没算权重。

传统系统怎么管理 KV cache？给每条序列预先分配整块连续显存，长度按最大可能值预留。但实际生成多少 token 没人知道。有的请求只生成 50 个 token 就停了，预留的 4096 个位置大部分空着。这种预分配方案的内存浪费在 60–80%。**60–80% 的显存是空占着不用的。**

2023 年 UC Berkeley 的论文《Efficient Memory Management for Large Language Model Serving with PagedAttention》把这个数字压到了 4% 以下。办法是从操作系统借来的——分页。

## 核心原理

### KV cache 为什么这么吃内存

Transformer 的 self-attention 需要 K 和 V 矩阵。每生成一个新 token，都要跟之前所有 token 做 attention。如果不缓存，生成第 1 个 token 得算 1 次 attention，生成第 2 个得重新算前 2 个，复杂度 O(n²)。缓存之后：第 N 步只需要算第 N 个 token 跟前面 N−1 个的 attention——O(n)。

代价是显存。单个 token 的 KV cache 大小 = 2 × layers × num_heads × head_dim × dtype_bytes。以 LLaMA-2-13B（40 层，40 头，d=128，FP16）为例：

| 参数 | 值 |
|------|-----|
| 每 token cache | 2 × 40 × 40 × 128 × 2 = 0.78 MiB |
| 4096 context 单序列 | 3.1 GiB |
| A100-40G 可并发序列 | 约 7 条（权重占 ~26 GiB） |

这是 GQA（分组查询注意力）版本的量。如果是 MHA（多头注意力），更大。

### 预处理的问题

传统方案（如 FasterTransformer、HF Transformers）的做法：每个请求进来时，一次性分配完整的连续显存块。长度按模型最大 context 来。

这就产了两种碎片：

1. **内部碎片**：分配了 4096 位置，请求只生成 50 个 token，剩下 4046 个 slot 白白占着。
2. **外部碎片**：不同请求先后到达释放，显存被切成一堆不连续的小块，新请求需要一个大的连续块时，虽然总空闲够用，但找不到连续的。

结果就是有效利用率 20–40%。

### PagedAttention：从操作系统借来的分页

PagedAttention 的核心洞察很直接：**操作系统管理物理内存的方式，为什么不能用来管理 KV cache？**

操作系统把虚拟地址空间切成固定大小的页，通过页表映射到离散的物理页帧。PagedAttention 做了同样的事：

| OS 概念 | PagedAttention 等价物 |
|---------|----------------------|
| 虚拟地址空间 | 逻辑 KV block（每个请求的视角） |
| 物理内存页帧 | 物理 KV block（GPU 显存） |
| 页表 | **Block Table**（逻辑 block → 物理 block 映射） |
| 缺页中断 | block 用完 → 从空闲池新分配一个 |
| 写时复制（CoW） | 共享前缀 block，写时复制 |

具体实现上，KV cache 被切成固定大小的 **block**（通常 16 个 token 一个 block）。GPU 显存里维护一个全局空闲 block 池。每个请求只有一张 **Block Table**，记录它的逻辑 block 对应哪些物理 block。

分配策略：
- **Prefill（预填充）阶段**：按 prompt 长度分配 ⌈prompt_tokens / 16⌉ 个 block
- **Decode（生成）阶段**：每生成 16 个 token 才分配一个新 block
- **序列完成**：全部 block 归还到空闲池

**内部碎片**被限制在最后一个 block 的最多 15 个 slot。**外部碎片**直接归零——所有 block 大小相同，空闲池里随便哪个物理 block 都能分配给任意请求。总浪费不到 4%。

### 内存共享：写时复制

PagedAttention 的 Block Table 机制还顺手解决了另一个问题：内存共享。多个请求如果共享前缀（比如同一个 system prompt 的开头 1000 个 token），它们的 Block Table 可以指向同一组物理 block。引用计数管理，哪个请求要修改了（比如 beam search 的分叉），才分配新 block 做写时复制。

LMSYS（Chatbot Arena）用这个特性把 KV cache 开销降了 55%。

## 代码实战

### 直接跑 vLLM 离线推理

```bash
pip install vllm
```

```python
from vllm import LLM, SamplingParams

# 加载模型（自动管理 KV cache）
llm = LLM(model="Qwen/Qwen2.5-7B-Instruct")

# 构造请求
prompts = [
    "用三句话解释什么是 KV cache：",
    "用三句话解释 PagedAttention 的核心思想：",
]

# 生成参数
sampling_params = SamplingParams(
    temperature=0.7,
    max_tokens=256,
)

# 批量生成（vLLM 内部自动做 continuous batching）
outputs = llm.generate(prompts, sampling_params)

for output in outputs:
    prompt = output.prompt
    generated_text = output.outputs[0].text
    print(f"Prompt: {prompt}")
    print(f"Response: {generated_text}\n")
```

### OpenAI 兼容的 API 服务

```bash
# 启动服务端
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-7B-Instruct \
    --gpu-memory-utilization 0.90
```

```bash
# 用 curl 调用，跟 OpenAI API 一样的格式
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "messages": [
      {"role": "system", "content": "你是一个技术助手。"},
      {"role": "user", "content": "PagedAttention 怎么减少显存浪费？"}
    ],
    "max_tokens": 200
  }'
```

注意 `--gpu-memory-utilization 0.90` 这个参数。它告诉 vLLM 把 90% 的显存分给 KV cache pool，剩下 10% 留给模型权重和其他开销。默认是 0.90，H100 上可以调到 0.95。如果设太低，KV cache 池不够大，并发能力先被卡住。设太高，权重加载可能 OOM。

### 观察内存使用

vLLM 提供了 `/metrics` 接口暴露 Prometheus 指标，可以看 KV cache 使用情况：

```bash
# 启动时加 --enable-prometheus-metrics
# 然后访问
curl http://localhost:8000/metrics | grep vllm:kv_cache
```

关键指标：
- `vllm:num_blocks_free`：空闲 block 数 → 判断显存是否够用
- `vllm:num_requests_running`：正在运行的请求数
- `vllm:num_requests_waiting`：排队中 → block 池不够了

## 生态现状

PagedAttention 被提出后，已经成为 LLM 推理引擎的标配技术。

| 引擎 | PagedAttention 支持 | 特色 | 部署复杂度 |
|------|---------------------|------|-----------|
| **vLLM** | 原生实现 | 社区最大，80k+ stars，2000+ 模型 | `pip install` |
| **TensorRT-LLM** | 支持 Page Attention（block 管理不同） | H100 FP8 峰值性能，NVIDIA 官方 | 编译构建，复杂 |
| **SGLang** | 支持 Paged KV Cache | RadixAttention、结构化生成 | `pip install` |
| **HuggingFace TGI** | v0.8+ 支持 Paged Attention | HuggingFace 生态 | Docker |
| **LMDeploy (TurboMind)** | 有类似实现 | 国内社区，支持 DeepSeek | `pip install` |

vLLM 是目前最主流的方案。GitHub 81k stars，2,650+ contributors，被 AMD、AWS、Databricks、NVIDIA、Roblox 等公司采用在生产环境。最新版本 v0.21.0（2026-05-15）已经支持 200+ 模型架构、FP8/INT4 量化、disaggregated prefill/decode、EAGLE 推测解码等。

## 今日可执行动作

1. **装个 vLLM 跑一次性能对比**：`pip install vllm`，用 LLaMA-3.1-8B 或 Qwen2.5-7B，分别用 vLLM 和 HF Transformers 跑同样的 50 条请求，记录吞吐量和显存占用。不跑一次不会真相信那个 24×。

2. **看一下生产环境的 GPU 显存利用率**：如果已经在跑 LLM 服务，检查 `/metrics` 的 `vllm:num_blocks_free`。如果空闲 block 数经常是 0，说明 KV cache pool 太小，可以调大 `--gpu-memory-utilization`。

3. **试一下 prefix sharing 的效果**：准备一组共享前缀的请求（比如同样 system prompt），用 vLLM 的 `--enable-prefix-caching` 启动，对比开/关的吞吐差异。

## 参考

- [Efficient Memory Management for Large Language Model Serving with PagedAttention (SOSP 2023)](https://arxiv.org/abs/2309.06180)
- [vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention](https://vllm.ai/blog/2023-06-20-vllm)
- [PagedAttention: vLLM Documentation](https://docs.vllm.ai/en/latest/design/paged_attention/)
- [Introduction to vLLM and PagedAttention (Runpod Blog)](https://www.runpod.io/blog/introduction-to-vllm-and-pagedattention)
- [Paged Attention from First Principles (Hamza's Blog)](https://hamzaelshafie.bearblog.dev/paged-attention-from-first-principles-a-view-inside-vllm/)
- [GitHub: vllm-project/vllm](https://github.com/vllm-project/vllm)
