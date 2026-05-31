+++
date = '2026-05-31T10:05:51+08:00'
draft = false
title = 'Rust Tokio 异步运行时：零成本抽象的代价与收益'
author = 'JekYUlll'
lastmod = '2026-05-31T10:05:51+08:00'
tags = ['rust', 'tokio', 'async-runtime', 'work-stealing']
categories = ['infra']
+++

## 背景

Rust 的 async/await 有一个奇怪的地方：语言本身不提供运行时。你写 `async fn` 只是定义了一个可以暂停的计算，谁来驱动它、谁在 I/O 就绪时唤醒它？那是一个运行时的职责。

这不是设计疏漏。Rust 团队刻意把 async runtime 留给第三方，因为不同场景需要不同的调度策略：服务端需要高吞吐、嵌入式需要零堆分配、CLI 工具需要轻量。Tokio 是其中用得最多的那个，主打事件驱动和多线程工作窃取调度。

这篇文章从调度器、I/O 驱动、定时器三个子系统拆 Tokio 的实现。看完你能回答两个问题：Tokio 到底在调度什么？为什么说这个调度是"零成本抽象"？

## Future：async 的"暂停"到底是什么意思

在拆 Tokio 之前，先搞清楚 Rust 的 async 基础。

`async fn` 不是魔法。编译器把它编译成一个状态机，一个实现 `Future` trait 的结构体：

```rust
pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}
```

每次调用 `poll()`，要么返回 `Ready(value)`（计算完成），要么返回 `Pending`（还没准备好，过会儿再来问）。`cx` 参数里藏着一个 `Waker`，当 I/O 就绪或定时器到时，系统通过它通知调度器"这个 Future 可以重新 poll 了"。

一个 `async fn` 里的每个 `.await` 点就是状态机的一个枚举变体。比如：

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

需要注意：任何跨越 `.await` 的局部变量都会被存到状态机结构体里。如果你有一个 1MB 的栈数组然后 `.await` 了一下，这个 Future 的大小就是 1MB + 其他字段。这是容易踩的坑。

## Tokio 调度器：工作的线程才是好线程

多线程调度器（`#[tokio::main]` 默认）是 Tokio 最复杂的部分。它启动 N 个 worker 线程（默认等于 CPU 核数），每个 worker 有自己的任务队列，并通过工作窃取实现负载均衡。

### 三层队列

每一个 worker 内部有三个级别的任务来源：

1. **LIFO Slot**：单任务槽，存最近一次从本 worker 唤醒的任务。利用 CPU cache locality：你刚唤醒的任务很可能是你刚生成的，它的数据可能还在 L1 缓存里。但这个槽最多连续用 3 次，之后必须切到本地队列，防止饿死其他任务。

2. **本地队列**：256 个槽位的固定大小环形缓冲区，FIFO 顺序。只被当前 worker 读写，无锁操作。

3. **全局注入队列**：线程安全的共享队列。一个线程通过 `tokio::spawn()` 提交任务时，任务先进这里，然后每个 worker 定期从中批量拉取。

### 工作窃取算法

当一个 worker 的本地队列空了，它不闲着：它去偷。

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

Tokio 的 `steal_half` 实现很直接：从 victim 本地队列的尾部拿走一半任务，这样双方都不会彻底空掉。随机选 victim 避免了所有 worker 同时去抢同一个。

### block_on 和驱动关系

如果你用 `#[tokio::main]`，底层展开是：

```rust
fn main() {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async { /* 你的代码 */ })
}
```

`block_on` 创建一个 Runtime，在当前线程上调度 `async main`，同时启动 worker 线程池。I/O 驱动在一个专用线程上运行，轮询 epoll/kqueue，把就绪事件通过 Waker 送回调度器。

## I/O 驱动：不走轮询

从 socket 读数据时，read 不会阻塞线程。流程是这样的：

1. 调用 `TcpStream::read(&mut buf)`。
2. 内核返回 `EAGAIN/WouldBlock` → 还没数据。
3. Tokio 通过 mio（一个跨平台 I/O 轮询库）向 epoll 注册这个 socket 和当前任务的 Waker。
4. Future 返回 `Poll::Pending`，调度器切去跑别的任务。
5. 数据到达 → epoll 触发 → I/O 驱动线程拿到事件 → 调用 Waker::wake() → 任务重新进入调度队列。
6. 下次 poll，数据已经在缓冲区里了。

这套模式叫 Reactor 模式。Tokio 的 I/O 驱动（reactor）跑在独立线程上，用 `epoll_wait` 收事件，每次最多处理 1024 个事件。超时时间设为 61ms（与定时器驱动对齐）。

## 定时器：分层时间轮

Tokio 用一个 6 层分级哈希时间轮来实现定时器，设计借鉴了 Linux 内核的 timer wheel。

每层 64 个槽，粒度递增：

