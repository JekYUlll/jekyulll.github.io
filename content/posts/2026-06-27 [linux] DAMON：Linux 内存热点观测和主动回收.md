+++
date = '2026-06-27T19:09:54+08:00'
draft = false
title = 'DAMON：Linux 内存热点观测和主动回收'
author = 'JekYUlll'
lastmod = '2026-06-27T19:09:54+08:00'
tags = ['damon', 'memory-management', 'reclaim']
categories = ['linux']
+++

DAMON 盯的是内存热度：哪一片还在被碰，哪一片只是占着地方。

普通业务排查内存时，常见手段是 `top`、`smem`、`perf`、`page-types`，再加上一堆猜测。它们能告诉你进程用了多少内存，却很难回答一件更要命的事：这 20 GiB RSS 里，有多少页过去两分钟根本没人碰？

Linux 的传统回收靠 LRU 近似访问历史。这个近似够用，但在大内存、虚拟化、数据库缓存、混部场景里会变钝。DAMON 试图把“访问热度”这个信号拿出来，让内核和用户态都能用。

这件事听起来像性能分析，落到线上其实是资源治理。比如一台宿主机上跑着几十个 guest，某个 guest 的缓存占了很多页，但其中一半已经冷了。host 看不到 guest 内部的语义，guest 自己又没有压力触发回收，内存就这样卡在那里。DAMON 给了一个折中办法：不用等 OOM，也不用粗暴清缓存，先找冷区，再按速度限制慢慢处理。

## 背景

DAMON 是 Linux 的 Data Access MONitoring 子系统，主线内核从 v5.15 开始就有它。到 2026 年它还在活跃开发，项目站点的新闻里能看到 7.1-rc1、LSF/MM/BPF 议题和新的监控属性补丁。

我更关心它的工程价值：它不是给你一张漂亮图就结束。DAMON 的结果可以直接驱动 DAMOS，也就是 DAMON-based Operation Schemes。冷页可以 page out，热页可以优先留在 LRU 上，用户态工具也能拿它做 working set 分析。

这和“我写个脚本定时 drop_caches”差很远。DAMON 的核心是先量，再动手，而且动手速度受 quota 和 watermark 管着。

## 核心原理

DAMON 的基本执行单位是 `kdamond`。一个 `kdamond` 运行一个或多个 context，context 里有监控目标、采样参数和可选的 DAMOS scheme。用户态通过 `/sys/kernel/mm/damon/admin` 配置这些东西。

它分三层。operations set 负责不同地址空间的具体实现，core 负责采样、聚合、区域调整和 scheme 调度，modules 把能力包装成 sysfs、`DAMON_RECLAIM`、`DAMON_LRU_SORT` 这类接口。

目前常见 operations 有三个：`vaddr` 监控某个进程的虚拟地址空间，`fvaddr` 监控固定虚拟地址范围，`paddr` 监控系统物理地址空间。写工具时别混。看某个服务的热点，用 `vaddr`。想做整机冷页回收，通常会碰到 `paddr`。

DAMON 不会傻到逐页高频扫描。它把目标地址空间切成若干 region，region 数量由 `nr_regions/min` 和 `nr_regions/max` 控住。每个采样周期里，它检查 region 对应页表的 Accessed bit，清掉后等一个 `sample_us`，再看它有没有被置回去。

一次采样只回答“有没有访问”。多个采样在 `aggr_us` 里聚合，变成访问频率。`update_us` 到了以后，DAMON 会按访问模式拆分或合并 region。冷的大片内存会被合成大 region，热的边界会变细。这个设计不完美，但比全量逐页扫现实得多。

这里有个容易误解的点：DAMON 不是精确 profiler。它给的是受控误差下的访问模式。你可以把它当成一张会自动调分辨率的热力图，而不是逐页审计日志。参数调得激进，它更敏感，也更吵；参数调得保守，它更稳，但会错过短促热点。

DAMOS 在这个监控结果上加动作。一个 scheme 可以描述“大小在某范围、访问次数低、age 足够老的 region”，再配置 action、quota、watermark 和 filter。`DAMON_RECLAIM` 的默认思路就是找出一段时间没访问的内存，把它 page out。官方文档给的默认 `min_age` 是 120 秒，默认 quota 是每秒最多花 10 ms 或尝试 128 MiB。

这也说明它不是 LRU reclaim 的替代品。传统 reclaim 仍然负责真正的压力路径。DAMON 更适合提前把冷页挪走，减少后面 direct reclaim 把业务线程卡住的机会。

## 代码实战

先别急着写 sysfs。第一步是确认这台机器有没有 DAMON 接口。下面这个脚本只读文件，不需要 root。

```python
from pathlib import Path

root = Path('/sys/kernel/mm/damon/admin')
print('damon_sysfs=', root.exists())

for path in [
    Path('/sys/module/damon_reclaim/parameters/enabled'),
    Path('/sys/module/damon_lru_sort/parameters/enabled'),
]:
    if path.exists():
        print(path, '=>', path.read_text().strip())
    else:
        print(path, '=> missing')
```

如果 `damon_sysfs=False`，先看内核配置。发行版可能把 DAMON 编进去了，也可能只开了一部分。

```bash
grep DAMON /boot/config-$(uname -r)
```

