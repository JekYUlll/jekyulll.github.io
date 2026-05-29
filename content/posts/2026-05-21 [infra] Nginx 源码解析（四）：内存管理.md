+++
date = '2026-05-21T12:00:00+08:00'
weight = 9
draft = false
title = 'Nginx 源码解析（四）：内存管理'
author = 'JekYUlll'
lastmod = '2026-05-21T12:00:00+08:00'
tags = ['nginx-source', 'nginx', 'memory-management', 'c']
categories = ['infra']
+++

## 为什么 Nginx 不用 malloc/free

传统 Web 服务器处理每个请求时，需要分配大量零散的小内存，，解析 HTTP 头、构建缓冲区、管理 upstream 连接……如果每个分配都走 malloc/free，至少有这些问题：

1. 系统调用开销：malloc 内部走 brk/mmap，每次分配都涉及用户态/内核态切换。
2. 内存碎片：大量大小不一、生命周期交织的小分配，堆碎片化后即使总空闲空间足够，也无法满足连续分配。
3. 性能抖动：free 后的内存归还给系统或被 glibc 缓存，下次请求又得重新系统调用。

Nginx 的思路很简单：**一次分配一批，用户态管理释放**。每个 HTTP 请求绑定一个 `ngx_pool_t`（内存池），请求内部的所有分配都在这个池子里走指针挪动，请求结束时一次把整个池子归还。全程没有 per-allocation 的系统调用，只需在创建池子和扩容时各调一次 malloc。

这也是 Nginx 高并发低延迟的底层基石之一。下面从源码拆解这套机制。

---

## ngx_pool_t 结构

`ngx_pool_t` 定义在 `src/core/ngx_palloc.h`，是 Nginx 内存池的核心数据结构：

```c
struct ngx_pool_s {
    ngx_pool_data_t    d;        // 小内存分配状态
    size_t             max;      // 大小阈值，决定走小内存还是大内存路径
    ngx_pool_t        *current;  // 当前可用的 pool 块
    ngx_chain_t       *chain;    // 缓冲区链表（与 filter 模块相关）
    ngx_pool_large_t  *large;    // 大内存链表
    ngx_pool_cleanup_t *cleanup; // 清理回调链表
    ngx_log_t         *log;      // 日志
};
```

其中 `d` 是实际分配指针的追踪器：

```c
typedef struct {
    u_char      *last;    // 当前空闲起始位置
    u_char      *end;     // 本块末尾
    ngx_pool_t  *next;    // 下一个 pool 块
    ngx_uint_t   failed;  // 分配失败的次数
} ngx_pool_data_t;
```

整个池子是一个**单向链表**。`current` 指向当前正在用的那个块，`last` 指向块内空闲空间的起始地址，`end` 指向块末尾。分配小内存时，直接把 `last` 往前推，返回原来的 `last` 位置，，这就是 bump pointer allocator。

大内存走另外一条路。`large` 字段指向 `ngx_pool_large_t` 链表，每个节点通过 `malloc` 独立分配，但节点本身（`sizeof(ngx_pool_large_t)`）还是从池子的小内存路径取出来的。

`cleanup` 是清理回调链表。比如文件描述符关闭、临时文件删除等操作，通过 `ngx_pool_cleanup_add()` 挂上 handler，池子销毁时自动执行。

---

## 池子创建：ngx_create_pool

```c
ngx_pool_t *
ngx_create_pool(size_t size, ngx_log_t *log)
{
    ngx_pool_t  *p;

    p = ngx_memalign(NGX_POOL_ALIGNMENT, size, log);
    if (p == NULL) {
        return NULL;
    }

    p->d.last  = (u_char *) p + sizeof(ngx_pool_t);
    p->d.end   = (u_char *) p + size;
    p->d.next  = NULL;
    p->d.failed = 0;

    size = size - sizeof(ngx_pool_t);
    p->max = (size < NGX_MAX_ALLOC_FROM_POOL) ? size : NGX_MAX_ALLOC_FROM_POOL;

    p->current = p;
    p->chain   = NULL;
    p->large   = NULL;
    p->cleanup = NULL;
    p->log     = log;

    return p;
}
```

