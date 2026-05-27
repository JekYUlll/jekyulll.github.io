+++
date = '2026-05-25T23:48:54+08:00'
draft = false
title = '[linux] io_uring 完全指南：从共享环形缓冲区到零系统调用异步 I/O'
author = 'JekYUlll'
lastmod = '2026-05-25T23:48:54+08:00'
tags = ['io-uring', 'async-io']
categories = ['linux']
+++

## 背景

Linux 的异步 I/O 一直是个尴尬的话题。

POSIX AIO（`aio_read`/`aio_write`）在 glibc 中是用户态线程池模拟，并非真正的内核异步。Linux 原生 AIO（`io_submit`/`io_getevents`）虽然在内核中实现，但限制极多——仅支持 `O_DIRECT` 模式，文件系统需要对齐到扇区大小，小文件场景几乎无法使用，且每个提交仍然涉及多次系统调用。select/poll/epoll 解决了网络 I/O 的事件通知问题，但读写操作本身还是同步阻塞的。

2019 年，Jens Axboe（Linux 块层维护者）在 5.1 内核中引入了 **io_uring**，彻底改变了这个局面。它不只是一个新的系统调用，而是一套全新的异步 I/O 架构：通过内核与用户态**共享环形缓冲区**来实现通信，将系统调用开销降到最低，支持缓冲区管理、文件注册、请求链接等高级特性。

截至 2026 年的主流内核，io_uring 已经支持超过 70 种操作码，覆盖文件读写、网络 socket、定时器、futex 等待、NVMe 直通等几乎所有 I/O 场景，成为 Linux 高性能编程的基石。

## 核心原理

### 共享环形缓冲区架构

传统系统调用的代价比许多人想象的要高。单次 `read()` 需要保存/恢复寄存器、切换页表、刷新 TLB、执行 Spectre/Meltdown 缓解代码——在现代内核上，一次空系统调用大约需要 50–150ns，而真正的 I/O 操作还会涉及数据拷贝。io_uring 的设计目标就是在 I/O 密集场景下**完全消除**这些开销。

io_uring 的核心是两个共享环形缓冲区：

```
用户态                                  内核态
┌──────────────────┐                  ┌──────────────────┐
│   SQ (提交队列)    │◄──共享内存──────►│   SQ (消费端)     │
│   ┌─┬─┬─┬─┬─┬─┐  │                  │   ┌─┬─┬─┬─┬─┬─┐  │
│   │S│S│S│ │ │ │  │                  │   │S│S│S│ │ │ │  │
│   │Q│Q│Q│ │ │ │  │                  │   │Q│Q│Q│ │ │ │  │
│   │E│E│E│ │ │ │  │                  │   │E│E│E│ │ │ │  │
│   └─┴─┴─┴─┴─┴─┘  │                  │   └─┴─┴─┴─┴─┴─┘  │
│     tail ▲        │                  │   ▲ head          │
└──────────┼────────┘                  └───┼──────────────┘
           │ 用户写 tail                     │ 内核读 head
           ▼                                ▼
┌──────────────────┐                  ┌──────────────────┐
│   CQ (完成队列)    │◄──共享内存──────►│   CQ (生产端)     │
│   ┌─┬─┬─┬─┬─┬─┐  │                  │   ┌─┬─┬─┬─┬─┬─┐  │
│   │C│C│C│ │ │ │  │                  │   │C│C│C│ │ │ │  │
│   │Q│Q│Q│ │ │ │  │                  │   │Q│Q│Q│ │ │ │  │
│   │E│E│E│ │ │ │  │                  │   │E│E│E│ │ │ │  │
│   └─┴─┴─┴─┴─┴─┘  │                  │   └─┴─┴─┴─┴─┴─┘  │
│     head ▲        │                  │   tail            │
│          │        │                  │                   │
└──────────┼────────┘                  └───────────────────┘
  用户读 head
```

