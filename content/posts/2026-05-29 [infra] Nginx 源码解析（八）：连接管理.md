+++
title = 'Nginx 源码解析（八）：连接管理'
date = '2026-05-29T17:40:09+08:00'
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'connection', 'c']
+++

前几篇文章我们聊了事件驱动和内存池，这次来看连接管理。连接是 Nginx 处理所有请求的起点，从 accept 到 close 的完整生命周期都在 `src/core/ngx_connection.c` 和 `src/event/ngx_event_accept.c` 中实现。理解这套机制，就理解了 Nginx 为什么能在亿级连接下保持稳定。

<!--more-->

## ngx_connection_t：连接的完整描述

`ngx_connection_s` 定义在 `src/core/ngx_connection.h`：

```c
struct ngx_connection_s {
    void               *data;         // 协议特定数据（HTTP request、stream 等）
    ngx_event_t        *read;         // 读事件
    ngx_event_t        *write;        // 写事件
    ngx_socket_t        fd;           // socket 文件描述符

    ngx_recv_pt         recv;        // 接收数据回调
    ngx_send_pt         send;        // 发送数据回调
    ngx_recv_chain_pt   recv_chain;  // 收链
    ngx_send_chain_pt   send_chain;  // 发链

    ngx_listening_t    *listening;   // 所属监听 socket

    off_t               sent;        // 已发送字节数
    ngx_log_t          *log;         // 日志对象
    ngx_pool_t         *pool;        // 连接内存池（accept 时创建，close 时销毁）

    struct sockaddr    *sockaddr;    // 对端地址
    socklen_t           socklen;
    ngx_str_t           addr_text;   // 文本形式的地址

    ngx_queue_t         queue;       // 侵入式队列节点（用于 reusable 队列）
    ngx_atomic_uint_t   number;      // 全局递增连接序号
    ngx_msec_t          start_time;  // 连接创建时间
    ngx_uint_t          requests;    // 连接上已处理的请求数（keepalive）

    unsigned            idle:1;      // keepalive 空闲状态
    unsigned            reusable:1;  // 是否可被回收
    unsigned            close:1;     // 标记需要关闭
    unsigned            destroyed:1; // 是否已销毁
    unsigned            tcp_nodelay:2;
    unsigned            tcp_nopush:2;
    // ...
};
```

关键设计点：

- **`data` 字段**是多态的，，在 HTTP 模块中指向 `ngx_http_request_t`，在 Stream 模块中指向 `ngx_stream_session_t`。这是 Nginx 用纯 C 实现面向对象的方式。
- **`recv/send` 是函数指针**，使得上层协议代码不用关心底层是普通 TCP 还是 SSL。SSL 模块初始化时直接替换这些指针。
- **`read` 和 `write` 是预分配的**，并不和 `ngx_connection_t` 在同一块内存里，，它们来自 `cycle->read_events[]` 和 `cycle->write_events[]` 数组。这样设计是为了避免缓存行伪共享：读事件、写事件、连接结构体各自在独立的内存区域。
- **`pool`** 是每个连接自己的内存池，accept 时创建，close 时销毁。所有请求相关的内存分配都从这个池子里走。

## 连接池的初始化

在 `src/event/ngx_event.c` 的 `ngx_event_process_init()` 中，Nginx 为每个 worker 进程预分配连接数组：

```c
// 分配 connection_n 个 ngx_connection_t
cycle->connections =
    ngx_alloc(sizeof(ngx_connection_t) * cycle->connection_n, cycle->log);

// 独立分配读/写事件数组
cycle->read_events = ngx_alloc(sizeof(ngx_event_t) * cycle->connection_n, cycle->log);
cycle->write_events = ngx_alloc(sizeof(ngx_event_t) * cycle->connection_n, cycle->log);

// 初始将所有事件标记为"已关闭"
for (i = 0; i < cycle->connection_n; i++) {
    rev[i].closed = 1;
    rev[i].instance = 1;
}
```

然后通过一个倒序循环将连接串成空闲链表：