关键点：

- `ngx_memalign` 按 16 字节对齐分配，保证 `ngx_pool_t` 头部的对齐要求。
- `d.last` 初始化为结构体末尾，即第一个可用内存的起始地址。
- `d.end` 指向整块内存的末尾。
- `max` 的计算：Nginx 默认 `NGX_DEFAULT_POOL_SIZE` 是 **16KB**。减去 `sizeof(ngx_pool_t)`（约 40~48 字节），剩余约 16336 字节。`NGX_MAX_ALLOC_FROM_POOL` 是 `ngx_pagesize - 1`（通常为 4095）。取较小值，所以 `max` 通常是 4095。超过这个大小的分配走大内存路径。

---

## 分配路径

Nginx 提供三个主要分配接口：

```c
void *ngx_palloc(ngx_pool_t *pool, size_t size);   // 对齐分配
void *ngx_pnalloc(ngx_pool_t *pool, size_t size);  // 不对齐分配
void *ngx_pcalloc(ngx_pool_t *pool, size_t size);  // 对齐 + 清零
```

它们的分发逻辑完全一致，，通过 `pool->max` 分流：

```c
void *ngx_palloc(ngx_pool_t *pool, size_t size) {
    if (size <= pool->max) {
        return ngx_palloc_small(pool, size, 1);  // 对齐
    }
    return ngx_palloc_large(pool, size);
}
```

`ngx_pnalloc` 的区别只是传入 `align = 0`。`ngx_pcalloc` 在 `ngx_palloc` 之上调用 `ngx_memzero` 清零。

### 小内存路径：ngx_palloc_small

```c
static ngx_inline void *
ngx_palloc_small(ngx_pool_t *pool, size_t size, ngx_uint_t align)
{
    u_char      *m;
    ngx_pool_t  *p;

    p = pool->current;

    do {
        m = p->d.last;
        if (align) {
            m = ngx_align_ptr(m, NGX_ALIGNMENT);
        }
        if ((size_t) (p->d.end - m) >= size) {
            p->d.last = m + size;
            return m;
        }
        p = p->d.next;
    } while (p);

    return ngx_palloc_block(pool, size);
}
```

从 `current` 开始遍历链表中的 pool 块，检查 `end - last >= size`。如果当前块空间足够，直接移动 `last` 指针返回。否则继续找下一个块。都找完了？调用 `ngx_palloc_block` 分配新块。

### 扩容：ngx_palloc_block

```c
static void *
ngx_palloc_block(ngx_pool_t *pool, size_t size)
{
    psize = (size_t) (pool->d.end - (u_char *) pool);
    m = ngx_memalign(NGX_POOL_ALIGNMENT, psize, pool->log);

    new = (ngx_pool_t *) m;
    new->d.end = m + psize;
    new->d.next = NULL;
    new->d.failed = 0;

    m += sizeof(ngx_pool_data_t);
    m = ngx_align_ptr(m, NGX_ALIGNMENT);
    new->d.last = m + size;

    for (p = pool->current; p->d.next; p = p->d.next) {
        if (p->d.failed++ > 4) {
            pool->current = p->d.next;
        }
    }
    p->d.next = new;
    return m;
}
```

几个有意思的细节：

- 新块大小 = 原始 pool 块大小（`psize`），而不是 size 本身。这意味着所有块大小一致。
- 新块头部只用了 `ngx_pool_data_t`（16 字节），而不是完整的 `ngx_pool_t`。因为只有链表管理需要，`max/large/cleanup` 等字段只在首块有意义。
- **failed 计数器**：每个块如果被遍历到但分配失败，`failed++`。超过 4 次之后，`pool->current` 跳过后面的块，，老的块频繁失败说明基本耗尽了，不再浪费遍历时间。这是一种自适应的 current 推进。

### 大内存路径：ngx_palloc_large

