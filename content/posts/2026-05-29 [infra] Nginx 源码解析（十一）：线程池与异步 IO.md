+++
title = 'Nginx 源码解析（十一）：线程池与异步 IO'
date = '2026-05-29T17:40:09+08:00'
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'thread-pool', 'async-io', 'c']
+++

Nginx 以事件驱动模型闻名，核心 worker 进程是一个单线程事件循环。但网络 I/O 之外还有一类阻塞操作，磁盘 I/O、gzip 压缩、SSL 握手，它们会卡住整个事件循环。这篇我们从源码角度拆解 Nginx 1.24.x 如何用线程池和异步 I/O 两条路径来解决这个矛盾。

<!--more-->

## 为什么需要线程池

Nginx 的 epoll 事件循环 `ngx_process_events_and_timers()` 是纯异步的，但它只适用于：能在非阻塞模式下完成，且能通过 epoll 回调通知的操作。磁盘 I/O 不满足这个条件，`pread()` 是同步阻塞调用，一旦发起就会挂起调用线程。

更麻烦的是：**你无法在事件循环里同步地读一个大文件**。假设一个请求需要读取 1MB 的静态文件，如果直接在 `ngx_http_output_filter` 里调 `pread()`，整个 worker 进程要等这 1MB 读完才能处理其他请求，，对 20M 并发来说这是灾难。

解决方案有两个：
1. **线程池**：把阻塞操作扔到后台线程，完成后通过 eventfd 通知事件循环
2. **Linux AIO**：内核级异步磁盘 I/O，免去线程调度开销

两者在源码中各自走过不同的路，后来在线程池的实现上合流了。

## 线程池核心结构

线程池的源码在 `src/core/ngx_thread_pool.h` 和 `.c`，总共不到 650 行。

### ngx_thread_pool_t

```c
struct ngx_thread_pool_s {
    ngx_thread_mutex_t        mtx;          // 互斥锁保护任务队列
    ngx_thread_pool_queue_t   queue;        // 待执行任务链表
    ngx_int_t                 waiting;      // 队列中等待的任务数
    ngx_thread_cond_t         cond;         // 条件变量：worker 线程在此等待

    ngx_log_t                *log;

    ngx_str_t                 name;         // 池名称，"default"
    ngx_uint_t                threads;      // worker 线程数，默认 32
    ngx_int_t                 max_queue;    // 最大队列深度，默认 65536

    u_char                   *file;         // 配置来源文件（debug）
    ngx_uint_t                line;
};
```

内部的任务队列 `ngx_thread_pool_queue_t` 是一个侵入式链表：

```c
typedef struct {
    ngx_thread_task_t        *first;
    ngx_thread_task_t       **last;
} ngx_thread_pool_queue_t;
```

`last` 指向 `first` 的地址（或 `next` 指针的地址），这种**双指针尾插**技巧在 Nginx 多处使用，实现 O(1) 追加而不需要遍历。

### ngx_thread_task_t

```c
struct ngx_thread_task_s {
    ngx_thread_task_t   *next;            // 链表指针
    ngx_uint_t           id;              // 递增的任务 ID
    void                *ctx;             // 任务上下文（如文件 I/O 参数）
    void               (*handler)(void *data, ngx_log_t *log);  // 线程执行函数
    ngx_event_t          event;           // 完成后的通知事件
};
```

关键设计：`event` 字段嵌入在 `ngx_thread_task_t` 中，这意味着任务完成后，线程池直接在 `event.handler` 上调用完成回调。整个 `ngx_thread_task_t` 同时在两个链表上存在，，提交时在 `tp->queue` 上等待执行，完成后被移到 `ngx_thread_pool_done` 队列等待事件循环回调。

## 线程池初始化

配置解析。配置文件中的 `thread_pool` 指令由 `ngx_thread_pool()` 解析：

```c
thread_pool default threads=32 max_queue=65536;
```

参数解析后存入 `ngx_thread_pool_t`，然后 `ngx_thread_pool_add()` 将其加入 `ngx_thread_pool_conf_t` 的 `pools` 数组。

初始化发生在 worker 进程启动时：

```c
// ngx_thread_pool_init_worker()
// 在每个 worker 进程启动时被调用
for (i = 0; i < tcf->pools.nelts; i++) {
    if (ngx_thread_pool_init(tpp[i], cycle->log, cycle->pool) != NGX_OK) {
        return NGX_ERROR;
    }
}
```

