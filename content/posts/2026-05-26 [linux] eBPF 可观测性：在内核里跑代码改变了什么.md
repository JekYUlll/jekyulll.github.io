+++
date = '2026-05-26T10:00:44+08:00'
draft = false
title = 'eBPF 可观测性：在内核里跑代码改变了什么'
author = 'JekYUlll'
lastmod = '2026-05-26T10:00:44+08:00'
tags = ['ebpf', 'observability']
categories = ['linux']
+++

## 背景

Linux 内核的可观测性一直很尴尬。

你想知道一个进程为什么卡住——是等 I/O？被锁阻塞？还是调度延迟？传统的答案无非是读 `/proc`、写 `strace`、或者在内核源码里插 `printk`。前者信息太粗，中间的性能抖动太大，后者要重新编译内核重启机器。

这三种方案有一个共同问题：**它们都是静态的。** 内核的行为在你启动时就固定了，你只能从预先定义的接口观察它。想观察一个没预留接口的内部状态？门都没有。

eBPF 改变了这个局面的方式很简单粗暴——**让你安全地在内核里跑代码。**

不是内核模块那种"慎重，跑崩了没人管"的代码。eBPF 程序经过严格的验证器（verifier）检查：所有路径必须可终止、不能越界访存、不能调用未授权的内核函数。通过了才让你加载。跑的时候还有 JIT 编译成原生指令，性能接近手写内核模块。

这篇文章不讲 eBPF 网络那个方向（Cilium 已经讲烂了），而是聚焦它怎么改变了 Linux 的可观测性——你如何在内核任意函数入口出口挂钩子、收集数据、传到用户态分析。值得聊聊这个管道怎么设计的。

## 核心原理

### 事件驱动的沙箱

eBPF 程序本身不是服务，它不循环不监听——它是**事件驱动的**。你把它挂到一个 hook 上，hook 触发了它才执行。常见的 hook：

| Hook 类型 | 触发时机 | 典型用途 |
|-----------|----------|----------|
| `kprobe` / `kretprobe` | 内核函数入口/返回 | 任意内核函数插桩 |
| `tracepoint` | 内核静态 tracepoint | 稳定 ABI，生产环境优先 |
| `fentry` / `fexit` | 函数入口/返回（BTF 版本） | 比 kprobe 更快更安全 |
| `uprobe` | 用户空间函数 | 应用层 profiling |
| `perf_event` | PMC 计数器溢出 | CPU 性能分析 |

其中 fentry/fexit 是最近几年才稳定的改进型——它不需要像 kprobe 那样在指令里插入断点，性能更好，参数访问也直接。

### 四阶段生命周期

一个 eBPF 程序在系统中的旅程分四步：

1. **Open**——libbpf 解析编译好的 `.o` 文件，发现 maps、programs、全局变量
2. **Load**——创建 BPF maps，校验程序，加载到内核
3. **Attach**——把程序挂到 hook 上，开始干活
4. **Destroy**——分离、卸载、释放

现代 libbpf 用 **BPF skeleton** 来管理这四步。你编译时 `bpftool` 从 `.o` 文件生成一个 `.skel.h` 头文件，用户态代码直接调用：

```c
// 用户态：三步搞定加载
struct minimal_bpf *skel = minimal_bpf__open();
minimal_bpf__load(skel);
minimal_bpf__attach(skel);

// 干完活清理
minimal_bpf__destroy(skel);
```

几乎就是 1:1 映射上面那四阶段。这个 skeleton 把 bytecode 也嵌在里面了，你部署时不用到处找 `.o` 文件。

### CO-RE：编译一次，到处跑

早期 BCC 的方案是运行时在目标机器上编译——它把 LLVM Clang 打包进 Python 包，每次加载现场编。这办法能用，但太重了：二进制几百 MB，依赖链长，容器里跑难受。

CO-RE（Compile Once – Run Everywhere）解决了这个问题。它依赖内核的 **BTF**（BPF Type Format）信息——内核已经把自己所有类型结构暴露在 `/sys/kernel/btf/vmlinux` 里了。你用 `bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h` 生成一个头文件，编译时引进去。结构体字段的偏移量不是硬编码的，而是作为重定位信息记录在 ELF 里。加载时 libbpf 根据当前内核的 BTF 自动调整。

这意味着你在 Ubuntu 22.04 上编译的程序，可以原封不动地跑在 CentOS 7 的 4.9 内核上——前提是内核开启了 BTF。

### BPF Maps：内核和用户态的共享内存

数据怎么从内核传出来？eBPF 程序不能直接写文件、不能发网络请求。它通过 **BPF Maps** 和用户态交换数据。

| Map 类型 | 用途 |
|----------|------|
| `BPF_MAP_TYPE_HASH` | KV 存储，记录临时状态 |
| `BPF_MAP_TYPE_ARRAY` | 预分配大小的计数器 |
| `BPF_MAP_TYPE_RINGBUF` | 高性能流式事件传输（内核 ≥5.8） |
| `BPF_MAP_TYPE_PERF_EVENT_ARRAY` | 旧版事件传输 |

Ring buffer 是推荐的现代方案。它是个多生产者单消费者的环形队列，支持可变长度事件，不会像 perf event array 那样丢事件。

### 安全性是怎么保证的

验证器是最有意思的组件。加载时它遍历程序的所有执行路径，检查：
- 所有循环必须有可证明的上界（或者没有循环）
- 没有空指针解引用
- 没有越界访问
- 不会调用危险的内核函数

**验证器通过的代码一定不会 panic 内核。** 这个保证太强了——你在生产环境挂一个 kprobe 看 `do_sys_open` 的参数，如果程序出错，内核直接拒绝加载，不会崩掉你的容器。

