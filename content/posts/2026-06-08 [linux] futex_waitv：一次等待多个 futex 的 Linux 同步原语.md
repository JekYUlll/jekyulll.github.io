+++
date = '2026-06-08T10:09:15+08:00'
draft = false
title = 'futex_waitv：一次等待多个 futex 的 Linux 同步原语'
author = 'JekYUlll'
lastmod = '2026-06-08T21:16:03+08:00'
tags = ['futex', 'futex-waitv', 'synchronization', 'linux-kernel']
categories = ['linux']
+++

## 背景

Linux 上的 futex 很快，但原始接口有个老问题：一次只能等一个 futex word。

普通互斥锁够用了。线程先在用户态用原子操作抢锁，抢不到再进内核睡觉。这个模型很省，因为无竞争路径不用系统调用。`pthread_mutex_t`、`pthread_cond_t`、很多运行时里的锁，底层都绕不开 futex。

麻烦出现在“等多个对象任意一个就绪”的场景。Windows 有 `WaitForMultipleObjects`，可以传一组 handle，任意一个 signal 就返回。Wine/Proton 要在 Linux 上模拟这个语义，早期常用 `eventfd`、`poll` 之类的方案兜底。能跑，但对象多了以后 fd 数量、读写次数和上下文切换都会变烦。

`futex_waitv()` 就是补这个洞的。它在 Linux 5.16 进入内核，是 futex2 工作里先落地的一块：给用户态一个“同时挂到多个 futex 上等待”的系统调用。它不是新锁，也不会替你管理状态。它只做一件事，把“检查多个值是否仍然等于预期值，然后睡到其中任意一个被唤醒”这件事交给内核做，并且把丢唤醒的窗口关掉。

这个 API 不适合写入门锁教程。它适合运行时、兼容层、游戏、语言库，以及那些已经把同步状态压进原子变量里的系统。

## 工作方式

### futex 仍然是“用户态状态 + 内核等待队列”

futex word 本身放在用户态内存里，通常是 32 位整数。锁是否可用、事件是否就绪、队列版本号是多少，这些状态由用户态原子变量表达。内核只在等待和唤醒时介入。

原始 `FUTEX_WAIT` 的关键动作是：

1. 读取 `uaddr` 指向的 32 位值。
2. 如果当前值不等于调用者传入的 `val`，马上返回 `EAGAIN`。
3. 如果值相等，把当前线程挂到这个 futex 的等待队列上，然后睡眠。
4. 其他线程调用 `FUTEX_WAKE` 后，内核唤醒等待者。

第二步不是多余的。它负责挡住丢唤醒：如果另一个线程已经把状态改掉并 wake 过了，等待线程不能再睡下去。

`futex_waitv()` 把这个模式扩成数组。

```c
struct futex_waitv {
    __u64 val;         // 预期值
    __u64 uaddr;       // futex word 地址，用 uintptr_t 填
    __u32 flags;       // FUTEX_32、FUTEX_PRIVATE_FLAG 等
    __u32 __reserved;  // 必须为 0
};
```

调用时传入 `struct futex_waitv waiters[]`，最多 128 个。内核会逐个检查每个 `uaddr` 的当前值是否等于对应的 `val`。只要有一个不相等，整个调用返回 `EAGAIN`，用户态重新检查自己的状态。

如果全都相等，线程会同时排队到这些 futex 上。任意一个 futex 被 `FUTEX_WAKE` 唤醒后，系统调用返回一个非负整数，表示被唤醒的 waiter 下标。

### 和 WaitForMultipleObjects 不完全一样

