+++
date = '2026-05-24T15:21:02+08:00'
draft = false
title = 'AI Agent 开发框架三强争霸：MCP、A2A 与 OpenAI Agents SDK 深度对比'
author = 'JekYUlll'
lastmod = '2026-05-24T15:21:02+08:00'
tags = ['ai-agent', 'mcp', 'a2a']
categories = ['infra']
summary = '2025-2026 年，AI Agent 从概念走向规模化落地。MCP 统一了模型与工具之间的连接标准，A2A 打通了 Agent 之间的协作壁垒，OpenAI Agents SDK 提供了极简的多 Agent 编排体验。本文通过核心原理解读、可运行的代码实战以及生态对比表格，帮你理清三者的定位与选型策略。'
+++

## 背景

如果说 2024 年是"百模大战"，那么 2025-2026 年无疑是 **Agent 元年**。你不再只用 API 调用一个模型回答问题，而是让模型拥有工具、记忆和自主决策能力，去完成复杂任务。

然而，Agent 开发面临三个核心问题：

1. **模型怎么像人一样使用工具？** —— 每个模型提供商都有自己的工具调用格式，接入不同数据源需要重复适配。
2. **不同 Agent 之间怎么对话？** —— 不同团队、不同框架构建的 Agent 是孤岛，无法协作。
3. **开发者怎么快速构建多 Agent 应用？** —— 每次都要从头实现 Agent 循环、工具编排、记忆管理，重复造轮子。

针对这三个问题，业界在 2025 年给出了三个重量级的答案：

- **MCP (Model Context Protocol)** — Anthropic 推出的开放协议，统一模型与外部工具/数据的连接标准，被称为"AI 界的 USB-C"。
- **A2A (Agent-to-Agent Protocol)** — Google 贡献给 Linux 基金会的开放协议，解决 Agent 与 Agent 之间的通信与协作。
- **OpenAI Agents SDK** — OpenAI 开源的轻量级 Python 框架，提供开箱即用的多 Agent 编排能力。

本文将从**核心原理、代码实战、生态对比**三个维度深度解析三门技术，帮你理清它们的定位和适用场景。

---

## 核心原理

### MCP：模型连接世界的"万能转接头"

MCP（Model Context Protocol）是 Anthropic 在 2024 年底推出的开放协议。它解决的问题很直接：每个 AI 应用如果要连接数据库、文件系统或第三方 API，都需要写大量的胶水代码。MCP 提供了统一的接口标准，让模型能通过标准化的方式发现和使用外部工具与数据。

MCP 的架构采用 **客户端-服务器（Client-Server）** 模型：

- **MCP Host**：发起请求的应用程序（如 Claude Desktop、VS Code AI 插件）
- **MCP Client**：负责与 Server 建立 1:1 连接的通道
- **MCP Server**：对外暴露资源（Resources）、工具（Tools）和提示词（Prompts）的轻量级服务

MCP 定义了三种核心原语：

| 原语 | 作用 | 类比 |
|------|------|------|
| **Resources** | 暴露数据（文件、数据库记录、API 响应） | GET 请求 |
| **Tools** | 可被模型调用的函数（创建文件、发送邮件） | POST 请求 |
| **Prompts** | 预定义的提示词模板 | 路由模板 |

传输层支持 stdio（本地进程通信）、SSE（Server-Sent Events）和 Streamable HTTP。通信协议基于 JSON-RPC 2.0，协议版本目前为 `2025-11-25`。

### A2A：Agent 之间的"社交协议"

A2A（Agent-to-Agent Protocol）由 Google 在 2025 年 4 月发布，并于同年 9 月贡献给 Linux 基金会。它解决的是 MCP 没有覆盖的问题——**Agent 与 Agent 之间的通信**。

MCP 的连接方向是：Model → Tools/Data。而 A2A 的连接方向是：Agent → Agent。它们是互补关系——你可以用 MCP 让 Agent 接入工具，再用 A2A 让这个 Agent 与另一个 Agent 协作。

