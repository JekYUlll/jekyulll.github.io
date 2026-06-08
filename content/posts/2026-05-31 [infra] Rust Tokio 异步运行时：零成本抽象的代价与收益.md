+++
date = '2026-05-31T10:05:51+08:00'
draft = false
title = 'Rust Tokio 异步运行时：零成本抽象的代价与收益'
author = 'JekYUlll'
lastmod = '2026-06-08T21:23:42+08:00'
tags = ['rust', 'tokio', 'async-runtime', 'work-stealing']
categories = ['infra']
+++

## 背景

Rust 的 async/await 有个反直觉的点：语言本身不带运行时。`async fn` 只定义一个可暂停的计算，真正驱动它、在 I/O 就绪时唤醒它的，是运行时。

这不是漏做了。Rust 把 async runtime 留给第三方，是因为服务端、嵌入式、CLI 的需求差很多：吞吐、内存分配、调度策略都不一样。Tokio 是服务端最常见的选择，靠事件驱动和多线程工作窃取调度吃饭。

这篇只拆三个部分：调度器、I/O 驱动、定时器。重点回答两个问题：Tokio 调度的到底是什么？“零成本抽象”的成本又藏在哪里？

## Future：async 的“暂停”到底是什么意思

`async fn` 不是魔法。编译器把它编译成一个状态机，一个实现 `Future` trait 的结构体：

```rust
pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

每次调用 `poll()`，要么返回 `Ready(value)`，表示计算完成；要么返回 `Pending`，表示还没准备好。`cx` 里带着一个 `Waker`。I/O 就绪或定时器到点后，系统通过它通知调度器：“这个 Future 可以重新 poll 了”。

一个 `async fn` 里的每个 `.await` 点，都会变成状态机的一个状态。比如：

```rust
async fn read_and_parse() -> Result<Data> {
    let raw = fetch_data().await?;   // 状态 0 → 状态 1
    let parsed = parse(raw).await?;  // 状态 1 → 状态 2
    Ok(parsed)
}
```

编译器会把它变成类似这样的枚举：

```rust
enum ReadAndParseState {
    Start,
    AwaitingFetch { fut: FetchFuture },
    AwaitingParse { data: String, fut: ParseFuture },
    Done,
}
```

这里有个常见坑：任何跨越 `.await` 的局部变量都会被存进状态机结构体。如果你放了一个 1MB 的栈数组，然后 `.await` 一下，这个 Future 的体积就是 1MB 加其他字段。零成本不是零体积。

## Tokio 调度器：工作的线程才是好线程

多线程调度器（`#[tokio::main]` 默认）是 Tokio 最复杂的部分。它启动 N 个 worker 线程（默认等于 CPU 核数），每个 worker 有自己的任务队列，再通过工作窃取把负载摊平。

### 三层队列

每个 worker 有三类任务来源：

1. LIFO Slot：单任务槽，保存最近一次从本 worker 唤醒的任务。它利用 CPU cache locality：刚唤醒的任务很可能刚由本线程生成，数据还在 L1 缓存里。但这个槽最多连续用 3 次，之后必须切到本地队列，避免饿死其他任务。

2. 本地队列：256 个槽位的固定大小环形缓冲区，FIFO 顺序。只被当前 worker 读写，无锁操作。

3. 全局注入队列：线程安全的共享队列。线程通过 `tokio::spawn()` 提交任务时，任务先进这里，然后 worker 定期批量拉取。

### 工作窃取算法

当一个 worker 的本地队列空了，它不会原地等，而是去别的 worker 那里偷任务。

```
if local_queue.pop() is Some(task):
    poll(task)
else if inject_queue.pop_batch() is Some(tasks):
    for t in tasks: local_queue.push(t)
    poll(first_task)
else:
    victim = random_worker()
    steal_half(victim.local_queue)  // 偷一半
    poll(first_stolen)
    park() if nothing found
```

Tokio 的 `steal_half` 很直接：从 victim 本地队列尾部拿走一半任务。这样偷的人有活干，被偷的人也不会立刻空掉。victim 随机选择，避免所有 worker 同时抢同一个队列。

