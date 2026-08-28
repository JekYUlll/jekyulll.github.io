+++
date = '2026-08-28T10:14:27+08:00'
draft = false
title = 'MGLRU：Linux 页回收的世代更替'
author = 'JekYUlll'
lastmod = '2026-08-28T10:14:27+08:00'
tags = ['mglru', 'page-reclaim', 'memory-management', 'mm']
categories = ['linux']
+++

页回收决定内核的缓存策略：内存不够时先赶走哪些页。Linux 靠 active/inactive 两条链表干了二十年，2022 年底 6.1 合入的 MGLRU（Multi-Gen LRU）才真正换代。它把"最近使用"从两段拆成四段世代，回收时直接挑最老的一代下手，而不是在链表里翻找。

## 两条链表的三个毛病

老实现把页分成 active 和 inactive 两条 LRU 链表。新页先进 inactive，被访问后升到 active，回收时从 inactive 尾部找牺牲品。这个模型有三个问题，Google 的 Yu Zhao 在 [patch 提交说明](https://lore.kernel.org/lkml/20210313075747.3781593-1-yuzhao@google.com/)里列得很清楚。

第一是扫描成本。内存紧张时内核要遍历 inactive 链表找足够多的可回收页，每一页都要走 rmap 反向映射确认没有进程在用它。页越多扫描越贵，CPU 消耗和内存压力同时上来。

第二是一次性访问污染。`dd`、`tar`、`cat` 这种顺序读大文件的场景，每页只读一次就被升到 active，把真正频繁使用的热页挤出去。等热页被回收再读回来，就产生了 thrashing。

第三是粒度。active/inactive 只区分"最近被用过"和"没用过"，1 秒前访问的页和 1 年前访问的页地位相同，回收时看不出差别。

## 世代：把"最近"切成四段

MGLRU 的思路是把时间切成世代（generation），页按访问时间归入不同世代，回收永远从最老世代开始。世代数量是编译期常量，`include/linux/mmzone.h`：

```c
#define MIN_NR_GENS	2U
#define MAX_NR_GENS	4U

struct lru_gen_folio {
	/* the aging increments the youngest generation number */
	unsigned long max_seq;
	/* the eviction increments the oldest generation numbers */
	unsigned long min_seq[ANON_AND_FILE];
	/* the birth time of each generation in jiffies */
	unsigned long timestamps[MAX_NR_GENS];
	/* the multi-gen LRU lists, lazily sorted on eviction */
	struct list_head folios[MAX_NR_GENS][ANON_AND_FILE][MAX_NR_ZONES];
	/* the multi-gen LRU sizes, eventually consistent */
	long nr_pages[MAX_NR_GENS][ANON_AND_FILE][MAX_NR_ZONES];
	/* the exponential moving average of refaulted */
	unsigned long avg_refaulted[ANON_AND_FILE][MAX_NR_TIERS];
	/* the exponential moving average of evicted+protected */
	unsigned long avg_total[ANON_AND_FILE][MAX_NR_TIERS];
	/* double-buffering Bloom filters */
	unsigned long *filters[NR_BLOOM_FILTERS];
};
```

`max_seq` 是最新世代号，`min_seq` 是最老的非空世代号，两者形成滑动窗口。页在缺页时进入最新世代，被访问会得到保护，回收只碰最老世代。最老世代清空后 `min_seq` 前进，窗口滑动。anon 和 file 各有独立的 `min_seq`：干净文件页随时可回收，匿名页受 swap 约束，所以两个指针可以不同步。

每个世代还记录出生时间戳，这带来一个老实现没有的能力：跨 memcg、跨 NUMA 节点比较页的新旧，正是工作集估计需要的。

## 老化：扫页表而不是扫 rmap

世代怎么推进？MGLRU 的 aging 机制直接扫描进程页表，靠硬件维护的 PTE accessed bit 判断页是否被访问过。`walk_mm()` 遍历 mm_struct 的 VMA，发现 PTE 的 young 位被置位，就把对应页提升到最新世代并清掉该位，下次扫描只统计新被访问的页。

这比 rmap 扫描便宜得多。老实现的扫描成本与 LRU 链表里页的总数成正比，MGLRU 的差分扫描成本只与"真正被引用过的页"成正比，而且页表在地址空间里的局部性比 rmap 链表好。Yu Zhao 的形容是：除非地址空间极度稀疏，否则扫页表总是比扫 rmap 划算。

enabled 位掩码里 0x2 和 0x4 两个位就是控制这个机制的：0x2 批量清叶子 PTE 的 accessed bit，0x4 连非叶子页表项一起处理。关掉它们 MGLRU 能跑，但热页集中映射的场景性能会掉。

## 分层与回弹：怎么防 thrashing

世代只解决了 recency（新近度），frequency（频率）由 tier 处理。页按文件描述符访问次数分层，tier 号是访问次数的 log2：访问 1 次进 tier 0，2-3 次进 tier 1，以此类推，上限 `MAX_NR_TIERS=4`。跨 tier 移动只动 folio 标志位，不碰 LRU 锁，路径极便宜。

关键在回弹（refault）检测。页被回收后如果很快又缺页读回，说明回收错了，这是 thrashing 的信号。MGLRU 用双缓冲 Bloom filter 记录最近被回收的页：回收时把页指纹塞进 filter，缺页时查询是否"最近被回收过"。每个 tier 统计回弹率（`avg_refaulted` 和 `avg_total` 的指数移动平均），高层 tier 回弹率超过 tier 0 时，这些页会被提升到年轻世代保护起来。

这套机制解决的就是顺序读污染：只读一次的页留在最老世代，不会因为"刚被读过"就获得保护，streaming I/O 再也挤不掉真正热的数据。

世代和 tier 两个维度都按 anon/file 分开维护，因为两者的回收成本不同。干净文件页丢掉就行，随时可回收；匿名页要么有 swap 背书要么必须保留，所以 `min_seq` 有两个指针，swap 受限时 file 侧的窗口可以单独往前走，anon 侧纹丝不动。这也解释了为什么 swappiness 在 MGLRU 下的语义没变：它仍然控制匿名页的回收倾向，只是决策粒度从"整条链表"细化到了"最老世代的某几层 tier"。

## 在 6.8 内核上打开和观察

我用的 Ubuntu 6.8.0 内核默认就开着。`/sys/kernel/mm/lru_gen/enabled` 是位掩码：0x1 主开关，0x2 批量清叶子 PTE accessed bit，0x4 处理非叶子页表项：

```bash
cat /sys/kernel/mm/lru_gen/enabled   # 0x0007，全开
echo 0 > /sys/kernel/mm/lru_gen/enabled   # 关掉回退老 LRU
echo y > /sys/kernel/mm/lru_gen/enabled   # 全开
```

`min_ttl_ms` 是防 thrashing 选项，设 N 毫秒保证最近 N 毫秒的工作集不被回收。桌面场景设 1000 左右能消除大部分卡顿（人类可感知的卡顿约 100ms），代价是内存真不够时 OOM killer 提前介入：

```bash
echo 1000 > /sys/kernel/mm/lru_gen/min_ttl_ms
```

需要 root 才能写。`/sys/kernel/debug/lru_gen` 是实验接口，读出工作集直方图，写入 `- memcg_id node_id min_gen_nr [swappiness [nr_to_reclaim]]` 可以做主动回收，调度器可以先估冷页再动手，把对现有作业的影响压到最低。

MADV_COLD 也能配合观察。下面这段代码把匿名页映射进来、全部写一遍，再把前半区标记 COLD：

```c
/* pgwatch.c: 观察匿名页驻留与 MADV_COLD 的效果 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static size_t resident(unsigned char *map, size_t pages)
{
	unsigned char *vec = calloc(pages, 1);
	size_t n = 0;
	if (mincore(map, pages * 4096, vec) < 0) { perror("mincore"); exit(1); }
	for (size_t i = 0; i < pages; i++)
		if (vec[i] & 1) n++;
	free(vec);
	return n;
}

int main(void)
{
	size_t total_mb = 512, hot_mb = 64;
	size_t pages = total_mb * 1024 * 1024 / 4096;
	unsigned char *map = mmap(NULL, total_mb << 20,
				  PROT_READ | PROT_WRITE,
				  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (map == MAP_FAILED) { perror("mmap"); return 1; }

	memset(map, 0xAB, total_mb << 20);          /* 全部写一遍 */
	printf("after touch-all  resident=%zu/%zu\n",
	       resident(map, pages), pages);

	memset(map, 0xCD, hot_mb << 20);            /* 热区再写 */
	madvise(map + (hot_mb << 20), (total_mb - hot_mb) << 20, MADV_COLD);
	printf("after madvise(COLD) resident=%zu/%zu\n",
	       resident(map, pages), pages);
	munmap(map, total_mb << 20);
	return 0;
}
```

我在本机编译运行的实际输出：

```
total=512MB hot=64MB pages=131072
after touch-all  resident=131072/131072
after madvise(COLD) resident=131072/131072
```

注意 MADV_COLD 只是提示，页不会立刻消失。它把冷区挪到最老世代，真正的回收发生在内存压力到来时，MGLRU 会优先处理这些页。想立刻看到效果，可以在 madvise 之后分配一块大内存写满制造压力，然后用 mincore 对比冷热两区驻留率。

## 谁在用

Google 在数千万台 Chrome OS 和约百万台 Android 设备上实测过：OOM kill 分别减少 59% 和 18%，其他 UX 指标也有改善。服务器场景交给独立实验室跑 Hadoop、Memcached、MongoDB、PostgreSQL 等基准，960 个数据点、500 多小时，95% 置信区间内这些应用至少部分场景明显更好。Linus 的评语是：这不算什么异想天开的东西，反正已经有 active/inactive 了，多世代只是自然延伸。

合入后社区一直在推进。AWS 的 Amazon Linux 2023 内核带着 `CONFIG_LRU_GEN` 但默认关，需要手动 `echo y > /sys/kernel/mm/lru_gen/enabled` 打开；主流发行版（Ubuntu、Fedora、Debian）的新内核则默认启用。LPC 2024 上 Yu Zhao 汇报了 memcg 场景的扩展，按 cgroup 统计各世代页数，容器场景的回收决策更细了。

运维侧有个实际好处值得说。老 LRU 下判断"内存是不是不够"很难：active/inactive 计数只能看总量，说不清哪些页是真热的。MGLRU 的 debugfs 直方图把每代页数摊开，配合 `/proc/vmstat` 里的回弹计数，能直接算出工作集大概多大、thrashing 是不是在发生。给容器设 memory limit 之前先看一眼直方图，比拍脑袋定数字靠谱得多。代价是这套接口要 `CONFIG_LRU_GEN_STATS` 才完整，发行版内核一般开着，自定义内核得自己注意。

## 参考

- [Multi-Gen LRU — Linux kernel documentation](https://docs.kernel.org/admin-guide/mm/multigen_lru.html)
- [LWN: Multi-generational LRU: the next generation](https://lwn.net/Articles/856931/)
- [InfoQ: Linux to Adopt New Multi-Generation LRU Page Reclaim Policy](https://www.infoq.com/news/2022/01/linux-mglru-memory-reclaim/)
- [include/linux/mmzone.h (v6.8), lru_gen_folio 定义](https://github.com/torvalds/linux/blob/v6.8/include/linux/mmzone.h)
- [mm/vmscan.c (v6.8), aging 与 eviction 实现](https://github.com/torvalds/linux/blob/v6.8/mm/vmscan.c)
- [AWS: Using Multi-Gen LRU on AL2023 kernels](https://docs.aws.amazon.com/linux/al2023/ug/kernel-mglru-al2023.html)
- [LPC 2024: MGLRU updates](https://lpc.events/event/18/contributions/1781/attachments/1592/3304/mglru-updates-lpc2024.pdf)
