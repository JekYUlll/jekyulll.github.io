+++
date = '2026-05-25T10:01:05+08:00'
draft = false
title = '[linux] io_uring 实战：Linux 异步 I/O 的终极答案'
author = 'JekYUlll'
lastmod = '2026-05-25T10:01:05+08:00'
tags = ['linux']
categories = ['linux']
+++

## 背景

Linux 的异步 I/O 一直是个尴尬的话题。你可能用过 epoll 处理网络事件，但它对文件 I/O 无能为力；你可能听过 POSIX AIO（aio_read / aio_write），它要么在 glibc 中用线程池模拟，要么走内核的 Linux AIO——但后者只支持 `O_DIRECT` 模式，且在某些场景下仍会阻塞。Windows 的 IOCP 早在 1993 年就提供了完整的异步 I/O 框架，而 Linux 在这方面的缺失，让数据库、存储系统和代理服务器等高性能应用只能靠自建线程池凑合了二十多年。

2019 年，Jens Axboe（Linux 块层维护者）在内核 5.1 合入了 **io_uring**，彻底改变了这个局面。它不是一个修补式的改进，而是重新设计了用户态与内核之间的 I/O 通道——用共享内存中的环形缓冲区替代传统的系统调用路径。七年来，io_uring 从最初的 8 个 opcode 扩展到了超过 40 个操作类型，覆盖了文件 I/O、网络 I/O、计时器、事件通知、甚至 GPU 内存注册。ScyllaDB 通过 io_uring 将延迟降低了 40%，Redis 在异步网络层中集成后吞吐提升了一倍。

本文从工程视角拆解 io_uring 的核心机制，并带你手写一个可运行的高性能文件复制工具。

## 核心原理

### 设计思想：零系统调用 I/O

传统 I/O 的每一次 read/write 都需要陷入内核（syscall），这是一次昂贵的上下文切换，约 50-200ns 的 CPU 开销（加上 Meltdown 修复后的页表切换更贵）。io_uring 的思路是：**创建两个共享内存的环形缓冲区**，用户态直接往缓冲区中写请求、读结果，只有在缓冲区满了或需要等待时才发起一条 `io_uring_enter` 系统调用。

```
┌──────────────────────────────────────┐
│           用户空间                    │
│  ┌─────────────────────────┐         │
│  │  提交队列 (SQ)          │ 写入    │
│  │  [SQE][SQE][SQE][ ]    │ ←──     │
│  └─────────────────────────┘         │
│           ↑ 共享内存 ↓               │
│  ┌─────────────────────────┐         │
│  │  完成队列 (CQ)          │ 读取    │
│  │  [CQE][CQE][ ][ ]       │ ──→     │
│  └─────────────────────────┘         │
└──────────────────────────────────────┘
           │  io_uring_enter()
           ▼
┌──────────────────────────────────────┐
│           内核空间                    │
│  ┌────────────────────────────────┐   │
│  │  SQ 线程 / 中断处理            │   │
│  │  消耗 SQE → 执行操作 →         │   │
│  │  写入 CQE                      │   │
│  └────────────────────────────────┘   │
└──────────────────────────────────────┘
```

### 三个系统调用

与 Linux AIO 需要大量系统调用不同，io_uring 核心只有三个，且 `io_uring_enter()` 可以在一次调用中批量提交多个请求：

| 系统调用 | 用途 | 触发频率 |
|---------|------|---------|
| `io_uring_setup()` | 创建 io_uring 实例，返回 fd、映射 SQ/CQ 内存 | 每个线程一次 |
| `io_uring_enter()` | 通知内核处理已提交的 SQE，同时可等待 CQE | 分批或满时调用 |
| `io_uring_register()` | 注册文件、缓冲区或 ring fd 以优化性能 | 初始化时一次 |

### 核心数据结构

- **SQE（Submission Queue Entry）**：一个 64 字节的请求描述，包含 opcode、fd、offset、flags 和数据指针。用户态在共享内存中预先填好 SQE，内核异步消费。
- **CQE（Completion Queue Entry）**：一个 16 字节的结果描述，包含返回值 `res` 和用户数据指针 `user_data`。用户态异步轮询或等待 CQE 到来。

