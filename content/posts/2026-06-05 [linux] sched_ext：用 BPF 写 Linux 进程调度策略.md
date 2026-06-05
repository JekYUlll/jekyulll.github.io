+++
date = '2026-06-05T10:31:49+08:00'
draft = false
title = 'sched_ext：用 BPF 写 Linux 进程调度策略'
author = 'JekYUlll'
lastmod = '2026-06-05T10:31:49+08:00'
tags = ['sched-ext', 'bpf', 'scheduler', 'linux-kernel']
categories = ['linux']
+++

## 背景

Linux 内核的进程调度器，二十年来只有两个选择：CFS（完全公平调度）和实时调度类。你想改调度策略？要么重编译内核，要么打 out-of-tree 补丁。两种方式对生产环境都太重了：改一次调度参数就要重启机器，谁能忍？

游戏玩家想要更低的输入延迟，数据库管理员想要 NUMA 感知的任务放置，云厂商想让后台任务别打扰前台容器。需求千差万别，靠一个通用调度器覆盖所有人没可能。

Linux 6.12 给了答案：**sched_ext**（extensible scheduler class）。它把调度策略写成了 BPF 程序，注入、热替换、出错了自动回退到 CFS，全程不用重启。

## 核心原理

sched_ext 不是一个新调度器。它是一个**可编程的调度框架**。思路很简单：内核在调度决策的每个关键点（选 CPU、入队、派发）预留钩子，你的 BPF 程序实现这些钩子的逻辑。

### 调度生命周期

一个任务从醒来到被 CPU 执行，经过三个钩子：

1. **`select_cpu()`**：任务唤醒时，选一个目标 CPU。返回优化提示，不强制绑定。
2. **`enqueue()`**：把任务放进调度队列。这里决定任务优先级和排队位置。
3. **`dispatch()`**：CPU 空闲时调用，从调度队列取下一个任务投入运行。

BPF 调度器不需要实现全部钩子，只实现你关心的那几个。`ops.name` 是唯一的强制字段，其余的都可以留空。

### DSQ：核心抽象

调度队列在 sched_ext 里叫做 DSQ（Dispatch Queue）。三种：

- `SCX_DSQ_LOCAL`：每 CPU 一个本地队列，任务放这里只被本 CPU 消费。
- `SCX_DSQ_GLOBAL`：全局 FIFO 队列，任何 CPU 缺任务了就从这取。
- 自定义 DSQ：通过 `scx_bpf_create_dsq()` 创建，可以绑 NUMA 节点，支持优先级排序（`scx_bpf_dsq_insert_vtime`）。

DSQ 的设计让 BPF 调度器用最少的代码表达丰富的调度意图。想搞全局公平排队？全部往 GLOBAL 塞。想 per-CPU 亲和？直接用 LOCAL。想分层调度？自定义 DSQ + 优先级键。

### 容错机制

sched_ext 的容错做得最到位。BPF 程序出 bug 不会拖垮系统：

- BPF verifier 在加载时做静态安全检查，非法内存访问直接拒绝。
- 运行时如果调度器卡住（超过阈值时间没调度），内核自动切回 CFS。
- 按 `SysRq-S` 手动回退，恢复所有任务到 CFS 调度类。

这意味着你可以在生产环境先加载一个实验性调度器，出问题内核兜底。

## 代码实战