A2A 的核心设计包括：

- **Agent Card**：每个 Agent 通过一个 JSON 格式的"名片"发布自己的能力、端点和认证方式。客户端通过 Agent Card 发现和连接 Agent。
- **JSON-RPC 2.0 over HTTP(S)**：标准化的通信协议，支持同步请求/响应、SSE 流式传输、以及异步推送通知。
- **Task 生命周期管理**：A2A 定义了标准的任务状态机（submitted → working → input-required → completed → failed），支持长时间运行的任务。
- **内容协商（Content Negotiation）**：Agent 之间可以协商交互格式（纯文本、结构化 JSON、文件等）。

A2A 的一个重要设计哲学是 **Preserving Opacity（保持不透明性）** ——Agent 之间协作时不需要暴露内部状态、记忆或工具实现细节，这对安全性和知识产权保护至关重要。

### OpenAI Agents SDK：极简的多 Agent 编排框架

OpenAI Agents SDK 是 OpenAI 在 2025 年开源的 Python 框架（npm 上也提供了 JS/TS 版本）。它不是协议，而是一个**开发框架**。其核心概念包括：

- **Agent**：配置了指令、工具、护栏（Guardrails）和转交权（Handoffs）的 LLM 实例。
- **Handoff**：当前 Agent 可以将任务转交给另一个 Agent，形成多 Agent 协作。
- **Guardrails**：在输入和输出阶段进行检查的安全机制。
- **Tool**：可以是普通 Python 函数、MCP 工具或 Hosted Tool。
- **Session**：自动管理对话历史，跨多次运行保持上下文。
- **Tracing**：内置的可观测性工具，追踪每个 Agent 的运行轨迹。

SDK 提供两种 Agent 模式：
1. **标准 Agent**：轻量级，使用 LLM 的 function calling 进行工具调用。
2. **Sandbox Agent**（v0.14+）：在隔离的文件系统环境中运行，适合需要写代码、操作文件的场景。

OpenAI Agents SDK 的一个重要特点是 **provider-agnostic（提供商无关）**——它不仅支持 OpenAI 自己的 Responses API 和 Chat Completions API，还通过 `any-llm` 和 `LiteLLM` 支持 100+ 其他 LLM。

---

## 代码实战

### MCP：用 Python 构建一个天气查询工具

首先安装 MCP Python SDK：

```bash
pip install "mcp[cli]"
```

以下代码实现了一个 MCP Server，暴露两个工具：`get_forecast`（获取天气预报）和 `get_alerts`（获取天气预警）：

```python
# weather_server.py
import httpx
from mcp.server.fastmcp import FastMCP

# 创建 MCP Server
mcp = FastMCP("Weather Service", json_response=True)

# 定义工具：获取天气预报
@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """获取指定坐标的天气预报"""
    url = f"https://api.weather.gov/points/{latitude},{longitude}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"User-Agent": "mcp-demo/1.0"})
        resp.raise_for_status()
        forecast_url = resp.json()["properties"]["forecast"]
        forecast_resp = await client.get(forecast_url)
        forecast_resp.raise_for_status()
        return forecast_resp.json()["properties"]["periods"][:3]

# 定义工具：获取天气预警
@mcp.tool()
async def get_alerts(state: str) -> list[dict]:
    """获取指定州的天气预警"""
    url = f"https://api.weather.gov/alerts/active/area/{state}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"User-Agent": "mcp-demo/1.0"})
        resp.raise_for_status()
        data = resp.json()
        return [
            {"event": a["properties"]["event"], "headline": a["properties"]["headline"]}
            for a in data.get("features", [])[:5]
        ]

# 定义资源：暴露静态数据
@mcp.resource("weather://supported-states")
async def get_supported_states() -> str:
    """返回支持的美国州代码列表"""
    return "AL, AK, CA, NY, TX, FL, WA, OR"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

运行方式：

```bash
# 开发模式（带 MCP Inspector 调试 UI）
uv run mcp dev weather_server.py

