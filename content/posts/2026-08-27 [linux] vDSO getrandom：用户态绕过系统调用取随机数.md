+++
date = '2026-08-27T10:25:04+08:00'
draft = false
title = 'vDSO getrandom：用户态绕过系统调用取随机数'
author = 'JekYUlll'
lastmod = '2026-08-27T10:25:04+08:00'
tags = ['getrandom', 'vdso', 'random', 'syscall']
categories = ['linux']
+++

getrandom() 是 Linux 上取安全随机数的标准入口，可它是个系统调用。一次调用要进出内核，数据还得拷来拷去。对大多数程序无所谓，但高频生成 UUID、nonce、会话 token 的服务，这部分开销就实实在在压在路径上。

Linux 6.11（2024 年 9 月发布）合入了 getrandom() 的 vDSO 实现：随机数在用户态直接生成，绝大多数调用根本不进内核。这套东西随后进了 glibc 2.41，应用换一次 libc 就白拿到加速。

## 用户态自己搞 RNG 为什么不行

vDSO 不是新概念。gettimeofday()、clock_gettime() 早就是这么干的：内核把只读数据映射进每个进程，用户态读一下内存就返回，省掉系统调用。随机数理论上也能这么做，难点在种子怎么管理。

自己维护一个用户态 PRNG，从内核取一次种子然后自行扩展，这是很多库的常规做法。内核 RNG 维护者 Jason Donenfeld 在补丁 cover letter 里说得不客气：在 T1 时刻展开一次 getrandom() 种子的用户态 RNG，几乎总比每次都调 getrandom() 更差。内核知道系统有多少熵、知道虚拟机快照和 fork 这类会毁掉 RNG 状态的事件，用户态拿到种子之后对这些一无所知。

所以他的方案是：把内核那套 fast key erasure 算法（ChaCha20）整个搬到用户态，内核通过共享数据页告诉用户态"你的 key 该换了"。调用方拿到的随机数和内核生成的是同一个算法，安全性没有打折。

## 最终设计里没有新系统调用

第一版方案有个专用系统调用 vgetrandom_alloc()，用来分配存放 RNG 状态的内存，配合内核 VMA 标志 VM_DROPPABLE（内存紧张时直接丢弃，不写 swap）。Linus 直接否了：为单个功能搞专用内存分配系统调用，早晚被人拿去干别的，"that nightmare has to be avoided"。他甚至一度只想要一个五行补丁，在 vDSO 数据页里导出一个 generation 计数器，让用户态 RNG 自己看着办。

拉扯的结局是各退一步：新系统调用删掉，"可丢弃内存"变成 mmap 的通用 flag。6.11 里 mmap 多了 MAP_DROPPABLE（0x08），vgetrandom_alloc() 不存在了。于是 vDSO getrandom 成了没有系统调用号的功能，连分配状态内存都走普通 mmap。

状态内存具体怎么分配，由 vDSO 函数自己回答。用 `__vdso_getrandom(NULL, 0, 0, &params, ~0UL)` 这种特殊调用方式，它往 params 里填一个结构：

```c
struct vgetrandom_opaque_params {
    uint32_t size_of_opaque_state;  /* 单个 state 的大小 */
    uint32_t mmap_prot;             /* 该用哪个 prot 去 mmap */
    uint32_t mmap_flags;            /* 该用哪个 flags（含 MAP_DROPPABLE） */
    uint32_t reserved[13];
};
```

调用方拿这些参数自己 mmap，每个线程一个 state。协议有点绕，但避免了专用系统调用。

## 共享数据页和每线程 state

vDSO 数据页里新增了一个结构：

```c
struct vdso_rng_data {
    u64 generation;   /* 内核 RNG 重播种的次数 */
    u8  is_ready;     /* 内核 RNG 是否已初始化 */
};
```

每个线程的 opaque state（struct vgetrandom_state）里有 96 字节的随机数缓冲（一个半 ChaCha20 块）、32 字节的 key、上次使用的 generation、缓冲位置和 in_use 标志。一次调用的流程：

