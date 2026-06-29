+++
date = '2026-06-29T19:20:38+08:00'
draft = false
title = 'cachestat：用系统调用观察 Linux 页缓存命中'
author = 'JekYUlll'
lastmod = '2026-06-29T19:20:38+08:00'
tags = ['cachestat', 'page-cache', 'linux-kernel']
categories = ['linux']
+++

Linux 页缓存平时像黑箱。读文件变快了，你知道大概是 cache 命中了；写入卡住了，你怀疑后台 writeback 在拖后腿。但真要问「这个文件的这一段有多少页在 cache 里」，老办法很别扭。

以前常见做法是 `mincore()`。它能告诉你 mmap 区间里的页面是否驻留在内存里，但它给的是逐页 bitmap。你还要先 mmap 文件，再自己数 bit，最后 munmap。小文件无所谓，遇到几百 GB 的数据文件或者目录树扫描，这个接口就开始折磨人。

`cachestat()` 是 Linux 6.5 加进来的系统调用。它不返回逐页细节，只返回一段文件范围的聚合统计：缓存页、脏页、正在回写的页、被回收过的页、最近被回收的页。这个取舍很工程：少给一点细节，换一个可以直接拿来做判断的结果。

## 背景

页缓存解决的是磁盘慢的问题。应用 `read()` 一个文件后，内核通常会把数据页留在 page cache 里；下一次读同一段数据，不用再碰磁盘。数据库、日志系统、构建缓存、对象存储网关都会被这个机制影响。

麻烦在于，页缓存不是某个进程的私有状态。它挂在文件和 inode 周围，被全系统共享，还会被内存压力、readahead、writeback、`posix_fadvise()` 这些东西一起影响。你在应用里看到一次慢查询，很难立刻判断是索引没进 cache，还是数据页被回收了。

`mincore()` 可以查 residency，但它的目标更底层：给一段虚拟地址范围，返回每页是否 resident。用它查文件 cache 要绕一圈：打开文件，mmap，调用 mincore，聚合 bitmap，munmap。LKML 上的 RFC 里给过一个很夸张的例子：2TB sparse file 上，`mincore()` 跑了 37.510 秒，`cachestat()` 跑了 0.009 秒。这个数字来自补丁作者的测试，不要直接当通用 benchmark，但方向很清楚：如果你只要聚合统计，逐页 bitmap 是浪费。

## 核心原理

`cachestat()` 的接口很小：

```c
#include <sys/mman.h>

int cachestat(unsigned int fd,
              struct cachestat_range *cstat_range,
              struct cachestat *cstat,
              unsigned int flags);

struct cachestat_range {
    __u64 off;
    __u64 len;
};

struct cachestat {
    __u64 nr_cache;
    __u64 nr_dirty;
    __u64 nr_writeback;
    __u64 nr_evicted;
    __u64 nr_recently_evicted;
};
```

`fd` 指向要查询的文件。`off` 和 `len` 描述文件里的字节范围；`len == 0` 表示从 `off` 查到文件结尾。`flags` 现在必须填 0，给以后扩展留位置。

返回值里的单位是页，不是字节。`nr_cache` 是命中的 page cache 页数；`nr_dirty` 是还没写回磁盘的脏页；`nr_writeback` 是正在写回的页。后两个 eviction 字段更偏内核内存回收视角：页面以前在 cache 里，后来被回收；如果它重新进 cache 能说明这段数据在内存压力下仍然活跃，就会落到 recently evicted 这类语义里。

这不是一致性快照。man page 说得很直白：内核取完状态到应用拿到结果之间，页面状态可能已经变了。所以它适合做观测和决策输入，不适合做锁，也不适合当审计依据。

还有两个边界要记住。第一，它是 Linux 专有接口，历史从 6.5 开始；老内核会返回 `ENOSYS` 或根本没有 syscall 号。第二，`hugetlbfs` 目前不支持，可能返回 `EOPNOTSUPP`。

## 代码实战

glibc 不一定已经包了一层 `cachestat()` 函数。更稳的写法是直接走 `syscall(SYS_cachestat, ...)`，结构体用内核 UAPI 头文件里的定义。

下面这个 demo 做三件事：创建 8MiB 文件，先用 `POSIX_FADV_DONTNEED` 尽量把干净页踢出 cache；读 4KiB 后再查一次；最后写 4KiB，不 fsync，看看 dirty 页数。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/mman.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef SYS_cachestat
#define SYS_cachestat __NR_cachestat
#endif

static int do_cachestat(int fd, unsigned long long len, struct cachestat *cs) {
    struct cachestat_range range = { .off = 0, .len = len };
    memset(cs, 0, sizeof(*cs));
    return syscall(SYS_cachestat, fd, &range, cs, 0);
}

static void print_stat(const char *label, const struct cachestat *cs) {
    printf("%-18s cache=%llu dirty=%llu writeback=%llu evicted=%llu recent=%llu\n",
           label,
           (unsigned long long)cs->nr_cache,
           (unsigned long long)cs->nr_dirty,
           (unsigned long long)cs->nr_writeback,
           (unsigned long long)cs->nr_evicted,
           (unsigned long long)cs->nr_recently_evicted);
}

static void die(const char *what) {
    perror(what);
    exit(1);
}

