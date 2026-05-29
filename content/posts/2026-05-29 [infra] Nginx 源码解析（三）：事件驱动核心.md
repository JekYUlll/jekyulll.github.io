+++
date = '2026-05-29T17:36:00+08:00'
draft = false
title = 'Nginx 源码解析（三）：事件驱动核心'
author = 'JekYUlll'
lastmod = '2026-05-29T17:40:09+08:00'
tags = ['nginx-source', 'nginx', 'event-loop', 'epoll', 'c']
categories = ['infra']
+++

如果说多进程模型是 Nginx 的骨架，那么事件驱动核心就是它的心脏。在 Linux 平台上，这个心脏由 epoll 机制与 Nginx 精密的封装层共同构成。本文将以 Nginx 1.24.x 源码为基础，深入剖析事件驱动的核心结构：`ngx_event_actions_t` 接口、事件循环函数 `ngx_process_events_and_timers()`、epoll 模块的具体实现，以及后事件队列的精巧设计。

---

#### 一、ngx_event_actions_t：事件操作接口的抽象层

Nginx 设计了一套 **事件模块接口**，用函数指针表 `ngx_event_actions_t` 屏蔽底层多路复用机制的差异（epoll、kqueue、poll 等）。其定义位于 `src/event/ngx_event.h`：

```c
typedef struct {
    ngx_int_t  (*add)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ngx_int_t  (*del)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ngx_int_t  (*enable)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ngx_int_t  (*disable)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ngx_int_t  (*add_conn)(ngx_connection_t *c);
    ngx_int_t  (*del_conn)(ngx_connection_t *c, ngx_uint_t flags);
    ngx_int_t  (*notify)(ngx_event_handler_pt handler);
    ngx_int_t  (*process_events)(ngx_cycle_t *cycle, ngx_msec_t timer, ngx_uint_t flags);
    ngx_int_t  (*init)(ngx_cycle_t *cycle, ngx_msec_t timer);
    void       (*done)(ngx_cycle_t *cycle);
} ngx_event_actions_t;
```

其中 `add/del` 负责增删单个事件的监听；`enable/disable` 是对应启停；`add_conn/del_conn` 则批量处理一个连接上的读/写事件；`process_events` 是最核心的事件等待与分发函数。Nginx 通过宏定义将这些接口暴露给上层：

```c
#define ngx_process_events   ngx_event_actions.process_events
#define ngx_add_event        ngx_event_actions.add
#define ngx_del_event        ngx_event_actions.del
#define ngx_add_conn         ngx_event_actions.add_conn
#define ngx_del_conn         ngx_event_actions.del_conn
```

这种策略模式的设计，让事件循环的调用方完全不需要关心底层用的是 epoll 还是 kqueue，一切通过全局变量 `ngx_event_actions` 间接调用。在 epoll 模块初始化时，这个全局变量被赋值为 `ngx_epoll_module_ctx.actions`（`ngx_epoll_module.c` 第 369 行）：

```c
ngx_event_actions = ngx_epoll_module_ctx.actions;
```

---

#### 二、ngx_event_t：事件的核心数据结构

每个事件对应一个 `ngx_event_s` 结构体（`ngx_event.h` 第 30 行），其关键字段如下：

```c
struct ngx_event_s {
    void            *data;        // 指向关联的 ngx_connection_t
    unsigned         write:1;     // 写事件标记
    unsigned         accept:1;    // accept 事件标记
    unsigned         instance:1;  // 陈旧事件检测
    unsigned         active:1;    // 已在事件驱动中注册
    unsigned         ready:1;     // 事件已就绪
    unsigned         timedout:1;  // 超时标记
    ngx_event_handler_pt  handler; // 事件回调函数
    ngx_rbtree_node_t   timer;    // 红黑树定时器节点
    ngx_queue_t      queue;       // 后事件队列节点
};
```