- **SQ（Submission Queue）**：用户把 I/O 请求（SQE）写入 SQ 的 tail，内核从 head 读取。
- **CQ（Completion Queue）**：内核把完成事件（CQE）写入 CQ 的 tail，用户从 head 读取。

通信几乎不依赖系统调用来传输数据本身——只需少量内存屏障保证一致性。

### 三个系统调用

io_uring 的完整生命周期仅需三个系统调用：

| 系统调用 | 用途 | 备注 |
|---------|------|------|
| `io_uring_setup` | 创建 io_uring 实例，初始化 SQ/CQ | 返回 fd，SQ/CQ 通过 mmap 映射到用户空间 |
| `io_uring_enter` | 通知内核处理已提交的 SQE，可选等待完成 | 在 SQ 轮询模式下可以完全不调用 |
| `io_uring_register` | 注册文件描述符、缓冲区等资源 | 减少内核内部查找开销 |

### SQE 与 CQE

**提交队列条目（SQE）**描述了要执行的 I/O 操作：

```c
struct io_uring_sqe {
    __u8    opcode;      /* IORING_OP_READ/WRITE/ACCEPT/... */
    __s32   fd;          /* 目标文件描述符 */
    __u64   off;         /* 文件偏移 */
    __u64   addr;        /* 数据缓冲区指针 */
    __u32   len;         /* 缓冲区大小 */
    __u64   user_data;   /* 用户自定义标识，完成时原样返回 */
    __u8    flags;       /* IOSQE_IO_LINK, IOSQE_BUFFER_SELECT 等 */
    /* ... 还有很多联合体字段，支持不同操作类型 */
};
```

**完成队列事件（CQE）**简洁得多：

```c
struct io_uring_cqe {
    __u64   user_data;   /* 对应 SQE 的 user_data */
    __s32   res;         /* 执行结果：成功返回值 or -errno */
    __u32   flags;       /* IORING_CQE_F_* 标志 */
};
```

因为 I/O 请求可以乱序完成，`user_data` 用于关联提交和完成。

### IORING_SETUP_SQPOLL：零系统调用模式

io_uring 最激进的设计是 **SQ 轮询（SQ Polling）**。开启 `IORING_SETUP_SQPOLL` 后，内核会启动一个内核线程持续轮询 SQ，用户只需往共享缓冲区写入 SQE，内核线程自动取走处理——**完全不需要调用 `io_uring_enter`**。

这意味着在理想情况下，I/O 操作可以做到**零系统调用**。对于 IOPS 敏感的场景（如 NVMe SSD、高速网络），这是量级的性能提升。根据 Jens Axboe 的论文数据，在轮询模式下 io_uring 可达 **1.7M 4K IOPS**，而传统 AIO 仅 **608K IOPS**（快约 2.8 倍）。

### 内核 5.17+ 重要优化

- **`IORING_SETUP_COOP_TASKRUN`**（5.17+）：配合 `IORING_SETUP_SINGLE_ISSUER`（6.0+），减少跨核唤醒和锁竞争，显著降低延迟抖动。
- **`io_uring_cmd`**（5.19+）：允许 NVMe 驱动通过 io_uring 直接提交命令，实现真正意义上的用户态 NVMe 直通。
- **`IORING_SETUP_DEFER_TASKRUN`**（6.0+）：将完成处理推迟到特定时机，进一步减少锁争用。

## 代码实战

在实际项目中使用 io_uring，**永远优先用 liburing**——它封装了所有底层细节，且已被 QEMU、SPDK 等项目验证。

### 示例：用 liburing 实现异步文件读取

以下是一个完整的 C 程序，演示 io_uring 的核心用法：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <liburing.h>