`ngx_thread_pool_init()` 的核心操作：

```c
static ngx_int_t
ngx_thread_pool_init(ngx_thread_pool_t *tp, ngx_log_t *log, ngx_pool_t *pool)
{
    // 1. 检查事件循环是否支持 ngx_notify（eventfd 机制）
    if (ngx_notify == NULL) {
        ngx_log_error(NGX_LOG_ALERT, log, 0,
               "the configured event method cannot be used with thread pools");
        return NGX_ERROR;
    }

    // 2. 初始化队列、互斥锁、条件变量
    ngx_thread_pool_queue_init(&tp->queue);
    ngx_thread_mutex_create(&tp->mtx, log);
    ngx_thread_cond_create(&tp->cond, log);

    // 3. 创建 N 个 worker 线程，均为 DETACHED 状态
    for (n = 0; n < tp->threads; n++) {
        pthread_create(&tid, &attr, ngx_thread_pool_cycle, tp);
    }
}
```

每个 worker 线程运行 `ngx_thread_pool_cycle()`，这是线程的主体循环。

## 任务提交与线程执行

### 提交任务

```c
ngx_int_t
ngx_thread_task_post(ngx_thread_pool_t *tp, ngx_thread_task_t *task)
{
    // 检查队列深度
    if (tp->waiting >= tp->max_queue) {
        return NGX_ERROR;  // 队列溢出
    }

    task->event.active = 1;
    task->id = ngx_thread_pool_task_id++;
    task->next = NULL;

    // 唤醒一个 worker 线程
    ngx_thread_cond_signal(&tp->cond, tp->log);

    // 插入任务队列尾部
    *tp->queue.last = task;
    tp->queue.last = &task->next;
    tp->waiting++;

    return NGX_OK;
}
```

注意顺序：**先 signal 再入队**。这确保 worker 线程被唤醒后一定能看到新任务（mutex 保护下不会丢失）。

### Worker 线程主循环

```c
static void *
ngx_thread_pool_cycle(void *data)
{
    for ( ;; ) {
        ngx_thread_mutex_lock(&tp->mtx, tp->log);

        tp->waiting--;  // 减少等待计数

        // 队列为空时等待条件变量
        while (tp->queue.first == NULL) {
            ngx_thread_cond_wait(&tp->cond, &tp->mtx, tp->log);
        }

        // 取出队首任务
        task = tp->queue.first;
        tp->queue.first = task->next;

        ngx_thread_mutex_unlock(&tp->mtx, tp->log);

        // 执行任务
        task->handler(task->ctx, tp->log);

        // 任务完成：放入完成队列
        ngx_spinlock(&ngx_thread_pool_done_lock, 1, 2048);
        *ngx_thread_pool_done.last = task;
        ngx_thread_pool_done.last = &task->next;
        ngx_unlock(&ngx_thread_pool_done_lock);

        // 通知事件循环
        (void) ngx_notify(ngx_thread_pool_handler);
    }
}
```

这里出现了完整的**线程→事件循环通信链**：

1. Worker 线程执行完 `task->handler()`
2. 将任务追加到全局完成队列 `ngx_thread_pool_done`
3. 调用 `ngx_notify(ngx_thread_pool_handler)` 唤醒主事件循环

## 完成回调与事件循环整合

`ngx_notify()` 在 epoll 模块中通过 eventfd 实现。`ngx_epoll_notify_init()` 创建一个 eventfd，注册到 epoll 实例：

```c
notify_fd = eventfd(0, 0);
notify_event.handler = ngx_epoll_notify_handler;

ee.events = EPOLLIN|EPOLLET;
epoll_ctl(ep, EPOLL_CTL_ADD, notify_fd, &ee);
```

当 worker 线程调用 `ngx_notify()` 时，向 eventfd 写入 1 个 uint64_t 值。epoll 立刻可读，触发 `ngx_epoll_notify_handler()`，它最终调用我们传进去的 `ngx_thread_pool_handler`。

```c
static void
ngx_thread_pool_handler(ngx_event_t *ev)
{
    ngx_spinlock(&ngx_thread_pool_done_lock, 1, 2048);

    // 摘走整个完成队列
    task = ngx_thread_pool_done.first;
    ngx_thread_pool_done.first = NULL;
    ngx_thread_pool_done.last = &ngx_thread_pool_done.first;

    ngx_unlock(&ngx_thread_pool_done_lock);

    // 遍历完成队列，调用每个任务的 event.handler
    while (task) {
        event = &task->event;
        task = task->next;

        event->complete = 1;
        event->active = 0;
        event->handler(event);
    }
}
```

