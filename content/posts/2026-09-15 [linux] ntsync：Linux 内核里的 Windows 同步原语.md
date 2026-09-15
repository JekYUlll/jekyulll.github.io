+++
date = '2026-09-15T10:05:32+08:00'
draft = false
title = 'ntsync：Linux 内核里的 Windows 同步原语'
author = 'JekYUlll'
lastmod = '2026-09-15T10:05:32+08:00'
tags = ['ntsync', 'wine', 'kernel', 'synchronization']
categories = ['linux']
+++

Linux 上跑 Windows 游戏，卡的地方很多时候不在渲染，而在线程同步。Windows 的互斥体、信号量、事件对象和 futex 语义对不上，Wine 过去只能把所有加锁请求转发给一个专门的用户态进程处理，每一次同步都付一遍进程间通信的开销。Linux 6.14 合入的 ntsync 驱动把这件事搬进了内核：`/dev/ntsync` 一个字符设备，直接提供 NT 风格的同步原语。

## wineserver 中转卡在哪

Wine 一直在用户态模拟 Windows API，NT 同步原语那一部分历史上是走 RPC 到 wineserver 进程完成的。这个设计能跑通，但代价也明显：应用线程每加一次锁、每等一个事件，都要跨进程走一圈。

驱动作者 Elizabeth Figura 在补丁说明里写得更直接：近些年的应用把这些 API 用得太重，RPC 开销已经成了瓶颈；而且 NT 同步 API 本身太复杂，用现有原语拼不出准确的语义。两个典型的"拼不出来"：

- `NtPulseEvent()` 要求原子地"唤醒恰好一个等待者、同时保持事件未触发"，用户态没有工具能保证"恰好一个"；
- `NtWaitForMultipleObjects()` 的 wait-for-all 模式要求"要么全部占有、要么一个不占"，中途不能拿着部分对象等剩下的，这需要直接操作内核等待队列。

社区之前有过两个绕路方案：esync 把对象尽量映射到 eventfd，fsync 走 futex。它们确实改善了一批游戏的性能，但本质还是用户态模拟，有各自的取舍。5.16 加入的 futex_waitv 就是为这个场景设计的，能一次等多个 futex 地址，但它只解决了"等"，没解决 wait-all 的"全或无占有"。绕到这里，结论是：与其把 futex 掰成 Windows 的形状，不如给这类语义开一个专用设备。

从 2024 年初的 RFC 到 2025 年 1 月进入 6.14 合并窗口，这组补丁磨了一年，早期讨论的焦点不在"要不要新设备"，而在 API 怎么设计。

## 三种对象，语义照搬 NT

`/dev/ntsync` 只提供三种对象，内核文档对它们的定义：

- 信号量：一个 32 位计数器加一个上限。计数非零就是"已触发"，等待成功时计数减一。
- 互斥体：一个递归计数加一个 owner 标识。owner 为零表示无人持有；同一个 owner 可以重复获取，计数递增。所有者线程死掉不会自动检测，需要另一侧显式用 `NTSYNC_IOC_MUTEX_KILL` 标记，之后这个互斥体变为 abandoned。
- 事件：一个布尔值，分自动重置和手动重置两种。自动重置事件在被等待满足时立刻复位，最多唤醒一个等待者；手动重置的要显式 RESET。

这套定义和 Windows 内核里的对象一一对应，连 abandoned mutex 这种边角都保留了：获取一个 abandoned 互斥体会返回 `EOWNERDEAD`，但对象已经归你了，错误码只是提醒你"上一个主人死得不明不白，先做个一致性检查"。

内核文档开篇还声明了一点：这是兼容层工具，不要拿它做一般意义上的同步，普通程序请用 futex 和 poll。换句话说，这个驱动存在的唯一理由是"Windows 语义"，不是来抢 futex 的活。

## 对象就是文件描述符

驱动把每个同步对象做成了一个匿名 inode，CREATE_SEM / CREATE_MUTEX / CREATE_EVENT 三个 ioctl 的返回值就是对象的 fd，最后一个 fd 关闭时对象自动销毁。每次 open `/dev/ntsync` 得到一个独立实例，用于支撑一个 NT 虚拟机；对象不能跨实例使用。对象跟着 fd 走，生命周期交给 VFS 的引用计数，驱动自己不用做对象回收。

这个设计有两个容易踩的坑，写代码前最好先知道。

一是 wait 不在对象 fd 上调用。看文档的目录结构，容易以为 `NTSYNC_IOC_WAIT_ANY` 和 `NTSYNC_IOC_WAIT_ALL` 也是"对象相关的 ioctl"，实际驱动把它们挂在字符设备的 ioctl 分发里，必须在 `/dev/ntsync` 的 fd 上调用；对象 fd 只接受 RELEASE、SET、RESET、PULSE、READ 这些操作。调用错了拿到的是 `ENOTTY`。

