+++
date = '2026-08-16T10:10:10+08:00'
draft = false
title = 'PAGEMAP_SCAN：一次 ioctl 找出进程里真正变脏的页'
author = 'JekYUlll'
lastmod = '2026-08-16T10:10:10+08:00'
tags = ['pagemap', 'userfaultfd', 'memory-tracking']
categories = ['linux']
+++

想知道一段内存里哪些页刚被写过，旧办法通常是读 `/proc/<pid>/pagemap` 的 soft-dirty 位，再往 `clear_refs` 写值重置状态。能用，但大区间反复扫起来不便宜，而且 VMA 合并、`mprotect()` 等操作可能让 soft-dirty 多报。做增量 checkpoint 时，多复制几页还只是浪费带宽；拿它判断内存是否被改过，误报就很烦。[5]

Linux 6.7 加入了 `PAGEMAP_SCAN`。它仍然作用于 `/proc/<pid>/pagemap`，但把筛选、结果压缩和重新写保护放进一次 `ioctl()`。接口返回的是虚拟地址区间，不暴露 PFN，粒度为一个页面。[1][7]

## 它盯的是写保护位，不是每条 store

`PAGE_IS_WRITTEN` 背后用的是 userfaultfd 异步写保护。程序先给一段 VMA 注册 `UFFDIO_REGISTER_MODE_WP`，再设置写保护。某页第一次被写时，内核清掉该页的 uffd-wp 位并让写入继续，不向用户态发送 fault 消息。之后扫描哪些页失去了保护，就知道哪些页被写过。[1][3]

这条路径和同步 userfaultfd 不太一样。同步模式需要另一个线程读取 fault 并解除保护，异步模式不用常驻处理线程。页面被写时不会停下来等用户态，代价是你只拿到“这一页写过”这个状态，不知道哪条指令写了几次。

```text
全部页设为 uffd-wp
        |
        +-- 写 page 1 -> page 1 的 uffd-wp 被清除
        +-- 写 page 6 -> page 6 的 uffd-wp 被清除
        |
PAGEMAP_SCAN(PAGE_IS_WRITTEN)
        -> [page 1, page 2)
        -> [page 6, page 7)
```

内核文档把它称作比 soft-dirty 更准确的替代方案：普通页的结果不受 VMA 合并影响。不过别把“更准确”理解成绝对精确。THP、Hugetlb 仍可能多报；匿名映射上的 `MADV_DONTNEED` 会丢掉 uffd-wp 位，也会被当作写脏。[2][3]

## `pm_scan_arg` 最容易填错的地方

调用入口很普通：打开 pagemap 文件，然后把 `struct pm_scan_arg` 传给 `ioctl()`。`start` 和 `end` 给出扫描范围，起始地址必须按页对齐。`vec` 指向 `struct page_region` 数组，`vec_len` 是数组容量。内核会把类别相同且连续的页合并成一个 region，所以返回值是 region 数量，不一定等于脏页数量。[1]

几个 mask 的分工不要混：`category_mask` 中的条件必须全部满足；`category_anyof_mask` 至少满足一项；`category_inverted` 把指定条件反过来匹配；`return_mask` 只决定结果里报告哪些位。只找写过的页时，前两个关键字段都填 `PAGE_IS_WRITTEN`：

```c
.category_mask = PAGE_IS_WRITTEN,
.return_mask = PAGE_IS_WRITTEN,
```

region 压缩会直接影响结果数组的大小。假设第 40 到 79 页连续写过，内核通常只返回 `[40, 80)` 一项；如果只写偶数页，就可能产生许多短 region。`vec_len` 消耗取决于脏页分布，不只取决于脏页总数。

输出数组装满后，扫描可能提前结束。此时要看内核写回的 `walk_end`，从该地址继续下一轮，不能只看 `ioctl()` 没报错。`walk_end == end` 才算走完整段范围。[1]

周期跟踪要用 `PM_SCAN_WP_MATCHING`。它在返回匹配页的同时重新给这些页加保护，为下一轮统计清零。查询和重设保护由一次内核操作完成，不会在“先查、后清”之间留出用户态窗口。[2]