| 层 | 槽范围 | 覆盖时长 |
|----|--------|---------|
| 0 | 1ms × 64 = 64ms | 64ms |
| 1 | 64ms × 64 = 4.096s | ~4s |
| 2 | 4s × 64 = 256s | ~4min |
| 3 | 4min × 64 = 4.5hr | ~4.5hr |
| 4 | 4.5hr × 64 = 12天 | ~12天 |
| 5 | 12天 × 64 = 2.1年 | ~2年 |

插入和取消定时器都是 O(1)。当底层走完一圈，就从上一层卸下一批定时器重新哈希到下一层（摊销 O(1)）。

对比二叉堆（`std::collections::BinaryHeap`，O(log n) 插入/删除），时间轮在大规模定时器场景下优势明显。一个处理 10 万 HTTP 长连接的代理，每连接一个 60 秒超时定时器，二叉堆的 O(log 100000) 操作就不便宜了。

## 代码实战

### TCP Echo Server

最经典的 Tokio 程序，但值得仔细看每行：

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

`tokio::spawn` 不是开线程，它是把任务提交到调度器的全局队列。每个新连接得到的是一个异步任务，而不是一个 OS 线程。这就是为什么 Tokio 能轻松撑万级并发。

### 不要阻塞调度器

新手最常犯的错误是在 async 函数里调同步阻塞 API：

```rust
// ❌ 错误示范
async fn get_user(id: i64) -> Result<User, DbError> {
    let conn = postgres::Client::connect("host=localhost dbname=myapp", NoTls)?;
    let row = conn.query_one("SELECT * FROM users WHERE id = $1", &[&id])?;
    Ok(User::from_row(row))
}
```

这段代码会在 worker 线程上阻塞数毫秒。worker 线程在阻塞期间无法处理其他任务，整个调度器少了一个工人。正确的做法是用 `spawn_blocking`：

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

`spawn_blocking` 把 CPU/阻塞密集操作卸到独立的阻塞线程池，不干扰事件循环。

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

`JoinSet` 是结构化并发的基本工具，所有子任务的生命周期被限定在 `fetch_all` 函数内，不会泄漏。每个请求都有 5 秒超时保护。

## 生态现状

Rust 生态里有几个 async runtime 可选：

| Runtime | 调度策略 | 适用场景 | 特性 |
|---------|---------|---------|------|
| **Tokio** | 多线程工作窃取 | 服务端、网络应用、数据库驱动 | 最成熟的生态，全功能 |
| **smol** | 单线程 + 全局队列 | CLI 工具、轻量场景 | 编译快（10s vs Tokio 60s），代码量小 |
| **embassy** | 协作式，无堆分配 | 嵌入式、STM32/RP2040 | 支持中断驱动，零堆运行时 |
| **async-std** | 多线程 + 全局队列 | 通用（小生态） | 模仿 std API，但生态萎缩 |
| **glommio** | 单线程 io_uring 直驱 | 存储系统、NVMe | 对 io_uring 最深的集成 |

实践中，Tokio 占据主导地位。大部分 Rust 网络库（reqwest、axum、tonic、sqlx、hyper）都依赖 Tokio。smol 适合只想跑个简单异步 CLI 的场景。embassy 是嵌入式领域的首选。

## 今日可执行动作

1. **用 tokio-console 看调度器内部**：在你的项目中加 `console-subscriber`，运行时用 `RUSTFLAGS="--cfg tokio_unstable"` 编译，启动 `tokio-console` 观察任务调度延迟和 worker 利用率。

2. **检查你的代码里有没有阻塞 worker 线程**：grep 所有 `.await` 附近的同步 I/O 调用（`std::fs`、同步 `std::net`、同步数据库驱动），把它们换成 `spawn_blocking` 或异步替代品。

3. **对比二叉堆和时间轮**：写一个小 benchmark，分别用 `BinaryHeap` 和模拟的时间轮管理 10 万个定时器，比较插入 + 触发 1000 次的开销。

## 参考

- Tokio 官方文档 — Runtime 模块：https://docs.rs/tokio/latest/tokio/runtime/index.html
- Tokio 团队博客 — 新定时器实现（2018）：https://tokio.rs/blog/2018-03-timers
- Tokio 源码 Worker 实现：https://github.com/tokio-rs/tokio/blob/master/tokio/src/runtime/scheduler/multi_thread/worker.rs
- Microsoft Rust Training — Tokio Deep Dive：https://microsoft.github.io/RustTraining/async-book/ch08-tokio-deep-dive.html
- Lucio Duran — Tokio Runtime Design：https://lucioduran.com/blog/async-rust-tokio-internals-runtime-design
- Rust Async 专题 Deep Dive：https://www.youngju.dev/blog/culture/2026-04-15-rust-tokio-async-runtime-future-waker-work-stealing-deep-dive-guide-2025.en