### block_on 和驱动关系

如果你用 `#[tokio::main]`，底层大致展开成：

```rust
fn main() {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async { /* 你的代码 */ })
}
```

`block_on` 创建 Runtime，用当前线程驱动 `async main`，同时启动 worker 线程池。I/O 驱动轮询 epoll/kqueue，把就绪事件通过 Waker 送回调度器。

## I/O 驱动：不忙等

从 socket 读数据时，read 不会阻塞 worker 线程。流程是：

1. 调用 `TcpStream::read(&mut buf)`。
2. 内核返回 `EAGAIN/WouldBlock` → 还没数据。
3. Tokio 通过 mio（一个跨平台 I/O 轮询库）向 epoll 注册这个 socket 和当前任务的 Waker。
4. Future 返回 `Poll::Pending`，调度器切去跑别的任务。
5. 数据到达 → epoll 触发 → I/O 驱动线程拿到事件 → 调用 Waker::wake() → 任务重新进入调度队列。
6. 下次 poll，数据已经在缓冲区里了。

这就是 Reactor 模式。Tokio 的 I/O 驱动（reactor）用 `epoll_wait` 收事件，每次最多处理 1024 个事件。超时时间设为 61ms，和定时器驱动对齐。

## 定时器：分层时间轮

Tokio 用 6 层分级哈希时间轮实现定时器，设计借鉴 Linux 内核的 timer wheel。

每层 64 个槽，粒度逐层放大：

| 层 | 槽范围 | 覆盖时长 |
|----|--------|---------|
| 0 | 1ms × 64 = 64ms | 64ms |
| 1 | 64ms × 64 = 4.096s | ~4s |
| 2 | 4s × 64 = 256s | ~4min |
| 3 | 4min × 64 = 4.5hr | ~4.5hr |
| 4 | 4.5hr × 64 = 12天 | ~12天 |
| 5 | 12天 × 64 = 2.1年 | ~2年 |

插入和取消定时器都是 O(1)。当底层走完一圈，就从上一层卸下一批定时器，重新哈希到下一层，摊销仍是 O(1)。

和二叉堆（`std::collections::BinaryHeap`，O(log n) 插入/删除）相比，时间轮在大规模定时器下更占便宜。一个处理 10 万 HTTP 长连接的代理，每个连接挂一个 60 秒超时定时器，二叉堆的 O(log 100000) 操作不会便宜。

## 写一个小型 Tokio 服务

### TCP Echo Server

最经典的 Tokio 程序，但每行都对应一个运行时动作：

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:8080").await?;

    loop {
        let (mut socket, _) = listener.accept().await?;
        tokio::spawn(async move {
            let mut buf = [0; 1024];
            loop {
                match socket.read(&mut buf).await {
                    Ok(0) => return,          // 连接关闭
                    Ok(n) => {
                        if let Err(e) = socket.write_all(&buf[0..n]).await {
                            eprintln!("write error: {e}");
                            return;
                        }
                    }
                    Err(e) => {
                        eprintln!("read error: {e}");
                        return;
                    }
                }
            }
        });
    }
}
```

`tokio::spawn` 不是开线程，而是把任务提交给调度器。每个新连接得到一个异步任务，不是一个 OS 线程。这就是 Tokio 能撑住大量并发连接的原因。

### 不要阻塞调度器

新手最容易犯的错，是在 async 函数里调用同步阻塞 API：

```rust
// ❌ 错误示范
async fn get_user(id: i64) -> Result<User, DbError> {
    let conn = postgres::Client::connect("host=localhost dbname=myapp", NoTls)?;
    let row = conn.query_one("SELECT * FROM users WHERE id = $1", &[&id])?;
    Ok(User::from_row(row))
}
```

这段代码会在 worker 线程上阻塞数毫秒。阻塞期间，这个 worker 不能处理其他任务，调度器少了一个工人。同步数据库驱动、文件 I/O、CPU 密集计算都类似。该卸载就用 `spawn_blocking`：

```rust
async fn get_user(pool: &PgPool, id: i64) -> Result<User, DbError> {
    tokio::task::spawn_blocking(move || {
        // 这个闭包在专用阻塞线程池上执行
        let mut conn = pool.get()?;
        let row = conn.query_one("SELECT * FROM users WHERE id = $1", &[&id])?;
        Ok(User::from_row(row))
    })
    .await?
}
```

`spawn_blocking` 把 CPU/阻塞密集操作放到独立的阻塞线程池，避免卡住事件循环。

### 超时与结构化并发

```rust
use tokio::time::{timeout, Duration};
use tokio::task::JoinSet;

