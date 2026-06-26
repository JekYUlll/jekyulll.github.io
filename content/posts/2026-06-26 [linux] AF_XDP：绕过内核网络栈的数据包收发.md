+++
date = '2026-06-26T11:14:50+08:00'
draft = false
title = 'AF_XDP：绕过内核网络栈的数据包收发'
author = 'JekYUlll'
lastmod = '2026-06-26T11:14:50+08:00'
tags = ['af-xdp', 'xdp', 'zero-copy', 'networking']
categories = ['linux']
+++

## 背景

Linux 网络栈很强，但它不是免费的。

一个包从网卡进来，正常路径要进 NAPI、分配或复用 `sk_buff`、跑协议栈、过 netfilter、排 socket 队列，最后才被用户态 `recv()` 读到。对普通服务端，这套东西很好用。对防火墙、负载均衡、包采集、用户态协议栈这种场景，它有点太重了。

DPDK 的做法更激进：直接接管网卡，内核基本退场。性能很好，代价也硬。你要处理驱动、HugeTLB、CPU 独占、网卡绑定，主机上别的流量也容易被你一起拖下水。

AF_XDP 走中间路线。它让 XDP 程序在收包早期做判断，命中的包重定向到用户态共享内存，不命中的包继续走正常内核协议栈。也就是说，它不是把 Linux 网络栈砸掉，而是在需要的时候绕过去。

## 核心原理

AF_XDP 是一个 socket address family。用户态用 `socket(AF_XDP, SOCK_RAW, 0)` 创建 XDP Socket，内核文档里通常叫 XSK。XSK 绑定到某个网卡和某个 RX queue，不能泛泛地收“这块网卡上的所有包”。这个限制很重要，因为高速路径必须贴着队列走。

### XDP 负责分流

AF_XDP 自己不会凭空收到包。前面必须有一个 XDP/eBPF 程序，程序里根据五元组、端口、协议或者队列号做判断。需要交给用户态的包，调用 `bpf_redirect_map()` 丢进 `BPF_MAP_TYPE_XSKMAP`。不需要的包返回 `XDP_PASS`，继续走内核协议栈。

这个设计比全量旁路更舒服。比如只把 UDP 9000 端口的流量交给用户态协议栈，SSH、DNS、监控 agent 还是按原来的内核路径工作。调试时也不至于一脚把整台机器的网络踢飞。

### UMEM 才是重点

AF_XDP 的数据面围着 UMEM 转。UMEM 是用户态分配的一段内存，注册给内核后被切成固定大小的 frame。描述符里放的不是 `char *`，而是 UMEM 内部的 offset 和长度。

四个 ring 管 ownership：

| ring | 方向 | 用途 |
| --- | --- | --- |
| FILL | 用户态到内核 | 用户态交出空 frame，给网卡收包用 |
| RX | 内核到用户态 | 内核告诉用户态哪些 frame 里有新包 |
| TX | 用户态到内核 | 用户态把要发的包排进去 |
| COMPLETION | 内核到用户态 | 内核归还已经发送完成的 frame |

读包路径大概是这样：用户态先把一批空 frame 地址塞进 FILL ring；驱动收到包后把数据放进这些 frame；内核在 RX ring 上发布描述符；用户态消费 RX ring，处理完包以后把 frame 再塞回 FILL ring。这里最容易写错的不是解析包头，而是忘了归还 frame。忘一次，吞吐会慢慢掉。忘多了，收包直接停住。

### copy mode 和 zero-copy

AF_XDP 有 copy mode，也有 zero-copy mode。copy mode 兼容性好，驱动把包复制到 UMEM。zero-copy 要网卡驱动支持，理想情况下 NIC DMA 的目标就是 UMEM frame，少一次拷贝，也少掉 `sk_buff` 这类对象的成本。

别把 zero-copy 当成默认保证。内核会按 bind 参数、驱动能力、队列配置决定能不能走。生产里要看驱动支持表和实际 bind 结果，不能只看代码里写了 `XDP_ZEROCOPY`。

### 它不是普通 socket 的快版本

AF_PACKET、PACKET_MMAP 也能把包交给用户态，但 AF_XDP 的位置更靠前。XDP 程序在驱动收包路径上先跑，命中的包可以不生成 `sk_buff`，也不用走后面的协议栈分发。这个差别就是它能快的原因。

代价也摆在台面上。你要关心 RSS 把流量打到哪个 queue，要保证 XSKMAP 的下标和 queue id 对齐，要管理 UMEM frame 的回收，还要决定哪些包 `XDP_PASS`，哪些包 `XDP_REDIRECT`。这里没有魔法。队列配错，程序看起来运行正常，但 RX ring 一直空；frame 回收写错，开始很快，跑一会儿就像漏水一样掉速。

还有 NUMA。网卡、中断、用户态线程、UMEM 分配如果跨 NUMA 节点，所谓零拷贝会被远端内存访问吃掉一截收益。AF_XDP 能给你一条短路径，但它不会替你把机器拓扑整理好。

## 代码实战

下面这段程序只做一件事：创建一个 RX-only 的 AF_XDP socket，注册 16 MiB UMEM，绑定到指定网卡和队列。它故意使用 `XDP_COPY`，因为这个模式更适合拿来验证环境。没有加载 XDP 程序、没有往 XSKMAP 写 socket fd 时，它不会收到任何包。

保存为 `afxdp_probe.c`：