有 sysfs 后，可以用下面的脚本监控一个进程的虚拟地址空间。它会配置一个 `kdamond`，采样间隔 5 ms，聚合间隔 100 ms，1 秒更新一次 region。没有 DAMON 或不是 root 时，它会直接退出，不会乱写系统文件。

```bash
#!/usr/bin/env bash
set -euo pipefail

ADMIN=/sys/kernel/mm/damon/admin
PID="${1:-}"

if [[ -z "$PID" ]]; then
  echo "usage: sudo $0 <pid>" >&2
  exit 2
fi

if [[ ! -d "$ADMIN" ]]; then
  echo "DAMON sysfs not found, check CONFIG_DAMON_SYSFS" >&2
  exit 0
fi

if [[ "$(id -u)" != "0" ]]; then
  echo "run as root, sysfs writes need privilege" >&2
  exit 0
fi

echo 1 > "$ADMIN/kdamonds/nr_kdamonds"
echo 1 > "$ADMIN/kdamonds/0/contexts/nr_contexts"
echo vaddr > "$ADMIN/kdamonds/0/contexts/0/operations"

echo 1 > "$ADMIN/kdamonds/0/contexts/0/targets/nr_targets"
echo "$PID" > "$ADMIN/kdamonds/0/contexts/0/targets/0/pid_target"

echo 5000 > "$ADMIN/kdamonds/0/contexts/0/monitoring_attrs/intervals/sample_us"
echo 100000 > "$ADMIN/kdamonds/0/contexts/0/monitoring_attrs/intervals/aggr_us"
echo 1000000 > "$ADMIN/kdamonds/0/contexts/0/monitoring_attrs/intervals/update_us"

echo 10 > "$ADMIN/kdamonds/0/contexts/0/monitoring_attrs/nr_regions/min"
echo 1000 > "$ADMIN/kdamonds/0/contexts/0/monitoring_attrs/nr_regions/max"

echo on > "$ADMIN/kdamonds/0/state"
echo "DAMON started for pid $PID"
echo "stop with: echo off > $ADMIN/kdamonds/0/state"
```

真要看报告，我建议先用 `damo`，别一上来自己解析 sysfs。官方 getting started 里就是 `sudo damo start <pid>`、`sudo damo report access`、`sudo damo record` 这一套。sysfs 适合写自动化工具，手工观察用 `damo` 更省心。

## 工程取舍

DAMON 的第一组取舍是精度和开销。`sample_us` 越小，热度变化越敏感，访问位检查也越频繁。`nr_regions/max` 越大，边界越细，元数据和扫描成本也会上去。不要在生产上照抄别人的参数。

第二组取舍是地址空间。`vaddr` 对单进程分析最直观，可以跟着 PID 看堆、mmap 区和栈。`paddr` 更接近整机治理，但物理地址到业务对象的解释成本更高。做平台侧内存回收时，`paddr` 有用。做某个服务的排障时，`vaddr` 更像人能读的东西。

第三组取舍是动作要不要自动化。`DAMON_RECLAIM` 适合内存超卖的虚拟化场景，尤其是 guest 提前腾冷页给 host 的场景。官方文档也说它不替代传统 LRU reclaim。我会先把 quota 设小，只在压测环境打开，再看 PSI、major fault、业务尾延迟和 swap I/O。

还有一个坑：DAMON 会用到 Accessed bit。官方设计文档提到，它可能和 Idle page tracking 这类机制互相影响。排查内存时工具越多越容易互相污染，别同时开一堆监控再把结果当真。

项目站点列出了 AWS Aurora Serverless、SK hynix HMSDK 等使用案例。这个信息能说明方向，但不能直接推出你的业务也该开 reclaim。内存管理最怕“看起来聪明”的自动动作。先观察，再限制速度，最后才自动化。

## 今日可执行动作

1. 在自己的开发机上跑 `grep DAMON /boot/config-$(uname -r)`，确认 `CONFIG_DAMON_SYSFS`、`CONFIG_DAMON_RECLAIM`、`CONFIG_DAMON_LRU_SORT` 哪些存在。
2. 安装 `damo`，挑一个会持续分配内存的测试进程，跑一次 `sudo damo report access`，先学会读 `addr`、`size`、`access`、`age`。
3. 如果你维护虚拟化或混部机器，在 staging 上开一个很小 quota 的 `DAMON_RECLAIM` 实验。只看 free memory 没意义，要同时看 PSI、swap I/O、major fault 和 P99 延迟。

## 参考

- Linux Kernel Docs：DAMON 总览：https://docs.kernel.org/admin-guide/mm/damon/index.html
- Linux Kernel Docs：DAMON sysfs 用法：https://docs.kernel.org/admin-guide/mm/damon/usage.html
- Linux Kernel Docs：DAMON 设计文档：https://docs.kernel.org/mm/damon/design.html
- Linux Kernel Docs：DAMON-based Reclamation：https://docs.kernel.org/admin-guide/mm/damon/reclaim.html
- Linux Kernel Docs：DAMON-based LRU-lists Sorting：https://docs.kernel.org/admin-guide/mm/damon/lru_sort.html
- DAMON 项目站点：https://damonitor.github.io/
- DAMO 用户态工具：https://github.com/damonitor/damo