| 点 | `futex_waitv()` | Windows `WaitForMultipleObjects` |
| --- | --- | --- |
| 等待对象 | 32 位 futex word | handle，可为 mutex、event、process 等 |
| 返回条件 | 任意一个 futex 被 wake | 可选 wait-any 或 wait-all |
| 返回值 | 某个被唤醒 futex 的下标，不承诺最低下标 | wait-any 时返回最低下标的 signaled handle |
| 状态修改 | 不修改 futex word | 某些对象会在等待成功后改变状态 |
| 超时 | 绝对时间，`CLOCK_MONOTONIC` 或 `CLOCK_REALTIME` | 毫秒相对时间 |

所以它是底层积木，不是 Windows API 的完整复制。Wine/Proton 这类项目会在上层补语义差异。

### 两个容易踩的细节

第一个，`flags` 分两层。系统调用自己的 `flags` 参数现在必须是 0。每个 waiter 的 `flags` 才写 `FUTEX_32 | FUTEX_PRIVATE_FLAG`。当前内核实际支持的是 32 位 futex，`FUTEX_32` 不能省。

第二个，`timeout` 是绝对时间，不是“等 100ms”。如果传超时，要先用 `clock_gettime()` 取当前时间，再加上你要等的时间。直接塞一个 `{.tv_sec = 1}`，大概率会被当成 1970 年附近的绝对时间，立刻超时。

### 错误返回该怎么读

`EAGAIN` 不是坏事。它说明至少一个 futex word 的当前值已经不等于预期值，用户态应该重新读状态，而不是把它当成失败日志刷屏。这个返回经常说明你的快路径已经有人推进过了。

`ETIMEDOUT` 才是超时。`EINTR` 或被信号打断的返回要按项目自己的取消语义处理。底层同步库最容易犯的错，是把所有负返回都揉成“等待失败”。这样写调试起来很痛，因为你分不清是状态已变、超时、信号，还是参数真的错了。

还有一个公平性问题。`futex_waitv()` 返回“某个”被唤醒的下标，不保证最低下标，也不替你做轮询公平。多个队列都可能就绪时，上层要决定先处理哪个队列。内核只负责把线程从睡眠里拉出来。

## 代码

下面这段代码等两个 futex word。主线程 100ms 后把 `signals[1]` 改成 1，再用普通 `FUTEX_WAKE_PRIVATE` 唤醒它。等待线程会从 `futex_waitv()` 返回，打印被唤醒的下标。

在 Linux 5.16 以上内核可跑。我在 Linux 6.8 上用 `gcc -std=c11 -Wall -Wextra -O2 -pthread` 编译通过。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <linux/futex.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#ifndef SYS_futex_waitv
# ifdef __NR_futex_waitv
#  define SYS_futex_waitv __NR_futex_waitv
# else
#  error "SYS_futex_waitv is not available on this architecture"
# endif
#endif

static _Atomic uint32_t signals[2];

static int futex_wake_one(_Atomic uint32_t *addr) {
    return (int)syscall(SYS_futex, (uint32_t *)addr,
                        FUTEX_WAKE_PRIVATE, 1, NULL, NULL, 0);
}

static void *waiter_thread(void *arg) {
    (void)arg;
    struct futex_waitv waiters[2] = {
        {
            .val = 0,
            .uaddr = (uintptr_t)&signals[0],
            .flags = FUTEX_32 | FUTEX_PRIVATE_FLAG,
            .__reserved = 0,
        },
        {
            .val = 0,
            .uaddr = (uintptr_t)&signals[1],
            .flags = FUTEX_32 | FUTEX_PRIVATE_FLAG,
            .__reserved = 0,
        },
    };

    long idx = syscall(SYS_futex_waitv, waiters, 2, 0, NULL, CLOCK_MONOTONIC);
    if (idx < 0) {
        fprintf(stderr, "futex_waitv failed: %s\n", strerror(errno));
        return NULL;
    }

    printf("woken by signals[%ld], values=(%u,%u)\n",
           idx,
           atomic_load_explicit(&signals[0], memory_order_relaxed),
           atomic_load_explicit(&signals[1], memory_order_relaxed));
    return NULL;
}

