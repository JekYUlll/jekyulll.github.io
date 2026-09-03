+++
date = '2026-09-03T10:12:30+08:00'
draft = false
title = 'mTHP：匿名内存的大页不再只有 2MB 一档'
author = 'JekYUlll'
lastmod = '2026-09-03T10:12:30+08:00'
tags = ['mthp', 'transparent-huge-pages', 'memory-management', 'kernel']
categories = ['linux']
+++

Linux 的透明大页（THP）过去只有一个尺寸：2MB。缺页一次要清 512 个 4KB 页，分配时又要求 512 个物理页连续，系统跑一阵子碎片化之后 2MB 经常凑不齐，内核只好退回 4KB 整段重来。服务器上 GB 级的堆还受得了这个粒度，手机上一个几十 KB 的匿名分配也要 2MB，就是纯粹的浪费。

mTHP（multi-size THP）把档位拆细了。从内核 6.8 起，匿名缺页可以按 16KB、32KB、直到 2MB 中任意启用的尺寸分配物理连续的大页。这篇用 v6.8 的源码和一台真实跑着 6.8 的机器，讲清楚它怎么选尺寸，以及为什么默认只开 2MB 一档。

## 2MB 一档的老 THP 卡在哪

x86-64 的页表分四层，第二层叫 PMD。经典 THP 的做法是让一个 PMD 表项直接映射 2MB，缺页时内核一次分配 512 个连续页，只建一条映射。收益是 TLB 覆盖变大、缺页次数降到 1/512，代价是 fault 时要清零 2MB 内存，分配失败时内存压缩（compaction）的成本也不低。

问题出在中间地带。分配器找不到 512 个连续页时，整个 2MB 区域直接退回 4KB 页，没有中间档位。系统越跑越碎，2MB 的分配成功率越来越低，大页覆盖率断崖式下跌。

2MB 档有两条供给路径。缺页时直接分配叫 fault 路径，只有全局 `always` 或打了 `MADV_HUGEPAGE` 标记才会走；另一个是内核线程 khugepaged 的后台折叠，它周期性扫描进程地址空间，把一段写满的 4KB 页复制进新分配的大页再换映射，`max_ptes_none`（默认 511）控制区域内允许有多少空页。全局开关是 always 或 madvise 时 khugepaged 自动启动，never 时退出。

## mTHP 的思路：PTE 映射的大 folio

mTHP 不追求必须占一个 PMD 表项。它以 folio 为单位，一次分配 2 的 order 次方个连续页，这些页仍然用普通 PTE 映射，只是物理上连续、内核按一个大 folio 管理。好处有两层：内核侧缺页次数、rmap 和 LRU 操作都按整块处理；硬件侧如果支持 TLB 压缩，还能把一段连续 PTE 折成一条 TLB 项。arm64 的 contiguous PTE 特性就是干这个的，16 个 4KB 项对齐连续时合成一条 TLB，正好对应 64KB。

哪些 order 可用是写死的，`include/linux/huge_mm.h` 里：

```c
/* Orders 2..PMD_ORDER, order-0 and order-1 excluded */
#define THP_ORDERS_ALL_ANON  ((BIT(PMD_ORDER + 1) - 1) & ~(BIT(0) | BIT(1)))
```

order-1（8KB）被显式排除，内核里对 order-1 大页有结构限制。所以 4KB 基础页的 x86-64 上，匿名 mTHP 就是 16KB 到 2048kB 八档，正好对应 sysfs 里的八个目录。

档位变小的直接收益在缺页路径上：分配 64KB 只需要 16 个连续页，比 512 个容易凑得多；fault 时清零的数据量只有 2MB 档的 1/32，延迟尖峰小得多。一次缺页会用 `set_ptes` 一次性填好 16 个 PTE，缺页次数按比例降下来。代价是页表项没有变少，64KB 占 16 个普通 PTE，不像 2MB 那样一个 PMD 项覆盖 512 个页。

## sysfs：每个尺寸一个开关

每个尺寸在 `/sys/kernel/mm/transparent_hugepage/hugepages-<size>kB/enabled` 有自己的开关，取值四个：`always`、`inherit`、`madvise`、`never`。接口是学 hugetlb 的 per-size 目录设计的，inherit 表示跟随顶层 `/sys/kernel/mm/transparent_hugepage/enabled`。

6.8 的默认值在 `mm/huge_memory.c` 的初始化代码里写得很直白：

```c
/*
 * Default to setting PMD-sized THP to inherit the global setting and
 * disable all other sizes. powerpc's PMD_ORDER isn't a compile-time
 * constant so we have to do this here.
 */
huge_anon_orders_inherit = BIT(PMD_ORDER);
```

也就是 2MB 档跟随全局设置，其余全部 never。Ryan Roberts 在 v9 cover letter 里也说了：默认行为和性能保持不变，谁要用小尺寸谁自己开。

## 缺页时怎么挑尺寸