- **`data`**：指向 `ngx_connection_t`，回调处理时通过它拿到 fd、读写缓冲区等信息。
- **`handler`**：事件触发时的回调。例如 accept 事件绑定的是 `ngx_event_accept()`，普通读写事件绑定的是 `ngx_http_init_request()` 等上层处理函数。
- **`accept` 标志**：由 `ngx_event_accept` 的 accept 事件设置，被 `ngx_epoll_process_events` 用于判断入队到 `ngx_posted_accept_events` 还是 `ngx_posted_events`。
- **`instance`**：这是 Nginx 处理**陈旧事件**的关键技巧。epoll 返回的 `data.ptr` 中最低位存有 instance 版本号；如果连接被关闭又重新分配，`instance` 不匹配，则丢弃该事件（见 `ngx_epoll_process_events` 第 839-854 行）。

---

#### 三、ngx_process_events_and_timers()：事件循环心脏

这是整个 Nginx Worker 进程的主循环函数（`ngx_event.c` 第 194 行），每次迭代执行以下流程：

```
① 确定超时 timer
② accept 互斥锁处理
③ 处理 next 后事件迁移
④ 调用 ngx_process_events() → epoll_wait()
⑤ 处理 accept 后事件队列
⑥ 释放 accept 互斥锁
⑦ 超时事件过期处理
⑧ 处理普通后事件队列
```

**Accept 互斥锁处理**（第 219-238 行）：当 `ngx_use_accept_mutex` 开启时，每个 Worker 在进入 epoll_wait 前先尝试锁住 accept 互斥锁。若成功获取锁（`ngx_accept_mutex_held` 为真），则设置 `flags |= NGX_POST_EVENTS`，表示本轮 epoll 产生的事件先**入队不处理**；若未拿到锁，则将 timer 限制为 `ngx_accept_mutex_delay`（默认 500ms），确保过段时间再尝试。此外，`ngx_accept_disabled` 计数器实现 Worker 间的负载均衡，当某个 Worker 的连接数超过总连接数的 7/8 时，它会主动放弃竞争锁。

**后事件队列（Posted Events）**：事件驱动层（epoll）发现事件后，不直接调用 handler，而是通过 `ngx_post_event()` 宏（`ngx_event_posted.h` 第 17 行）将事件插入队列尾部：

```c
#define ngx_post_event(ev, q)
    if (!(ev)->posted) {
        (ev)->posted = 1;
        ngx_queue_insert_tail(q, &(ev)->queue);
    }
```

Nginx 维护三个后事件队列：

| 队列变量 | 用途 |
|---|---|
| `ngx_posted_accept_events` | 新连接 accept 事件，优先处理 |
| `ngx_posted_events` | 普通读写事件 |
| `ngx_posted_next_events` | 延迟递送的 next 事件 |

持有 accept 锁的 Worker 在 `ngx_process_events` 返回后，先处理 `ngx_posted_accept_events`（第 255 行），再释放锁（第 258 行），最后处理超时和普通事件。这种设计确保 accept 事件被持有锁的 Worker 快速处理，而普通读写事件可以与其他未持锁的 Worker 并行，因为后者无需 accept 锁即可处理已建立的连接。

处理函数 `ngx_event_process_posted()`（`ngx_event_posted.c` 第 18 行）很简单：循环从队列头部取出事件，标记为未发布，然后调用 `ev->handler(ev)`。

---

#### 四、ngx_epoll_module.c：epoll 的 Nginx 封装

##### 4.1 ngx_epoll_init() ， 初始化

```c
static ngx_int_t
ngx_epoll_init(ngx_cycle_t *cycle, ngx_msec_t timer)
{
    if (ep == -1) {
        ep = epoll_create(cycle->connection_n / 2);
        if (ep == -1) return NGX_ERROR;
        // 可选初始化 eventfd 通知机制
        // 可选初始化 AIO 支持
        // 测试 EPOLLRDHUP 是否可用
    }
    // 分配事件列表数组
    event_list = ngx_alloc(sizeof(struct epoll_event) * epcf->events, ...);
    nevents = epcf->events;

    // 将 epoll 的 actions 注册为全局事件接口
    ngx_event_actions = ngx_epoll_module_ctx.actions;

    // 设置事件标志掩码
    ngx_event_flags = NGX_USE_CLEAR_EVENT
                      | NGX_USE_GREEDY_EVENT
                      | NGX_USE_EPOLL_EVENT;
}
```