关键优化在于：**用户态可以不通过系统调用直接修改 SQ 的 tail 指针**来提交请求，内核通过观察 tail 发现新请求。这称为 "syscall-less" I/O。

### 三种工作模式

| 模式 | 机制 | 适用场景 | 延迟 |
|------|------|---------|------|
| **中断驱动**（默认） | 提交后内核硬件中断完成，用户 wait_cqe | 通用场景 | 低 |
| **SQPOLL** | 内核启动一个内核线程持续轮询 SQ | 高 QPS 场景，减少系统调用 | 极低 |
| **IOPOLL** | 针对 `O_DIRECT` 的块设备轮询 | NVMe SSD，极致性能 | 最低 |

### 近年的重要演进

io_uring 自 2019 年上线后从未停止进化，以下是最近几个内核版本的关键特性：

- **Linux 6.12**：新增 `IORING_OP_READ_FIXED` 上下文切换优化、Ring 级联支持
- **Linux 6.13**：NAPI busy polling 原生集成，显著降低网络 I/O 延迟
- **Linux 6.14+**：skb 零拷贝发送、registered ring fds（将 ring fd 预注册以便在关闭 fd 后继续操作）

最新的内核主线还在探索 "Spend-thread" 模型，让 io_uring 直接接管 syscall 的执行路径，理论上可以让系统调用延迟再降一个数量级。

## 代码实战

下面用 liburing（官方用户态封装库）写一个完整的异步文件复制工具。相比 `io_uring-cp` 稍做简化，突出核心 API 流程。

首先安装 liburing：

```bash
git clone https://github.com/axboe/liburing.git
cd liburing
./configure
make -j$(nproc)
sudo make install
```

编译：

```bash
gcc -Wall -O2 -o io_uring-demo io_uring-demo.c -luring
./io_uring-demo input.dat output.dat
```

完整代码：