这个回调是在事件循环上下文中执行的，所以可以安全地访问共享数据、调用 Nginx 内部接口。

## 使用场景：sendfile 的回退路径

线程池最常见的触发场景是 **sendfile + 直接 I/O 的兼容性问题**。

Nginx 发送静态文件时优先使用 `sendfile()`，它直接从内核页缓存拷贝到 socket，完全不经过用户态。但如果文件系统启用了 `directio`，或者文件超过 `sendfile_max_chunk`，`sendfile()` 无法使用，Nginx 需要把文件读到用户态内存再发送。

这个"文件数据拷贝"动作在 `ngx_output_chain_copy_buf()` 中完成：

```c
// ngx_output_chain_copy_buf()
// 当 sendfile 不可用时，需要把文件数据读到内存缓冲区

#if (NGX_HAVE_FILE_AIO)
        if (ctx->aio_handler) {
            n = ngx_file_aio_read(src->file, dst->pos, size, file_pos, pool);
            if (n == NGX_AGAIN) {
                ctx->aio_handler(ctx, src->file);
                return NGX_AGAIN;
            }
        } else
#endif
#if (NGX_THREADS)
        if (ctx->thread_handler) {
            src->file->thread_handler = ctx->thread_handler;
            n = ngx_thread_read(src->file, dst->pos, size, file_pos, pool);
            if (n == NGX_AGAIN) {
                return NGX_AGAIN;
            }
        } else
#endif
        {
            n = ngx_read_file(src->file, dst->pos, size, file_pos);
        }
```

优先级链：`AIO → 线程池 → 同步读`。同步读是最后的回退，会阻塞事件循环。

`ngx_thread_read()` 是线程池文件读取的入口。它复用 `ngx_thread_task_t`，在事件循环返回 `NGX_AGAIN` 表示 I/O 异步进行中：

```c
ssize_t ngx_thread_read(ngx_file_t *file, u_char *buf, size_t size,
    off_t offset, ngx_pool_t *pool)
{
    task = file->thread_task;

    // 如果上次任务已完成，直接返回结果
    if (task->event.complete) {
        task->event.complete = 0;
        return ctx->nbytes;
    }

    // 设置任务参数
    task->handler = ngx_thread_read_handler;
    ctx->fd = file->fd;
    ctx->buf = buf;
    ctx->size = size;
    ctx->offset = offset;

    // 通过 thread_handler 提交到线程池
    file->thread_handler(task, file);

    return NGX_AGAIN;  // 告诉调用者：数据还没准备好
}
```

这里的 `file->thread_handler` 实际上调用 `ngx_thread_task_post()` 将任务提交到线程池。worker 线程中运行的 `ngx_thread_read_handler` 就是简单地调 `pread()` 读文件数据。

## Linux AIO

Linux AIO（`io_submit`/`io_getevents` 系列）是另一种异步 I/O 路径，实现位于 `src/os/unix/ngx_file_aio_read.c`。

初始化在 epoll 模块中完成：

```c
// ngx_epoll_aio_init()
ngx_eventfd = eventfd(0, 0);           // 创建 eventfd
io_setup(epcf->aio_requests, &ngx_aio_ctx);  // 创建 AIO 上下文

// 把 eventfd 注册到 epoll
epoll_ctl(ep, EPOLL_CTL_ADD, ngx_eventfd, &ee);
```

AIO 读取入口：

```c
ssize_t
ngx_file_aio_read(ngx_file_t *file, u_char *buf, size_t size,
    off_t offset, ngx_pool_t *pool)
{
    // 如果上次 AIO 已完成，直接返回结果
    if (ev->complete) {
        ev->complete = 0;
        return aio->nbytes;
    }

    // 构造 aiocb 结构
    aio->aiocb.aio_fildes = file->fd;
    aio->aiocb.aio_offset = offset;
    aio->aiocb.aio_buf = buf;
    aio->aiocb.aio_nbytes = size;

    // 提交异步读
    n = aio_read(&aio->aiocb);
    // ...
    return NGX_AGAIN;
}
```

AIO 完成事件通过 eventfd 通知。`ngx_epoll_eventfd_handler()` 在 epoll 事件循环中被调用：