#define QUEUE_DEPTH 4
#define BLOCK_SIZE  4096

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file>\n", argv[0]);
        return 1;
    }

    /* 1. 打开文件 */
    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) {
        perror("open");
        return 1;
    }

    /* 2. 初始化 io_uring（队列深度 4） */
    struct io_uring ring;
    int ret = io_uring_queue_init(QUEUE_DEPTH, &ring, 0);
    if (ret) {
        fprintf(stderr, "io_uring_queue_init: %s\n", strerror(-ret));
        return 1;
    }

    /* 3. 准备缓冲区 */
    char *buf = malloc(BLOCK_SIZE);
    if (!buf) {
        perror("malloc");
        return 1;
    }

    /* 4. 提交读请求 */
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    if (!sqe) {
        fprintf(stderr, "get_sqe failed\n");
        return 1;
    }
    io_uring_prep_read(sqe, fd, buf, BLOCK_SIZE, 0);
    /* 用 buf 指针作为 user_data，方便完成时识别 */
    io_uring_sqe_set_data(sqe, buf);

    /* 5. 提交到内核 */
    ret = io_uring_submit(&ring);
    if (ret < 0) {
        fprintf(stderr, "io_uring_submit: %s\n", strerror(-ret));
        return 1;
    }

    /* 6. 等待完成 */
    struct io_uring_cqe *cqe;
    ret = io_uring_wait_cqe(&ring, &cqe);
    if (ret < 0) {
        fprintf(stderr, "io_uring_wait_cqe: %s\n", strerror(-ret));
        return 1;
    }

    /* 7. 检查结果 */
    if (cqe->res < 0) {
        fprintf(stderr, "IO error: %s\n", strerror(-cqe->res));
    } else {
        printf("Read %d bytes:\n", cqe->res);
        /* user_data 就是我们在提交时设置的 buf */
        char *data = (char *)io_uring_cqe_get_data(cqe);
        write(STDOUT_FILENO, data, cqe->res);
        printf("\n");
    }

    /* 8. 通知内核我们已消费 CQE */
    io_uring_cqe_seen(&ring, cqe);

    /* 清理 */
    io_uring_queue_exit(&ring);
    close(fd);
    free(buf);
    return 0;
}
```

**编译方法：**

```bash
# 安装 liburing（Debian/Ubuntu）
sudo apt install liburing-dev

# 编译
gcc -O2 -o iouring_cat iouring_cat.c -luring

# 运行
./iouring_cat /etc/os-release
```

这个 80 行程序展示了完整的 io_uring 生命周期：初始化 → 获取 SQE → 填充操作 → 提交 → 等待完成 → 消费 CQE → 清理。

### 关键 API 速查

| liburing API | 说明 |
|-------------|------|
| `io_uring_queue_init(depth, ring, flags)` | 初始化 io_uring 实例，flags 可传 IORING_SETUP_SQPOLL |
| `io_uring_get_sqe(ring)` | 从 SQ 中获取下一个空闲 SQE |
| `io_uring_prep_read(sqe, fd, buf, nbytes, offset)` | 填充读操作 |
| `io_uring_prep_write(sqe, fd, buf, nbytes, offset)` | 填充写操作 |
| `io_uring_sqe_set_data(sqe, ptr)` | 设置 user_data 为用户指针 |
| `io_uring_submit(ring)` | 将 SQ 中所有 SQE 提交到内核 |
| `io_uring_wait_cqe(ring, cqe_ptr)` | 等待至少一个 CQE 完成 |
| `io_uring_peek_cqe(ring, cqe_ptr)` | 非阻塞地检查 CQE |
| `io_uring_cqe_seen(ring, cqe)` | 标记 CQE 已消费 |
| `io_uring_queue_exit(ring)` | 销毁 io_uring 实例 |

### SQ 轮询模式

只需一行改动即可开启 SQ 轮询：

```c
/* 将 flags 改为 IORING_SETUP_SQPOLL */
io_uring_queue_init(QUEUE_DEPTH, &ring, IORING_SETUP_SQPOLL);
```

开启后 `io_uring_submit` 不再执行系统调用（内核线程自动轮询 SQ），适用于毫秒级持续提交的场景（如数据库、代理服务器）。

### 固定缓冲区（Fixed Buffers）

对于频繁使用的缓冲区，可以通过 `IORING_REGISTER_BUFFERS` 提前注册，让内核固定其物理页，避免每次 I/O 时对缓冲区进行页锁定和解锁：

```c
struct iovec iov = {
    .iov_base = buf,
    .iov_len  = BLOCK_SIZE,
};