关键点：
- `epoll_create()` 的 size 参数传 `cycle->connection_n / 2`，这只是给内核的提示，2.6.8+ 后已忽略。
- `ngx_event_flags` 通过位掩码标记 epoll 的特性：`NGX_USE_CLEAR_EVENT` 表示支持边沿触发（ET）；`NGX_USE_GREEDY_EVENT` 表示 epoll 的贪心行为（需要循环读取直到 EAGAIN）；`NGX_USE_EPOLL_EVENT` 是 epoll 自身的标识。

##### 4.2 ngx_epoll_add_event() / ngx_epoll_del_event()

以添加事件为例（第 578 行）：

```c
c = ev->data;
events = (uint32_t) event;

if (event == NGX_READ_EVENT) {
    e = c->write;
    prev = EPOLLOUT;
    events = EPOLLIN|EPOLLRDHUP;   // 监听可读 + 对端关闭
} else {
    e = c->read;
    prev = EPOLLIN|EPOLLRDHUP;
    events = EPOLLOUT;
}

if (e->active) {
    op = EPOLL_CTL_MOD;   // 另一个方向已注册，用 MOD
    events |= prev;
} else {
    op = EPOLL_CTL_ADD;   // 首次注册
}

ee.events = events | (uint32_t) flags;
ee.data.ptr = (void *) ((uintptr_t) c | ev->instance);

epoll_ctl(ep, op, c->fd, &ee);
ev->active = 1;
```

设计亮点：
- 读/写事件共享同一个 epoll 条目。如果对端方向已激活，就用 `EPOLL_CTL_MOD` 修改，避免重复 ADD。
- `data.ptr` 的低 1 位携带 `instance` 版本号，用于陈旧事件检测。
- **`EPOLLEXCLUSIVE` 标志**：在 `NGX_HAVE_EPOLLEXCLUSIVE` 条件下定义 `NGX_EXCLUSIVE_EVENT`。当 accept 事件加入 epoll 时带有该标志，内核只唤醒一个 Worker，避免惊群（thundering herd）。Nginx 在 `ngx_epoll_add_event` 第 614-618 行做了特殊处理：如果使用了 `NGX_EXCLUSIVE_EVENT`，需要移除 `EPOLLRDHUP`，因为两者在内核中不兼容。

删除事件同理（`ngx_epoll_del_event`，第 642 行）：若 `flags & NGX_CLOSE_EVENT`，说明 fd 即将关闭，epoll 会自动清理，所以只设 `active = 0` 后直接返回，避免不必要的系统调用；否则若对端方向还活跃则用 `EPOLL_CTL_MOD` 移除当前方向，只有两边都关闭时用 `EPOLL_CTL_DEL`。

##### 4.3 ngx_epoll_process_events() ， 核心事件分发

```c
events = epoll_wait(ep, event_list, (int) nevents, timer);

// 更新系统时间
if (flags & NGX_UPDATE_TIME || ngx_event_timer_alarm) {
    ngx_time_update();
}

// 遍历每个就绪事件
for (i = 0; i < events; i++) {
    c = event_list[i].data.ptr;
    instance = (uintptr_t) c & 1;
    c = (ngx_connection_t *) ((uintptr_t) c & (uintptr_t) ~1);

    rev = c->read;

    // 陈旧事件检测：fd 已关闭或 instance 不匹配
    if (c->fd == -1 || rev->instance != instance) {
        continue;
    }

    revents = event_list[i].events;

    // EPOLLERR|EPOLLHUP 强制添加可读可写标志
    if (revents & (EPOLLERR|EPOLLHUP)) {
        revents |= EPOLLIN|EPOLLOUT;
    }

    // 处理可读事件（包括 accept）
    if ((revents & EPOLLIN) && rev->active) {
        rev->ready = 1;
        rev->available = -1;

        if (flags & NGX_POST_EVENTS) {
            queue = rev->accept ? &ngx_posted_accept_events
                                : &ngx_posted_events;
            ngx_post_event(rev, queue);   // 入队
        } else {
            rev->handler(rev);             // 直接回调
        }
    }

    // 处理可写事件
    if ((revents & EPOLLOUT) && wev->active) {
        // 同样的陈旧检测 + 入队或回调
    }
}
```