如果只想做一次盘点，可以把 `flags` 设为 0，此时扫描不会改变保护状态。同一页会在后续查询里继续出现。周期统计则应在每轮带上 `PM_SCAN_WP_MATCHING`，把“读取结果”和“建立下一轮基线”绑在一起。两种用法的生命周期不同，不能只看返回数据是否一样。

## 跑一个 8 页的最小程序

下面的程序申请 8 页匿名内存，写保护整段区域，只改第 1 页和第 6 页，然后扫描两次。初始化顺序来自内核主线 selftest，删掉了 huge page、错误参数和并发压力测试部分。[6]

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <linux/userfaultfd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static void die(const char *what)
{
    fprintf(stderr, "%s: %s\n", what, strerror(errno));
    exit(EXIT_FAILURE);
}

static long scan_written(int fd, void *base, size_t len,
                         struct page_region *regions, size_t cap)
{
    struct pm_scan_arg arg = {
        .size = sizeof(arg),
        .flags = PM_SCAN_WP_MATCHING | PM_SCAN_CHECK_WPASYNC,
        .start = (uintptr_t)base,
        .end = (uintptr_t)base + len,
        .vec = (uintptr_t)regions,
        .vec_len = cap,
        .category_mask = PAGE_IS_WRITTEN,
        .return_mask = PAGE_IS_WRITTEN,
    };

    long n = ioctl(fd, PAGEMAP_SCAN, &arg);
    if (n < 0)
        die("PAGEMAP_SCAN");
    if (arg.walk_end != arg.end) {
        fprintf(stderr, "scan stopped early at %#" PRIx64 "\n",
                (uint64_t)arg.walk_end);
        exit(EXIT_FAILURE);
    }
    return n;
}

