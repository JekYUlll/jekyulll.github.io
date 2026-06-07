+++
date = '2026-06-07T10:15:28+08:00'
draft = false
title = 'RDMA 与 RoCE：远程直接内存访问实战'
author = 'JekYUlll'
lastmod = '2026-06-07T10:15:28+08:00'
tags = ['rdma', 'roce', 'infiniband', 'kernel-bypass']
categories = ['linux']
+++

## 背景

想搞明白 RDMA 这东西，起因很简单。有一台 GPU 服务器做分布式训练，NVIDIA 文档里反复出现 InfiniBand 和 RoCE 这两个词，网卡选项里还有 ConnectX。不弄清楚选什么设备，几百万的 GPU 跑着跑着带宽瓶颈就来了。

RDMA（Remote Direct Memory Access）让一台机器直接读写另一台机器的内存，CPU 全程不参与数据搬运。这和传统的 `send()/recv()` 有什么本质区别？**内核旁路**和**零拷贝**。数据从应用 buffer 直接到网卡 wire，不经内核协议栈拷贝一次。在高频交易、AI 集群、分布式存储里，每微秒都算成本。

Linux 的 RDMA 支持由 **rdma-core** 和 **libibverbs** 提供。上层可以用两条路径：底层 `libibverbs` 手动管理 QP/MR/CQ，高层 `librdmacm` 帮你处理连接。往下有三种硬件实现：InfiniBand（专有网络）、RoCE（以太网跑 RDMA）、iWARP（TCP 上跑）。

## 核心原理

### 四种关键对象

RDMA 编程模型建立在四个对象上。每写一行 RDMA 代码，都绕不开它们：

**Queue Pair（QP）**：通信的基本单元。一个 QP 包含一个 Send Queue 和一个 Receive Queue。你把 Work Request 投到队列里，硬件异步处理，完成后在 Completion Queue 里通知你。

**Memory Region（MR）**：RDMA 硬件只能访问已注册的内存。`ibv_reg_mr()` 做了两件事：pin 住物理页防止换出，并把虚拟地址到物理地址的映射表交给网卡。注册后的 MR 有两个 key——`lkey`（本地访问）和 `rkey`（远程访问）。

**Protection Domain（PD）**：安全隔离边界。同一个 PD 内的 QP 和 MR 可以互相访问，跨 PD 不行。类似进程地址空间的概念，但作用在 RDMA 资源上。

**Completion Queue（CQ）**：Work Request 完成后，Completion Queue Entry（CQE）被硬件推到这里。轮询 CQ 是唯一的完成通知方式——没有中断，没有信号，纯 polling。

### QP 状态机

QP 不是创建完就能用的。它有一个严格的状态机：

```
RESET → INIT → RTR（Ready to Receive）→ RTS（Ready to Send）
```

INIT 阶段配置本地属性，RTR 需要交换对端的 QP 信息（qp_num、LID/GID），RTS 完成后才能发数据。两个端点必须通过带外通道（通常是一条 TCP 连接）交换这些参数。这个带外交换是 RDMA 新手最容易卡住的地方。

### InfiniBand vs RoCEv2

| 维度 | InfiniBand | RoCEv2 |
|------|-----------|--------|
| 网络层 | 专有 LRH + GRH | Ethernet + IP + UDP |
| 传输层 | BTH（相同） | BTH（相同） |
| 流控 | 硬件信用机制 | PFC + ECN |
| 路由 | Subnet Manager（SM）分配 LID | 标准 IP 路由 |
| 成本 | 专有交换机 + 线缆 | 标准以太网交换机 |
| 延迟 | ~1μs | ~2-3μs |

BTH（Base Transport Header）在两种协议里完全相同。差异全在低层：IB 用专有硬件做无损网络，RoCEv2 把 RDMA 报文塞进 UDP 封装（端口 4791），依赖 PFC 和 ECN 在以太网上模拟无损。

选型经验：自建集群、预算充足→InfiniBand。云上部署、已有以太网基础设施→RoCEv2。没有 RDMA 网卡还想学→Soft-RoCE（rxE），纯软件模拟，性能打折扣但零成本入门。

## 代码实战

下面是一个完整的 RDMA Write 示例。Server 注册一块内存，Client 直接从远端写进去，Server 不需要 CPU 参与数据接收。

### Server 端

