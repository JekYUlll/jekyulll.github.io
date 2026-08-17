+++
date = '2026-08-17T10:05:28+08:00'
draft = false
title = 'Green Tea GC：Go 1.26 为什么改成按页扫描小对象'
author = 'JekYUlll'
lastmod = '2026-08-17T10:05:28+08:00'
tags = ['golang', 'garbage-collection', 'runtime']
categories = ['backend']
+++

Go 的垃圾回收器慢下来时，问题常常不在停顿，而在 CPU 等内存。经典并发标记沿着指针图逐个对象追踪，刚读完堆的一头，下一次访问可能已经跳到另一头。缓存帮不上忙，预取器也猜不出方向。

Green Tea GC 改的是标记阶段的工作顺序。Go 1.25 把它放在 `GOEXPERIMENT=greenteagc` 后面试运行，Go 1.26 已默认启用。三色标记、写屏障和并发回收并没有被推翻，变化集中在一件事上：先把同一片内存里的待扫描对象攒起来，再一起扫。

## 旧扫描器为什么总在等内存

Go 团队给出的剖析数据很直接：垃圾回收成本约九成花在标记阶段，其中通常至少 35% 的时间卡在堆内存访问。对象队列按指针图推进，队列里的相邻任务不保证位于相邻地址。对 CPU 来说，这是一串短小、相互依赖、难以预测的读取。

旧算法的工作队列近似 LIFO。每个 GC worker 有本地对象栈，为了维持并行度，还得频繁碰全局工作列表。核心数增加后，内存延迟和队列争用会一起冒出来。继续微调单对象扫描循环，收益已经不太够看了。

## Green Tea 把对象队列换成 span 队列

官方博客为了讲清思路，称工作单位为 8 KiB 的 page。翻 Go 1.26.5 源码会看到更准确的名字是 `span`：堆以 8 KiB page 为分配粒度，一个 span 可以包含一个或多个 page，同一 span 内放同一 size class 的对象。

Green Tea 为适用的 span 保存两组位图。`marks` 表示对象已经被发现，`scans` 表示对象已经扫描。扫描指针遇到新对象时，运行时先设置它的 mark bit；如果对应 span 尚未排队，就把 span 放进 FIFO 队列。worker 取出 span 后计算两组位图的差，只扫描“已发现但未扫描”的对象。

```text
发现对象指针
    |
    v
设置对象的 mark bit ---- span 已在队列中？ ---- 是 ----> 继续积累
    |                         |
    |                         否
    v                         v
稍后按 span 扫描 <------ 放入 FIFO span 队列
    |
    v
扫描 marks - scans 对应的对象，并更新 scans
```

FIFO 在这里不是随手选的。span 等得稍久一点，同一 span 内可能又有几个对象被标记；轮到它时，一次就能处理更多连续内存。Go 团队试过 LIFO、按密度排序、随机和地址排序，FIFO 积累出的平均扫描密度最好。

当前 64 位 Go 1.26.5 中，`gcUsesSpanInlineMarkBits` 把这条路径用于 16 到 512 字节的 size class。大对象仍走原来的对象扫描路径。小对象单次工作太少，队列和元数据开销很难摊薄，恰好也是批处理最有用的区域。

源码里的 `spanInlineMarkBits` 把 `scans`、`marks`、队列所有权和 size class 信息放在 span 尾部。扫描器只凭对象地址做对齐和偏移计算，就能找到这块元数据，不必先解引用 `mspan` 再追一层指针。heap arena 里另有一张按 8 KiB page 编号的位图，用来快速判断目标地址是否适用 Green Tea。这个快路径很抠门，正因为它位于每个指针都会经过的热循环。

span 队列也没有退回一把全局大锁。实现借了调度器 work-stealing 的思路，每个 worker 优先消费本地工作，空闲时再从别处偷。span 比对象少得多，跨 worker 搬运的队列项随之减少。Green Tea 改的是内存访问顺序，但它顺手处理了多核下的队列争用，这也是官方测试里核心数越多、收益往往越明显的原因。

## 只有一个对象时不能硬凑批次

按 span 排队也有反例。对象图缺少局部性时，一个 span 出队后可能只有一个对象可扫，此时合并位图、查找差集反而比旧路径更贵。

Go 1.26 的实现用 `spanScanOneMark` 和 `spanScanManyMark` 区分这种情况。若排队期间始终只有一个新 mark，扫描器直接处理那个代表对象，跳过整套位图合并。这个分支很实在：Green Tea 可以利用应用已有的局部性，但不能凭空造出局部性。