int main(void) {
    pthread_t tid;
    atomic_store(&signals[0], 0);
    atomic_store(&signals[1], 0);

    if (pthread_create(&tid, NULL, waiter_thread, NULL) != 0) {
        perror("pthread_create");
        return 1;
    }

    usleep(100000);
    atomic_store_explicit(&signals[1], 1, memory_order_release);
    if (futex_wake_one(&signals[1]) < 0) {
        perror("futex wake");
        return 1;
    }

    pthread_join(tid, NULL);
    return 0;
}
```

编译运行：

```bash
gcc -std=c11 -Wall -Wextra -O2 -pthread futex_waitv_demo.c -o futex_waitv_demo
./futex_waitv_demo
```

输出类似这样：

```text
woken by signals[1], values=(0,1)
```

真正写库时，返回后还要回到用户态重新读取状态。futex 的 wake 只说明“有人喊你了”，不保证状态一定还属于你。锁、事件、队列都应该把状态机写在原子变量里，futex 只负责睡眠路径。

## 谁在用

| 项目/领域 | 怎么用 `futex_waitv()` |
| --- | --- |
| Wine / Proton | 用来更接近 Windows `WaitForMultipleObjects` 的 wait-any 语义，减少 `eventfd` 方案的开销。 |
| Linux kernel selftests | `tools/testing/selftests/futex/functional/futex_waitv.c` 覆盖基础等待、超时、共享内存等行为。 |
| 语言运行时 | .NET runtime 社区讨论过用它改进 `InternalWaitForMultipleObjectsEx` 一类路径。 |
| 游戏与兼容层 | 同时等待多个同步对象是常见需求，尤其是移植 Windows 同步模型时。 |
| 低层同步库 | 可以拿它做多队列等待、多个条件变量的 wait-any，但要自己处理公平性和状态机。 |

我不会建议普通业务代码直接碰它。业务代码用 pthread、C++ `std::mutex`、Go channel、Rust async runtime 就够了。`futex_waitv()` 的价值在更底层：当你已经在写运行时或者兼容层，原来的单 futex 等待让你绕了一堆 fd 和 poll，它才开始变得香。

它现在也还不是“futex2 全家桶”。内核文档里能看到 8/16/64 位 futex、NUMA 等扩展方向，但实际可用的主力仍是 32 位 futex wait-any。把它当成小而硬的补丁，比当成全新的同步体系更准确。

## 可以马上试的三件事

1. 在本机确认内核和头文件支持：`uname -r`，再看 `/usr/include/linux/futex.h` 里有没有 `struct futex_waitv` 和 `FUTEX_WAITV_MAX`。
2. 把上面的代码保存为 `futex_waitv_demo.c` 编译运行，再用 `strace -e futex,futex_waitv ./futex_waitv_demo` 看系统调用路径。
3. 去读 Linux selftest 的 `tools/testing/selftests/futex/functional/futex_waitv.c`。比起博客里的短例子，selftest 更适合查边界条件，比如超时、共享内存和错误返回。

## 参考

- Linux man-pages: `futex_waitv(2)`，https://man7.org/linux/man-pages/man2/futex_waitv.2.html
- Linux kernel docs: `futex2` userspace API，https://www.kernel.org/doc/html/latest/userspace-api/futex2.html
- Collabora: The `futex_waitv()` syscall and gaming on Linux，https://www.collabora.com/news-and-blog/blog/2023/02/17/the-futex-waitv-syscall-gaming-on-linux/
- LKML patchset: `futex2: Add wait on multiple futexes syscall`，https://lkml.iu.edu/hypermail/linux/kernel/2109.0/03770.html
- Microsoft Learn: `WaitForMultipleObjects`，https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitformultipleobjects
- Linux selftest source: `tools/testing/selftests/futex/functional/futex_waitv.c`，https://raw.githubusercontent.com/torvalds/linux/master/tools/testing/selftests/futex/functional/futex_waitv.c
