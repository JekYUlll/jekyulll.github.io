+++
date = '2026-05-24T14:52:36+08:00'
draft = false
title = 'io_uring 完全指南：从 ring buffer 到零系统调用 I/O'
author = 'JekYUlll'
lastmod = '2026-05-24T14:52:36+08:00'
tags = ['io-uring', 'async-io']
categories = ['linux']
+++

## 背景

Linux 的异步 I/O 一直是个尴尬的话题。

老的 POSIX AIO（`aio_read`/`aio_write`）在用户态用线程池模拟，内核态 AIO（`io_submit`）只支持 O_DIRECT 文件，连 socket 都做不了。epoll 虽然解决了网络 I/O 的 C10K 问题，但对文件 I/O 无能为力——epoll 对普通文件 fd 永远返回 ready，于是 Node.js 的 libuv 只能开线程池来处理文件读写。

2019 年，Jens Axboe（当时在 Facebook）向内核提交了一套全新的异步 I/O 接口，命名为 **io_uring**。它用两个共享 ring buffer 实现了内核-用户态零拷贝通信，支持文件、网络、甚至 `accept()` 等操作，并且提供 **SQPOLL 模式**让应用可以完全免去系统调用。Linux 5.1 合入主线，之后每个版本都在扩充 opcode。

## io_uring 的核心设计：两个 ring buffer

io_uring 的命名来自它的核心数据结构——两个 ring buffer（环形缓冲区）：

- **Submission Queue (SQ)**：用户态写入 I/O 请求（SQE）
- **Completion Queue (CQ)**：内核写入 I/O 完成结果（CQE）

两个队列通过 `mmap()` 映射到用户态和内核态共享的内存区域。这意味着：**大部分情况下，用户态和内核态不需要拷贝数据，也不需要系统调用来传递结构体**。

### 数据结构层面

SQE（Submission Queue Entry）描述一个 I/O 操作：

```c
struct io_uring_sqe {
    __u8    opcode;     /* IORING_OP_READV, IORING_OP_WRITEV ... */
    __u8    flags;      /* IOSQE_IO_LINK 等链式标记 */
    __s32   fd;         /* 操作目标 fd */
    __u64   off;        /* 文件偏移 */
    void    *addr;      /* buffer 或 iovec 数组指针 */
    __u32   len;        /* buffer 大小或 iovec 数量 */
    __u64   user_data;  /* 用户自定义标记，关联 CQE */
    __u16   buf_index;  /* fixed buffer 索引 */
};
```

CQE（Completion Queue Event）返回结果：

```c
struct io_uring_cqe {
    __u64  user_data;   /* 原样返回 SQE 中设置的 user_data */
    __s32  res;         /* 结果码（类似 read/write 返回值） */
    __u32  flags;
};
```

### 工作流程

```
用户态                        内核态
   |                            |
   |— 填充 SQE (写 SQ ring)     |
   |— io_uring_enter() ————————→|— 消费 SQ ring
   |                            |— 执行 I/O 操作
   |— 读取 CQE (读 CQ ring) ←——|— 填充 CQ ring
   |                            |
```

关键优势：**多个 I/O 请求只需要一次系统调用**。传统的 `read()` 一次调用一个，epoll 也是等事件然后逐个调用。io_uring 可以在 SQ 里批量提交 256 个请求，一次 `io_uring_enter()` 全部下发。

### SQPOLL 模式：零系统调用 I/O

如果应用对延迟有极致要求，io_uring 提供 `IORING_SETUP_SQPOLL` 标志。启动后内核创建一个内核线程（sqpoll），持续轮询 SQ ring 是否有新的 SQE。应用只需写 SQ → 内核线程自动消费 → 写 CQ。**应用全程不需要调用 `io_uring_enter()`**。

在 Spectre/Meltdown 修复之后，系统调用的开销显著增加（因为页表隔离和 TLB 刷新）。SQPOLL 模式完全消除了这个成本。

## 实战：用 liburing 写一个零系统调用读文件

原始 io_uring 系统调用接口很底层：需要 `io_uring_setup()` + 3 次 `mmap()` 映射不同区域 + 手动管理 ring buffer 的 head/tail 指针。liburing 封装了这些细节。

### 安装

```bash
git clone https://github.com/axboe/liburing
cd liburing && ./configure && make && sudo make install
```

### 代码：io_uring 版的文件读取器

```c
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <liburing.h>

#define QUEUE_DEPTH 1
#define BLOCK_SZ    4096

off_t get_file_size(int fd) {
    struct stat st;
    if (fstat(fd, &st) < 0) return -1;
    return st.st_size;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file>\n", argv[0]);
        return 1;
    }

    /* 1. 初始化 io_uring —— 一个 ring 搞定 */
    struct io_uring ring;
    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

    int fd = open(argv[1], O_RDONLY);
    off_t file_sz = get_file_size(fd);

    char *buf = malloc(file_sz);

    /* 2. 获取一个 SQE，准备 read 操作 */
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, file_sz, 0);

    /* 3. 提交到内核 */
    io_uring_submit(&ring);

    /* 4. 等待完成，读取 CQE */
    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);

    if (cqe->res < 0) {
        fprintf(stderr, "read failed: %s\n", strerror(-cqe->res));
    } else {
        write(STDOUT_FILENO, buf, cqe->res);
    }

    /* 5. 标记 CQE 已消费 */
    io_uring_cqe_seen(&ring, cqe);

    io_uring_queue_exit(&ring);
    free(buf);
    close(fd);
    return 0;
}
```