```c
i = cycle->connection_n;
next = NULL;
do {
    i--;
    c[i].data = next;          // data 字段退化为空闲链表 next 指针
    c[i].read = &cycle->read_events[i];
    c[i].write = &cycle->write_events[i];
    c[i].fd = (ngx_socket_t) -1;
    next = &c[i];
} while (i);

cycle->free_connections = next;
cycle->free_connection_n = cycle->connection_n;
```

这个初始化很巧妙，，`data` 字段在连接空闲时被复用为链表指针，避免了额外的释放链表数据结构。`free_connections` 指向链表头，`free_connection_n` 记录剩余空闲数。

`connection_n` 来自配置文件中的 `worker_connections` 指令，所有连接在一个连续数组中。`cycle->files[]` 数组（大小等于系统 `RLIMIT_NOFILE`）则通过 fd 索引直接定位连接：`c = cycle->files[fd]`，实现 O(1) 查找。

## ngx_get_connection 和 ngx_free_connection

从空闲池获取连接：

```c
ngx_connection_t *
ngx_get_connection(ngx_socket_t s, ngx_log_t *log)
{
    // 检查 fd 是否超出 files[] 范围
    if (ngx_cycle->files && (ngx_uint_t) s >= ngx_cycle->files_n) {
        return NULL;
    }

    // 如果空闲连接太少，尝试回收 keepalive 连接
    ngx_drain_connections((ngx_cycle_t *) ngx_cycle);

    c = ngx_cycle->free_connections;
    if (c == NULL) {
        // "worker_connections are not enough"
        return NULL;
    }

    // 取出链表头
    ngx_cycle->free_connections = c->data;
    ngx_cycle->free_connection_n--;

    // 注册到 files 数组中
    if (ngx_cycle->files && ngx_cycle->files[s] == NULL) {
        ngx_cycle->files[s] = c;
    }

    // 零初始化（保留 read/write 指针）
    rev = c->read;
    wev = c->write;
    ngx_memzero(c, sizeof(ngx_connection_t));
    c->read = rev;
    c->write = wev;
    c->fd = s;
    c->log = log;

    // 翻转 instance 标志位（用于过期事件检测）
    instance = rev->instance;
    ngx_memzero(rev, sizeof(ngx_event_t));
    ngx_memzero(wev, sizeof(ngx_event_t));
    rev->instance = !instance;
    wev->instance = !instance;
    // ...
}
```

归还连接的逻辑正好相反：

```c
void
ngx_free_connection(ngx_connection_t *c)
{
    c->data = ngx_cycle->free_connections;  // data 复用为链表指针
    ngx_cycle->free_connections = c;
    ngx_cycle->free_connection_n++;

    if (ngx_cycle->files && ngx_cycle->files[c->fd] == c) {
        ngx_cycle->files[c->fd] = NULL;    // 从 files 中清除
    }
}
```

`instance` 标志位的翻转是为了解决"事件过期"问题：如果一个连接被关闭后又被重用，epoll 可能还在通知旧的事件。通过翻转 `instance`，`ngx_event.c` 中的事件处理函数能检测到事件已过期而忽略它。

## ngx_event_accept：连接的诞生

核心入口在 `src/event/ngx_event_accept.c` 的 `ngx_event_accept()`。当监听 socket 上 epoll 返回 EPOLLIN 时，这个函数被调用。

