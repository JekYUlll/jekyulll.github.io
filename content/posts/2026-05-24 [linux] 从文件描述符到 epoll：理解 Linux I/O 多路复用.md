+++
date = '2026-05-24T14:49:14+08:00'
draft = false
title = '[linux] 从文件描述符到 epoll：理解 Linux I/O 多路复用'
author = 'JekYUlll'
lastmod = '2026-05-24T14:49:14+08:00'
tags = ['linux']
categories = ['linux']
+++

## 背景

每个后端开发都听过 epoll，但很多人对它的理解止步于"高性能 I/O"这个标签。为什么不能用多线程做并发？为什么 Nginx、Redis、Node.js 都绕不开 epoll？它的"高性能"到底来自哪里？

要回答这些问题，得先搞清楚 Linux 里一个最基本的概念：**文件描述符（File Descriptor, FD）**。

## 核心概念

### 文件描述符是什么

在 Linux 中，一切皆文件——socket、pipe、甚至 GPU 设备，都用文件描述符来表示。每个进程有个 FD 表（`task_struct->files`），本质上就是文件描述符整数到内核文件结构体的映射表。

当你调用 `socket()`，内核返回一个整数（比如 3）。之后 `recv()`、`send()`、`close()` 都是对这个整数的操作。FD 是进程级别的抽象，不同进程的 FD 3 不指向同一个东西。

### 阻塞与非阻塞

默认情况下，`read()` 一个没有数据的 socket 会阻塞——进程被挂起，内核在数据到达时唤醒它。这是最简单的并发模型：一个线程处理一个连接，逻辑清晰，但开销很大。

问题在于**一个连接对应一个线程**。线程栈默认 8MB，1000 个连接就快 8GB 了，还不算线程切换的上下文开销。这就是 C10K 问题的核心瓶颈。

### select → poll → epoll

Linux 为此演化出三个 I/O 多路复用系统调用：

| 系统调用 | 复杂度 | FD 上限 | 内核实现 |
|---------|--------|---------|---------|
| `select` | O(n) | 1024 (FD_SETSIZE) | 每次调用拷贝整个 FD 位图 |
| `poll` | O(n) | 无硬限制 | 每次调用拷贝 FD 数组 |
| `epoll` | O(1)~O(n) | 无限制 | 内核注册回调，就绪列表直接返回 |

`select` 和 `poll` 的工作方式是：每次调用都把全部 FD 集合从用户态拷贝到内核态，内核遍历所有 FD 检查状态，再拷贝回用户态。大部分 FD 根本没有事件，这些遍历和拷贝都是浪费的。

**epoll 的突破在于**：它将"注册 FD"和"等待事件"分离为两个操作：

- `epoll_ctl` — 告诉内核"帮我关注这个 FD 的读事件"，内核在该 FD 上挂一个回调函数
- `epoll_wait` — 阻塞等待，内核直接返回"有哪些 FD 就绪了"

就绪列表中只包含活跃的 FD，没有无效遍历。传输的数据只是一个数组，不是整个 FD 表。这就是 epoll 在大量连接下依然高效的原因。

### 边缘触发 vs 水平触发

这是面试常考点，也是实际开发容易出 bug 的地方。

- **水平触发（LT，默认）**：只要 buffer 有数据，`epoll_wait` 就返回该 FD。如果一次没读完，下次调用还会再通知。
- **边缘触发（ET）**：只在状态从无数据变为有数据的**时刻**通知一次。如果你没读完剩余数据，它不会再次通知——直到有新数据到达。

ET 模式的效率更高（减少重复通知），但要求你**一次性读完所有数据**（循环调用 `read()` 直到返回 `EAGAIN`），否则数据会永远留在内核 buffer 里不被处理。Nginx 就是 ET 模式。

## 工程视角：实践中 epoll 怎么用

一个典型的 epoll 事件循环长这样：

```c
int epfd = epoll_create1(0);
struct epoll_event ev, events[MAX_EVENTS];

ev.events = EPOLLIN;
ev.data.fd = listen_sock;
epoll_ctl(epfd, EPOLL_CTL_ADD, listen_sock, &ev);

while (1) {
    int n = epoll_wait(epfd, events, MAX_EVENTS, -1);
    for (int i = 0; i < n; i++) {
        if (events[i].data.fd == listen_sock) {
            // 新连接 → accept → epoll_ctl ADD
        } else {
            // 读写事件 → 处理
        }
    }
}
```

核心思想：**单线程处理所有 I/O**。一个线程（或者少量线程）管理成千上万连接，计算密集型操作才丢给工作线程池。

在 Redis 中，这就是整个事件循环的全部——单线程处理网络 I/O 和命令执行，靠着 epoll 在万级连接下依然保持微秒延迟。

说个我踩过的坑：**`EPOLLONESHOT` 的误用**。某个项目需要把 socket 的读事件分发给工作线程（避免一个线程阻塞拖慢其他连接），我们给每个 FD 加了 `EPOLLONESHOT`，期望事件被消费后自动移除。但实现中，工作线程处理完调用 `epoll_ctl(EPOLL_CTL_MOD, EPOLLONESHOT)` 重新注册时忘了设置 `EPOLLIN`，导致该连接再也不会收到读事件——直到客户端重连才发现。这提醒我：epoll 的所有标记位都是位掩码，**修改时必须保留原有的标记**。

## 今日可执行动作

1. **用实战验证理解**：写一个最小的 echo server，用 epoll 管理多个客户端连接，分别测试 LT 和 ET 模式，观察行为差异。源码不到 200 行，但能彻底搞清楚 epoll 的工作模型。
2. **阅读 Redis 源码的 ae.c**：`src/ae.c` 中的 `aeProcessEvents()` 是 epoll 封装的教科书级实现。只看 100 行就能理解工业级的事件循环怎么组织。
3. **gdb 看一下 kernel 是怎么通知的**：如果手头有 Linux 虚拟机（或者 WSL2），`strace -e epoll_ctl,epoll_wait` 跑一个 epoll 程序，看到系统调用的调用次数和参数，会比读十篇博客都直观。