```c
#define _GNU_SOURCE
#include <errno.h>
#include <linux/if_xdp.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static void die_errno(const char *what) {
    fprintf(stderr, "%s: %s\n", what, strerror(errno));
    exit(1);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <ifname> [queue_id]\n", argv[0]);
        return 2;
    }

    const char *ifname = argv[1];
    unsigned int queue_id = argc > 2 ? (unsigned int)strtoul(argv[2], NULL, 10) : 0;
    unsigned int ifindex = if_nametoindex(ifname);
    if (!ifindex) die_errno("if_nametoindex");

    const size_t frame_size = 4096;
    const size_t frame_count = 4096;
    const size_t umem_size = frame_size * frame_count;

    void *umem = NULL;
    int rc = posix_memalign(&umem, (size_t)sysconf(_SC_PAGESIZE), umem_size);
    if (rc != 0) {
        errno = rc;
        die_errno("posix_memalign");
    }

    int fd = socket(AF_XDP, SOCK_RAW, 0);
    if (fd < 0) die_errno("socket(AF_XDP)");

    struct xdp_umem_reg reg = {
        .addr = (uint64_t)umem,
        .len = umem_size,
        .chunk_size = frame_size,
        .headroom = 0,
        .flags = 0,
    };
    if (setsockopt(fd, SOL_XDP, XDP_UMEM_REG, &reg, sizeof(reg)) < 0)
        die_errno("setsockopt(XDP_UMEM_REG)");

    int ring_size = 2048;
    if (setsockopt(fd, SOL_XDP, XDP_RX_RING, &ring_size, sizeof(ring_size)) < 0)
        die_errno("setsockopt(XDP_RX_RING)");
    if (setsockopt(fd, SOL_XDP, XDP_UMEM_FILL_RING, &ring_size, sizeof(ring_size)) < 0)
        die_errno("setsockopt(XDP_UMEM_FILL_RING)");

    struct sockaddr_xdp sxdp = {
        .sxdp_family = AF_XDP,
        .sxdp_ifindex = ifindex,
        .sxdp_queue_id = queue_id,
        .sxdp_flags = XDP_COPY,
    };
    if (bind(fd, (struct sockaddr *)&sxdp, sizeof(sxdp)) < 0)
        die_errno("bind(AF_XDP)");

    printf("AF_XDP socket bound to %s queue %u in copy mode.\n", ifname, queue_id);
    printf("Attach an XDP program with XSKMAP redirect before expecting packets.\n");

    close(fd);
    free(umem);
    return 0;
}
```

编译：

```bash
gcc -std=c11 -Wall -Wextra -O2 afxdp_probe.c -o afxdp_probe
```

运行时需要网络相关权限，通常直接用 root：

```bash
sudo ./afxdp_probe eth0 0
```

如果 `socket(AF_XDP)` 报 `Address family not supported by protocol`，先查内核配置里有没有 `CONFIG_XDP_SOCKETS=y`。如果 `bind()` 报错，再查网卡名、queue id、驱动 XDP 支持，以及当前是否已经有别的 XDP 程序占着接口。

## 生态现状

AF_XDP 从 Linux 4.18 起可用。后面几个版本补了不少工程拼图：`need_wakeup` 在 5.4 以后更实用，shared UMEM 在 5.10 以后进入常见部署讨论，busy polling 在 5.11 以后被 DPDK AF_XDP PMD 拿来做单核吞吐优化。版本号不是装饰，排查问题时很有用。

用户态接口最好别从裸 `setsockopt()` 开始手写。xdp-project 的 `xdpsock` 示例覆盖 rxdrop、txpush、l2fwd、shared UMEM、busy poll、多 buffer 等路径，很适合拆开看。新项目通常优先看 `libxdp`，因为 AF_XDP helper 已经从旧的 libbpf 用法迁到更适合 XDP 的库里。

DPDK 也有 AF_XDP PMD。它适合已经在 DPDK 体系里的应用：不用把网卡切到 `vfio-pci`，可以通过 Linux netdev 绑定 AF_XDP socket。代价是调参仍然不少，比如 queue 数、busy budget、是否 force copy、是否用 pinned map。它不像普通 UDP socket 那样“打开就完事”。

安全边界也别误会。XDP 程序会过 verifier，AF_XDP socket 仍然受内核权限和网卡队列约束，这比完全接管设备温和。但包解析一旦进了你的进程，越界读写、长度字段信任、批处理里的 use-after-free 都是普通用户态 bug。高速路径不会自动变安全。

我的判断很简单：如果你只是想写一个高性能 HTTP 服务，AF_XDP 多半太重。先把 `SO_REUSEPORT`、RSS、批量收发、io_uring 网络路径这些常规方案吃干净。如果你要做包处理平面、DDoS 清洗、用户态 L2/L3 转发，或者某个端口的协议栈完全自定义，AF_XDP 才开始划算。

## 今日可执行动作

1. 查内核和配置：`uname -r`，再看 `/boot/config-$(uname -r)` 里有没有 `CONFIG_XDP_SOCKETS=y`。发行版内核通常已经打开，但别猜。
2. 查网卡队列：`ethtool -l <iface>` 和 `ethtool -L <iface> combined 1`。先用单队列把变量降下来，跑通以后再扩多队列。
3. 跑 xdp-project 的 `xdpsock -r`。先做 rxdrop，不要一上来写 L2 forward。rxdrop 能稳定收包以后，再加 TX、shared UMEM 和 busy poll。

## 参考

- Linux Kernel Documentation: AF_XDP, https://docs.kernel.org/networking/af_xdp.html
- eBPF Docs: AF_XDP, https://docs.ebpf.io/linux/concepts/af_xdp/
- xdp-project bpf-examples: AF_XDP-example, https://github.com/xdp-project/bpf-examples/tree/master/AF_XDP-example
- xdp-project xdp-tools: libxdp, https://github.com/xdp-project/xdp-tools/tree/master/lib/libxdp
- DPDK documentation: AF_XDP Poll Mode Driver, https://doc.dpdk.org/guides/nics/af_xdp.html