```c
void
ngx_event_accept(ngx_event_t *ev)
{
    do {
        // 优先使用 accept4（SOCK_NONBLOCK，一次系统调用完成两个操作）
        s = accept4(lc->fd, &sa.sockaddr, &socklen, SOCK_NONBLOCK);
        // 如果不支持，回退到 accept + 手动设置非阻塞
        if (s == (ngx_socket_t) -1) {
            if (use_accept4 && err == NGX_ENOSYS) {
                use_accept4 = 0;  // 内核不支持，禁用 accept4
                continue;
            }
            // 处理 EAGAIN/ECONNABORTED/EMFILE 等错误
            if (err == NGX_EAGAIN) return;
            if (err == NGX_EMFILE || err == NGX_ENFILE) {
                ngx_disable_accept_events(cycle, 1); // 关闭 accept 事件
                return;
            }
            return;
        }

        // 计算 accept_disabled 阈值
        ngx_accept_disabled = ngx_cycle->connection_n / 8
                              - ngx_cycle->free_connection_n;

        // 从连接池获取
        c = ngx_get_connection(s, ev->log);

        // 创建连接内存池
        c->pool = ngx_create_pool(ls->pool_size, ev->log);

        // 设置回调函数指针
        c->recv = ngx_recv;
        c->send = ngx_send;
        c->recv_chain = ngx_recv_chain;
        c->send_chain = ngx_send_chain;

        // 分配唯一连接序号
        c->number = ngx_atomic_fetch_add(ngx_connection_counter, 1);

        // 调用监听 socket 注册的 handler（HTTP 模块是 ngx_http_init_connection）
        ls->handler(c);

    } while (ev->available);  // multi_accept: 一次事件循环处理多个连接
}
```

`accept4()` 的 `SOCK_NONBLOCK` 标志合并了 `accept()` + `fcntl(O_NONBLOCK)` 两个系统调用，在连接速率极高时减少了一半的系统调用开销。

accept 之后还会设置 `TCP_NODELAY` 和 `TCP_DEFER_ACCEPT`（延迟 accept）等 socket 选项。延迟 accept 让内核在收到真正的数据之后才唤醒 worker，而不是 TCP 握手完成就触发，能减少空连接占用的资源。

## Accept 互斥锁：避免惊群

在 `ngx_event.c` 的 `ngx_process_events_and_timers()` 中，每个事件循环周期都会尝试获取 accept 互斥锁：

```c
if (ngx_use_accept_mutex) {
    if (ngx_accept_disabled > 0) {
        ngx_accept_disabled--;       // 仍然放弃竞争
    } else {
        if (ngx_trylock_accept_mutex(cycle) == NGX_ERROR) {
            return;
        }
        if (ngx_accept_mutex_held) {
            flags |= NGX_POST_EVENTS; // 持有时延迟处理事件
        } else {
            // 未获取到，缩短超时以尽快重试
            timer = min(timer, ngx_accept_mutex_delay);
        }
    }
}
```

`ngx_trylock_accept_mutex()` 的定义在 `ngx_event_accept.c`：

```c
ngx_int_t
ngx_trylock_accept_mutex(ngx_cycle_t *cycle)
{
    if (ngx_shmtx_trylock(&ngx_accept_mutex)) {
        // 成功获取锁，开启当前 worker 的所有 accept 事件
        if (ngx_enable_accept_events(cycle) == NGX_ERROR) {
            ngx_shmtx_unlock(&ngx_accept_mutex);
            return NGX_ERROR;
        }
        ngx_accept_mutex_held = 1;
        return NGX_OK;
    }

    // 未获取到，如果之前持有则关闭 accept 事件
    if (ngx_accept_mutex_held) {
        ngx_disable_accept_events(cycle, 0);
        ngx_accept_mutex_held = 0;
    }
    return NGX_OK;
}
```

`NGX_POST_EVENTS` 标志位让 accept 事件先被"暂存"到 `ngx_posted_accept_events` 队列中，等释放互斥锁后再处理。这样互斥锁的持有时间极短，—只在 epoll_wait 返回后到释放锁之间。

`ngx_accept_disabled` 是一个更精细的节流机制：

```c
// 在 ngx_event_accept 中每 accept 一个连接后更新
ngx_accept_disabled = ngx_cycle->connection_n / 8
                      - ngx_cycle->free_connection_n;
```

当空闲连接少于 `connection_n / 8` 时，`ngx_accept_disabled` 变为正数。事件循环中检查到正数就跳过锁竞争（`ngx_accept_disabled--` 递减），直到空闲连接恢复。这防止了某个 worker 在连接压力大时还去抢 accept，给了其他 worker 喘息空间。

## SO_REUSEPORT：绕过互斥锁

`accept_mutex` 虽然解决了惊群，但增加了锁竞争的开销。Linux 3.9+ 引入的 `SO_REUSEPORT` 提供了更优雅的方案，—内核级负载均衡。

