+++
date = '2026-08-25T10:00:45+08:00'
draft = false
title = 'MADV_GUARD_INSTALL：不用新建 VMA 的 Linux 内存护栏'
author = 'JekYUlll'
lastmod = '2026-08-25T10:00:45+08:00'
tags = ['madvise', 'virtual-memory', 'guard-region']
categories = ['linux']
+++

保护线程栈或内存分配区时，常见做法是在边界放一页 `PROT_NONE`。越界读写会触发 `SIGSEGV`，问题能在第一次踩过界时暴露，而不是等相邻对象被改坏后再追查。

这个办法很实用，但保护页多起来以后，VMA 也会跟着膨胀。Linux 6.13 加入的 `MADV_GUARD_INSTALL` 把保护信息放进页表，绕开了这个麻烦。

## 一页保护区为什么会拆出 VMA

VMA（Virtual Memory Area）描述一段属性连续的虚拟地址。假设先 `mmap()` 一块连续的读写内存，再把中间一页改成 `PROT_NONE`，内核通常要把原来的 VMA 拆开：

```text
原来： [            RW            ]
之后： [     RW     ][ PROT_NONE ][     RW     ]
```

线程运行时和内存分配器可能维护大量栈、arena 或隔离槽位。每个保护区都靠独立 VMA 表达时，进程的 VMA 树会越来越碎，还会逼近 `vm.max_map_count`。这不是某一次 `mprotect()` 慢几纳秒的问题，麻烦来自对象数量不断累积。

轻量 guard region 的思路很直接：保留覆盖整块地址范围的原 VMA，只在页表项里写入 `PTE_MARKER_GUARD`。访问该页时，缺页处理路径识别这个标记并发送 `SIGSEGV`。

```text
VMA：      [                  RW                  ]
页表：     [ normal ][ normal ][ GUARD ][ normal ]
```

保护页不需要物理页，也不需要新建 VMA。原补丁沿用了 userfaultfd poison 机制使用的 PTE marker 框架，但为 guard region 单独定义了标记，因为硬件内存损坏的错误语义并不适合普通越界检测。

### guard 状态落在页表里

`MADV_GUARD_INSTALL` 先检查目标 VMA 是否允许安装，再清掉范围内已经存在的页映射，随后由 page walker 写入 guard marker。内存访问走到缺页处理路径时，内核看到的不是“尚未分配”，而是一个明确的拒绝标记，于是向进程发送 `SIGSEGV`。这也解释了为什么安装操作会吃掉匿名页原内容。

guard marker 借用了 swap-like PTE 的表示方式，但它没有对应的交换槽和物理页。`MADV_GUARD_REMOVE` 只删除这类 marker。补丁特意避免为了移除 guard 去拆普通 huge page，也不会误删范围里已经存在的正常映射。

安装过程可能和其他线程的缺页访问碰在一起，内核会重试，必要时通过可重启系统调用重新进入。不过，内核没法替应用判断对象是否还在使用。分配器把一个槽位改成 guard region 前，仍要先收回所有引用，否则一次合法访问也会把进程打崩。

## 安装和移除到底改了什么

接口仍是 `madvise()`：

```c
madvise(addr, length, MADV_GUARD_INSTALL);
madvise(addr, length, MADV_GUARD_REMOVE);
```

`addr` 必须按页对齐，`length` 会向上取整到页大小。安装后，范围内的读和写都会收到 `SIGSEGV`。重复安装不会叠加一层状态，移除一个混合范围时也只清理 guard marker，普通页保持原样。

安装操作会替换已有页映射。对匿名私有映射执行 `MADV_GUARD_REMOVE` 后，那一页变回按需分配的零页，原数据不会回来。Linux 6.15 开始支持文件映射，移除标记后会按底层文件的最新内容重新填充。

版本差异也得写进兼容逻辑。Linux 6.13 只接受可写的匿名私有映射；Linux 6.15 扩展到匿名映射和文件映射，也允许只读映射。`mlock()` 区域、HugeTLB 和 `VM_PFNMAP`、`VM_IO`、`memfd_secret()` 这类特殊映射不能安装 guard region，内核会返回 `EINVAL`。

### `/proc/self/maps` 看不到每一页 guard

`/proc/<pid>/maps` 展示的是 VMA。轻量 guard region 没有拆 VMA，所以它不会像独立的 `PROT_NONE` 映射那样占一行。这正是接口省事的地方，也给调试工具留了个坑：只解析 `maps` 无法还原哪些页被保护。

当前内核自测会读取 `/proc/<pid>/pagemap` 的 `PM_GUARD_REGION` 位，还会通过 `PAGEMAP_SCAN` 查询 `PAGE_IS_GUARD`。这两个接口能给出页级结果。`smaps` 中的 VMA guard 标志只适合做粗筛，它可能在 marker 全部移除后继续保留，因为内核不会为了清这个提示再扫描整段页表。

我会把“fork 一个子进程，故意访问目标页并检查信号”留在测试里。办法有点笨，但它验证的是程序真正关心的语义，而且不依赖诊断接口是否已经跟上新内核。

