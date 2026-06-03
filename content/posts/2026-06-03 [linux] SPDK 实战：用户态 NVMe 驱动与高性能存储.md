+++
date = '2026-06-03T10:10:56+08:00'
draft = false
title = 'SPDK 实战：用户态 NVMe 驱动与高性能存储'
author = 'JekYUlll'
lastmod = '2026-06-03T10:10:56+08:00'
tags = ['spdk', 'nvme', 'storage', 'user-space']
categories = ['linux']
+++

## 背景

Linux 内核的存储 I/O 栈很重。一次 `read()` 调用要穿过 VFS、文件系统、block layer、IO scheduler，最后才到 NVMe 驱动。中间全是上下文切换和内存拷贝。内核路径下一次 4K 随机读，系统调用本身就要消耗 1-2 微秒，加上中断处理和缓存拷贝，延迟轻松上两位数微秒。

SPDK（Storage Performance Development Kit）是 Intel 在 2015 年开源的方案，把整个存储栈搬到用户态。核心思路就三个：用户态驱动、轮询、无锁。效果是单核能跑到百万 IOPS，延迟压到 10 微秒以内。对比内核路径，同样跑 `fio`，SPDK 的 `perf` 工具能多出 2-3 倍的 IOPS。这不是 20% 的优化，是换个量级。

我用 SPDK 给后端存储做过加速，最直观的感受：kernel bypass 不是优化，是直接换了条路。你不再跟内核讨价还价，自己控制一切。代价也明显：CPU 核心被轮询占满，不能共享。一个跑 SPDK 的核，OS 调度器不能再放别的任务上去。

## 核心原理

### 用户态驱动：抢走设备

Linux 上用户态程序不能直接访问 PCI 设备。SPDK 的做法是先把内核驱动解绑：

```bash
echo 0000:04:00.0 > /sys/bus/pci/drivers/nvme/unbind
echo 0000:04:00.0 > /sys/bus/pci/drivers/vfio-pci/bind
```

设备先被 `uio_pci_generic` 或 `vfio-pci` 接管。这两个是"占位驱动"，防止内核自动重新绑定，但不初始化硬件。区别在于 `vfio` 能配置 IOMMU，把设备的 DMA 访问限制在授权的物理地址范围内。没有 IOMMU 的话，用户态驱动的 DMA 可以写到任何物理内存。这是安全灾难，所以生产环境只用 vfio。

之后 SPDK 用 `mmap` 把 PCI BAR 映射到进程地址空间，直接通过 MMIO 操作 NVMe 寄存器。整个流程完全不进内核。

`/dev/nvme0n1` 消失了。内核的文件系统、block layer 全被绕开。SPDK 自己实现了 bdev（块设备抽象层）和 blobstore（块分配器）来替代它们。

### 轮询 vs 中断

内核驱动靠中断通知 I/O 完成。中断有延迟抖动，高负载下上下文切换开销巨大。

SPDK 不依赖中断。它用 `spdk_nvme_qpair_process_completions()` 持续轮询 Completion Queue。轮询读的是主机内存（不是 MMIO），配合 Intel DDIO 技术，设备写入 CQ 后数据直接留在 LLC 缓存里，一次轮询开销极小——比中断路径少了一个数量级的延迟抖动。

代价是 CPU 不能被共享。一个跑 SPDK 的核心要独占，OS 调度器不能在上面放别的任务。这也是为什么 SPDK 适合专用存储节点，不适合跟业务混跑。

### 队列对模型

NVMe 协议支持最多 64K 个 I/O Submission Queue 和对应的 Completion Queue。每个 SQ/CQ 对是一个独立的通道。

SPDK 把这个特性用到了极致：每个线程独占一个 queue pair。提交 I/O 不需要锁。多线程扩展就是加线程、加 queue pair。NVMe 规范支持最多 65535 个 I/O queue pair，实际设备通常在 32 到 128 之间——对大多数应用绰绰有余。

```c
// 每个线程分配自己的 queue pair
struct spdk_nvme_qpair *qpair = spdk_nvme_ctrlr_alloc_io_qpair(ctrlr, NULL, 0);
// 之后所有 I/O 通过这个 qpair 提交，无需加锁
```

### 大页与 DMA

用户态 DMA 需要物理连续的内存。普通 `malloc` 分配的是虚拟连续、物理可能分散的页面。SPDK 依赖 DPDK 的 hugepage 机制：

```bash
echo 4096 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

所有 I/O 缓冲区通过 `spdk_zmalloc()` 分配，底层是 `mmap` 映射的 2MB 大页。大页三个好处：物理连续所以能直接做 DMA、TLB 命中率高（一个 TLB entry 覆盖 2MB 而不是 4KB）、不会被内核 swap 出去。代价是内存预分配，启动时就要吃掉 8GB（4096 × 2MB），用不完也只能闲置。

## 代码实战

SPDK 的 `examples/nvme/hello_world/hello_world.c` 是最小化的 NVMe 读写示例。完整流程：

```c
// 1. 初始化 SPDK 环境（解析参数、设置 hugepage）
spdk_env_opts_init(&opts);
spdk_env_init(&opts);

// 2. 探测 NVMe 设备，probe_cb 返回 true 才会 attach
spdk_nvme_probe(&trid, NULL, probe_cb, attach_cb, NULL);