在 `ngx_cycle.c` 的 `ngx_clone_listening()` 中：

```c
ngx_int_t
ngx_clone_listening(ngx_cycle_t *cycle, ngx_listening_t *ls)
{
    if (!ls->reuseport || ls->worker != 0) {
        return NGX_OK;
    }

    for (n = 1; n < ccf->worker_processes; n++) {
        ls = ngx_array_push(&cycle->listening);
        *ls = ols;
        ls->worker = n;  // 每个 worker 有自己的 socket
    }
    return NGX_OK;
}
```

每个 worker 进程各自创建一个绑定相同地址端口的 socket，内核在接收 TCP 连接时通过哈希将连接分发到不同的 socket，完全无需用户态锁。

当 `SO_REUSEPORT` 启用时，`ngx_event_process_init()` 中每个 worker 只打开自己的那份 listening socket：

```c
for (i = 0; i < cycle->listening.nelts; i++) {
#if (NGX_HAVE_REUSEPORT)
    if (ls[i].reuseport && ls[i].worker != ngx_worker) {
        continue;  // 不是我的 socket，跳过
    }
#endif
    c = ngx_get_connection(ls[i].fd, cycle->log);
    // 注册读事件...
}
```

而在 `ngx_disable_accept_events()` 中，禁用 accept 事件时会跳过 `reuseport` 的 socket（因为每个 worker 操作自己的 socket，不影响其他 worker）：

```c
#if (NGX_HAVE_REUSEPORT)
    if (ls[i].reuseport && !all) {
        continue;  // reuseport socket 不受 accept 互斥锁影响
    }
#endif
```

所以在 `reuseport` 模式下，`ngx_use_accept_mutex` 为 0，所有 worker 的 epoll 中监听 socket 始终处于活跃状态。

## ngx_close_connection：连接的终结

`ngx_close_connection()` 执行三个步骤：

1. **删除事件**：从 epoll 中移除读/写事件，删除定时器，清除 posted 事件
2. **归还连接**：调用 `ngx_free_connection()` 回收到空闲链表
3. **关闭 fd**：调用 `close()`，释放连接池

```c
void
ngx_close_connection(ngx_connection_t *c)
{
    if (c->fd == (ngx_socket_t) -1) return;  // 已关闭

    // 1. 删除定时器
    if (c->read->timer_set) ngx_del_timer(c->read);
    if (c->write->timer_set) ngx_del_timer(c->write);

    // 2. 从 epoll 中删除
    if (ngx_del_conn) {
        ngx_del_conn(c, NGX_CLOSE_EVENT);
    } else {
        if (c->read->active || c->read->disabled) ngx_del_event(c->read, ...);
        if (c->write->active || c->write->disabled) ngx_del_event(c->write, ...);
    }

    // 3. 标记关闭
    c->read->closed = 1;
    c->write->closed = 1;

    // 4. 从 reusable 队列移除（如果是 keepalive 连接）
    ngx_reusable_connection(c, 0);

    // 5. 归还连接到空闲池
    ngx_free_connection(c);

    // 6. 关闭 socket
    fd = c->fd;
    c->fd = (ngx_socket_t) -1;
    ngx_close_socket(fd);

    // 7. 释放连接内存池
    if (c->pool) {
        ngx_destroy_pool(c->pool);
    }
}
```

注意 `c->read->closed = 1` 的作用，—与前面提到的 `instance` 标志位配合，确保任何尚未处理的事件回调在检查 `closed` 后立即返回。

## Keepalive 与连接的复用

Keepalive 是 HTTP/1.1 减少新建连接开销的关键机制。当 HTTP 请求处理完毕且连接可以复用时，`ngx_http_set_keepalive()` 被调用：

```c
// ngx_http_request.c
rev->handler = ngx_http_keepalive_handler;
c->idle = 1;
ngx_reusable_connection(c, 1);           // 加入 reusable 队列
ngx_add_timer(rev, clcf->keepalive_timeout);  // 超时关闭
```