1. is_ready 为假，直接回退到 getrandom 系统调用。RNG 没就绪时行为很复杂（要阻塞、要等熵），用户态处理不了。
2. state 记录的 generation 和内核不一致，说明内核重播种过（或者 state 被清零过），先用 getrandom 系统调用取 32 字节新 key。
3. 用户态跑 ChaCha20 生成随机数，从缓冲里取，取走的同时把源清零，保住前向保密。
4. 返回前再读一次 generation。调用期间内核又重播种的话，刚才的结果作废重来。

第 2 步有个顺序讲究：先写 state 的 generation 再调系统调用取 key。如果刚好在这中间 fork，父子进程会从系统调用拿到不同的 key，不会产出相同随机流。

"fast key erasure"这个名字对应第 3 步的细节：缓冲用完时用旧 key 生成新的一批随机数，新缓冲的前 32 字节立刻覆盖旧 key。也就是说 key 用一次就被擦掉，即使未来某个时刻内存被拖走分析，也拿不到能回推旧输出的密钥材料。

还有个安全细节：x86 的 ChaCha20 实现是纯寄存器、不碰栈的。调用中途进程 core dump，密钥不会留在栈上被 dump 出去。

## MAP_DROPPABLE：允许消失的内存

vDSO 状态内存用 mmap(MAP_DROPPABLE | MAP_ANONYMOUS) 分配，内核里展开成四个 VM 标志：

- VM_DROPPABLE：内存压力下直接丢弃页面，不写 swap
- VM_WIPEONFORK：fork 后子进程这部分内存清零
- VM_DONTDUMP：不进 core dump
- VM_NORESERVE：不占 overcommit 记账

fork 之后子进程的 state 是零页，generation 变成 0，和内核的 generation 永远对不上，下次调用必然走系统调用重新取 key。用户态 RNG 最头疼的 fork 问题就这么解决了：父子进程不会共享同一段随机流。

不用 mlock 也很有讲究。最初版本用 mlock 锁住状态内存，但 mlock 有资源限额，还和进程现有的 mlock 用法打架，fork 后还会解锁。droppable 内存没这些毛病：它本质是个缓存，丢了再取就行。

## 一段最小调用代码

符号查找不能靠 dlvsym，glibc 的 dlsym 不返回 vDSO 里的符号。内核 selftest（tools/testing/selftests/vDSO/vdso_test_getrandom.c）的做法是拿 AT_SYSINFO_EHDR 找到 vDSO 的 ELF 头，自己扫 .dynsym。下面是简化版：