## 代码实战

用 libbpf-bootstrap 的 minimal 和 bootstrap 两个例子说明。

### 最小 eBPF 程序

内核侧（minimal.bpf.c）：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

SEC("tp/syscalls/sys_enter_write")
int handle_tp(void *ctx)
{
    int pid = bpf_get_current_pid_tgid() >> 32;
    bpf_printk("BPF triggered from PID %d.\n", pid);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

这 10 行代码在每次 `write()` 系统调用时打印一行日志。`SEC()` 宏声明了这个 eBPF 程序挂在 `sys_enter_write` tracepoint 上。

用户侧（minimal.c）：

```c
int main(int argc, char **argv)
{
    struct minimal_bpf *skel;

    skel = minimal_bpf__open();
    skel->bss->my_pid = getpid();    // 只监控自己
    minimal_bpf__load(skel);
    minimal_bpf__attach(skel);

    printf("Running. Check /sys/kernel/debug/tracing/trace_pipe\n");
    sleep(100);

    minimal_bpf__destroy(skel);
    return 0;
}
```

编译运行：

```bash
$ make minimal
$ sudo ./minimal
$ sudo cat /sys/kernel/debug/tracing/trace_pipe
# <...>-3840345 [001] .... 123.456: BPF triggered from PID 3840345.
```

### 实战级：进程生命周期监控

bootstrap 例子更贴近真实用途——它监控 `exec()` 和 `exit()`，记录每个进程的 PID、PPID、存活时长。内核侧核心逻辑：

```c
/* 记录 fork 时间 */
SEC("tp/sched/sched_process_exec")
int handle_exec(struct trace_event_raw_sched_process_exec *ctx)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    u64 ts = bpf_ktime_get_ns();
    bpf_map_update_elem(&exec_start, &pid, &ts, BPF_ANY);
    // ...
}

/* 进程退出时计算用时，传回用户态 */
SEC("tp/sched/sched_process_exit")
int handle_exit(struct trace_event_raw_sched_process_template *ctx)
{
    u64 *start_ts = bpf_map_lookup_elem(&exec_start, &pid);
    duration_ns = bpf_ktime_get_ns() - *start_ts;

    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    e->pid = pid;
    e->duration_ns = duration_ns;
    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

用户态用 `ring_buffer__poll()` 轮询事件，收到就格式化打印：

```bash
$ sudo ./bootstrap -d 50
TIME     EVENT COMM             PID     PPID    FILENAME/EXIT CODE
19:18:32 EXEC  bash             3817109 402466  /bin/bash
19:18:33 EXIT  timeout          3817109 402466  [0] (126ms)
```

`-d 50` 过滤掉存活不到 50ms 的短命进程，避免输出被 `grep` 刷爆。

## 生态现状

eBPF 已经不是一个实验性技术了。以下产品都在生产环境依赖它：

| 项目 | 用途 | eBPF 角色 |
|------|------|-----------|
| **Cilium** | Kubernetes 网络 + 安全 | XDP/TC BPF 做转发、策略、Hubble 可观测 |
| **Falco** | 容器运行时安全 | kprobe/tracepoint 监控系统调用异常 |
| **Pixie** | K8s 应用可观测 | uprobe 自动捕获 HTTP/gRPC/TLS 请求 |
| **Katran** | 四层负载均衡 | XDP BPF 做 DSR 转发，Facebook 生产使用 |
| **bpftrace** | 单行命令 tracing | 类似 awk 的语法，编译成 BPF 程序即时执行 |
| **Inspektor Gadget** | K8s 容器调试 | 利用 eBPF 分析容器级别的系统行为 |

其中 Cilium 是体量最大的——它用 eBPF 替换了 kube-proxy 的 iptables，在 5.10+ 内核上数据面性能提升一个数量级。Falco 被 Sysdig 收购后已经成为容器安全领域的主流工具之一。

## 今日可执行动作

1. **装 bpftrace，跑一条命令看看你的系统在干嘛**
   ```bash
   # Ubuntu/Debian
   sudo apt install bpftrace
   # 追踪所有 openat 系统调用
   sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat { printf("%s %s\n", comm, str(args->filename)); }'
   ```
   不需要写 C 代码，不需要编译——单行命令就能看到所有进程在打开什么文件。

2. **用 libbpf-bootstrap 编译运行 minimal 示例**
   ```bash
   git clone --recurse-submodules https://github.com/libbpf/libbpf-bootstrap
   cd libbpf-bootstrap/examples/c
   make minimal
   sudo ./minimal
   ```
   然后 `cat /sys/kernel/debug/tracing/trace_pipe` 看输出。

3. **检查你的内核是否支持 BTF**
   ```bash
   ls -lh /sys/kernel/btf/vmlinux
   ```
   如果有这个文件（几百 KB 到几 MB），你的内核就支持 CO-RE 可移植。没有的话考虑升级内核或者用 BCC 方案代替。

## 参考

- [ebpf.io - What is eBPF?](https://ebpf.io/what-is-ebpf/)
- [libbpf Overview - Linux Kernel Documentation](https://docs.kernel.org/bpf/libbpf/libbpf_overview.html)
- [BPF CO-RE Reference Guide - Andrii Nakryiko](https://nakryiko.com/posts/bpf-core-reference-guide/)
- [libbpf-bootstrap - GitHub](https://github.com/libbpf/libbpf-bootstrap)
- [eBPF Applications Landscape](https://ebpf.io/applications/)
- [eBPF Ecosystem Progress in 2024–2025 (Archived)](https://eunomia.dev/blog/2025/02/12/ebpf-ecosystem-progress-in-20242025-a-technical-deep-dive/)