int main(void)
{
    long page = sysconf(_SC_PAGESIZE);
    size_t len = 8 * (size_t)page;
    volatile unsigned char *mem = mmap(NULL, len,
        PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED)
        die("mmap");
    memset((void *)mem, 0, len);

    int uffd = syscall(SYS_userfaultfd,
        O_CLOEXEC | O_NONBLOCK | UFFD_USER_MODE_ONLY);
    if (uffd < 0)
        die("userfaultfd");

    struct uffdio_api api = {
        .api = UFFD_API,
        .features = UFFD_FEATURE_WP_ASYNC,
    };
    if (ioctl(uffd, UFFDIO_API, &api) < 0)
        die("UFFDIO_API");
    if (!(api.features & UFFD_FEATURE_WP_ASYNC)) {
        fprintf(stderr, "kernel lacks UFFD_FEATURE_WP_ASYNC\n");
        return EXIT_FAILURE;
    }

    struct uffdio_register reg = {
        .range = {.start = (uintptr_t)mem, .len = len},
        .mode = UFFDIO_REGISTER_MODE_WP,
    };
    if (ioctl(uffd, UFFDIO_REGISTER, &reg) < 0)
        die("UFFDIO_REGISTER");

    struct uffdio_writeprotect wp = {
        .range = reg.range,
        .mode = UFFDIO_WRITEPROTECT_MODE_WP,
    };
    if (ioctl(uffd, UFFDIO_WRITEPROTECT, &wp) < 0)
        die("UFFDIO_WRITEPROTECT");

    mem[1 * page] = 0x11;
    mem[6 * page] = 0x66;

    int fd = open("/proc/self/pagemap", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        die("open pagemap");

    struct page_region regions[8] = {0};
    long n = scan_written(fd, (void *)mem, len, regions, 8);
    printf("written regions: %ld\n", n);
    for (long i = 0; i < n; ++i) {
        uint64_t first = (regions[i].start - (uintptr_t)mem) / page;
        uint64_t past = (regions[i].end - (uintptr_t)mem) / page;
        printf("  pages [%" PRIu64 ", %" PRIu64 ") categories=%#" PRIx64 "\n",
               first, past, (uint64_t)regions[i].categories);
    }

    memset(regions, 0, sizeof(regions));
    n = scan_written(fd, (void *)mem, len, regions, 8);
    printf("second scan: %ld region(s)\n", n);

    close(fd);
    close(uffd);
    munmap((void *)mem, len);
    return 0;
}
```

编译时需要 Linux 6.7 之后的 UAPI 头文件：

```bash
cc -std=c11 -O2 -Wall -Wextra -Werror pagemap_scan_demo.c -o pagemap_scan_demo
./pagemap_scan_demo
```

我在 Linux 6.8 上跑到的输出是：

```text
written regions: 2
  pages [1, 2) categories=0x2
  pages [6, 7) categories=0x2
second scan: 0 region(s)
```

两次写入不连续，因此结果是两个 region。`0x2` 就是 `PAGE_IS_WRITTEN`。第一次扫描带了 `PM_SCAN_WP_MATCHING`，两个脏页在返回结果时已重新进入跟踪，所以没有新写入的第二次扫描得到 0。

## 把 `walk_end` 当成下一轮游标

示例只有 8 页，结果数组肯定装得下。生产里的 VMA 可能有几十 GB，直接按页面数分配 `page_region` 数组很笨。更稳的写法是固定一个小批次，例如 256 个 region，每次消费完再继续：

```text
cursor = start
while cursor < end:
    arg.start = cursor
    arg.end = end
    n = PAGEMAP_SCAN(arg)
    consume(arg.vec[0:n])
    assert arg.walk_end > cursor
    cursor = arg.walk_end
```

这里的前进条件必须检查。如果 `walk_end` 没有变化还继续循环，程序会原地打转。`max_pages` 也能限制一轮最多返回多少页，但它和 `vec_len` 不是一回事：前者约束匹配页数，后者约束压缩后的 region 数量。数据分布碎的时候，往往先撞到 `vec_len`。

固定批次可以边扫描边消费。拿到一组 region 后，马上把这些地址交给拷贝、哈希或落盘线程，扫描线程继续走下一段。这样不需要把完整脏页集合长期留在内存里，背压也比较好做。

`page_region.start` 和 `end` 是进程虚拟地址，`end` 仍按左闭右开解释。跨进程扫描时，别把它们误当成调用者自己的指针；先换算成目标地址空间里的偏移，再交给后续读取逻辑。[1]

## 适合增量同步，不适合业务判定

我会把这个接口用在页粒度的增量工作：CRIU 的预拷贝、虚拟机或模拟器的共享内存同步、进程快照。它能回答“上轮之后哪些页变了”，而且返回连续区间，后续 `process_vm_readv()` 或文件写入都容易批处理。LWN 介绍该补丁时也提到了 CRIU 和模拟器一类需求。[5]

它不适合拿来判断某个对象是否修改，更不能代替应用层事务日志。4 KiB 页面里只改一个字节，整页仍算 written；THP 下粒度还可能更粗。容器的 seccomp 也可能禁止 `userfaultfd()`。启动时应把这类错误当作能力探测失败，不要悄悄退回一个语义不同的方案。[3]

部署时要同时检查运行内核和 UAPI 头文件。头文件太旧，`PAGEMAP_SCAN`、`pm_scan_arg` 这些名字在编译期就不存在；运行内核太旧，带着新头文件编出的程序也用不了。我的习惯是在启动阶段做一次很小的能力探测，失败就明确关闭增量模式，而不是等业务跑了半天才在第一次 checkpoint 时退出。

## 参考

Sources:

[1] https://man7.org/linux/man-pages/man2/PAGEMAP_SCAN.2const.html
[2] https://docs.kernel.org/admin-guide/mm/pagemap.html
[3] https://docs.kernel.org/admin-guide/mm/userfaultfd.html
[5] https://lwn.net/Articles/940704
[6] https://raw.githubusercontent.com/torvalds/linux/master/tools/testing/selftests/mm/pagemap_ioctl.c
[7] https://kernelnewbies.org/Linux_6.7