/* 注册缓冲区到 io_uring */
io_uring_register_buffers(&ring, &iov, 1);

/* 在 SQE 中引用固定缓冲区（buf_index 指向注册的缓冲区） */
io_uring_prep_read_fixed(sqe, fd, NULL, BLOCK_SIZE, 0, 0);
```

固定缓冲区可以减少每次 I/O 的页锁定开销，对高 IOPS 场景有显著收益。

## 生态现状

以下项目已在实际生产中使用 io_uring：

| 项目 | 领域 | 使用方式 | 状态 |
|------|------|---------|------|
| **QEMU** | 虚拟化 | 通过 liburing 实现 virtio-blk/virtio-fs 后端 I/O | ✅ 默认启用（7.0+） |
| **RocksDB** | KV 存储 | 通过 `MultiRead` 接口批量提交点查 | ✅ 生产可用 |
| **ScyllaDB** | 数据库 | 替换 Seastar 框架的 AIO 后端 | ✅ 生产中 |
| **SPDK** | 存储 | lib/uring 模块支持 io_uring 作为传输层 | ✅ 可选后端 |
| **Nginx** | Web 服务 | `aio` 模块增加 io_uring 支持（patch） | ⚠️ 需要自定义编译 |
| **Redis** | 缓存 | 社区 fork 支持 io_uring 网络 I/O | ⚠️ 实验性 |
| **FIO** | 基准测试 | 原生支持 io_uring 引擎 | ✅ 默认内置 |

io_uring 的生态仍在快速扩展。2024–2026 年间，越来越多的项目将其作为默认 I/O 后端，网络 io_uring（`IORING_OP_SEND`/`IORING_OP_RECV`）的成熟使得 Web 服务器和代理的采用加速。

## 今日可执行动作

1. **安装 liburing 并运行上面的示例**：`sudo apt install liburing-dev`，然后用 gcc 编译运行，体验零拷贝异步 I/O 的完整流程。

2. **测量你的应用 I/O 延迟**：用 `strace -c` 统计系统调用频率——如果你的应用每秒发起数万次 `read`/`write`，io_uring 可以在减少 90%+ 系统调用的同时提升吞吐。

3. **用 FIO 对比 io_uring 与 AIO 性能**：
   ```bash
   # AIO 模式
   fio --name=aio-test --ioengine=libaio --rw=randread --bs=4k --size=1G --direct=1 --runtime=30
   # io_uring 模式
   fio --name=uring-test --ioengine=io_uring --rw=randread --bs=4k --size=1G --direct=1 --runtime=30
   ```
   在 NVMe SSD 上，io_uring 通常比 libaio 快 1.5–3 倍，差异主要在 IOPS 较高时变得更加明显。

## 参考

- [io_uring(7) - Linux manual page (man7.org)](https://man7.org/linux/man-pages/man7/io_uring.7.html)
- [Lord of the io_uring - What is io_uring? (unixism.net)](https://unixism.net/loti/what_is_io_uring.html)
- [The Low-level io_uring Interface (unixism.net)](https://unixism.net/loti/low_level.html)
- [liburing - Linux kernel async I/O library (Github)](https://github.com/axboe/liburing)
- [Efficient IO with io_uring (kernel.dk)](https://kernel.dk/io_uring.pdf) — Jens Axboe 原始论文
- [Linux kernel io_uring documentation](https://www.kernel.org/doc/html/latest/admin-guide/io_uring.html)