2MB 档在 `__handle_mm_fault` 阶段优先处理：缺页地址正好落在 2MB 边界且 PMD 档允许时，走 `create_huge_pmd()`。剩下的匿名缺页进 `do_anonymous_page()`，调用 v6.8 新加的 `alloc_anon_folio()`，核心逻辑如下（有删节）：

```c
static struct folio *alloc_anon_folio(struct vm_fault *vmf)
{
	/* uffd 激活的 VMA 必须保持按页的 fault 语义，直接退回 4K */
	if (unlikely(userfaultfd_armed(vma)))
		goto fallback;

	/* 拿这个 VMA 允许的所有小于 PMD 的 order */
	orders = thp_vma_allowable_orders(vma, vma->vm_flags, false, true, true,
					  BIT(PMD_ORDER) - 1);
	/* 按 fault 地址对齐和 VMA 边界过滤 */
	orders = thp_vma_suitable_orders(vma, vmf->address, orders);
	if (!orders)
		goto fallback;

	/* 从最大的 order 开始，要求整段 PTE 都是空的 */
	order = highest_order(orders);
	while (orders) {
		addr = ALIGN_DOWN(vmf->address, PAGE_SIZE << order);
		if (pte_range_none(pte + pte_index(addr), 1 << order))
			break;
		order = next_order(&orders, order);
	}
	/* 从大到小尝试分配，失败就降一档 */
	while (orders) {
		folio = vma_alloc_folio(gfp, order, vma, addr, true);
		if (folio) {
			clear_huge_page(&folio->page, vmf->address, 1 << order);
			return folio;
		}
		order = next_order(&orders, order);
	}
fallback:
	return vma_alloc_zeroed_movable_folio(vmf->vma, vmf->address);
}
```

前半段的关键是 `thp_vma_allowable_orders()` 的内联包装，它把 sysfs 的位图和 VMA 标记合成一个 mask：

```c
mask = READ_ONCE(huge_anon_orders_always);
if (vm_flags & VM_HUGEPAGE)              /* 打过 MADV_HUGEPAGE */
	mask |= READ_ONCE(huge_anon_orders_madvise);
if (hugepage_global_always() ||
    ((vm_flags & VM_HUGEPAGE) && hugepage_global_enabled()))
	mask |= READ_ONCE(huge_anon_orders_inherit);
orders &= mask;
```

套到默认配置上结果很干净：全局是 madvise、inherit 只有 2MB 一位，那么没打标记的 VMA mask 为 0，什么都不给；打了 `MADV_HUGEPAGE` 的 VMA 才把 2MB 档加进来。16KB 到 1MB 那些位永远没人 set，所以 6.8 上就算你打了标记，内核也只会尝试 2MB，拿不到 64KB 大页。

## 为什么小尺寸默认 never

能分配 64KB 不代表默认就该给你 64KB。2024 LSFMM 上 Barry Song（OPPO）给的实测数据很说明问题：大 folio 支持已经铺到百万级 Android 设备上，但内存碎片化之后分配成功率掉得飞快，系统运行 1 小时 mTHP 分配成功率约 50%，2 小时后失败率超过 90%。

Yang Shi 在 80 核 Ampere Altra 上的基准讲了另一个故事：Memcached 吞吐提升约 20%、延迟降 10% 到 30%，但只在基础页比较大的平台上成立；4KB 基础页配 64KB mTHP 几乎没有收益，页表维护的开销把好处吃掉了。他当场建议分配时直接试最大的尺寸，失败就退回基础页，中间档位不值得逐级尝试。Jason Gunthorpe 反驳说 hugetlbfs 之所以好用，正是因为应用知道自己要什么尺寸。

Johannes Weiner 在会上说 Meta 在服务器上开过 2MB THP，很快又关掉了。David Hildenbrand 的总结是：THP 一直保持 opt-in 是有原因的，内存内部碎片是真实代价。

所以 6.8 把选择权留给管理员和应用，缺省只保 PMD 档。这套默认值后来也一直没变。

## 之后补上的几块

想看小尺寸大页到底分配出去多少，6.8 的 `/proc/self/smaps` 帮不上忙：`AnonHugePages` 字段只统计 PMD 尺寸，代码里就是 `mss->anonymous_thp += HPAGE_PMD_SIZE`，PTE 映射的 16KB 到 1MB 大页不进这个字段。后来版本在 sysfs 每个尺寸目录下补了 `stats/` 子目录，`anon_fault_alloc`、`anon_fault_fallback` 这些计数器按 order 单列，6.8 上还没有。

khugepaged 更晚。它长期只做 PMD 折叠，Nico Pache（Red Hat）的 khugepaged mTHP collapse 系列 2025 年 7 月发到 v9，经历了将近一年的评审，到 7.2 才合入主线。对照 v7.1 和 v7.2 的 `mm/khugepaged.c` 就能看到差别：v7.2 里出现了 `collapse_possible_orders()`、按 order 缩放的 `max_ptes_none` 这些逻辑。也就是说 6.8 到 7.1 之间，小尺寸大页只能靠缺页时直接分配，khugepaged 只负责把已存在的 4KB 页折成 2MB。

