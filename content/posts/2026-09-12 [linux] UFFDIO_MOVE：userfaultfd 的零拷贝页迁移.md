+++
date = '2026-09-12T10:08:42+08:00'
draft = false
title = 'UFFDIO_MOVE：userfaultfd 的零拷贝页迁移'
author = 'JekYUlll'
lastmod = '2026-09-12T10:08:42+08:00'
tags = ['userfaultfd', 'memory-management', 'kernel']
categories = ['linux']
+++

并发压缩 GC 最忙的动作，是把对象从 from-space 搬到 to-space。userfaultfd 把缺页控制权交给用户态之后，这个动作长期由 UFFDIO_COPY 完成：内核分配一张新页，把内容拷进去。ART 团队在提交记录里把动机写得直白：源页马上要回收复用，页本身没坏，内容再拷一遍纯属浪费。

6.8 内核加了 UFFDIO_MOVE：不拷内容，直接把物理页挂到目标地址。uABI 由 Andrea Arcangeli 实现、Suren Baghdasaryan 完成上游化，随后进了 6.8；到现在两年多，相关补丁还在往主线打。

## UFFDIO_COPY 的开销结构

userfaultfd 的用法不算复杂。把一个 VMA 范围注册进 fd，之后访问范围内未就绪的页，线程挂起；管理侧从 fd 读到缺页事件，用 UFFDIO_COPY 之类的 ioctl 把页补上，再唤醒。ART 的并发压缩 GC 把它做成了流水线：GC 线程负责搬对象，mutator 线程撞上没就绪的页就交给 GC 处理；压缩全程没有全局停顿。

UFFDIO_COPY 的语义是"分配新页 + 把数据拷进去"。搬一页 4KB，内核要分配一张新页、复制一份内容；源页还得 madvise 归还内核才能复用。数据还是那些数据，开销却多了一整轮。

提交记录承认得很坦率：真要分配新页的时候，UFFDIO_COPY 比 UFFDIO_MOVE 还快 20%。MOVE 赢的前提是页"已经在用户态手里、可以直接复用"，而这正是堆压缩的日常。

压缩整堆意味着搬页量跟堆大小成正比。ART 的 CC GC 一直把"暂停时间与堆大小无关"当成卖点，搬页开销都摊在并发阶段；mutator 线程撞上正在搬的页要等 GC 处理，单页搬得慢了，卡顿就露头。

## UFFDIO_MOVE 直接搬物理页

ioctl 的参数结构只有五个字段：

```c
struct uffdio_move {
    __u64 dst;   /* 目标地址 */
    __u64 src;   /* 源地址 */
    __u64 len;   /* 搬运长度 */
    __u64 mode;  /* 行为旗标 */
    __s64 move;  /* 输出: 实搬字节数, 失败为负 errno */
};
```

调用成功后，src 范围的物理页挂到 dst，源侧留下空洞。move 字段是输出：成功报实际搬运的字节数，失败报负的 errno。

两个 mode 旗标各管一摊。DONTWAKE 让内核别唤醒等缺页的线程；ALLOW_SRC_HOLES 允许源范围里有空洞，并把空洞按成功搬运计数，主要是给大页对齐的场景兜底，免得为了一个洞去拆大页。

失败语义相当严格。目标范围必须整段是空洞，否则 EEXIST；源范围默认不许有洞，否则 ENOENT。内核注释的态度很坚决：宁可失败，也不给内存破坏留悄悄发生的机会。两个线程同时对同一个目标地址发 MOVE，第二个会直接拿到错误，而不是把页表搅乱。

对页的独占性也有要求。被 pin 的页、被 KSM 合并过的页、fork 之后 COW 共享的页，都会被 EBUSY 拒掉；手册页的建议是源范围提前 MADV_DONTFORK。

收益数字来自 Google 的测试：Pixel 6 上用 MOVE 代替 COPY 做堆压缩，压缩线程完成时间减少了 40% 以上。另外 MOVE 能在同一个 VMA 里搬走已经 swap 出去的页，只动页表不触发换入；之前只有 mremap 能做这件事，代价是拆 VMA。

## 内核里的搬运路径

move_pages() 入口先做一轮校验：源和目标都要是可写、保护位一致的私有匿名 VMA，排除 hugetlb、PFNMAP 这类特殊映射；目标范围还必须注册在同一个 userfaultfd 上。跨进程搬页（cross-mm）还没做，只支持同一个进程。

接着按页表状态分路：