```c
// gcc -o rdma_server rdma_server.c -lrdmacm -libverbs
#include <rdma/rdma_cma.h>
#include <infiniband/verbs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>

struct ConnInfo {
    uint64_t addr;      // 远端 buffer 的虚拟地址
    uint32_t rkey;      // 远端访问密钥
    uint32_t len;       // buffer 长度
} __attribute__((packed));

static void die(const char *msg) { perror(msg); exit(1); }

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <port>\n", argv[0]); return 1; }

    struct rdma_event_channel *ec = rdma_create_event_channel();
    struct rdma_cm_id *listen_id, *conn_id;

    struct sockaddr_in addr = { .sin_family = AF_INET,
                                .sin_port   = htons(atoi(argv[1])) };
    rdma_create_id(ec, &listen_id, NULL, RDMA_PS_TCP);
    rdma_bind_addr(listen_id, (struct sockaddr *)&addr);
    rdma_listen(listen_id, 1);
    printf("[server] listening on port %s\n", argv[1]);

    struct rdma_cm_event *event;
    rdma_get_cm_event(ec, &event);
    conn_id = event->id;
    rdma_ack_cm_event(event);

    // 创建 QP——Reliable Connection
    struct ibv_qp_init_attr qp_attr = {
        .qp_type    = IBV_QPT_RC,
        .cap        = { .max_send_wr = 8, .max_recv_wr = 8,
                        .max_send_sge = 1, .max_recv_sge = 1 },
        .sq_sig_all = 1   // 每个 Send 都产生 CQE
    };
    rdma_create_qp(conn_id, conn_id->pd, &qp_attr);

    // 注册内存区域，允许远端写入
    size_t buf_len = 4096;
    char *buf = aligned_alloc(4096, buf_len);
    memset(buf, 0, buf_len);
    struct ibv_mr *mr = ibv_reg_mr(
        conn_id->pd, buf, buf_len,
        IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE);

    // 把远端需要的地址信息打包，通过 rdma_accept 带过去
    struct ConnInfo info = { (uint64_t)buf, mr->rkey, (uint32_t)buf_len };
    struct rdma_conn_param conn_param = {
        .private_data      = &info,
        .private_data_len  = sizeof(info)
    };
    rdma_accept(conn_id, &conn_param);

    // 等待连接建立完成
    rdma_get_cm_event(ec, &event);
    rdma_ack_cm_event(event);

    sleep(2);   // 给 client 时间做 RDMA Write
    printf("[server] received: '%.*s'\n", 64, buf);

    rdma_disconnect(conn_id);
    ibv_dereg_mr(mr); free(buf);
    rdma_destroy_qp(conn_id);
    rdma_destroy_id(conn_id);
    rdma_destroy_id(listen_id);
    rdma_destroy_event_channel(ec);
    return 0;
}
```

### Client 端

```c
// gcc -o rdma_client rdma_client.c -lrdmacm -libverbs
#include <rdma/rdma_cma.h>
#include <infiniband/verbs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
#include <netdb.h>

struct ConnInfo { uint64_t addr; uint32_t rkey, len; } __attribute__((packed));
static void die(const char *msg) { perror(msg); exit(1); }

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <ip> <port>\n", argv[0]); return 1; }

    struct rdma_event_channel *ec = rdma_create_event_channel();
    struct rdma_cm_id *id;
    rdma_create_id(ec, &id, NULL, RDMA_PS_TCP);

    struct addrinfo *res;
    getaddrinfo(argv[1], argv[2], NULL, &res);
    rdma_resolve_addr(id, NULL, res->ai_addr, 2000);

    struct rdma_cm_event *event;
    rdma_get_cm_event(ec, &event);
    rdma_ack_cm_event(event);

    rdma_resolve_route(id, 2000);
    rdma_get_cm_event(ec, &event);
    rdma_ack_cm_event(event);

    // 创建 QP 并连接。连接成功后 event 的 private_data 里有 server 的地址信息
    struct ibv_qp_init_attr qp_attr = {
        .qp_type = IBV_QPT_RC,
        .cap = { .max_send_wr = 8, .max_recv_wr = 8,
                 .max_send_sge = 1, .max_recv_sge = 1 },
        .sq_sig_all = 1
    };
    rdma_create_qp(id, id->pd, &qp_attr);

    struct rdma_conn_param param = {};
    rdma_connect(id, &param);

    rdma_get_cm_event(ec, &event);
    struct ConnInfo *remote = (struct ConnInfo *)event->param.conn.private_data;
    printf("[client] remote addr=0x%lx, rkey=0x%x, len=%u\n",
           remote->addr, remote->rkey, remote->len);
    rdma_ack_cm_event(event);

    // 准备要写入的数据
    char *send_buf = aligned_alloc(4096, 4096);
    strcpy(send_buf, "Hello RDMA from client! Data lands directly in server memory.");
    struct ibv_mr *mr = ibv_reg_mr(id->pd, send_buf, 4096, IBV_ACCESS_LOCAL_WRITE);

    // 构造 RDMA Write WR——这是单边操作，server 不需要 post recv
    struct ibv_sge sge = { .addr = (uint64_t)send_buf, .length = 4096, .lkey = mr->lkey };
    struct ibv_send_wr wr = {
        .wr_id      = 1,
        .opcode     = IBV_WR_RDMA_WRITE,
        .send_flags = IBV_SEND_SIGNALED,
        .sg_list    = &sge,
        .num_sge    = 1,
        .wr.rdma    = { .remote_addr = remote->addr, .rkey = remote->rkey }
    };
    struct ibv_send_wr *bad_wr;
    ibv_post_send(id->qp, &wr, &bad_wr);

    // 轮询 CQ 等待完成
    struct ibv_wc wc;
    while (ibv_poll_cq(id->send_cq, 1, &wc) == 0);
    printf("[client] write done, status=%d\n", wc.status);

    rdma_disconnect(id);
    ibv_dereg_mr(mr); free(send_buf);
    rdma_destroy_qp(id); rdma_destroy_id(id);
    rdma_destroy_event_channel(ec);
    return 0;
}
```