async fn fetch_all(urls: Vec<String>) -> Vec<Result<String, String>> {
    let mut set = JoinSet::new();

    for url in urls {
        set.spawn(async move {
            let resp = timeout(Duration::from_secs(5), reqwest::get(&url))
                .await
                .map_err(|_| "timeout".to_string())?
                .text()
                .await
                .map_err(|e| e.to_string())?;
            Ok(resp)
        });
    }

    let mut results = vec![];
    while let Some(res) = set.join_next().await {
        results.push(res.unwrap_or(Err("task panic".into())));
    }
    results
}
```

`JoinSet` 是结构化并发的基本工具。所有子任务的生命周期都被限制在 `fetch_all` 函数内，不会静默泄漏。每个请求也有 5 秒超时保护。

## 运行时选择

Rust 里有几个 async runtime 可选：

| Runtime | 调度策略 | 适用场景 | 特性 |
|---------|---------|---------|------|
| Tokio | 多线程工作窃取 | 服务端、网络应用、数据库驱动 | 最成熟的生态，全功能 |
| smol | 单线程 + 全局队列 | CLI 工具、轻量场景 | 编译快（10s vs Tokio 60s），代码量小 |
| embassy | 协作式，无堆分配 | 嵌入式、STM32/RP2040 | 支持中断驱动，零堆运行时 |
| async-std | 多线程 + 全局队列 | 通用（小生态） | 模仿 std API，但生态萎缩 |
| glommio | 单线程 io_uring 直驱 | 存储系统、NVMe | 对 io_uring 最深的集成 |

现实里，Tokio 仍是默认选项。reqwest、axum、tonic、sqlx、hyper 这些网络库都依赖它。smol 适合简单异步 CLI。embassy 则是嵌入式里的常见选择。

## 今天就能做的事

1. 用 tokio-console 看调度器内部：在项目中加 `console-subscriber`，运行时用 `RUSTFLAGS="--cfg tokio_unstable"` 编译，启动 `tokio-console` 观察任务调度延迟和 worker 利用率。

2. 检查代码里有没有阻塞 worker 线程：搜 `.await` 附近的同步 I/O 调用（`std::fs`、同步 `std::net`、同步数据库驱动），换成 `spawn_blocking` 或异步替代品。

3. 对比二叉堆和时间轮：写一个小 benchmark，分别用 `BinaryHeap` 和模拟时间轮管理 10 万个定时器，比较插入和触发 1000 次的开销。

## 参考

- Tokio 官方文档：Runtime 模块：https://docs.rs/tokio/latest/tokio/runtime/index.html
- Tokio 团队博客：新定时器实现（2018）：https://tokio.rs/blog/2018-03-timers
- Tokio 源码 Worker 实现：https://github.com/tokio-rs/tokio/blob/master/tokio/src/runtime/scheduler/multi_thread/worker.rs
- Microsoft Rust Training：Tokio Deep Dive：https://microsoft.github.io/RustTraining/async-book/ch08-tokio-deep-dive.html
- Lucio Duran：Tokio Runtime Design：https://lucioduran.com/blog/async-rust-tokio-internals-runtime-design
- Rust Async 专题 Deep Dive：https://www.youngju.dev/blog/culture/2026-04-15-rust-tokio-async-runtime-future-waker-work-stealing-deep-dive-guide-2025.en