## 跑一个三页的最小例子

下面的程序映射三页，把中间页设成 guard region，再 fork 子进程去读它。父进程检查子进程是否死于 `SIGSEGV`，因此不需要在信号处理器里做 `longjmp()`。最后移除保护，并确认匿名页内容已经清零。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef MADV_GUARD_INSTALL
#define MADV_GUARD_INSTALL 102
#endif
#ifndef MADV_GUARD_REMOVE
#define MADV_GUARD_REMOVE 103
#endif

static int access_signal(void *addr)
{
    pid_t pid = fork();
    int status;

    if (pid < 0) {
        perror("fork");
        return -1;
    }
    if (pid == 0) {
        volatile unsigned char value = *(volatile unsigned char *)addr;
        (void)value;
        _exit(0);
    }
    if (waitpid(pid, &status, 0) < 0) {
        perror("waitpid");
        return -1;
    }
    return WIFSIGNALED(status) ? WTERMSIG(status) : 0;
}

int main(void)
{
    long page_size = sysconf(_SC_PAGESIZE);
    size_t length;
    unsigned char *buf;
    int sig;

    if (page_size <= 0) {
        fputs("cannot read page size\n", stderr);
        return 1;
    }
    length = 3 * (size_t)page_size;
    buf = mmap(NULL, length, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buf == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    buf[0] = 'L';
    buf[page_size] = 'M';
    buf[2 * page_size] = 'R';

    if (madvise(buf + page_size, (size_t)page_size,
                MADV_GUARD_INSTALL) < 0) {
        fprintf(stderr, "MADV_GUARD_INSTALL: %s\n", strerror(errno));
        fputs("kernel may be older than Linux 6.13\n", stderr);
        munmap(buf, length);
        return 77;
    }

    sig = access_signal(buf + page_size);
    printf("guarded access: signal=%d\n", sig);
    printf("neighbors: %c %c\n", buf[0], buf[2 * page_size]);
    if (sig != SIGSEGV) {
        fputs("guard region did not raise SIGSEGV\n", stderr);
        munmap(buf, length);
        return 1;
    }

    if (madvise(buf + page_size, (size_t)page_size,
                MADV_GUARD_REMOVE) < 0) {
        perror("MADV_GUARD_REMOVE");
        munmap(buf, length);
        return 1;
    }
    printf("after remove: middle=%u\n", buf[page_size]);

    munmap(buf, length);
    return 0;
}
```

编译命令没有特殊依赖：

```bash
cc -std=c11 -O2 -Wall -Wextra -pedantic guard_demo.c -o guard_demo
./guard_demo
```

Linux 6.13 及更新内核上的匿名映射会得到类似输出：

```text
guarded access: signal=11
neighbors: L R
after remove: middle=0
```

头文件较旧时，代码里的两个后备宏让程序仍能编译。它们不能给旧内核补功能，旧内核会对未知 advice 返回 `EINVAL`，程序以 77 退出。部署到混合内核版本的机器时，运行时探测比只检查头文件靠谱。

手册还给了一个便宜的探测法：`madvise(0, 0, advice)` 只有在内核认识该 advice 时才返回零。它只能回答“接口在不在”，不能验证目标映射是否合规。分配器启动时最好再建一小段匿名映射，完整走一遍安装和移除。

## 什么时候继续用 `PROT_NONE`

轻量 guard region 最适合“同一个大映射里散布许多保护槽位”的场景。分配器可以一次保留 arena，在对象边界安装 marker；线程库也能减少栈保护页带来的 VMA 数量。内核自测还覆盖了 `process_madvise()` 批量操作，用一个 `iovec` 数组处理多段地址。

只有一两个保护页时，我仍会用 `mmap(PROT_NONE)` 或 `mprotect(PROT_NONE)`。它们可用时间更久，兼容范围也更宽。新接口依赖 Linux 6.13，而且安装会丢掉匿名页原内容，调用位置写错会很难看。

它也不是安全边界。同一进程能调用 `MADV_GUARD_REMOVE`，内存破坏同样可能改掉负责管理保护区的用户态元数据。这个机制擅长让越界更早崩溃，不能把不可信代码关进沙箱。

还有一点挺合我的胃口：guard marker 会跟随 `fork()` 和 `mremap()`，`MADV_DONTNEED`、`MADV_FREE`、`MADV_COLD`、`MADV_PAGEOUT` 也不会顺手清掉它。保护语义不会因为一次内存回收建议悄悄消失。真正清理它的路径很明确，调用 `MADV_GUARD_REMOVE`、`munmap()`，或者结束进程。

## 参考

- Linux `madvise(2)` 手册：https://man7.org/linux/man-pages/man2/madvise.2.html
- Linux 6.13 轻量 guard pages 补丁说明：https://lwn.net/Articles/996126/
- Linux man-pages 接口说明补丁：https://lwn.net/Articles/1000908/
- Linux 内核 guard regions 自测源码：https://github.com/torvalds/linux/blob/master/tools/testing/selftests/mm/guard-regions.c
- Linux UAPI `mman-common.h`：https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/mman-common.h