二是 owner 必须非零，哪怕你等的只是信号量和事件。驱动在 `setup_wait()` 里无条件检查 `args->owner`，理由也简单：等待队列是共用的，mutex 的 owner 语义在排队时就要记录。文档里这条要求写在 mutex 的说明段落里，容易看漏。

另外，一次 wait 最多等 64 个对象（`NTSYNC_MAX_WAIT_COUNT`），`objs` 字段是一个用户态指针，指向 int fd 数组。超时是绝对时间，纳秒为单位，默认走 MONOTONIC 时钟，带 `NTSYNC_WAIT_REALTIME` 标志则走 REALTIME，全 `U64_MAX` 表示永久等待。Wine 里 NT 的 100ns 时间戳就是在这个 ioctl 边界上换算的。

顺带提醒：补丁早期的 API 和最终合入版有差异，fd 的返回方式和 ioctl 命名都改过，动手前以 `include/uapi/linux/ntsync.h` 为准。

## WAIT_ANY 的排队模型

`NTSYNC_IOC_WAIT_ANY` 长得像 poll，行为却不是。poll 只是观察，不改变对象状态；WAIT_ANY 会"消费"对象，相当于从这堆对象里原子地占有恰好一个。

驱动里的流程分四段：`setup_wait()` 解析参数、把 fd 换成对象引用、分配等待队列节点；然后把这些节点挂到每个对象的 any_waiters 链表上；再按顺序检查一遍有没有已经可用的对象（挂上之后再检查，是为了避免丢唤醒竞态）；都没有就去睡，超时靠 hrtimer，醒来后把节点从所有队列摘掉。

唤醒是"点对点"的：信号量 release 一个计数、自动重置事件被 set、mutex 被 unlock，这些操作都会在自己对象的等待队列上尝试唤醒合适的等待者，把对象直接交给对方。这里没有广播，也没有惊群：一次等待只会满足一个等待者，多余的继续睡。wait-all 则相反，等所有对象同时可用才一次性全部占有，一个都不能缺，因此在高竞争场景理论上有饥饿风险，驱动文档也明说了这一点。

alert 是个可选参数：额外的、独立于对象列表的终止源。等待因为 alert 被唤醒时，返回的 `index` 等于对象个数；正常唤醒时 `index` 是命中对象的下标。Wine 的 alertable wait 就落在这个参数上。

## 写一个最小的等待 demo

看一圈源码不如自己跑一遍。下面这段程序打开 `/dev/ntsync`，建一个初始计数为 0 的信号量，起一个线程等它，主线程 300ms 后 release，观察等待线程被唤醒。老发行版的 `<linux/ntsync.h>` 可能还没有这些定义，代码里做了内联兜底。

```c
/* ntsync-demo.c: 用 /dev/ntsync 的信号量做一次跨线程唤醒 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <linux/ioctl.h>
#include <linux/types.h>

/* 内核 6.14+ 的用户态头文件自带这些定义，没有就内联一份 */
#if defined(__has_include)
#  if __has_include(<linux/ntsync.h>)
#    include <linux/ntsync.h>
#  endif
#endif

#ifndef NTSYNC_IOC_WAIT_ANY

struct ntsync_sem_args { __u32 count; __u32 max; };
struct ntsync_wait_args {
    __u64 timeout; __u64 objs;
    __u32 count;   __u32 index;
    __u32 flags;   __u32 owner;
    __u32 alert;   __u32 pad;
};
#define NTSYNC_IOC_CREATE_SEM  _IOW ('N', 0x80, struct ntsync_sem_args)
#define NTSYNC_IOC_WAIT_ANY    _IOWR('N', 0x82, struct ntsync_wait_args)
#define NTSYNC_IOC_SEM_RELEASE _IOWR('N', 0x81, __u32)

#endif /* NTSYNC_IOC_WAIT_ANY */

static int devfd;
static int semfd;

static long long now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void *waiter(void *arg)
{
    long long start = now_ns();
    int sems[1] = { semfd };
    struct ntsync_wait_args w = { 0 };

    (void)arg;
    w.timeout = (__u64)(now_ns() + 5000000000LL); /* 绝对超时：5 秒后放弃 */
    w.objs = (__u64)(uintptr_t)sems;
    w.count = 1;
    w.owner = (__u32)(uintptr_t)pthread_self(); /* 必须非零 */

    if (ioctl(devfd, NTSYNC_IOC_WAIT_ANY, &w) < 0) {
        printf("waiter: wait failed: %s\n", strerror(errno));
        return NULL;
    }
    printf("waiter: woken at +%lld ms, index=%u\n",
           (now_ns() - start) / 1000000, w.index);
    return NULL;
}

int main(void)
{
    struct ntsync_sem_args sem = { .count = 0, .max = 1 };
    __u32 rel = 1; /* 输入是要增加的计数，输出是旧计数 */
    pthread_t th;

    devfd = open("/dev/ntsync", O_RDWR | O_CLOEXEC);
    if (devfd < 0) {
        fprintf(stderr, "open /dev/ntsync: %s\n", strerror(errno));
        fprintf(stderr, "需要 Linux 6.14+，并且加载了 ntsync 模块\n");
        return 1;
    }

    semfd = ioctl(devfd, NTSYNC_IOC_CREATE_SEM, &sem);
    if (semfd < 0) {
        perror("create sem");
        return 1;
    }

    pthread_create(&th, NULL, waiter, NULL);
    usleep(300 * 1000); /* 先让等待线程排进队列 */

    if (ioctl(semfd, NTSYNC_IOC_SEM_RELEASE, &rel) < 0) {
        perror("sem release");
        return 1;
    }
    printf("main: released semaphore (prev count=%u)\n", rel);

    pthread_join(th, NULL);
    close(semfd);
    close(devfd);
    return 0;
}
```