```c
static void *
ngx_palloc_large(ngx_pool_t *pool, size_t size)
{
    p = ngx_alloc(size, pool->log);
    // 先遍历 large 链表，找 alloc==NULL 的空槽复用
    for (large = pool->large; large; large = large->next) {
        if (large->alloc == NULL) {
            large->alloc = p;
            return p;
        }
        if (n++ > 3) break;
    }
    // 没有空槽则从池子分配一个 large 节点
    large = ngx_palloc_small(pool, sizeof(ngx_pool_large_t), 1);
    large->alloc = p;
    large->next = pool->large;
    pool->large = large;
    return p;
}
```

大内存走 `ngx_alloc()`（即 `malloc`）独立分配，但 `ngx_pool_large_t` 节点本身从池子的小内存路径分配。注意 `n > 3` 的限制，，large 链表只遍历前 4 个节点找空槽，没找到就头插新节点。这是 Nginx 常用的"部分遍历"优化，避免大链表拖慢分配。

---

## 释放

### ngx_pfree ， 只释放大内存

```c
ngx_int_t ngx_pfree(ngx_pool_t *pool, void *p) {
    for (l = pool->large; l; l = l->next) {
        if (p == l->alloc) {
            ngx_free(l->alloc);
            l->alloc = NULL;   // 标记为空槽
            return NGX_OK;
        }
    }
    return NGX_DECLINED;
}
```

`ngx_pfree` 只能释放大内存块。遍历 large 链表，找到后 `free` 并将 `alloc` 置 NULL，后续分配可以复用这个槽。小内存不能在请求中期单独释放，—它们只能随池子整体销毁。

### ngx_destroy_pool ， 一次性释放

```c
void ngx_destroy_pool(ngx_pool_t *pool) {
    // 1. 遍历 cleanup 链表，执行所有回调
    for (c = pool->cleanup; c; c = c->next) {
        if (c->handler) {
            c->handler(c->data);
        }
    }
    // 2. 释放大内存链表
    for (l = pool->large; l; l = l->next) {
        if (l->alloc) ngx_free(l->alloc);
    }
    // 3. 释放所有 pool 块（包括首块）
    for (p = pool, n = pool->d.next; ; p = n, n = n->d.next) {
        ngx_free(p);
        if (n == NULL) break;
    }
}
```

销毁分三步：执行清理 → 释放大块 → 释放所有 pool 块。小内存不需要逐块 free，因为整个池子是一整块 mmap/malloc 区域，`ngx_free(p)` 一次释放即可。

### ngx_reset_pool ， 复用重置

```c
void ngx_reset_pool(ngx_pool_t *pool) {
    // 释放所有大内存
    for (l = pool->large; l; l = l->next) {
        if (l->alloc) ngx_free(l->alloc);
    }
    // 重置所有 pool 块：last 回退到块头
    for (p = pool; p; p = p->d.next) {
        p->d.last = (u_char *) p + sizeof(ngx_pool_t);
        p->d.failed = 0;
    }
    pool->current = pool;
    pool->chain = NULL;
    pool->large = NULL;
}
```

`ngx_reset_pool` 不释放 pool 块本身，只把 `last` 指针复位。典型场景：keepalive 连接上的多个请求，每个请求结束时 reset pool，复用同一块内存池，避免反复创建销毁。

---

## 与 HTTP 请求生命周期的绑定

Nginx 的每个 HTTP 请求在 `ngx_http_init_request` 时调用 `ngx_create_pool(4096, ...)` 创建池子。请求处理全程，，解析请求行、解析请求头、读取 body、生成响应——所有内存分配都从这个池子走。请求结束时 `ngx_destroy_pool` 一次回收。

这种设计的好处：

- **零泄漏风险**：只要没忘记 destroy pool，内部分配绝不会泄漏。开发者不需要追踪每个 malloc/free 的对齐。
- **确定性释放**：没有 GC 停顿，没有引用计数开销，请求结束即释放。
- 极致性能：小分配只是指针加减，连锁都不用（单线程处理单个请求）。

---

## Slab 分配器：共享内存场景