下面是一个最简调度器：全局 FIFO 队列 + 动态时间片。来自 Johannes Bechberger 的 [minimal-scheduler](https://github.com/parttimenerd/minimal-scheduler) 项目。

### 初始化：创建共享 DSQ

```c
// sched_ext.bpf.c
#include <vmlinux.h>
#include <bpf/bpf_helpers.h>

#define SHARED_DSQ_ID 0

s32 BPF_STRUCT_OPS_SLEEPABLE(sched_init)
{
    // 创建 ID=0 的共享 DSQ，不绑 NUMA (-1)
    return scx_bpf_create_dsq(SHARED_DSQ_ID, -1);
}
```

### 入队：动态时间片

```c
int BPF_STRUCT_OPS(sched_enqueue, struct task_struct *p,
                   u64 enq_flags)
{
    // 基础时间片 5ms，除以队列长度，任务越多片越短
    u64 slice = 5000000u / scx_bpf_dsq_nr_queued(SHARED_DSQ_ID);
    scx_bpf_dispatch(p, SHARED_DSQ_ID, slice, enq_flags);
    return 0;
}
```

`scx_bpf_dispatch()` 一次调用搞定"把任务放入队列 + 指定时间片"。不用维护额外的 runqueue 数据结构。

### 派发：从全局队列取任务

```c
int BPF_STRUCT_OPS(sched_dispatch, s32 cpu, struct task_struct *prev)
{
    // consume 从 DSQ 头部取一个任务放到当前 CPU 上执行
    scx_bpf_consume(SHARED_DSQ_ID);
    return 0;
}
```

### 注册调度器

```c
SEC(".struct_ops.link")
struct sched_ext_ops sched_ops = {
    .enqueue   = (void *)sched_enqueue,
    .dispatch  = (void *)sched_dispatch,
    .init      = (void *)sched_init,
    .flags     = SCX_OPS_ENQ_LAST | SCX_OPS_KEEP_BUILTIN_IDLE,
    .name      = "minimal_scheduler",
};

char _license[] SEC("license") = "GPL";
```

### 编译加载

```bash
# 生成内核类型定义
bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h

# 编译 BPF 目标文件
clang -target bpf -g -O2 -c sched_ext.bpf.c -o sched_ext.bpf.o -I.

# 注册调度器（需 root）
sudo bpftool struct_ops register sched_ext.bpf.o /sys/fs/bpf/sched_ext

# 验证
cat /sys/kernel/sched_ext/root/ops   # → minimal_scheduler

# 卸载（从内核摘除）
sudo rm /sys/fs/bpf/sched_ext/sched_ops
```

整个流程 5 条命令，从编译到运行不超过 30 秒。和重编译内核比起来，效率差了三个数量级。

## 生态现状

sched_ext 自 6.12 合入主线后，社区围绕着 `tools/sched_ext` 和 GitHub 上的 [sched-ext/scx](https://github.com/sched-ext/scx) 项目快速成长。目前可用的调度器：

| 调度器 | 定位 | 核心思路 |
|--------|------|----------|
| `scx_simple` | 教学/基准 | FIFO 或加权虚拟时间 |
| `scx_lavd` | 游戏/低延迟 | 提升交互任务优先级，压后台 |
| `scx_bpfland` | 桌面交互 | 按阻塞频率判断交互性 |
| `scx_rustland` | 混合架构 | BPF 做快速路径，Rust 用户态做复杂决策 |
| `scx_rusty` | 缓存亲和 | 按 L3 cache 拓扑分组任务 |
| `scx_nest` | 异构 CPU | 高频核跑前台，低频核跑后台 |

CachyOS（基于 Arch 的性能优化发行版）已经将 sched_ext 集成进默认内核，提供一键切换调度器的 GUI 工具。以前你只能在 Phoronix 评测里看这些调度器的对比数据，现在点点鼠标就能实测。

从架构演进看，`scx_rustland` 的混合模式最值得关注。纯 BPF 调度器受限于 BPF verifier 的指令数上限（100 万条）和禁止循环的约束，复杂算法（遗传调度、ML 驱动的预测）写不了。Rust 用户态 + BPF 内核态的架构破了这个限制：用户态做重计算，BPF 做快速派发。

## 今日可执行动作

1. 跑一个最简调度器：clone `minimal-scheduler`，在 VM 或测试机上加载，`dmesg` 看内核日志确认 "BPF scheduler enabled"。
2. 用 scx_lavd 打游戏：如果你用 Arch/CachyOS，`paru -S scx-scheds`，然后 `sudo scx_lavd` 启动，开一局 CS2 感受输入延迟变化。
3. 改时间片实验：把 `minimal_scheduler` 的 5ms 基础片改成 50ms 或 100μs，用 `perf sched latency` 看调度延迟分布的变化。

## 参考

- [Kernel docs: Extensible Scheduler Class](https://docs.kernel.org/scheduler/sched-ext.html)
- [Johannes Bechberger: A Minimal Scheduler with eBPF, sched_ext and C](https://mostlynerdless.de/blog/2024/10/25/a-minimal-scheduler-with-ebpf-sched_ext-and-c/)
- [William Good: Exploring sched_ext: BPF-Powered CPU Schedulers](https://hackmd.io/@williamgood/Hkp1fKA5Jg)
- [eunomia: eBPF Tutorial – BPF Scheduler](https://eunomia.dev/tutorials/44-scx-simple/)
- [DeepWiki: sched_ext and BPF Framework](https://deepwiki.com/sched-ext/scx/2.1-sched_ext-and-bpf-framework)
- [sched-ext/scx on GitHub](https://github.com/sched-ext/scx)
- [CachyOS sched-ext tutorial](https://wiki.cachyos.org/configuration/sched-ext/)