编译和运行：

```bash
gcc -O2 -Wall -Wextra -pthread ntsync-demo.c -o ntsync-demo
sudo modprobe ntsync        # 内核按模块编译时需要的加载步骤
./ntsync-demo
```

在 6.14 以上内核上，输出大致是（时序取决于调度器）：

```
main: released semaphore (prev count=0)
waiter: woken at +300 ms, index=0
```

逻辑是确定的：等待线程在 release 之前阻塞在 WAIT_ANY 上；release 把计数从 0 加到 1，驱动直接把信号量交给排队中的等待者，计数同时被消耗回 0。整个唤醒路径上两边各只有一次 ioctl，没有任何进程间通信。

反过来说，如果内核太老，程序会在 open 处直接失败，报 `ENOENT`。这是最典型的部署问题：驱动在 6.14 才真正可用，很多发行版默认还没有启用它。

## 6.14 之后：谁在用

驱动框架 6.13 就进了一部分，但当时标着 broken，处于不可用状态；6.14 补齐后才是真正能用的版本，2025 年 3 月随 6.14 发布。Greg Kroah-Hartman 在 char/misc 的 pull request 里写了一句 "Should make many SteamOS users happy"，还特意提了句"自带测试"，内核确实附了 selftests（`tools/testing/selftests/drivers/ntsync/`）。

Wine 这边，10.15 开发版开始出现使用 ntsync 的代码，10.16 的发布说明正式写上 Fast synchronization support using NTSync，前提是内核 6.14+ 且模块已加载。Proton 侧，GE-Proton 10-9 起可以用环境变量 `PROTON_USE_NTSYNC=1` 打开，SteamOS 3.7.20 的内核已经默认带上模块。发行版层面，Fedora 44 专门走了一个提案：只在检测到 Wine 或 Steam 被使用时自动加载 ntsync 模块，避免给所有人默认开着。

性能数字要带着上下文看。补丁系列里那张表很抓眼球：Dirt 3 从 110.6 涨到 860.7，Resident Evil 2 从 26 到 77，最低的 Metro 2033 也有 21%。Figura 自己的总结是"帧率提升 50% 到 150% 不罕见"。但注意对照组是 vanilla Wine 的 wineserver 同步路径，也就是最慢的那条路。如果你本来就在用 fsync，提升会温和得多；同步不是瓶颈的游戏则几乎没变化。

这些数字背后是一次架构更换：Wine 的同步路径从用户态进程的 RPC，换成了内核里的等待队列。以后再翻讲 Wine 架构的旧文章，RPC 到 wineserver 那一段已经可以跳过。

## 参考

- 内核文档 NT synchronization primitive driver: https://docs.kernel.org/userspace-api/ntsync.html
- UAPI 头文件 include/uapi/linux/ntsync.h: https://github.com/torvalds/linux/blob/master/include/uapi/linux/ntsync.h
- 驱动实现 drivers/misc/ntsync.c: https://github.com/torvalds/linux/blob/master/drivers/misc/ntsync.c
- 内核自带 selftest: https://github.com/torvalds/linux/blob/master/tools/testing/selftests/drivers/ntsync/ntsync.c
- LWN: Windows NT synchronization primitives for Linux: https://lwn.net/Articles/961884/
- Phoronix: Completed NTSYNC Driver Merged For Linux 6.14: https://www.phoronix.com/news/Linux-6.14-Char-Misc-NTSYNC
- Phoronix: Wine 10.15 To Feature Initial Support For Using NTSYNC: https://www.phoronix.com/news/Wine-10.15-With-NTSYNC
- GamingOnLinux: NTSYNC driver should finally land in Linux kernel 6.14（含 benchmark 表）: https://www.gamingonlinux.com/2025/01/ntsync-driver-for-improving-windows-games-on-linux-with-wine-proton-should-finally-land-in-linux-kernel-614/
- GamingOnLinux: Wine 10.16 released with fast synchronization support using NTSync: https://www.gamingonlinux.com/2025/10/wine-10-16-released-with-fast-synchronization-support-using-ntsync/
- GamingOnLinux: Fedora Linux 44 will get improved NTSYNC enabling: https://www.gamingonlinux.com/2025/12/fedora-linux-44-will-get-improved-ntsync-enabling-for-proton-wine/