- present 页走 move_present_ptes()：清源 PTE，把 folio 的 mapping 和线性地址改到目标 VMA，装上目标 PTE，并打软脏位
- swap 页走 move_swap_pte()，换个地方放 swap entry，不碰内容
- 只读零页走 move_zeropage_pte()
- 大页走 move_pages_huge_pmd()，整个 PMD 一起搬，避免拆分

单页搬运在源、目标两把页表锁里完成，其他线程要么看到页在源地址，要么看到页在目标地址，没有中间态。

ioctl 包装层还有两道硬门槛：uffd 必须属于调用进程的 mm，跨进程搬页在 uABI 层直接被拒；mode 里出现未知位也是直接 EINVAL。没搬完的情况用 EAGAIN 表达：只搬了一部分就中断的话，返回 EAGAIN，move 字段写入实际搬走的字节数，重试还是收场由用户态自己决定。

这个 ioctl 的打磨到现在没停。2025 年 9 月合入的 TLB 批量刷新把搬页时的 ptep_clear_flush 批量化，此前它占掉 move_pages_pte() 四成以上的时间；同一组数据里，system_server 的 GC 压缩时间减少超过一半。2025 年 11 月上游又去掉了搬页路径上的 anon_vma 写锁，补丁说明里附了现场数据：主线程曾被这把锁卡出 50ms 以上的不可中断睡眠，直接反映为可感知的交互卡顿。2026 年 9 月初，还有 move_pages_ptes 返回值的小修复在走 review。一个内核接口的可靠性和性能，就是这么两年一点点抠出来的。

## 最小例子：搬走两页

写个能跑的小程序把流程验证一遍。准备两个两页的匿名区间，都注册进同一个 uffd；用 UFFDIO_COPY 往源区间填两个"对象"，再用一次 UFFDIO_MOVE 整体搬到目标区间；最后对已经空掉的源区间再发一次 MOVE，观察失败语义。

```c
/* uffd_move_demo.c: UFFDIO_MOVE 把 src 的物理页原样搬到 dst, 不拷贝内容 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/userfaultfd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define PS 4096UL

static int uffd_create(void)
{
    /* UFFD_USER_MODE_ONLY: 普通用户即可创建, 只接管用户态缺页 */
    int uffd = syscall(SYS_userfaultfd, O_CLOEXEC | O_NONBLOCK | UFFD_USER_MODE_ONLY);

    if (uffd < 0) {
        perror("userfaultfd");
        exit(1);
    }
    struct uffdio_api api = { .api = UFFD_API, .features = UFFD_FEATURE_MOVE };

    if (ioctl(uffd, UFFDIO_API, &api) < 0) {
        perror("UFFDIO_API");
        exit(1);
    }
    if (!(api.features & UFFD_FEATURE_MOVE)) {
        fprintf(stderr, "kernel 太老: 没有 UFFD_FEATURE_MOVE\n");
        exit(1);
    }
    printf("[init] UFFD_FEATURE_MOVE = yes\n");
    return uffd;
}

static void uffd_register_range(int uffd, void *addr, size_t len)
{
    struct uffdio_register reg = {
        .range = { .start = (unsigned long)addr, .len = len },
        .mode = UFFDIO_REGISTER_MODE_MISSING,
    };

    if (ioctl(uffd, UFFDIO_REGISTER, &reg) < 0) {
        perror("UFFDIO_REGISTER");
        exit(1);
    }
    if (!(reg.ioctls & (1ULL << _UFFDIO_MOVE))) {
        fprintf(stderr, "该范围不支持 UFFDIO_MOVE\n");
        exit(1);
    }
}

static void fill_page(int uffd, void *dst, const char *text)
{
    char page[PS];
    struct uffdio_copy cp = {
        .dst = (unsigned long)dst,
        .src = (unsigned long)page,
        .len = PS,
    };

    memset(page, 0, sizeof(page));
    strcpy(page, text);
    if (ioctl(uffd, UFFDIO_COPY, &cp) < 0) {
        perror("UFFDIO_COPY");
        exit(1);
    }
}

int main(void)
{
    int uffd = uffd_create();
    size_t len = 2 * PS;

    /* from-space (src) 和 to-space (dst) 各两页, dst2 用来做探测 */
    char *src = mmap(NULL, len, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    char *dst = mmap(NULL, len, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    char *dst2 = mmap(NULL, PS, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

    if (src == MAP_FAILED || dst == MAP_FAILED || dst2 == MAP_FAILED) {
        perror("mmap");
        return 1;
    }
    uffd_register_range(uffd, src, len);
    uffd_register_range(uffd, dst, len);
    uffd_register_range(uffd, dst2, PS);

    /* 模拟 GC: 两个对象放在 from-space, 用 UFFDIO_COPY 填入 */
    fill_page(uffd, src, "object-A lives in src page 0");
    fill_page(uffd, src + PS, "object-B lives in src page 1");
    printf("[fill] src 两页已就绪\n");

    /* 关键一步: 把 src 的物理页搬到 dst, 一次搬两页 */
    struct uffdio_move mv = {
        .dst = (unsigned long)dst,
        .src = (unsigned long)src,
        .len = len,
    };

    if (ioctl(uffd, UFFDIO_MOVE, &mv) < 0) {
        perror("UFFDIO_MOVE");
        return 1;
    }
    printf("[move] moved = %lld bytes\n", (long long)mv.move);
    printf("[read] dst page0 = \"%s\"\n", dst);
    printf("[read] dst page1 = \"%s\"\n", dst + PS);

    /* src 已经空了, 再搬一次 (不带 ALLOW_SRC_HOLES) 应该被拒绝 */
    struct uffdio_move probe = {
        .dst = (unsigned long)dst2,
        .src = (unsigned long)src,
        .len = PS,
    };

    errno = 0;
    if (ioctl(uffd, UFFDIO_MOVE, &probe) < 0)
        printf("[probe] src 已是空洞: %s (errno=%d, move=%lld)\n",
               strerror(errno), errno, (long long)probe.move);
    else
        printf("[probe] 意外成功: move=%lld\n", (long long)probe.move);

    return 0;
}
```