// 3. attach_cb 中分配 queue pair
ns_entry->qpair = spdk_nvme_ctrlr_alloc_io_qpair(ctrlr, NULL, 0);

// 4. 分配 DMA 缓冲区（优先用 CMB，fallback 到 host memory）
buf = spdk_zmalloc(0x1000, 0x1000, NULL, SPDK_ENV_SOCKET_ID_ANY, SPDK_MALLOC_DMA);

// 5. 异步写入，传入回调
snprintf(buf, 0x1000, "%s", "Hello world!");
spdk_nvme_ns_cmd_write(ns, qpair, buf, 0 /* LBA */, 1 /* 块数 */,
                       write_complete, &sequence, 0);

// 6. 轮询直到完成
while (!sequence.is_completed)
    spdk_nvme_qpair_process_completions(qpair, 0);

// 7. write_complete 回调中发起 read
//    read_complete 中校验数据，设置 is_completed = 1
```

关键细节：

- `spdk_nvme_ns_cmd_write()` 立刻返回，不阻塞。真正的 I/O 在后台执行。回调函数 `write_complete` 会在 completion 被轮询到时调用——不是在中断上下文中，而是在你主动调用 `spdk_nvme_qpair_process_completions()` 的线程里。
- 轮询函数的第二个参数 `0` 表示处理所有可用的 completion。传正数 N 会限制最多处理 N 个，适合需要穿插其他逻辑的场景。
- 每个 queue pair 只能被一个线程使用。SPDK 不做内部同步——这是用户的责任。多线程场景下弄混了 queue pair，数据损坏是静默的，不会报错。
- CMB（Controller Memory Buffer）是 NVMe 设备上的一块内存，映射过来可以让设备直接往里写，省一次 PCIe DMA 传输。不是所有设备都支持，大部分消费级 SSD 没有。
- `probe_cb` 和 `attach_cb` 的调用时机：`spdk_nvme_probe()` 扫描 PCI 总线，每发现一个 NVMe 控制器就调用 `probe_cb`；`probe_cb` 返回 `true` 才会触发 `attach_cb` 做实际绑定。这给了应用层在 attach 之前做筛选的机会——比如只 attach 特定型号的设备。

整套 write-read 校验逻辑不到 150 行 C 代码。没有 VFS、没有 page cache、没有 block scheduler——I/O 路径短到只剩你的代码和硬件。

## 生态现状

SPDK 不只是 NVMe 驱动库，它是一整套存储栈：

| 组件 | 功能 | 应用场景 |
|------|------|----------|
| NVMe driver | 用户态 NVMe 驱动 | 所有 SPDK 应用的基础 |
| bdev | 块设备抽象层 | 统一 NVMe、malloc、AIO 等后端 |
| blobstore | 块分配器 | RocksDB 的存储引擎后端 |
| NVMe-oF target | 网络 NVMe 设备导出 | 分布式存储、云原生存储 |
| iSCSI target | iSCSI 协议目标端 | 传统存储协议加速 |
| vhost target | QEMU/KVM virtio 后端 | 虚拟机本地存储加速 |
| spdk-csi | Kubernetes CSI 驱动 | 容器存储卷动态供给 |

实际使用 SPDK 的项目：

- RocksDB 通过 blobfs 直接跑在 SPDK 上，绕开内核文件系统
- Ceph 的 BlueStore 后端集成了 SPDK 的 NVMe 驱动
- 阿里云 PolarDB、腾讯云 CBS 等云数据库/块存储产品在存储节点上用了 SPDK
- DAOS（Intel 的分布式存储）底层 I/O 引擎就是 SPDK

SPDK 的进入门槛在运维。要绑定 CPU 核心、配 hugepage、解绑内核驱动，这不是 `apt install` 就能搞定的。但一旦搭建好，性能收益是数量级的。

## 今日可执行动作

1. 跑通 hello_world：找一台有 NVMe SSD 的机器，`git clone https://github.com/spdk/spdk`，按 `README` 编译，运行 `examples/nvme/hello_world/hello_world`。验证能读到 "Hello world!"。
2. 用 perf 工具压测：`build/examples/perf -q 128 -o 4096 -w randread -r 'trtype:PCIe traddr:0000:04:00.0' -t 30`，记录 4K 随机读的 IOPS 和延迟，和内核路径的 `fio` 结果对比。
3. 搭建 NVMe-oF 目标：启动 `build/bin/nvmf_tgt`，通过 JSON-RPC 创建 subsystem 并导出 namespace，从另一台机器用 Linux 内核的 `nvme connect` 挂载。

## 参考

- SPDK 官方文档：https://spdk.io/doc/
- SPDK GitHub：https://github.com/spdk/spdk
- SPDK NVMe Driver 设计：https://spdk.io/doc/nvme.html
- User Space Drivers 原理：https://spdk.io/doc/userspace.html
- Simplyblock SPDK Poll Mode Drivers 解析：https://www.simplyblock.io/glossary/spdk-poll-mode-drivers/
- Intel SPDK + RocksDB 整合：https://www.intel.com/content/www/us/en/developer/articles/technical/storage-performance-development-kit-application-event-framework.html