按 span 组织元数据还有一个额外收益。固定布局让运行时能按 size class 生成扫描逻辑，并在较新的 amd64 CPU 上使用向量指令。Go 1.26 发布说明预计，Intel Ice Lake、AMD Zen 4 及更新平台上的向量扫描还能再降低约 10% 的 GC 开销。这个数字说的是 GC 开销，不是整套服务吞吐。

## 用两种运行时跑同一份小对象负载

下面的程序故意构造大量 56 字节左右、带三个指针的对象。`nodes` 切片让这些对象在强制 GC 时全部存活，同一 size class 的 span 会快速积累待扫描对象。这是偏向 Green Tea 的合成负载，适合确认开关和采集方法，不适合拿来替生产服务背书。

```go
package main

import (
    "fmt"
    "runtime"
    "time"
)

const n = 250_000

type node struct {
    next, left, right *node
    value             uint64
    pad               [24]byte
}

func makeGraph() []*node {
    nodes := make([]*node, n)
    for i := 0; i < n; i++ {
        nodes[i] = &node{value: uint64(i)}
    }
    for i := 0; i < n; i++ {
        nodes[i].next = nodes[(i+1)%n]
        nodes[i].left = nodes[(i*2+1)%n]
        nodes[i].right = nodes[(i*2+2)%n]
    }
    return nodes
}

func main() {
    start := time.Now()
    var checksum uint64
    for round := 0; round < 8; round++ {
        nodes := makeGraph()
        checksum += nodes[n/3].value
        runtime.GC()
        runtime.KeepAlive(nodes)
    }
    fmt.Printf("elapsed=%s checksum=%d\n", time.Since(start), checksum)
}
```

Go 1.26 默认构建 Green Tea 版本，并保留了 `nogreenteagc` 退路。用同一个 Go 工具链构建两个二进制，能少混入编译器版本差异：

```bash
go build -o gc-green main.go
GOEXPERIMENT=nogreenteagc go build -o gc-old main.go

: > gc-green.trace
: > gc-old.trace
for bin in gc-green gc-old; do
    for run in 1 2 3 4 5; do
        GOMAXPROCS=8 GODEBUG=gctrace=1 ./$bin 2>> "$bin.trace"
    done
done
```

不要盯着一次 `elapsed` 下结论。至少交错运行多轮，同时看 `gctrace` 中 mark 的 CPU 时间，再用 CPU profile 确认服务是否真的受 GC 限制。测试机还要固定 `GOMAXPROCS`，关闭会迁移任务的后台负载，并记录 Go 补丁版本；否则调度抖动很容易盖过几个百分点的差异。若 GC 只占总 CPU 的 5%，即便 GC 自身省下 20%，整机也只有约 1% 的空间。

## 10% 到 40% 不是升级承诺

Go 1.26 发布说明给出的预期是：对重度使用 GC 的真实程序，GC 开销下降 10% 到 40%。官方博客也明确说结果取决于堆布局。高扇出的树、成批分配且一起存活的小对象更容易受益；频繁旋转、把关联节点打散到大堆各处的树就难得多。

Dolt 团队测出来的数字冷静得多。他们在一轮 60 秒 OLTP 测试中测到旧 GC 为 73.20 transactions/s，Green Tea 为 73.36 transactions/s，两边中位延迟都是 13.22 ms。增加并发并跑十分钟后，延迟分布仍几乎重合，Green Tea 的 mark CPU 时间甚至略高。数据库的瓶颈不在这段扫描路径，换 GC 自然救不了它。

我会直接升级到 Go 1.26 的默认实现，但保留一次 A/B 测试。先从 profile 判断 GC 占比，再比较相同负载、相同核心数下的总 CPU 与尾延迟。若 `nogreenteagc` 更快，把 heap profile 和复现条件交给 Go 团队；这个兼容开关按计划会在 Go 1.27 移除。

## 参考

- Go Blog, The Green Tea Garbage Collector: https://go.dev/blog/greenteagc
- Go 1.26 Release Notes, Runtime: https://go.dev/doc/go1.26#runtime
- Go issue #73581, runtime: green tea garbage collector: https://github.com/golang/go/issues/73581
- Go 1.26.5 runtime source, `mgcmark_greenteagc.go`: https://github.com/golang/go/blob/go1.26.5/src/runtime/mgcmark_greenteagc.go
- DoltHub, We tried Go's experimental Green Tea garbage collector and it didn't help performance: https://www.dolthub.com/blog/2025-09-26-greentea-gc-with-dolt/