# 直接运行（stdio 传输）
python weather_server.py

# Streamable HTTP 传输
python -c "
from weather_server import mcp
mcp.run(transport='streamable-http')
"
```

代码要点：

- `@mcp.tool()` 装饰器将函数注册为 Tool，模型可以通过 function calling 自动调用。
- `@mcp.resource()` 注册静态资源，支持 URI 模式匹配。
- FastMCP 自动处理 JSON-RPC 消息序列化和反序列化。
- `json_response=True` 让工具返回结构化 JSON 而非纯文本。

### A2A：让两个 Agent 互相协作

安装 A2A Python SDK：

```bash
pip install a2a-sdk
```

以下代码实现了一个简单的 A2A Server（翻译 Agent）和 A2A Client（请求翻译的编排 Agent）：

**Step 1: A2A Server — 翻译 Agent**

```python
# a2a_translation_server.py
from a2a_sdk.server import AgentCard, A2AServer
from a2a_sdk.types import (
    Task, TaskState, TaskStatus, Message, TextContent,
    AgentCard as AgentCardModel,
)

class TranslationAgent:
    """翻译 Agent，支持中英文互译"""

    async def get_agent_card(self) -> AgentCardModel:
        return AgentCardModel(
            name="Translation Agent",
            description="中英文翻译服务",
            url="http://localhost:8080",
            version="1.0.0",
            capabilities={
                "translation": {
                    "source_languages": ["zh", "en"],
                    "target_languages": ["zh", "en", "ja", "ko", "fr"],
                }
            },
        )

    async def handle_task(self, task: Task) -> Task:
        """处理翻译任务"""
        # 从任务消息中提取待翻译文本
        message = task.messages[-1]
        text = message.content.text

        # 简单翻译逻辑（生产环境应调用 LLM 或翻译 API）
        translations = {
            "hello": "你好",
            "world": "世界",
            "你好": "Hello",
            "世界": "World",
        }
        translated = translations.get(text.strip().lower(), f"[翻译]{text}")

        task.status = TaskStatus(state=TaskState.COMPLETED)
        task.messages.append(
            Message(
                role="agent",
                content=TextContent(text=translated),
            )
        )
        return task

# 启动 A2A Server
if __name__ == "__main__":
    import uvicorn
    from a2a_sdk.server import create_app

    agent = TranslationAgent()
    app = create_app(agent, host="0.0.0.0", port=8080)
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

**Step 2: A2A Client — 编排 Agent 调用翻译服务**

```python
# a2a_client_example.py
from a2a_sdk.client import A2AClient
from a2a_sdk.types import Task, TaskState, Message, TextContent

async def translate_via_a2a():
    """通过 A2A 协议调用翻译 Agent"""
    client = A2AClient(base_url="http://localhost:8080")

    # 1. 获取 Agent Card（发现能力）
    card = await client.get_agent_card()
    print(f"发现 Agent: {card.name}")
    print(f"能力: {list(card.capabilities.keys())}")

    # 2. 发送翻译任务
    task = Task(
        messages=[
            Message(
                role="user",
                content=TextContent(text="hello"),
            )
        ]
    )
    result = await client.send_task(task)

    # 3. 轮询任务结果
    while result.status.state != TaskState.COMPLETED:
        result = await client.get_task(result.id)

    print(f"翻译结果: {result.messages[-1].content.text}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(translate_via_a2a())
```

A2A 的关键工作流：

1. **服务发现**：Client 通过 `get_agent_card()` 获取 Agent Card，了解对方的能力。
2. **任务提交**：Client 通过 `send_task()` 提交包含消息的 Task。
3. **状态轮询/推送**：Server 通过 Task 状态机（submitted → working → completed/failed）通知 Client。
4. **多轮对话**：如果 Agent 需要更多信息，可以返回 `input-required` 状态，Client 补充输入后继续。

