+++
date = '2026-06-02T10:06:38+08:00'
draft = false
title = 'C++26 std::execution：Senders & Receivers 异步模型'
author = 'JekYUlll'
lastmod = '2026-06-02T10:06:38+08:00'
tags = ['execution', 'senders', 'async', 'concurrency']
categories = ['cpp']
+++

## 背景

C++ 的异步编程一直很尴尬。

`std::future` / `std::promise` 用起来像在泥地里跑步：每次 `.then()` 都要堆分配加一把锁，`std::function` 做类型擦除再来一次间接调用。这不是零开销原则，这是负开销。

2024 年 6 月，ISO C++ 委员会在圣路易斯会议上投票通过 P2300 `std::execution` 进入 C++26 工作草案。这个提案又叫 **Senders & Receivers**，它不是又一个异步库，而是 C++ 对结构化并发的第一次标准级回答。

Eric Niebler 的比喻很精准：`goto` → `for/while/if` 是从非结构化编程到结构化编程的跨越；`std::future`/`mutex` → Senders/Receivers 是从非结构化并发到结构化并发的同一类跨越。

## 核心原理

### Sender：惰性计算描述

Sender 是一个惰性的计算描述。它不执行任何东西，直到你把它连接到一个 Receiver。

```cpp
auto snd = stdexec::just("hello")
         | stdexec::then([](std::string_view s) { std::print("{}", s); });
// 什么都没发生
std::this_thread::sync_wait(std::move(snd));
// 现在才执行
```

三件事值得注意：

1. `just(X)` 创建一个 sender，它产生值 X
2. `| then(f)` 把 sender 和函数 f 组合成新 sender
3. `sync_wait` 是 consumer，它把 sender 连上 receiver，阻塞等待完成

这跟函数组合没区别：`just(X) | then(f)` 等价于 `f(X)`。区别在于 sender 可以跨线程、跨调度器，而且严格不分配堆内存。

### 三个通道：值、错误、取消

每个 sender 最终走三条路之一。跟 `std::future` 比，取消不是事后补丁，是一等公民。

| 通道 | 含义 | 对应算法 |
|------|------|----------|
| `set_value` | 正常完成 | `then`, `let_value` |
| `set_error` | 异常/出错 | `upon_error`, `let_error` |
| `set_stopped` | 被取消 | `upon_stopped`, `let_stopped` |

### Scheduler：执行位置

Sender 不碰线程。执行在哪跑由 Scheduler 决定：

```cpp
auto sch = stdexec::get_parallel_scheduler(); // 系统线程池

auto work = stdexec::schedule(sch)
          | stdexec::then([]{ std::print("跑在线程池里\n"); });
```

`schedule(sch)` 返回一个 sender，在 sch 指定的上下文中调度执行。你可以随时用 `continues_on` 在中途切换调度器。

### when_all：并行编排

f 和 g 并发跑，拿到两者的结果后求和：

```cpp
auto s1 = stdexec::on(sch, stdexec::just(1) | stdexec::then(f));
auto s2 = stdexec::on(sch, stdexec::just(2) | stdexec::then(g));
auto both = stdexec::when_all(s1, s2)
          | stdexec::then([](int a, int b) { return a + b; });
```

`when_all` 是结构化并发的核心：多个 sender 并发执行，全部完成后打包传给下一个 `then`。不需要手动 join，不需要锁。

### split()：复用而不重复执行

普通 sender 只能消费一次。如果同一个 sender 要喂给两条不同管线：

```cpp
auto common = stdexec::schedule(sch)
            | stdexec::then(preprocess)
            | stdexec::split();

auto branch1 = common | stdexec::then(process_a);
auto branch2 = common | stdexec::then(process_b);
auto result  = stdexec::when_all(branch1, branch2);
```

`split()` 让 sender 可共享。内部自动处理多 receiver 连接。

## 代码实战

完整例子：I/O 线程读数据，CPU 线程池处理，再写回连接。

