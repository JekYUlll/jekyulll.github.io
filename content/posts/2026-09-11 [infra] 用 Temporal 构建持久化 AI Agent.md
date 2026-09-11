+++
date = '2026-09-11T21:56:49+08:00'
draft = false
title = '用 Temporal 构建持久化 AI Agent'
author = 'JekYUlll'
lastmod = '2026-09-11T21:56:49+08:00'
tags = ['temporal', 'durable-execution', 'ai-agent']
categories = ['infra']
+++

Agent 的 demo 都好写：调 LLM、解析 tool call、执行工具、循环。上线之后，麻烦几乎全在循环外面：provider 限流要等、网络抖动要重试、进程可能被部署挤掉、有些操作还得等一个人点「同意」。

这些事堆在一个普通 Python 循环里，你迟早要亲手实现一套东西：状态持久化、幂等重试、崩溃恢复、定时器、外部事件唤醒。听着耳熟，是因为工作流引擎干这些干了几十年。多跑一遍的代价也不只是 token 账单，同样输入两次回答可能不一样，业务逻辑会跟着飘。

Temporal 就是一个持久化执行（durable execution）平台，开源版本和托管云都有。它的思路是把上面这些东西搬过来：agent 的编排逻辑放进 Workflow，所有副作用（LLM 调用、工具执行）放进 Activity，状态和重试由引擎负责。

要不要引入这一层，判断标准很朴素：流程里有没有「跨进程存活」的等待（审批、定时、外部事件），有没有重试起来很贵的副作用（按次计费的 LLM 调用、真实退款）。都没有，直接写个循环就好。

## 为什么不自己存一个状态行

「用 Postgres 存个 checkpoint 不就行了？」可以，但下面这些边界都得你自己处理。

崩溃恰好发生在「调了 LLM 但结果还没写库」的瞬间，这步算做了还是没做？重试会不会把已经执行的副作用做第二遍（比如重复退款）？等待外部事件的那几天，谁负责在事件到来时把流程叫醒？多步骤之间的顺序和并行又怎么保证？工作流引擎把这些全变成了默认语义，你不用逐个发明。

## 先分清 Workflow 和 Activity

Workflow 负责编排：分支、循环、等待事件。它必须是确定性的，同样的输入和历史，重放多少次结果都一样。所以 Workflow 里不能直接发起网络请求，也不能用 `random`、`datetime.now()` 这类东西，要用 `workflow.uuid4()`、`workflow.now()` 代替。

Activity 负责副作用：调 LLM、查数据库、发退款。Activity 里随便做 I/O，失败了 Temporal 按重试策略自动重试。默认指数退避，从 1 秒开始，系数 2，最长间隔 100 秒，次数不限。注意「次数不限」这条：发退款这类非幂等操作要么自己带上幂等 key，要么把重试上限收紧。

崩溃恢复靠两者配合：每个 Activity 的结果都写进 Event History。worker 重启后，引擎拿 history 把 Workflow 代码重放到断点，已完成 Activity 的结果直接从历史里取，不再执行。把 Event History 当成一本账：谁在什么时候决定了什么、执行了什么、结果是什么，全在账上。恢复就是照着账本把编排代码重演一遍，副作用一律不重演。

历史存在 Temporal Server 上（自托管后端可选 Postgres、MySQL、Cassandra，本地开发模式一个二进制搞定），worker 只是干活的进程池，挂几个都不影响流程存续。

## 一个能扛住重启的 agent loop

下面两段代码可以直接跑。LLM 用一个脚本化的 stub 顶替，不配 key 也能把整条链路跑通；接真实模型只改 `call_llm` 的函数体。

```python
# activities.py
import asyncio
from pathlib import Path

from temporalio import activity

LOG_DIR = Path("/tmp/durable_agent_demo")
LOG_DIR.mkdir(exist_ok=True)


@activity.defn
async def call_llm(messages: list[dict]) -> dict:
    """真实项目里把函数体换成 OpenAI / LiteLLM 调用，签名不变。"""
    await asyncio.sleep(0.5)  # 假装做了一次网络往返
    with (LOG_DIR / "llm_calls.txt").open("a") as f:
        f.write("call\n")

    tool_results = [m for m in messages if m["role"] == "tool"]
    if tool_results:
        return {"type": "final", "text": f"结束：{tool_results[-1]['content']}"}
    return {
        "type": "tool",
        "tool": "refund",
        "args": {"order_id": "A123"},
        "requires_approval": True,
    }


@activity.defn
async def execute_tool(decision: dict) -> str:
    await asyncio.sleep(1)
    with (LOG_DIR / "tool_calls.txt").open("a") as f:
        f.write(f"{decision['tool']} {decision['args']}\n")
    return f"已为订单 {decision['args']['order_id']} 发起退款"
```