### OpenAI Agents SDK：三行代码搭建多 Agent 系统

```python
# multi_agent_demo.py
import asyncio
from agents import Agent, Runner, set_trace_processors
from agents.tracing.processors import ConsoleTracingProcessor

# 启用控制台追踪
set_trace_processors([ConsoleTracingProcessor()])

# 定义三个专业 Agent
triage_agent = Agent(
    name="Triage Agent",
    instructions="你是客服分流 Agent。根据用户的问题类型，将任务转交给对应的专业 Agent。",
    handoffs=["billing_agent", "tech_support_agent"],
)

billing_agent = Agent(
    name="Billing Agent",
    instructions="你是账单 Agent。回答用户关于账单、发票和支付的问题。",
)

tech_support_agent = Agent(
    name="Tech Support Agent",
    instructions="你是技术支持 Agent。帮助用户解决产品使用问题和技术故障。",
)

# 也可以用函数作为工具
async def get_account_info(account_id: str) -> dict:
    """获取用户账户信息"""
    return {
        "account_id": account_id,
        "name": "张三",
        "plan": "pro",
        "balance": 199.00,
    }

billing_agent.tools = [get_account_info]

async def main():
    # 运行 Agent
    result = await Runner.run(
        triage_agent,
        input="我的账单上个月扣了 199 元，能帮我查一下吗？",
    )
    print(result.final_output)

if __name__ == "__main__":
    asyncio.run(main())
```

**Sandbox Agent 模式**（OpenAI Agents SDK v0.14+）：

```python
# sandbox_agent_demo.py
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.entries import GitRepo
from agents.sandbox.sandboxes import UnixLocalSandboxClient

agent = SandboxAgent(
    name="Code Reviewer",
    instructions="检查克隆下来的仓库代码，发现 Bug 并修复。",
    default_manifest=Manifest(
        entries={
            "my_project": GitRepo(
                repo="openai/openai-agents-python",
                ref="main",
            ),
        }
    ),
)

result = Runner.run_sync(
    agent,
    "检查这个项目的 README，并告诉我它解决什么问题。",
    run_config=RunConfig(
        sandbox=SandboxRunConfig(client=UnixLocalSandboxClient())
    ),
)
print(result.final_output)
```

---

## 生态现状 & 对比

### MCP 生态（截至 2026 年 5 月）

MCP 的生态发展最为成熟：

- **客户端支持**：Claude Desktop、ChatGPT、VS Code（GitHub Copilot）、Cursor、JetBrains IDE、Eclipse
- **官方 SDK**：Python（`mcp`）、TypeScript（`@modelcontextprotocol/sdk`）、Go、Java
- **预构建 Server**：官方和社区提供了数百个现成的 MCP Server（文件系统、数据库、GitHub、Slack、Notion、Figma 等）
- **托管平台**：ModelScope MCP、Cloudflare Workers MCP
- **MCP Inspector**：官方调试工具，可视化查看 Server 暴露的工具和资源

### A2A 生态（截至 2026 年 5 月）

A2A 虽然年轻，但发展迅速：

- **发起方**：Google（2025-04 发布 → 2025-09 捐赠给 Linux 基金会）
- **官方 SDK**：Python（`a2a-sdk`）、Go、JS/TS、Java、.NET
- **DeepLearning.AI 课程**：与 Google Cloud、IBM Research 合作的专项课程
- **合作伙伴**：Google Cloud、IBM、LangChain（LangGraph 集成）
- **示例项目**：多 Agent 医疗系统（A2A + LangGraph）、跨框架 Agent 协作

### OpenAI Agents SDK 生态

- **开源协议**：Apache 2.0
- **安装量**：PyPI 高下载量，社区活跃
- **支持模型**：OpenAI 全系列 + 100+ 第三方 LLM（via any-llm / LiteLLM）
- **集成**：内置 MCP 工具支持（可与 MCP Server 互连）、Tracing 可视化、Pydantic 数据结构
- **Sandbox 模式**：支持 Manifest 声明式环境配置、GitRepo 克隆、Docker 容器