这段代码体现了 Nginx 事件分发的两个关键设计决策：

1. 陈旧事件过滤：通过 `instance` 位 + `fd == -1` 检测，优雅地处理了"事件已就绪但连接已被关闭"的竞态条件。
2. 后事件入队 vs 直接回调：由 `flags & NGX_POST_EVENTS` 决定。只有持有 accept 锁的 Worker 才需要入队，确保 accept 事件在锁释放前被处理。未持锁的 Worker 直接回调 handler，这些 handler 只处理已建立的连接 I/O，无需锁保护。

---

#### 五、事件标志掩码优化

Nginx 用 `ngx_event_flags` 位掩码标记当前事件驱动的特性，上层函数 `ngx_handle_read_event()` 和 `ngx_handle_write_event()` 据此选择策略：

```c
// ngx_event.h 第 196-230 行
#define NGX_USE_LEVEL_EVENT      0x00000001
#define NGX_USE_ONESHOT_EVENT    0x00000002
#define NGX_USE_CLEAR_EVENT      0x00000004
#define NGX_USE_KQUEUE_EVENT     0x00000008
#define NGX_USE_GREEDY_EVENT     0x00000020
#define NGX_USE_EPOLL_EVENT      0x00000040
```

以 `ngx_handle_read_event` 为例（`ngx_event.c` 第 267 行），若 `ngx_event_flags & NGX_USE_CLEAR_EVENT`（epoll 边沿触发模式），则仅当事件不在活跃状态且未就绪时调用 `ngx_add_event` 注册，否则跳过；若 `NGX_USE_LEVEL_EVENT`（select/poll 水平触发），则还需要在事件就绪后主动删除，避免重复触发。这种按位掩码做分支预测的方式，比函数指针调用更高效，且编译时即可优化。

---

#### 结语

Nginx 的事件驱动核心是一次精巧的**分层抽象**：`ngx_event_actions_t` 提供了平台无关的操作接口，`ngx_process_events_and_timers()` 定义了优雅的事件循环节奏（accept 锁 → 处理 accept 事件 → 释放锁 → 处理超时 → 处理 I/O 事件），而 epoll 模块在底层用 epoll_create/epoll_ctl/epoll_wait 三件套高效驱动着数万连接。加上后事件队列的延迟处理机制和 instance 陈旧事件检测技巧，共同铸就了 Nginx 高并发、低延迟的基石。

**下一篇预告**：Nginx 源码解析（四）：HTTP 请求处理与阶段式架构，深入 Nginx HTTP 模块的 11 个处理阶段，看一个请求从接收到响应完成的全链路源码流程。

---

## 参考

- [1] Nginx 1.24.x 源码：`src/event/ngx_event.h` ， 事件核心结构与接口定义
- [2] Nginx 1.24.x 源码：`src/event/ngx_event.c` ， `ngx_process_events_and_timers()` 事件循环实现
- [3] Nginx 1.24.x 源码：`src/event/modules/ngx_epoll_module.c` ， epoll 模块完整实现
- [4] Nginx 1.24.x 源码：`src/event/ngx_event_posted.h` / `ngx_event_posted.c` ， 后事件队列机制
- [5] Nginx 1.24.x 源码：`src/event/ngx_event_accept.c` ， accept 互斥锁及 `ngx_trylock_accept_mutex`