## 本机 6.8 跑一遍

下面的程序开两个 32MB 匿名映射，一个打 `MADV_HUGEPAGE`，一个不打，逐页写一遍后各自从 smaps 看结果：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <stdint.h>

#define REGION_SIZE (32UL << 20)   /* 32 MiB */

static void show_smaps(uintptr_t addr, const char *label)
{
	FILE *f = fopen("/proc/self/smaps", "r");
	char line[512];
	unsigned long start, end;
	int in_range = 0;

	printf("--- %s ---\n", label);
	while (fgets(line, sizeof(line), f)) {
		if (sscanf(line, "%lx-%lx", &start, &end) == 2) {
			in_range = (addr >= start && addr < end);
			if (in_range)
				printf("VMA range: %s", line);
			continue;
		}
		if (!in_range)
			continue;
		if (!strncmp(line, "Rss:", 4) ||
		    !strncmp(line, "AnonHugePages:", 14) ||
		    !strncmp(line, "THPeligible:", 12))
			printf("  %s", line);
	}
	fclose(f);
}

int main(void)
{
	char *a, *b;
	size_t off;

	a = mmap(NULL, REGION_SIZE, PROT_READ | PROT_WRITE,
		 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	b = mmap(NULL, REGION_SIZE, PROT_READ | PROT_WRITE,
		 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (a == MAP_FAILED || b == MAP_FAILED)
		return 1;
	if (madvise(b, REGION_SIZE, MADV_HUGEPAGE) != 0)
		perror("madvise");

	for (off = 0; off < REGION_SIZE; off += 4096) {
		a[off] = 1;
		b[off] = 1;
	}

	show_smaps((uintptr_t)a, "区域 A: 普通匿名映射(无 madvise)");
	show_smaps((uintptr_t)b, "区域 B: madvise(MADV_HUGEPAGE) 之后");
	return 0;
}
```

编译运行后，先看这台机器的开关状态：

```text
$ cat /sys/kernel/mm/transparent_hugepage/enabled
always [madvise] never
$ cat /sys/kernel/mm/transparent_hugepage/hugepages-64kB/enabled
always inherit madvise [never]
$ cat /sys/kernel/mm/transparent_hugepage/hugepages-2048kB/enabled
always [inherit] madvise never
```

全局是 madvise，2048kB 档 inherit（跟随全局），其余尺寸默认 never。程序输出：

```text
--- 区域 A: 普通匿名映射(无 madvise) ---
VMA range: 737b10c00000-737b12c00000 rw-p ...
  Rss:               32768 kB
  AnonHugePages:         0 kB
  THPeligible:           0
--- 区域 B: madvise(MADV_HUGEPAGE) 之后 ---
VMA range: 737b0ec00000-737b10c00000 rw-p ...
  Rss:               32768 kB
  AnonHugePages:     32768 kB
  THPeligible:           1
```

两个区域都真实占着 32MB，差别全在物理页的组织方式上。区域 A 是 8192 个零散的 4KB 页，AnonHugePages 为 0；区域 B 的 32MB 全部落在 16 个 2MB 大页里（`AnonHugePages` 恰好等于 32768 kB），因为 2048kB 档 inherit 了全局的 madvise，打标记后 PMD 路径就放行了。极端地看 TLB 覆盖：区域 A 最坏要 8192 条页表缓存项才能全部覆盖，区域 B 只要 16 条，差了 512 倍。

顺带一提 `THPeligible` 的含义：它问的是这个 VMA 现在有没有任何一种尺寸被允许，不是问它是不是已经用上了大页。区域 B 显示 1，是因为 2MB 档可用。如果哪天你在机器上 `echo always > /sys/kernel/mm/transparent_hugepage/hugepages-64kB/enabled`（要 root），64KB 档会加进 mask：2MB 对齐的缺页仍走 PMD 路径，其余地址才轮到 `alloc_anon_folio` 在 64KB 到 1MB 之间按对齐和空洞情况挑。

## 参考

- kernel docs: Transparent Hugepage Support（mTHP 与 sysfs 说明） https://docs.kernel.org/admin-guide/mm/transhuge.html
- v6.8 源码: mm/memory.c（alloc_anon_folio）、mm/huge_memory.c（hugepage_init_sysfs）、include/linux/huge_mm.h（THP_ORDERS_ALL_ANON、thp_vma_allowable_orders）、fs/proc/task_mmu.c
- LWN: Ryan Roberts 的 mTHP v9 cover letter（2023-12） https://lwn.net/Articles/954094/
- LWN: Two talks on multi-size THP performance（LSFMM 2024 报道） https://lwn.net/Articles/974826/
- LWN: khugepaged mTHP support v9（Nico Pache，2025-07） https://lwn.net/Articles/1029804/
- torvalds/linux master 提交记录: mm/khugepaged.c（khugepaged mTHP collapse 合入 7.2）