```python
# workflows.py
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import call_llm, execute_tool

MAX_STEPS = 10  # 单次执行的步数上限，防止 agent 循环失控


@dataclass
class AgentTask:
    goal: str
    messages: list = field(default_factory=list)
    step: int = 0


@workflow.defn
class DurableAgent:
    def __init__(self) -> None:
        self._approved: bool | None = None
        self._pending_tool: str | None = None

    @workflow.signal
    def approve(self, approved: bool) -> None:
        self._approved = approved

    @workflow.query
    def pending_tool(self) -> str | None:
        return self._pending_tool

    @workflow.run
    async def run(self, task: AgentTask) -> str:
        messages = task.messages or [{"role": "user", "content": task.goal}]
        step = task.step

        while step < MAX_STEPS:
            # 历史太长就让 Temporal 换个新执行继续，状态随身带走
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(
                    AgentTask(goal=task.goal, messages=messages, step=step)
                )

            step += 1
            decision = await workflow.execute_activity(
                call_llm,
                messages,
                start_to_close_timeout=timedelta(seconds=60),
            )

            if decision["type"] == "final":
                return decision["text"]

            if decision.get("requires_approval"):
                self._pending_tool = decision["tool"]
                await workflow.wait_condition(
                    lambda: self._approved is not None,
                    timeout=timedelta(hours=24),
                )
                approved = self._approved
                self._approved = None
                self._pending_tool = None
                if not approved:
                    messages.append({"role": "tool", "content": "人工拒绝，退款未执行"})
                    continue

            result = await workflow.execute_activity(
                execute_tool,
                decision,
                start_to_close_timeout=timedelta(seconds=30),
            )
            messages.append({"role": "tool", "content": result})

        return "步数超限，放弃"
```

接线就是标准写法：

```python
# worker.py（省略 import 和连接代码）
worker = Worker(
    client,
    task_queue="agent-demo",
    workflows=[DurableAgent],
    activities=[call_llm, execute_tool],
)
await worker.run()
```

```python
# start / approve（客户端侧，省略 import 和连接）
handle = await client.start_workflow(
    DurableAgent.run, AgentTask(goal="帮我退掉订单 A123"),
    id="agent-demo-1", task_queue="agent-demo",
)
# 审批人点「同意」之后：
await handle.signal("approve", True)
```

几个容易忽略的细节：

- 每轮循环都是一次 execute_activity。LLM 调用写进 Event History，worker 被杀死再拉起来，已经完成的那次调用不会重发。省的是 token，也省掉了「同一问题两次回答不一致」的麻烦。
- 工具调用同样走 Activity，失败自动重试；成功之后结果进 messages，循环继续。
- requires_approval 的分支：把待办工具挂到 `self._pending_tool` 上（query 能查，UI 拿到之后展示给审批人），然后 `wait_condition` 挂起等信号。审批方可以是页面、IM 机器人、另一个服务，只要能发 Temporal signal。
- 等待期间没有任何进程占着。状态在服务端，等 24 小时还是 3 天，对 worker 都无所谓。
- 如果某个工具自己要跑几十分钟（批量导出、大文件处理），把它写成带 heartbeat 的长任务 Activity，失败后能从检查点续跑，而不是从零再来。
- while step < MAX_STEPS 是兜底：agent 自己跑飞时要有上限，而不是让历史无限膨胀。

## 历史会涨，记得 continue-as-new

长会话 agent 每个 turn 都在往历史里写。Temporal 的硬上限是 51200 个事件或 50MB（到 10240 事件 / 10MB 开始警告）。到了就该换新执行：`workflow.continue_as_new(state)` 让当前执行体面结束，新执行沿用同一个 Workflow Id，带着你的状态继续，历史清零。

上面代码里已经放了检查（`is_continue_as_new_suggested`）。官方还建议：有 signal/update 在途时先 `all_handlers_finished()` 再换新。openai-agents 集成里的 customer_service 示例就是这么处理的：

```python
await workflow.wait_condition(
    lambda: workflow.info().is_continue_as_new_suggested()
    and workflow.all_handlers_finished()
)
workflow.continue_as_new(CustomerServiceWorkflowState(...))
```

## 测试也在顺手的地方

Python SDK 带一个 time-skipping 测试环境（`WorkflowEnvironment.start_time_skipping`）：写单测时「等三天审批」这类等待会被瞬间跳过，流程毫秒级跑完。要验证崩溃恢复也可以不用真去 kill 进程：测试里停掉 worker 再起一个，然后断言 activity 的调用次数没有增加。这类断言别靠人工观察，写进 CI，agent 的可靠性才有回归保障。