关键流程：Server 注册内存→带外交换地址→Client 构造 RDMA Write→`ibv_post_send()`→轮询 CQ。全程 Server 的 CPU 没有执行任何 `recv()` 调用。

## 生态现状

RDMA 已经不是 HPC 的专属玩具。以下是实际在用 RDMA 的项目：

| 项目 | RDMA 用法 | 传输层 |
|------|----------|--------|
| PyTorch Monarch | 分布式训练参数同步，TorchStore 用 RDMA 做 tensor 跨节点搬运 | RoCEv2 |
| NCCL | GPU 间 AllReduce 通信，默认优先走 InfiniBand/RoCE | IB / RoCEv2 |
| Ceph | OSD 间数据复制，ms_async 后端支持 RDMA | InfiniBand |
| SPDK / NVMe-oF | NVMe over Fabrics，RDMA 做 target-initiator 传输 | RoCEv2 |
| TensorFlow | gRPC + RDMA 插件做分布式训练通信 | IB / RoCE |
| Apache Spark | Shuffle 阶段用 RDMA 加速数据交换 | RoCEv2 |

AI 训练集群是 RDMA 最大的消费场景。一台 8×H100 的节点，GPU 间用 NVLink（900GB/s），节点间靠 InfiniBand NDR400（400Gb/s）。没有 RDMA，千卡集群的通信开销能把 GPU 利用率从 90% 拖到 30%。

## 今日可执行动作

1. **搭 Soft-RoCE 环境**。如果没有 RDMA 网卡，在你的 Linux 机器上装 `rdma-core` 和 `libibverbs-dev`，然后 `sudo modprobe rdma_rxe && sudo rdma link add rxe0 type rxe netdev eth0`。上面两段代码可以直接跑。
2. **跑 RDMA-Primer 示例**。`git clone https://github.com/ManiAm/RDMA-Primer`，从 step1 到 step7 逐层理解，每个程序都是自包含的。
3. **用 ibv_devinfo 查看硬件**。`ibv_devinfo -v` 列出所有 RDMA 设备、端口状态、速率。如果你有 ConnectX 网卡，看看 `link_layer` 是 IB 还是 Ethernet。

## 参考

- [RDMA-Primer: Hands-on RDMA Application Development in C](https://github.com/ManiAm/RDMA-Primer)
- [NVIDIA RDMA Aware Networks Programming User Manual v1.7](https://docs.nvidia.com/networking/display/RDMAAwareProgrammingv17)
- [InfiniBand RDMA and RoCE Explained (NADDOD)](https://www.naddod.com/ai-insights/infiniband-rdma-and-roce-explained-protocols-messages-and-network-architecture)
- [DeepWiki: libibverbs](https://deepwiki.com/linux-rdma/rdma-core/2.1-libibverbs)
- [RDMA Write Example (KylieLeo)](https://kyli-leo.github.io/289D-RDMA-toturial/code_examples/rdma_write/)
- [PyTorch Monarch: RDMA-powered distributed training](https://pytorch.org/blog/introducing-pytorch-monarch/)