对比传统的同步读取，这段代码的关键区别是：`io_uring_submit()` 立即返回，不阻塞，你可以在这期间做别的计算。`io_uring_wait_cqe()` 才真正等待 I/O 完成。

### 编译

```bash
gcc -o iouring-cat iouring-cat.c -luring
```

## 进阶：用 io_uring 写一个完整的 HTTP 服务器

下面是一个基于 io_uring 的简化版 HTTP 服务器框架。它用 io_uring 同时处理 accept、readv 和 writev，**全程只需要 3 个系统调用变体**：`io_uring_queue_init`、`io_uring_submit`、`io_uring_wait_cqe`。

核心事件循环：

```c
#define QUEUE_DEPTH 256

enum { EVENT_ACCEPT, EVENT_READ, EVENT_WRITE };

struct conn {
    int event_type;
    int client_fd;
    struct iovec iov[];
};

void server_loop(int server_fd) {
    struct io_uring ring;
    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

    /* 初始注册一个 accept 请求 */
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_accept(sqe, server_fd, NULL, NULL, 0);
    io_uring_sqe_set_data(sqe, new_req(EVENT_ACCEPT, 0));
    io_uring_submit(&ring);

    while (1) {
        struct io_uring_cqe *cqe;
        io_uring_wait_cqe(&ring, &cqe);
        struct conn *req = (struct conn *)cqe->user_data;

        switch (req->event_type) {
        case EVENT_ACCEPT: {
            int client_fd = cqe->res;  /* accept() 返回的客户端 fd */
            /* 再注册下一个 accept（持续监听）*/
            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_accept(sqe, server_fd, NULL, NULL, 0);
            io_uring_sqe_set_data(sqe, new_req(EVENT_ACCEPT, 0));

            /* 注册读客户端请求 */
            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_readv(sqe, client_fd, /* ... */);
            io_uring_sqe_set_data(sqe, new_req(EVENT_READ, client_fd));
            break;
        }
        case EVENT_READ: {
            /* 解析 HTTP 请求 → 打开文件 → 注册 writev 写回响应 */
            handle_http(req);
            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_writev(sqe, req->client_fd, req->iov, n, 0);
            io_uring_sqe_set_data(sqe, req);
            break;
        }
        case EVENT_WRITE:
            /* 写完关闭连接 */
            close(req->client_fd);
            free(req);
            break;
        }

        io_uring_cqe_seen(&ring, cqe);
        io_uring_submit(&ring);  /* 批量提交所有新注册的请求 */
    }
}
```

这是单线程异步模型的极致形态——**一个 `io_uring_submit` 提交所有类型的 I/O（accept、read、write），一个 `io_uring_wait_cqe` 等待任何完成**。不需要 epoll、不需要线程池、不需要区分网络 I/O 和文件 I/O。

ZeroHTTPd（Shuveb Hussain 的开源项目）基于这个架构做了 benchmark：**在单核 VM 上，io_uring 版本比 epoll + 线程池版本吞吐提升约 30-50%**，延迟降低更明显，因为没有了线程切换和系统调用开销。

## io_uring 生态现状

| 场景 | 项目 | 说明 |
|------|------|------|
| 数据库 | RocksDB | 6.15+ 开始集成 io_uring 做文件 I/O |
| 存储 | SPDK / FIO | SPDK 的 io_uring 引擎，FIO 原生支持 io_uring |
| 网络代理 | Envoy | 社区有 io_uring 集成 PR |
| 编程语言 | Rust (tokio-uring) | tokio-uring 项目将 io_uring 引入 Rust 异步生态 |
| 文件系统 | XFS / Btrfs | 内核原生支持，io_uring 绕过 VFS 的某些路径 |

Rust 的 `tokio-uring` 值得一提——它把 io_uring 封装成 Rust 的 async/await 接口，实现了"真正零开销异步 I/O"：

```rust
use tokio_uring::fs::File;

fn main() -> io::Result<()> {
    tokio_uring::start(async {
        let file = File::open("hello.txt").await?;
        let buf = vec![0u8; 4096];
        let (res, buf) = file.read_at(buf, 0).await;
        println!("read {} bytes", res?);
        Ok(())
    })
}
```

## 今日可执行动作

1. **本地实验**：`sudo apt install -y liburing-dev` 然后编译上面的 io_uring cat 代码。用 `strace -e io_uring_enter,io_uring_setup,io_uring_register ./iouring-cat` 观察实际发生了几次系统调用。对比普通 `cat` 走 `read()` 的次数。

2. **理解性能差异**：运行 `fio --engine=io_uring --rw=randread --bs=4k --size=1G --runtime=10` 对比 `--engine=psync`，观察 IOPS 差距。

3. **读源码**：liburing 的 `src/queue.c` 只有 200 行。看 `io_uring_submit` 如何管理 SQ tail 指针、`io_uring_wait_cqe` 如何决定走 `io_uring_enter` 还是直接从 CQ ring 读取——这是理解"批处理 + 共享内存"设计的最佳入口。

## 参考

- [LWN: Ringing in a new asynchronous I/O API](https://lwn.net/Articles/776703/) — Jonathan Corbet 对 io_uring 的原始报道
- [Lord of the io_uring](https://unixism.net/loti/) — Shuveb Hussain 编写的完整 io_uring 指南，附带 ZeroHTTPd 源码
- [liburing GitHub](https://github.com/axboe/liburing) — Jens Axboe 维护的用户态封装库
- [tokio-uring](https://github.com/tokio-rs/tokio-uring) — Rust 生态的 io_uring 异步运行时
- Linux kernel source: `fs/io_uring.c` — 编译后的 io_uring 内核实现约 10000+ 行
