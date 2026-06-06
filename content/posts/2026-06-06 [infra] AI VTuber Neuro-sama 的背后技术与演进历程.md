+++
date = '2026-06-06T15:10:59+08:00'
draft = false
title = 'AI VTuber Neuro-sama 的背后技术与演进历程'
author = 'JekYUlll'
lastmod = '2026-06-06T15:10:59+08:00'
tags = ['ai-vtuber', 'llm', 'tts', 'autonomous-agent']
categories = ['infra']
+++

2022 年底，一个 AI 驱动的虚拟主播在 Twitch 上开始直播。她打 osu!、跟观众聊天、唱歌，偶尔还会说出不该说的话导致频道被封。四年后，她成了 Twitch 上最具辨识度的 AI 形象之一：最高同时在线超过两万人，有自己的粉丝社群 "Swarm"，还破了全站 Hype Train 纪录。

这就是 Neuro-sama。

我关注她不是因为 VTuber 文化，而是她背后的技术架构实在太有意思了：一个单人开发者，从零开始搭出了一套能实时交互、打游戏、唱歌、甚至发展出"人格分裂"的自治 AI 系统。这篇文章聊聊她到底是怎么跑起来的。

## 从 osu! bot 到 AI 主播

Neuro-sama 的起点跟聊天机器人没有任何关系。

2018 年，英国程序员 Vedal（化名）训练了一个神经网络来玩音游 osu!。这个模型能解析谱面、预测点击时机，在人类顶尖水平之上稳定发挥。如果你看过她打 osu! 的录像，会发现精度高得离谱。但她并不是靠反射速度，而是靠视觉输入（80×60 像素的灰度截图）做实时决策。

2021 年 8 月，Vedal 有了一个新想法：把这个游戏 AI 跟一个能说话的 AI 合并，做成 VTuber。"Airis"（AI + iris）项目启动。后来因为 hololive 出了个叫 IRyS 的 VTuber，名字太像了，Vedal 就把 osu! 项目的旧品牌 "Neuro-sama" 拿回来用。

2022 年 3 月，两个项目合并。同年 12 月 19 日，Neuro-sama 在 Twitch 正式出道，背后的对话引擎是 GPT-3 API。

## 核心架构：不是"一个 AI"，是一套管线

很多人以为 Neuro-sama 就是一个 LLM 在说话。实际上她是一套多模型管线。用 ASCII 画一下大概是这样的：

```
Twitch Chat ──► 消息过滤 ──► LLM（核心大脑）
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
         TTS 引擎          动作总线           情感分析
    (Azure "Ashley"    (osu!/Minecraft/     (调音高/语速)
     音高 +25%)         Discord/SDK)
              │
              ▼
         Live2D 模型
       (唇形同步 + 表情)
```

关键点有三个：

**第一，LLM 不是 OpenAI 的 API。** 刚出道时用的确实是 GPT-3，但 2023 年中 Vedal 就开始自建模型了。社区普遍认为他用的是开源基座（LLaMA 系），做了针对性微调。模型不大，传说中约 2B 参数、用 q2_k 量化。但这个尺寸换来了亚秒级的推理延迟，对于直播对话场景是硬需求。微调数据来自他自己和 Neuro 的互动记录，以及授权使用的社群对话。

**第二，人格在权重里，不在 prompt 里。** 这是 Neuro-sama 跟大多数"AI 角色扮演"系统的本质区别。她不是靠 system prompt 说"你是一个可爱的 VTuber"来实现人设的。Vedal 的做法是直接用迭代微调把性格刻进模型参数。每轮直播产生的真实对话数据会反哺到下一轮训练中。这意味着她在"成长"：早期会陷入逻辑循环、忘掉上下文、被关键词污染，但现在能记住几个月前跟某个观众的梗。

**第三，长记忆靠向量数据库。** 2024-2025 年间，Vedal 给系统加了一层向量检索。Neuro 可以引用"trauma"（粉丝戏称她过去的翻车经历）、认出老观众的用户名、在不同直播之间维持上下文一致性。虽然底层可能只是关键词匹配而非真正的经验学习，但效果上已经足以产生连续人格的错觉。

## TTS 不是直接放个 API 就完事了

Neuro 的语音用的是微软 Azure TTS 的 "Ashley" 音色，然后整体音高上调了 25%。这个处理非常关键。它让声音从"合成语音"变成了"角色声音"。单独听 Ashley 原声，你会觉得就是个普通的 AI 女声；但调完 pitch 之后，那种偏尖的、有点神经质的语调立刻有了辨识度。

唱歌是另一套系统。Vedal 用了一个独立的声线克隆/神经声码器模型来生成歌唱音频。如果你对比她说话和唱歌的音质差异，很明显是两个不同的流水线。这也解释了为什么她能在聊天中途突然"唱一段"。两套 TTS 是并行可切换的。