```c
/*
 * io_uring 异步文件复制演示
 * 编译: gcc -Wall -O2 -o io_uring-demo io_uring-demo.c -luring
 *
 * 核心流程: 
 *   1. io_uring_queue_init → 创建环形缓冲区
 *   2. io_uring_prep_readv → 准备异步读请求 (填写 SQE)
 *   3. io_uring_submit     → 通知内核开始处理
 *   4. io_uring_wait_cqe   → 等待完成事件 (获取 CQE)
 *   5. io_uring_cqe_seen   → 标记 CQE 已消费
 *   6. io_uring_prep_writev → 准备异步写请求
 */

#include <stdio.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <liburing.h>

#define QUEUE_DEPTH 4     /* 环形缓冲区深度 */
#define BLOCK_SIZE  (32 * 1024)  /* 每块 32KB */

/* 每个 I/O 请求的上下文 */
struct io_data {
    struct iovec iov;        /* 数据缓冲区 */
    off_t offset;            /* 文件偏移 */
    int read_done;           /* 1=读取完成, 0=需要读取 */
};

static int infd, outfd;

/* 准备一个读请求，将其压入提交队列 */
static void queue_read(struct io_uring *ring, off_t offset, size_t size)
{
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    if (!sqe) {
        fprintf(stderr, "无法获取 SQE，队列满\n");
        exit(1);
    }

    struct io_data *data = malloc(sizeof(struct io_data) + size);
    data->read_done = 0;
    data->offset = offset;
    data->iov.iov_base = data + 1;   /* 缓冲区紧跟在结构体后面 */
    data->iov.iov_len = size;

    /* 配置 SQE：异步读一个 iovec 块 */
    io_uring_prep_readv(sqe, infd, &data->iov, 1, offset);
    io_uring_sqe_set_data(sqe, data);  /* 设置自定义数据，完成时可找回 */
}

/* 将已读的数据块异步写入输出文件 */
static void queue_write(struct io_uring *ring, struct io_data *data)
{
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    if (!sqe) {
        fprintf(stderr, "无法获取 SQE\n");
        exit(1);
    }

    data->read_done = 1;  /* 标记写入阶段 */

    io_uring_prep_writev(sqe, outfd, &data->iov, 1, data->offset);
    io_uring_sqe_set_data(sqe, data);  /* 保留指针以便后续释放 */
}

int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr, "用法: %s <输入文件> <输出文件>\n", argv[0]);
        return 1;
    }

    /* 1. 打开文件 */
    infd = open(argv[1], O_RDONLY);
    if (infd < 0) { perror("open infile"); return 1; }

    outfd = open(argv[2], O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (outfd < 0) { perror("open outfile"); return 1; }

    struct stat sb;
    if (fstat(infd, &sb) < 0) { perror("fstat"); return 1; }
    off_t file_size = sb.st_size;

    /* 2. 初始化 io_uring：深度为 4，使用默认的中断驱动模式 */
    struct io_uring ring;
    int ret = io_uring_queue_init(QUEUE_DEPTH, &ring, 0);
    if (ret < 0) {
        fprintf(stderr, "io_uring_queue_init: %s\n", strerror(-ret));
        return 1;
    }

    /* 3. 主循环：流水线式读取 + 写入 */
    off_t offset = 0;
    size_t bytes_remaining = file_size;
    int reads_in_flight = 0;
    int writes_in_flight = 0;

    printf("开始复制: %s (%lld bytes)\n", argv[1], (long long)file_size);

    while (bytes_remaining > 0 || reads_in_flight > 0 || writes_in_flight > 0) {
        /* 阶段 1: 队列不满时，持续提交读请求 */
        while (bytes_remaining > 0 && reads_in_flight < QUEUE_DEPTH) {
            size_t chunk = (bytes_remaining > BLOCK_SIZE) ? BLOCK_SIZE : bytes_remaining;
            queue_read(&ring, offset, chunk);
            offset += chunk;
            bytes_remaining -= chunk;
            reads_in_flight++;
        }

        /* 阶段 2: 将读 SQE 提交给内核 */
        if (reads_in_flight > 0) {
            int submitted = io_uring_submit(&ring);
            if (submitted < 0) {
                fprintf(stderr, "io_uring_submit: %s\n", strerror(-submitted));
                break;
            }
        }

        /* 阶段 3: 收割完成事件（可能等待） */
        struct io_uring_cqe *cqe;
        ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) {
            fprintf(stderr, "io_uring_wait_cqe: %s\n", strerror(-ret));
            break;
        }

        struct io_data *data = io_uring_cqe_get_data(cqe);

        if (cqe->res < 0) {
            fprintf(stderr, "I/O 错误: %s\n", strerror(-cqe->res));
            free(data);
        } else if (cqe->res != data->iov.iov_len) {
            /* 部分读写: 调整偏移重新提交 */
            data->iov.iov_base  += cqe->res;
            data->iov.iov_len   -= cqe->res;
            data->offset        += cqe->res;
            reads_in_flight++;
            /* 重新入队到 SQ */
            struct io_uring_sqe *nsqe = io_uring_get_sqe(&ring);
            if (data->read_done)
                io_uring_prep_writev(nsqe, outfd, &data->iov, 1, data->offset);
            else
                io_uring_prep_readv(nsqe, infd, &data->iov, 1, data->offset);
            io_uring_sqe_set_data(nsqe, data);
            io_uring_submit(&ring);
        } else {
            /* 正常完成：读完成则切到写，写完成则释放 */
            if (data->read_done) {
                /* 写操作完成 */
                free(data);
                writes_in_flight--;
            } else {
                /* 读操作完成 → 发起异步写 */
                reads_in_flight--;
                queue_write(&ring, data);
                int wr = io_uring_submit(&ring);
                if (wr < 0) {
                    fprintf(stderr, "io_uring_submit write: %s\n", strerror(-wr));
                    break;
                }
                writes_in_flight++;
            }
        }

        io_uring_cqe_seen(&ring, cqe);  /* 告知内核该 CQE 已消费 */
    }

    /* 4. 清理 */
    close(infd);
    close(outfd);
    io_uring_queue_exit(&ring);

    struct stat out_sb;
    stat(argv[2], &out_sb);
    printf("复制完成: %lld bytes → %s\n",
           (long long)out_sb.st_size, argv[2]);
    return 0;
}
```