```cpp
#include <stdexec/execution.hpp>
#include <print>

namespace ex = stdexec;

struct Connection { int fd; };
struct Buffer     { char data[4096]; int len; };

auto read_data(Connection conn, Buffer buf) -> void {
    buf.len = ::read(conn.fd, buf.data, sizeof(buf.data));
}

auto process_data(Buffer buf) -> Buffer {
    // CPU 密集：压缩/加密/序列化
    for (int i = 0; i < buf.len; ++i)
        buf.data[i] ^= 0x55;
    return buf;
}

auto write_result(Connection conn, Buffer buf) -> void {
    ::write(conn.fd, buf.data, buf.len);
}

int main() {
    auto io_sch   = ex::get_io_scheduler();
    auto work_sch = ex::get_parallel_scheduler();
    Connection conn{3};
    Buffer     buf{};

    auto pipeline =
        ex::starts_on(io_sch, ex::just(conn, buf)
            | ex::then(read_data))
        | ex::continues_on(work_sch)
        | ex::then(process_data)
        | ex::continues_on(io_sch)
        | ex::then(write_result);

    ex::sync_wait(std::move(pipeline));
}
```

四个关键点：

- `starts_on(io_sch, ...)` 在 I/O 调度器上启动整条链
- `continues_on` 在中途切换执行上下文，数据跟着 move 过去
- 零堆分配：所有 lambda 类型编译期已知，不需要 `std::function`
- 无锁：sender/receiver 协议保证同一时刻只有一处持有数据

### 对比 std::future

同样的事用 `std::future` 写：

```cpp
auto fut = std::async(std::launch::async, read_data, conn, buf);
// .then() 在 std::future 中没有标准实现
// 需要手动编排：promise + future + 线程池队列 = 大量样板代码
fut.get();
```

`.then()` 不是标准 `std::future` 的成员。要实现类似的链式调用，开发者得自己搞 promise/future 配对、管理工作线程、处理取消。每一层 `then` 都触发：`std::function` 的类型擦除（堆分配）、shared state 的原子引用计数、内部互斥锁防竞态。

Sender 把所有这些类型在编译期叠成嵌套结构体，move 着传。零开销。

## 生态现状

| 项目 | 说明 | 状态 |
|------|------|------|
| [NVIDIA stdexec](https://github.com/NVIDIA/stdexec) | P2300 参考实现，header-only | 活跃维护，2.4k ⭐ |
| [libunifex](https://github.com/facebookexperimental/libunifex) | Meta 的实验版本 | 已被 stdexec 取代 |
| [HPX](https://github.com/STEllAR-GROUP/hpx) | 分布式 C++ 异步运行时 | 支持 sender/receiver |
| [P3109](https://wg21.link/P3109) | C++26 额外 sender 设施规划 | 提案中 |

stdexec 是目前唯一的活跃完整实现。它在标准提案之外还提供：`async_scope`（结构化并发作用域）、`task`（coroutine 互操作）、`io_uring` 调度器、GPU 调度器等扩展。

## 今日可执行动作

1. **跑 hello world**：clone stdexec，编译 `examples/hello_world.cpp`，感受 sender 管线的延迟执行特性
2. **改写现有代码**：找项目里一段用 `std::async` 的地方，用 `stdexec::on(sch, just(X) | then(f))` 改写，对比性能
3. **读 P2300R10 motivation 章节**：不到 10 页，讲清楚了为什么 `std::future` 这条路走不通

## 参考

- [P2300R10: std::execution](https://wg21.link/P2300R10) — 标准提案全文
- [Senders/Receivers: An Introduction (ACCU Overload 184)](https://accu.org/journals/overload/32/184/teodorescu/) — Lucian Radu Teodorescu
- [Senders/receivers in C++ (CompuTruthing)](http://lucteo.ro/2024/08/12/senders-receivers-in-cxx/) — P2300 合著者立场说明
- [NVIDIA stdexec](https://github.com/NVIDIA/stdexec) — 参考实现
- [CppCon 2019: A Unifying Abstraction for Async in C++](https://www.youtube.com/watch?v=tF-Nz4aRWAM) — Eric Niebler 演讲