## 不想手写循环：官方的框架集成

Temporal 已经给了主流 agent 框架的官方集成，手写循环主要是理解原理用的。

- OpenAI Agents SDK（Python / TypeScript）。装 `temporalio[openai-agents]`，Agent、Runner 直接在 workflow 里用；模型调用自动变成 Activity，工具用 `activity_as_tool()` 包一层就跑在 Activity 里，MCP、sandbox、streaming、tracing 都有对应支持。2025 年 7 月公测，2026 年 3 月 GA。

```python
@workflow.defn
class HelloWorldAgent:
    @workflow.run
    async def run(self, prompt: str) -> str:
        agent = Agent(name="Assistant", instructions="You only respond in haikus.")
        result = await Runner.run(agent, input=prompt)
        return result.final_output
```

- Pydantic AI：`pydantic-ai[temporal]`，给 agent 挂 `TemporalDurability` capability，模型请求、工具调用、MCP 通信自动走 Activity。
- LangGraph：`temporalio[langgraph]`（需要 temporalio 1.27+），`LangGraphPlugin` 把图接进来；每个节点声明 `execute_in` "activity" 还是 "workflow"；checkpointer 用 `InMemorySaver` 就行，持久化交给 Temporal。
- 其他框架也都有集成：Google ADK（Python / Go）、AWS Strands（Python / TypeScript）、Vercel AI SDK 与 Mastra（TypeScript）、Spring AI（Java）。完整目录在 docs.temporal.io/ai。

今年 8 月发布的 Temporal Agent Harness（实验阶段）是另一个方向：不替换你用的 SDK，在外面包一层，提供工具审批策略、跨轮次 turn 编排、完整事件流。项目还很早期，API 会变；想了解这套思路的可以直接读它的公告博客，链接在文末。

## 可以马上试的三件事

1. 装 Temporal CLI（docs.temporal.io/cli），`temporal server start-dev` 起本地服务（自带 Web UI，端口 8233），把上面的代码跑起来。
2. worker 杀掉再拉起来，看 workflow 从断点继续。我本地验证过：kill -9 之后重启，已完成的 `call_llm` 没有重发（调用记录还是 1 次），流程跑完全程只有 2 次调用，崩溃前一次、审批后一次。
3. 挑你项目里一个 agent，先把「调 LLM」这一步挪进 Activity，只做这一件事，收益立刻可见。

## 参考

- Temporal「Durable AI」文档总目录：https://docs.temporal.io/ai
- Temporal CLI（本地开发服务 temporal server start-dev）：https://docs.temporal.io/cli
- AI Cookbook（可运行的 recipe 合集）：https://docs.temporal.io/ai/cookbook，源码：https://github.com/temporalio/ai-cookbook
- 其中两个 recipe 与本文代码最接近：agentic loop（https://github.com/temporalio/ai-cookbook/tree/main/agents/agentic_loop_tool_call_openai_python）、人工审批（https://github.com/temporalio/ai-cookbook/tree/main/agents/human_in_the_loop_python）
- OpenAI Agents SDK 集成文档：https://docs.temporal.io/develop/python/integrations/openai-agents
- 官方样例仓库（含 customer_service、sandbox、hosted_mcp）：https://github.com/temporalio/samples-python/tree/main/openai_agents
- 官方教程 Building a Durable AI Agent：https://learn.temporal.io/tutorials/ai/durable-ai-agent/，配套代码：https://github.com/temporal-community/tutorial-temporal-ai-agent
- OpenAI Agents SDK 集成公告（2025-07 公测，2026-03 GA）：https://temporal.io/blog/announcing-openai-agents-sdk-integration
- openai-agents-demos（三个独立 demo）：https://github.com/temporal-community/openai-agents-demos
- Temporal Agent Harness（实验项目）：https://temporal.io/blog/temporal-agent-harness-durable-agent-infrastructure，仓库：https://github.com/temporal-community/temporal-agent-harness
- Pydantic AI 的 Temporal 集成：https://ai.pydantic.dev/durable_execution/temporal/
- LangGraph 集成文档：https://docs.temporal.io/develop/python/integrations/langgraph
- 设计模式：审批 https://docs.temporal.io/design-patterns/approval；长任务 Activity（heartbeat 断点续跑）https://docs.temporal.io/design-patterns/long-running-activity
- 事件历史限制：https://docs.temporal.io/workflow-execution/limits
- Python SDK 测试套件（time-skipping）：https://docs.temporal.io/develop/python/testing-suite
- 社区讨论「The Lord of the Loop」：https://community.temporal.io/t/the-lord-of-the-loop-in-search-of-the-best-abstraction-for-llm-driven-temporal-applications-with-the-simplest-possible-dx/18631