这个 demo 展示了 io_uring 编程的四个基本动作：**get_sqe → prep_xxx → submit → wait_cqe**。它使用了一个简单的流水线：读完成立即触发对应的写，深度为 4 的队列让读和写可以重叠执行。你可以尝试增加 `QUEUE_DEPTH` 观察性能变化——IOPS 通常会随深度增加而提高，直到达到硬件的瓶颈。

## 生态现状

io_uring 已经被广泛集成到数据库、中间件和编程框架中：

| 项目 | 用途 | 效果 |
|------|------|------|
| **RocksDB** | 替代原生的 `pread`/`pwrite` | WAL 写入延迟降低 30% |
| **ScyllaDB** | 替换 seastar 框架的 AIO 后端 | 延迟降低 40%，事务吞吐提升 25% |
| **QEMU** | virtio-blk 后端使用 io_uring | 虚机磁盘 I/O 延迟减半 |
| **nginx** | 实验性 module 接入 io_uring | 静态文件吞吐提升 60% |
| **Redis** | io_uring network layer（实验） | 多线程模式下 QPS 翻倍 |
| **Node.js** | libuv 实验性 io_uring 后端 | 文件 I/O 不再阻塞 event loop |
| **LD_PRELOAD 包装** | `iouringctl` 等工具无侵入替换 glibc I/O | 零改代码获得加速 |

值得注意的是：**io_uring 并非万能银弹**。对于小文件随机读（如 OLTP 场景），`O_DIRECT` 配合 io_uring 的提升有限，瓶颈往往在磁盘寻道而非系统调用。io_uring 的真实价值体现在**高 IOPS、大批量提交**的场景——一次 `io_uring_enter` 提交几百个请求，分摊后的上下文切换成本接近零。

## 今日可执行动作

1. **亲手编译运行 demo**：把上面的代码保存为 `io_uring-demo.c`，用 `dd if=/dev/urandom of=input.dat bs=1M count=256` 生成一个 256MB 测试文件，对比普通 `cp` 和 io_uring 版本的耗时差异。

2. **尝试 SQPOLL 模式**：将 `io_uring_queue_init(QUEUE_DEPTH, &ring, 0)` 改为 `io_uring_queue_init(QUEUE_DEPTH, &ring, IORING_SETUP_SQPOLL)`，观察在高 IOPS 场景（如 100M+ 文件批量复制）下的性能差异。

3. **理解内核内的 io_uring 路径**：阅读内核源码 `fs/io_uring.c` 中的 `io_submit_sqes()` 和 `io_issue_sqe()` 函数，或使用 `perf trace -e io_uring:io_uring_submit_req` 跟踪系统调用频率。

## 参考

- [io_uring man page (man7.org)](https://man7.org/linux/man-pages/man7/io_uring.7.html)
- [The rapid growth of io_uring - LWN.net (Jonathan Corbet, 2020)](https://lwn.net/Articles/810414/)
- [An Introduction to the io_uring Asynchronous I/O Framework (unixism.net)](https://unixism.net/loti/)
- [liburing 官方仓库 - GitHub (axboe/liburing)](https://github.com/axboe/liburing)
- [Lord of the io_uring 性能分析](https://scylladb.com/2020/05/05/how-io_uring-and-ebpf-will-revolutionize-programming-in-linux/)
- [Linux io_uring PDF 规范 (Jens Axboe, kernel.dk)](https://kernel.dk/io_uring.pdf)
- [ScyllaDB 对 io_uring 的性能评测](https://www.scylladb.com/2020/05/05/how-io_uring-and-ebpf-will-revolutionize-programming-in-linux/)