### 三者核心对比

| 维度 | MCP | A2A | OpenAI Agents SDK |
|------|-----|-----|-------------------|
| **定位** | 协议（Model ↔ Tools） | 协议（Agent ↔ Agent） | 开发框架（多 Agent 编排） |
| **发起方** | Anthropic | Google（Linux 基金会） | OpenAI |
| **核心问题** | 模型如何统一调用工具 | Agent 之间如何协作 | 如何快速构建多 Agent 应用 |
| **通信协议** | JSON-RPC 2.0 | JSON-RPC 2.0 over HTTP(S) | Python SDK（内部编排） |
| **传输层** | stdio / SSE / Streamable HTTP | HTTP(S) / SSE / Push | N/A（内存进程间） |
| **服务发现** | 资源 URI 模式 | Agent Card（JSON） | 代码静态定义 |
| **任务模式** | 请求-响应 | 异步任务状态机 | Runner.run() / Handoff |
| **学习曲线** | 中等 | 中等 | 低 |
| **成熟度** | 成熟（2024-11 发布） | 发展中（2025-04 发布） | 成熟（2025 年开源） |
| **供应商锁定** | 无（开放协议） | 无（开放协议） | 低（支持第三方 LLM） |
| **可观测性** | 需自行集成 | 标准日志 | 内置 Tracing |
| **适用场景** | 工具集成、数据接入 | 跨组织 Agent 协作 | 单体多 Agent 应用 |

### 如何选择？

- **你需要让 LLM 访问数据库/文件/API？** → **MCP**。开发一个 MCP Server，所有支持 MCP 的客户端都能用它。
- **你有多个 Agent 需要互相协作？** → **A2A**。通过 Agent Card 发现能力，用标准任务协议通信。
- **你只想快速构建一个多 Agent 应用？** → **OpenAI Agents SDK**。Handoff、Guardrails、Session 开箱即用。
- **三者可以组合使用**：Agent（OpenAI SDK）→ 通过 MCP 接入工具 → 通过 A2A 与其他 Agent 协作。

---

## 今日可执行动作

1. **跑通 MCP 天气 Server 示例**：安装 `pip install mcp`，运行上面的 `weather_server.py`，然后用 MCP Inspector (`uv run mcp dev weather_server.py`) 查看可视化界面，体验工具调用的全过程。

2. **尝试 A2A 翻译 Agent 协作**：安装 `pip install a2a-sdk`，启动翻译 Server 后运行 Client 脚本，观察 Agent 之间如何通过 Agent Card 发现能力并完成翻译任务。

3. **用 OpenAI Agents SDK 改写一个简单脚本**：把你平时需要手动调 LLM API 的小工具（如摘要生成、翻译工具），用 Agent + Tool 的方式重写，体验 Agent 自动规划调用链的能力。

---

## 参考

- MCP 官方文档: https://modelcontextprotocol.io/docs/getting-started/intro
- MCP 协议规范: https://github.com/modelcontextprotocol/specification
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- A2A Protocol 文档: https://a2a-protocol.org/latest/
- A2A GitHub 仓库: https://github.com/a2aproject/A2A
- A2A Python SDK: https://github.com/a2aproject/a2a-python
- A2A 协议中文资源: https://www.a2aprotocol.org/zh
- OpenAI Agents SDK: https://github.com/openai/openai-agents-python
- OpenAI Agents SDK 文档: https://openai.github.io/openai-agents-python/
- 深度长文：谷歌 A2A 协议权威详解 - 知乎: https://zhuanlan.zhihu.com/p/1894797987739324876
- MCP 一篇就够了 - 知乎: https://zhuanlan.zhihu.com/p/29001189476
- 深入理解 MCP 协议 - JavaGuide: https://javaguide.cn/ai/agent/mcp.html
- AI Agent 框架全景指南: https://www.cnblogs.com/qiniushanghai/p/19952939