编译运行（需要 6.8+ 内核和带 uffdio_move 的头文件；普通用户走 UFFD_USER_MODE_ONLY 就能跑）：

```console
$ gcc -Wall -Wextra -O2 -o uffd_move_demo uffd_move_demo.c
$ ./uffd_move_demo
[init] UFFD_FEATURE_MOVE = yes
[fill] src 两页已就绪
[move] moved = 8192 bytes
[read] dst page0 = "object-A lives in src page 0"
[read] dst page1 = "object-B lives in src page 1"
[probe] src 已是空洞: No such file or directory (errno=2, move=-2)
```

输出里几个细节都对得上：moved = 8192 是两页一次搬完的字节数；dst 直接读就拿到内容，全程没有拷贝用户数据；最后一次 probe 故意不带 ALLOW_SRC_HOLES，源侧空洞让内核返回 ENOENT，并把 -2 写进 move 字段，跟手册页说的"负 errno"一致。

## 谁在用

主要用户是 Android：Android 13 的 AOSP 发布文里写明，ART 引入了一个基于内核特性 userfaultfd 的新垃圾回收器；UFFDIO_MOVE 进主线后，Google 又把它回移到 Android 常用的 6.1 和 6.6 内核，提交信息原话是 "for ART GC to use it"，还加了 CONFIRM_FIXED 模式，让用户态能探测这两个内核是否带齐了修复补丁。

MOVE 早期踩过两个坑：搬 swap cache 里的页会 panic，多个线程搬同一页会活锁；修复到没到这两个内核上，用户态必须能确认，于是有了 CONFIRM_FIXED 这个探测模式。

边界也要说清楚：MOVE 只支持私有匿名页，文件页、shmem 不在其列；跨进程搬页还在 TODO；被 pin 或共享的页搬不动。它解决的是"页往哪放"，不解决"页从哪来"。如果你的场景是一批页已经在手里、只想换个地址继续用，比如用户态内存管理或者语言运行时的压缩，它就是对口的原语；处理 EEXIST / ENOENT / EBUSY 时，把它们当正常控制流，不是异常。

## 参考

- UFFDIO_MOVE(2const) 手册页：https://man7.org/linux/man-pages/man2/uffdio_move.2const.html
- LWN 合并讨论（userfaultfd move option）：https://lwn.net/Articles/952319/
- 内核提交 userfaultfd: UFFDIO_MOVE uABI：https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=adef440691bab824e39c1b17382322d195e1fab0
- LWN：MOVE 的 TLB 批量刷新补丁：https://lwn.net/Articles/1033755/
- 内核文档 Userfaultfd：https://docs.kernel.org/admin-guide/mm/userfaultfd.html
- Android Developers Blog：Android 13 is in AOSP!：https://android-developers.googleblog.com/2022/08/android-13-is-in-aosp.html
- ACK 提交：为 ART GC 回移 MOVE 并加 CONFIRM_FIXED：https://android.googlesource.com/kernel/common/+/cda53df1cbb4ce72720af3c5a5506fa7d46c509e
- ACK 提交：去 anon_vma 锁的现场数据：https://android.googlesource.com/kernel/common/+/3b9607fc464353d00b8e971281119f7e0549ca44