```c
static void
ngx_epoll_eventfd_handler(ngx_event_t *ev)
{
    n = read(ngx_eventfd, &ready, 8);   // 读取 eventfd 计数

    ts.tv_sec = 0;
    ts.tv_nsec = 0;

    while (ready) {
        // 从 AIO 完成队列收完成事件
        events = io_getevents(ngx_aio_ctx, 1, 64, event, &ts);

        for (i = 0; i < events; i++) {
            e = (ngx_event_t *) (uintptr_t) event[i].data;
            e->complete = 1;
            e->ready = 1;

            aio = e->data;
            aio->res = event[i].res;    // 读取结果

            // 投递到 posted_events 队列，在事件循环中处理
            ngx_post_event(e, &ngx_posted_events);
        }
    }
}
```

这里 `event[i].data` 存的是 `ngx_event_t *` 指针，这个指针在发起 AIO 时通过 `aio_sigevent.sigev_value.sival_ptr` 设置。

## 线程池与主事件循环的完整协调

一条完整的任务生命周期：

```
事件循环                              Worker 线程
    │                                     │
    ├─ ngx_output_chain_copy_buf()        │
    │   └─ ngx_thread_read()              │
    │       └─ file->thread_handler()     │
    │           └─ ngx_thread_task_post()─┼──→ 加锁，入队，signal
    │                                     │
    │ 返回 NGX_AGAIN                      │
    │                                     ├─ 被唤醒，取任务
    │                                     ├─ pread() 读取文件
    │                                     ├─ 放入完成队列
    │                                     ├─ eventfd 写 8 字节
    │                                     │
    ├─ epoll_wait() 返回                  │
    │   └─ ngx_epoll_notify_handler()     │
    │       └─ ngx_thread_pool_handler()  │
    │           └─ event->handler(event)  │
    │               └─ thread_handler()   │
    │                   └─ 继续发送数据   │
```

关键观察：Nginx 的线程池不是传统的"主线程 submit + 后台线程 execute"这么简单。它是一个**异步反馈系统**，—任务提交时返回 `NGX_AGAIN`，事件循环继续处理其他事件，worker 线程完成后通过 eventfd 回调重新进入事件循环，再调用完成回调。整个过程不阻塞事件循环的任一 tick。

## 总结

Nginx 的线程池和 AIO 两条路径解决同一个问题：**在单线程事件循环中执行阻塞 I/O 而不阻塞事件循环**。

- **线程池**（`ngx_thread_pool.c`）：通用方案，适用于任何阻塞操作（磁盘读、gzip、SSL）。通过 pthread worker 线程执行任务，eventfd 完成通知，约 650 行代码实现。
- **Linux AIO**（`ngx_file_aio_read.c`）：内核级方案，仅适用于磁盘 I/O。需要文件系统支持，且 io_setup 有上下文数量限制（`/proc/sys/fs/aio-max-nr`）。
- **sendfile 回退路径**：线程池最常见的触发场景。当 sendfile 无法使用时（directio、sendfile_max_chunk 限制），Nginx 通过线程池将文件数据读到内存。

生产环境中，线程池是默认启用的后备方案。配置文件中可以这样调优：

```nginx
thread_pool default threads=16 max_queue=65536;
```

选择 16 个线程而不是默认 32 个通常已经足够，—线程数过多导致上下文切换成本超过磁盘并行收益。`aio_requests` 在 epoll 配置中设置，默认为 32。

源码虽然只有几百行，但这个"任务提交 → 线程执行 → eventfd 写 → 事件循环回调"的异步反馈模式，是 Nginx 在纯事件驱动和阻塞 I/O 之间搭起的一座精妙的桥。

---

**下一篇预告：** Nginx 源码解析（十二）：SSL 与加密优化，深入 OpenSSL 异步引擎、session cache 和硬件加速路径。

## 参考

- Nginx 1.24.x 源码: `src/core/ngx_thread_pool.h`, `src/core/ngx_thread_pool.c`
- `src/os/unix/ngx_file_aio_read.c` ， Linux AIO 实现
- `src/event/modules/ngx_epoll_module.c` ， eventfd 通知机制
- `src/core/ngx_output_chain.c` ， sendfile 回退路径与线程池集成
- `src/os/unix/ngx_files.c` ， ngx_thread_read() 实现
- `src/event/ngx_event_pipe.c` ， 管道 I/O 中的线程池使用