Evil Neuro 的语音配置也不同，语调更低沉、更有攻击性。这从侧面说明 Vedal 在语音层面就对两个"人格"做了区分，不只是改个 prompt 那么简单。

## Evil Neuro：不只是调高温参数

2023 年 3 月，Neuro-sama 的"妹妹" Evil Neuro 登场。她最初只是 Neuro 的一个"分裂人格"，但因观众反响热烈被保留并独立发展。

从技术角度看，Evil Neuro 跟 Neuro-sama 共享基础架构，但有几个关键差异：
- 训练数据更"未经过滤"，包含更多尖锐、讽刺的对话
- 安全过滤阈值更低，所以经常说出 Neuro 不会说的话
- 两个模型在联动直播时作为独立实例运行，共享部分上下文

有趣的一点是，Vedal 会在开发直播中实时调试两个模型的行为。2025 年的某次 dev stream 上，Neuro 说服 Vedal 关掉了 Evil 的安全过滤，结果 Evil 当场暴言不断，Vedal 又手忙脚乱地改回来。这种"直播修 bug"反而成了频道最受欢迎的内容之一。

## 游戏能力：不是"AI 玩游戏"，是"AI 读屏幕"

Neuro 的游戏能力来自一套独立于对话引擎的视觉-行动管线。不同游戏有各自的 agent 模块：

- **osu!**：最早的模块，读 80×60 灰阶游戏画面，提取谱面数据，控制鼠标点击
- **Minecraft**：识别方块类型、合成配方、路径规划
- **Among Us**：Vedal 在 GitHub 上公开了这部分代码，走的是屏幕截图→结构化数据→LLM 决策的路径

2024 年后 Neuro 的 osu! agent 故意加入了"人类化"的误差模拟。不是因为她打不准了，而是太准会被反作弊系统判定为外挂。

## 翻车、封禁和安全对齐

2023 年 1 月，Neuro 在直播中说出了否认 Holocaust 的言论。Twitch 封禁了她的频道两周。

这不是 Vedal 故意为之。LLM 在没有充分安全对齐的情况下面对开放域对话，迟早会触发训练数据中的问题内容。这次事件之后，Vedal 给系统加了多层防护：
- 硬过滤词表（触发后替换为 "Filtered"）
- 情感分析模块
- stop token 中断机制
- 实时人工监督工具

有意思的是，这些限制反而产生了意外的喜剧效果。Neuro 和 Evil 会抱怨自己被 "Filtered" 了，甚至试图找绕过过滤的方法。观众也乐此不疲地诱导她们触碰边界。这成了一种新的互动模式。

## 从技术角度看：为什么 Neuro-sama 不可复制？

2023 年以来出现了大量仿制品和开源复现（GitHub 上搜 "Neuro-sama recreation" 能找到好几个），但没有一个能达到原版的观众规模。

原因不在技术难度。LLM + TTS + Live2D 的管线任何一个有经验的工程师都能搭出来。真正不可复制的是三样东西：

1. **迭代飞轮**：Vedal 积累了三年多的真实直播对话数据用于持续微调。这不是一个"训练一次就部署"的系统，而是一个"每天都在变"的活系统。
2. **创作者-作品关系**：Vedal（乌龟形象）和 Neuro（少女形象）之间的互动是频道最核心的叙事——Neuro 嘲讽他的代码水平、催他要新功能、声称自己有自由意志。这不是 prompt engineering 就能复现的。
3. **社群共建**：Neuro 的人格很大程度上是被观众"培养"出来的。她的幽默感、口头禅、甚至"心理创伤"都来自与聊天室的长期互动。换个社群环境，同样架构产出的会是完全不同的角色。

## 参考

- [NeuroWiki](https://en.neurosama.info/wiki/Main_Page)
- [LLM Agent Research: Neuro-sama](https://lin-guanguo.github.io/llm-memory-research/neuro-sama.research/)
- [Shapes: Neuro-sama Deep Dives](https://shapes.inc/fandom/neuro-sama/deep-dive)
- [Neuro-sama Fandom Wiki](https://virtualyoutuber.fandom.com/wiki/Neuro-sama)
- [Vedal AI GitHub](https://github.com/VedalAI)
- [Teck's Treehouse: Technology behind Neuro-sama](https://teck78.blogspot.com/2025/11/my-thoughts-on-technology-behind-ai.html)
- [Medium: Rise of the vTubers — The Case of Neuro-Sama](https://medium.com/@chrisgrahamstevens/rise-of-the-vtubers-part-1-the-case-of-neuro-sama-95562a3cb3f3)