int main(void) {
    const char *path = "/tmp/cachestat-demo.bin";
    const size_t file_size = 8 * 1024 * 1024;
    const size_t chunk = 4096;
    char buf[4096];
    struct cachestat cs;

    memset(buf, 'x', sizeof(buf));

    int fd = open(path, O_CREAT | O_TRUNC | O_RDWR, 0644);
    if (fd < 0) die("open");

    for (size_t off = 0; off < file_size; off += chunk) {
        if (write(fd, buf, chunk) != (ssize_t)chunk) die("write");
    }
    if (fsync(fd) != 0) die("fsync");

    if (posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED) != 0) {
        errno = EINVAL;
        die("posix_fadvise");
    }

    if (do_cachestat(fd, file_size, &cs) != 0) die("cachestat after drop");
    print_stat("after DONTNEED", &cs);

    if (pread(fd, buf, sizeof(buf), 0) != (ssize_t)sizeof(buf)) die("pread");
    if (do_cachestat(fd, file_size, &cs) != 0) die("cachestat after read");
    print_stat("after 4K read", &cs);

    memset(buf, 'y', sizeof(buf));
    if (pwrite(fd, buf, sizeof(buf), 0) != (ssize_t)sizeof(buf)) die("pwrite");
    if (do_cachestat(fd, file_size, &cs) != 0) die("cachestat after write");
    print_stat("after 4K write", &cs);

    close(fd);
    unlink(path);
    return 0;
}
```

在一台 6.8 x86_64 机器上，我这里的输出是：

```text
after DONTNEED     cache=0 dirty=0 writeback=0 evicted=0 recent=0
after 4K read      cache=4 dirty=0 writeback=0 evicted=0 recent=0
after 4K write     cache=4 dirty=1 writeback=0 evicted=0 recent=0
```

为什么读 4KiB 后 cache 不是 1？因为文件系统和块层可能触发 readahead，多读几页进来。这个细节反而很适合说明 `cachestat()` 的定位：它告诉你内核现在看到了什么，不保证你的应用刚才只碰过哪一页。

如果 `nr_evicted` 和 `nr_recently_evicted` 一直是 0，也不奇怪。这个 demo 太小，基本不会制造真正的内存压力。要观察 eviction，得用更大的文件、限制 cgroup 内存，或者在同一台机器上跑会挤压 page cache 的负载。别为了让输出好看就在系统盘上乱造压力，测试机和生产机要分开。

编译命令：

```bash
cc -Wall -Wextra -O2 cachestat_demo.c -o cachestat_demo
./cachestat_demo
```

如果运行时报 `Function not implemented`，先看内核版本和 `CONFIG_CACHESTAT_SYSCALL`。头文件里有结构体，不代表当前内核运行时一定支持这个 syscall。

## 工程取舍

`cachestat()` 最适合做「便宜的信号」。比如数据库可以在扫描大索引前看一下索引区间是否已经在 cache 里；备份工具可以估算自己会不会把热数据挤出去；排查写入抖动时，可以看某个日志文件是不是堆了很多 dirty 或 writeback 页。

它不适合做精确账本。页缓存会被全系统共享，别的进程可以在你查询后立刻把同一段文件读进来或者挤出去。你拿到的是一个瞬时近似值。

和 `mincore()` 的关系也别理解成替代。你需要逐页 bitmap，继续用 `mincore()`；你只关心「这一段大概有多少页在 cache 里」，`cachestat()` 更顺手。

我会把它放进性能排查工具箱，而不是业务热路径。业务热路径里加 syscall 本身就要谨慎，更何况 cache 状态不是稳定输入。做诊断、做采样、做离线扫描，它很舒服。

另一个常见误用是把它当成自动预热开关：发现 `nr_cache` 低，就立刻全量读一遍文件。这个动作可能把别人的热页挤出去，最后你只是把一个服务的问题转嫁给另一个服务。更稳的做法是先采样，确认热点区间真的反复 miss，再决定要不要预热，而且最好放在低峰期。

## 今日可执行动作

1. 在自己的 Linux 机器上跑上面的 demo。先看 `uname -r`，确认内核至少是 6.5，再观察 `nr_cache` 和 `nr_dirty` 怎么变。
2. 找一个大文件，把 demo 改成接受文件路径和 offset。查询前后分别跑 `dd if=file of=/dev/null bs=4M count=16`，看 readahead 会让 cache 页数增长到什么程度。
3. 如果你维护数据库或日志服务，写一个只读小工具定时采样几个热点文件。不要先上自动调参，先把 cache 状态画出来。很多「磁盘慢」其实是页缓存工作方式和预期不一致，先量清楚再动手。

## 参考

- `cachestat(2)` Linux manual page: https://www.man7.org/linux/man-pages/man2/cachestat.2.html
- LWN / LKML patch summary, `cachestat: a new syscall for page cache state of files`: https://lwn.net/Articles/930785/
- LKML RFC, `[RFC][PATCH 0/4] cachestat`: https://lkml.org/lkml/2022/11/15/1068
- Phoronix, Linux 6.5 MM update with `cachestat`: https://www.phoronix.com/news/Linux-6.5-MM-cachestat
- LKDDB, `CONFIG_CACHESTAT_SYSCALL`: https://cateee.net/lkddb/web-lkddb/CACHESTAT_SYSCALL.html