```c
// vdso_getrandom_demo.c —— 探测并调用 vDSO getrandom，测量三种路径开销
// 编译: gcc -O2 -o vdso_getrandom_demo vdso_getrandom_demo.c
// 内核 >= 6.11 且有 x86_64 vDSO 实现时走 vDSO 快路径，否则回退并给出提示。
#define _GNU_SOURCE
#include <elf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/auxv.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/syscall.h>

typedef ssize_t (*vdso_getrandom_fn)(void *, size_t, unsigned int, void *, size_t);

struct vgetrandom_opaque_params {
    uint32_t size_of_opaque_state;
    uint32_t mmap_prot;
    uint32_t mmap_flags;
    uint32_t reserved[13];
};

/* 在 vDSO 的 .dynsym 里线性查找符号（demo 面向 x86_64） */
static void *vdso_sym(const char *name)
{
    uintptr_t base = getauxval(AT_SYSINFO_EHDR);
    if (!base)
        return NULL;

    const Elf64_Ehdr *eh = (const Elf64_Ehdr *)base;
    const Elf64_Shdr *sh = (const Elf64_Shdr *)(base + eh->e_shoff);
    const char *shstr = (const char *)(base + sh[eh->e_shstrndx].sh_offset);

    const Elf64_Sym *dynsym = NULL;
    const char *dynstr = NULL;
    size_t nsym = 0;

    for (int i = 0; i < eh->e_shnum; i++) {
        if (sh[i].sh_type == SHT_DYNSYM) {
            dynsym = (const Elf64_Sym *)(base + sh[i].sh_offset);
            nsym = sh[i].sh_size / sizeof(Elf64_Sym);
        } else if (sh[i].sh_type == SHT_STRTAB &&
                   strcmp(shstr + sh[i].sh_name, ".dynstr") == 0) {
            dynstr = (const char *)(base + sh[i].sh_offset);
        }
    }
    if (!dynsym || !dynstr)
        return NULL;

    for (size_t i = 0; i < nsym; i++) {
        if (dynsym[i].st_name &&
            strcmp(dynstr + dynsym[i].st_name, name) == 0)
            return (void *)(base + dynsym[i].st_value);
    }
    return NULL;
}

static vdso_getrandom_fn vdso_fn;
static void *vdso_state;
static size_t vdso_state_size;

/* 探测 vDSO 符号；返回 0 表示当前内核没有 vDSO getrandom */
static int vdso_init(void)
{
    vdso_fn = (vdso_getrandom_fn)vdso_sym("__vdso_getrandom");
    if (!vdso_fn)
        return 0;

    /* 参数查询：buffer/len/flags 全 0、opaque_len = ~0UL */
    struct vgetrandom_opaque_params p;
    if (vdso_fn(NULL, 0, 0, &p, ~0UL) != 0)
        return 0;

    void *s = mmap(NULL, p.size_of_opaque_state, p.mmap_prot, p.mmap_flags, -1, 0);
    if (s == MAP_FAILED)
        return 0;
    vdso_state = s;
    vdso_state_size = p.size_of_opaque_state;

    printf("[vDSO] __vdso_getrandom 可用\n");
    printf("[vDSO] state %u 字节, mmap prot=0x%x flags=0x%x\n",
           p.size_of_opaque_state, p.mmap_prot, p.mmap_flags);
    return 1;
}

/* 返回每 op 的纳秒数 */
static double bench(const char *name, ssize_t (*fn)(void *, size_t, unsigned int),
                    size_t len, long iters)
{
    struct timespec a, b;
    unsigned char buf[64];
    uint64_t acc = 0;

    clock_gettime(CLOCK_MONOTONIC, &a);
    for (long i = 0; i < iters; i++) {
        ssize_t r = fn(buf, len, 0);
        if (r != (ssize_t)len) {
            fprintf(stderr, "%s: 返回 %zd != %zu\n", name, r, len);
            exit(1);
        }
        acc ^= buf[0] ^ buf[len - 1];
    }
    clock_gettime(CLOCK_MONOTONIC, &b);

    double ns = (double)(b.tv_sec - a.tv_sec) * 1e9 + (double)(b.tv_nsec - a.tv_nsec);
    printf("  %-8s %3zu 字节: %8.1f ns/op (checksum %#lx)\n",
           name, len, ns / iters, (unsigned long)acc);
    return ns / iters;
}

static ssize_t wrap_libc(void *buf, size_t len, unsigned int flags)
{
    return getrandom(buf, len, flags);
}

static ssize_t wrap_syscall(void *buf, size_t len, unsigned int flags)
{
    return syscall(SYS_getrandom, buf, len, flags);
}

static ssize_t wrap_vdso(void *buf, size_t len, unsigned int flags)
{
    return vdso_fn(buf, len, flags, vdso_state, vdso_state_size);
}

int main(void)
{
    long iters = 5000000;

    if (!vdso_init()) {
        printf("[vDSO] 未找到 __vdso_getrandom：内核不支持（需要 >= 6.11），只测系统调用路径\n");
        bench("libc", wrap_libc, 4, iters);
        bench("syscall", wrap_syscall, 4, iters);
        return 0;
    }

    bench("vdso", wrap_vdso, 4, iters);
    bench("libc", wrap_libc, 4, iters);
    bench("syscall", wrap_syscall, 4, iters);
    return 0;
}
```

在 6.8 内核上编译运行，走回退分支：