`ngx_reusable_connection()` 将连接加入 `cycle->reusable_connections_queue` 队列：

```c
void
ngx_reusable_connection(ngx_connection_t *c, ngx_uint_t reusable)
{
    if (c->reusable) {
        ngx_queue_remove(&c->queue);                // 从队列中移除
        ngx_cycle->reusable_connections_n--;
    }
    c->reusable = reusable;
    if (reusable) {
        ngx_queue_insert_head(
            (ngx_queue_t *) &ngx_cycle->reusable_connections_queue, &c->queue);
        ngx_cycle->reusable_connections_n++;
    }
}
```

当空闲连接不足时，`ngx_drain_connections()` 从队列尾部（最老的 keepalive 连接）回收：

```c
static void
ngx_drain_connections(ngx_cycle_t *cycle)
{
    // 空闲连接多于 1/16 时不做回收
    if (cycle->free_connection_n > cycle->connection_n / 16
        || cycle->reusable_connections_n == 0) {
        return;
    }

    // 一次回收最多 32 个
    n = ngx_max(ngx_min(32, cycle->reusable_connections_n / 8), 1);
    for (i = 0; i < n; i++) {
        q = ngx_queue_last(&cycle->reusable_connections_queue);
        c = ngx_queue_data(q, ngx_connection_t, queue);
        c->close = 1;
        c->read->handler(c->read);  // 触发 keepalive handler 中的关闭逻辑
    }
}
```

`ngx_http_keepalive_handler()` 在收到新数据时，从 `c->buffer` 中尝试读取请求数据，然后调用 `ngx_http_create_request()` 创建新的 request 对象，并切换到 `ngx_http_process_request_line` 处理。

如果超时（`rev->timedout`）或连接标记为关闭（`c->close`），则直接调用 `ngx_http_close_connection()` 关闭连接。

## 整体流程串起来

```
worker 启动
  └─ ngx_event_process_init()
      ├─ 分配 connections[] / read_events[] / write_events[]
      ├─ 串成 free_connections 链表
      └─ 为每个 listening socket 分配连接并注册读事件

每轮事件循环
  └─ ngx_process_events_and_timers()
      ├─ accept 互斥锁（如果启用）
      ├─ epoll_wait()
      ├─ 处理 accept 事件
      │   └─ ngx_event_accept()
      │       ├─ accept4()
      │       ├─ ngx_get_connection()
      │       ├─ 创建 c->pool
      │       └─ ls->handler(c) → ngx_http_init_connection()
      ├─ 释放 accept 互斥锁
      └─ 处理普通事件

请求结束
  ├─ keepalive → ngx_http_set_keepalive()
  │   ├─ 注册 ngx_http_keepalive_handler
  │   └─ ngx_reusable_connection(c, 1)
  └─ 关闭 → ngx_close_connection()
      ├─ 删除事件/定时器
      ├─ ngx_free_connection()
      ├─ close(fd)
      └─ ngx_destroy_pool(c->pool)
```

从连接池的预分配，到 accept 时的快速获取，再到 keepalive 复用的精细控制，最后到关闭时的干净回收，—Nginx 的连接管理代码不到 500 行，却覆盖了从内核 API 的极致利用（accept4、SO_REUSEPORT、TCP_DEFER_ACCEPT）到用户态锁的设计（accept mutex、accept_disabled），再到应用层连接复用（keepalive）的完整链路。每一行都体现出"为百万并发而设计"的工程哲学。

## 参考

- Nginx 1.24.x 源码: `src/core/ngx_connection.h`, `src/core/ngx_connection.c`
- `src/event/ngx_event_accept.c` ， accept 处理 + 互斥锁
- `src/event/ngx_event.c` ， `ngx_event_process_init()` 连接初始化 + `ngx_process_events_and_timers()` 事件循环
- `src/http/ngx_http_request.c` ， `ngx_http_set_keepalive()` 和 `ngx_http_keepalive_handler()`
- Linux `man 7 socket` ， SO_REUSEPORT 说明
- Linux `man 2 accept4` ， SOCK_NONBLOCK 标志位