除了 per-request 的内存池，Nginx 还有一个独立的内存管理子系统，—**slab 分配器**，用于管理共享内存。共享内存在多进程架构（master + workers）中用于进程间通信，比如共享字典（`ngx_http_shm_zone`）、限流计数器等场景。

`ngx_slab_pool_t` 定义在 `src/core/ngx_slab.h`：

```c
typedef struct {
    ngx_shmtx_sh_t    lock;       // 自旋锁
    size_t            min_size;
    size_t            min_shift;
    ngx_slab_page_t  *pages;
    ngx_slab_page_t  *last;
    ngx_slab_page_t   free;
    ngx_slab_stat_t  *stats;
    ngx_uint_t        pfree;
    u_char           *start;
    u_char           *end;
    ngx_shmtx_t       mutex;
    // ...
} ngx_slab_pool_t;
```

Slab 的核心思想：**将大块连续内存按 2 的幂次划分为不同大小的槽（slot）**。分配时根据 size 找到对应的槽，从槽的空闲链表取一块。释放时归还到对应槽。

Nginx slab 的槽分类：

- **small**：size < `ngx_slab_exact_size`（通常 < 512 字节），一个页面内放多个对象，用 bitmap 追踪
- **exact**：size == `ngx_slab_exact_size`，刚好一个页面塞满，bitmap 完整覆盖
- **big**：size > `ngx_slab_exact_size` 但 < `ngx_pagesize / 2`，对象数 < bitmap 位数
- **page**：size >= `ngx_pagesize / 2`，直接分配整页

接口签名很直观：

```c
void *ngx_slab_alloc(ngx_slab_pool_t *pool, size_t size);
void *ngx_slab_calloc(ngx_slab_pool_t *pool, size_t size);
void  ngx_slab_free(ngx_slab_pool_t *pool, void *p);
// _locked 变体由调用方持有锁时使用
void *ngx_slab_alloc_locked(ngx_slab_pool_t *pool, size_t size);
void  ngx_slab_free_locked(ngx_slab_pool_t *pool, void *p);
```

`ngx_slab_alloc` 内部先加锁，再调 `ngx_slab_alloc_locked`，最后解锁。`_locked` 变体适合调用方已经持锁的批量操作场景，减少锁竞争。

与 per-request 内存池不同，slab 分配器是进程间共享的，所有 worker 进程通过同一个 mmap 区域操作同一块内存，因此需要加锁（自旋锁 + atomic 操作）。slab 分配器的内存来自 Nginx 启动时预先 mmap 的共享内存段，大小在配置中指定（如 `zone name=xxx size=10m`）。

---

## 总结

Nginx 的内存管理分两层：

| 层面 | 分配器 | 适用范围 | 释放策略 | 是否需要锁 |
|------|--------|----------|----------|------------|
| 请求级 | `ngx_pool_t`（bump pointer + large 链表） | 单个 HTTP 请求内的所有分配 | 请求结束一次释放 | 不需要（单线程处理请求） |
| 进程间 | `ngx_slab_pool_t`（slab 分配器） | 共享内存区（字典、计数器等） | 按需 free | 需要（自旋锁 + atomic） |

内存池让 Nginx 避免了对 malloc 的频繁调用，slab 让共享内存的管理高效且紧凑。两套机制配合，支撑了 Nginx 在数十万并发连接下的稳定运行。

---

## 下一篇预告

第五篇将分析 Nginx 的事件驱动核心，—epoll 事件模块，包括 `ngx_epoll_module` 的初始化、事件添加/修改/删除、以及 `ngx_process_events_and_timers` 的主循环调度。结合源码讲清楚 Nginx 如何用 epoll + 非阻塞 I/O 实现高并发。

---

## 参考

- Nginx 1.24.x 源码：`src/core/ngx_palloc.h`、`src/core/ngx_palloc.c`
- Nginx 1.24.x 源码：`src/core/ngx_slab.h`、`src/core/ngx_slab.c`
- [Nginx Development Guide](https://nginx.org/en/docs/dev/development_guide.html) ， 官方开发指南
- Nginx 内存池设计分析，Evan Jones 著：《Nginx memory pool implementation》