```
$ gcc -O2 -o vdso_getrandom_demo vdso_getrandom_demo.c
$ ./vdso_getrandom_demo
[vDSO] 未找到 __vdso_getrandom：内核不支持（需要 >= 6.11），只测系统调用路径
  libc       4 字节:    421.5 ns/op
  syscall    4 字节:    418.7 ns/op
```

glibc 2.39 的 getrandom() 就是裸系统调用，两条路径数字一样，符合预期。0.42 微秒一次，意味着单线程每秒最多做两百万次 4 字节调用，纯系统调用开销。这个数字是虚拟机上测的，物理机可能快一些，但数量级摆在那里：高频取随机数的服务，省掉这 0.4 微秒就是实打实的收益。

在 6.11+ 内核上同一份代码会打印 state 大小和 mmap 参数，然后给出 vdso/libc/syscall 三行对比。Jason 声称 uint32_t 生成能快约 15 倍，这是他自己的 microbenchmark，真实收益取决于调用频率。Linus 当时对此很不满，原话是 "I'm not AT ALL interested in microbenchmarks"，他要真实用户站出来，举出"这个程序 10% 的时间花在 getrandom() 上"这种负载。后来 glibc 的集成证明确实有人需要，v21 在 2024 年 7 月合入主线。

## 落地情况和坑

glibc 2.41（2025 年 2 月）的 getrandom() 和 arc4random() 包装层会自动使用 vDSO：按页批量 mmap 一批 state，每个线程首次调用时领一个，线程退出时回收，信号打断导致的竞争通过 state 地址最低位的 guard 回退到系统调用。glibc 2.36 引入的 arc4random() 本来就是 getrandom() 的薄包装，现在也一并提速。x86_64 从内核 6.11 开始支持，arm64、loongarch64、powerpc、s390x 在 6.12 跟上，RISC-V 在 6.16 周期合入。

坑也真实存在。CVE-2025-0577：glibc 的 vDSO 加速在"fork 与 getrandom 调用并发"的竞态下可能返回可预测随机数，影响的是 Fedora/CentOS 的回移植版本，上游在 2.41 发布前修掉，没有发布版受影响。另外 Chromium 的 seccomp 过滤器最初没放行 MAP_DROPPABLE，新版 glibc 下 getrandom 直接失败（Gentoo bug 949654）。自己写 seccomp 策略的沙箱，都得把 MAP_DROPPABLE 加进白名单。

vDSO 导出的符号带版本号 "LINUX_2.6"，沿袭 Linux 2.6 时代的约定，跟内核版本迭代无关。内核 selftest 查找 __vdso_getrandom 时也要带上这个版本；用 ELF 解析按名字扫符号的写法（上面的 demo）不需要关心版本，但用 dlvsym 之类的接口就得写对。

## 参考

- LWN: A vDSO implementation of getrandom()（2023-01）: https://lwn.net/Articles/919008/
- LWN: Another try for getrandom() in the vDSO（2024-07）: https://lwn.net/Articles/980447/
- LWN: What became of getrandom() in the vDSO（2024-07）: https://lwn.net/Articles/983186/
- 内核源码: lib/vdso/getrandom.c、include/vdso/getrandom.h、include/vdso/datapage.h、mm/mmap.c、drivers/char/random.c（v6.11）
- 内核 selftest: tools/testing/selftests/vDSO/vdso_test_getrandom.c
- Phoronix: Linux 6.11 Lands Support For getrandom() In The vDSO: https://www.phoronix.com/news/Linux-6.11-Lands-getrandom-vDSO
- Phoronix: GNU C Library Merges Support for getrandom vDSO: https://www.phoronix.com/news/glibc-getrandom-vDSO-Merged
- Red Hat Bugzilla 2338871（CVE-2025-0577）: https://bugzilla.redhat.com/show_bug.cgi?id=2338871
- Gentoo Bug 949654（Chromium 与 MAP_DROPPABLE）: https://bugs.gentoo.org/show_bug.cgi?id=949654
- man 7 vdso: https://man7.org/linux/man-pages/man7/vdso.7.html
