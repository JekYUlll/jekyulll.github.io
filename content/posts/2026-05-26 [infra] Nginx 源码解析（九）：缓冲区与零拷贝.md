+++
title = 'Nginx 源码解析（九）：缓冲区与零拷贝'
date = '2026-05-26T12:00:00+08:00'
lastmod = '2026-05-26T12:00:00+08:00'
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'i-o', 'buffer', 'c']
+++

响应 1000 并发静态文件请求时，单进程 CPU 都花在哪？大部分在拷贝上：磁盘→内核→用户→内核→网卡。每一跳都是宝贵的内存带宽和 CPU 缓存污染。Nginx 用两类手段解决：一是 `sendfile` 零拷贝，完全绕过用户态；二是在必须拷贝的地方，用 `ngx_buf_t` / `ngx_chain_t` 这套设计把拷贝量压到最低，且让 buffer 在 filter 链中复用。

<!--more-->

## ngx_buf_t：一切数据的统一视图

Nginx 里所有数据，，无论是内存里的字符串、映射文件、还是磁盘文件，，都被抽象为 `ngx_buf_t`。定义在 `src/core/ngx_buf.h`：

```c
struct ngx_buf_s {
    u_char          *pos;
    u_char          *last;
    off_t            file_pos;
    off_t            file_last;

    u_char          *start;         /* start of buffer */
    u_char          *end;           /* end of buffer */
    ngx_buf_tag_t    tag;
    ngx_file_t      *file;
    ngx_buf_t       *shadow;

    unsigned         temporary:1;
    unsigned         memory:1;
    unsigned         mmap:1;
    unsigned         recycled:1;
    unsigned         in_file:1;
    unsigned         flush:1;
    unsigned         sync:1;
    unsigned         last_buf:1;
    unsigned         last_in_chain:1;
    unsigned         last_shadow:1;
    unsigned         temp_file:1;
};
```

`pos`/`last` 指向内存区间，`file_pos`/`file_last` 是文件偏移量。`start`/`end` 标记整块 buffer 的可写范围，而 `pos`/`last` 是当前有效数据的边界，，这是典型的"消费指针"设计。

三个标志位描述数据来源：

- `temporary` ， 数据在分配的堆内存中，filter 可以修改它。
- `memory` ， 数据在只读内存（比如常量字符串），不能修改。
- `mmap` ， 数据是 mmap 映射的，同样不能修改。

`in_file` 表示数据直接在磁盘文件中，配合 `file` 指针和文件偏移使用。

辅助宏 `ngx_buf_size()` 用来统一获取数据长度：

```c
#define ngx_buf_size(b) \
    (ngx_buf_in_memory(b) ? (off_t) ((b)->last - (b)->pos):  \
                            ((b)->file_last - (b)->file_pos))
```

`ngx_buf_special()` 判断是不是控制类 buf（flush、last_buf、sync 等），不含实际数据。这样 filter 在处理链时，遇到特殊 buf 就直接透传。

## ngx_chain_t：链式组织

单个 buf 不够，Nginx 用一个单向链表串起多个 buf：

```c
struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};
```

简单到极致。所有输出、输入、filter 之间的数据传递都靠这个结构。分配一个 `ngx_chain_t` 链接头用 `ngx_alloc_chain_link()`，它会优先从 pool 的空闲链表取，避免重复 malloc。

`ngx_chain_coalesce_file()` 是专门为 sendfile 优化的：当连续多个 chain 节点的 `buf->in_file` 为真且属于同一个文件描述符时，将它们合并为一次 sendfile 调用，减少系统调用次数。

## tag 机制：buffer 所有权追踪

`ngx_buf_t.tag` 是一个 `void *` 指针，通常指向分配该 buffer 的模块上下文（比如 `ngx_http_output_filter`）。它的核心用途在 `ngx_chain_update_chains()` 中：

```c
while (*busy) {
    cl = *busy;
    if (cl->buf->tag != tag) {
        *busy = cl->next;
        ngx_free_chain(p, cl);
        continue;
    }
    if (ngx_buf_size(cl->buf) != 0) break;
    /* reset and move to free list */
    cl->buf->pos = cl->buf->start;
    cl->buf->last = cl->buf->start;
    *busy = cl->next;
    cl->next = *free;
    *free = cl;
}
```

这里 `tag` 作为"谁的孩子谁回收"的令牌。当 busy 链表中的 buf 被消费完（数据已发送），只有 tag 匹配的 buf 会被重置并放回 free 链表以备复用。不匹配的 buf 直接被释放，，这通常意味着它来自另一个模块的临时分配，不能复用。

整个 buffer 池的管理通过 free/busy/out 三链表协作，数据只在三链间流动，尽量减少分配和释放。

## ngx_output_chain()：输出链的分帧器

`ngx_output_chain()` 是 Nginx 输出路径上的核心函数。它的输入是 filter 链传下来的 `ngx_chain_t`，输出经过分帧、对齐、可能拷贝后传给 `output_filter`（通常是 `ngx_chain_writer` 或 `ngx_http_output_filter`）。

```c
ngx_int_t ngx_output_chain(ngx_output_chain_ctx_t *ctx, ngx_chain_t *in);
```

它有一个"short path"优化：如果 `ctx->in` 和 `ctx->busy` 都为空，且传入的 chain 只有一个 buf 且不需要拷贝，就直接调用 `output_filter` 透传。否则进入主循环：

1. 检查每个输入 buf，`ngx_output_chain_as_is()` 判断是透传还是需要拷贝。
2. 如果不需要拷贝（比如 sendfile 启用的 in_file buf），直接转移到输出链。
3. 如果需要拷贝，先尝试对齐文件 buf（`ngx_output_chain_align_file_buf`），再从 free 链或新分配获取目标 buf，调用 `ngx_output_chain_copy_buf()` 执行实际拷贝。
4. 组装好的输出链交给 `output_filter`。
5. 调用 `ngx_chain_update_chains()` 回收已发送的 buf。

`ngx_output_chain_as_is()` 决定一个 buf 能否直接送出的逻辑相当精细：

```c
static ngx_inline ngx_int_t
ngx_output_chain_as_is(ngx_output_chain_ctx_t *ctx, ngx_buf_t *buf)
{
    sendfile = ctx->sendfile;
    if (buf->in_file && buf->file->directio) {
        sendfile = 0;  /* disable sendfile under directio */
    }
    if (!sendfile && !ngx_buf_in_memory(buf)) return 0;
    if (ctx->need_in_memory && !ngx_buf_in_memory(buf)) return 0;
    if (ctx->need_in_temp && (buf->memory || buf->mmap)) return 0;
    return 1;
}
```

`need_in_memory` 和 `need_in_temp` 是上游模块设给下游的约束。比如 SSL 模块要求所有数据在内存中（它要加解密），而 upstream 临时存储可能要求数据在可写的 temp buf 中。

## ngx_chain_writer()：链式写 socket

`ngx_chain_writer()` 是 `output_filter` 的一个实现，用于 upstream 场景。它把输出链串起来，最后调用 `c->send_chain()` 发出去。

```c
for (size = 0; in; in = in->next) {
    size += ngx_buf_size(in->buf);
    cl = ngx_alloc_chain_link(ctx->pool);
    cl->buf = in->buf;
    *ctx->last = cl;
    ctx->last = &cl->next;
}
chain = c->send_chain(c, ctx->out, ctx->limit);
```

`c->send_chain` 是一个函数指针，在 Linux 上指向 `ngx_linux_sendfile_chain()`。这也是零拷贝的真正入口。

## ngx_linux_sendfile_chain()：零拷贝实现

`ngx_linux_sendfile_chain()` 位于 `src/os/unix/ngx_linux_sendfile_chain.c`，核心循环做了三件事：

1. **收集 header**：调用 `ngx_output_chain_to_iovec()` 将内存中的前导数据（比如 HTTP 响应头）打包成 `struct iovec` 数组，准备用 `writev` 发送。
2. **设置 TCP_CORK**：如果 header 后面紧跟文件数据，就把 TCP_CORK 打开，避免 TCP 报文碎片。
3. **发送文件主体**：获取文件 buf，调用 `ngx_linux_sendfile()` 触发 `sendfile` 系统调用。

```c
n = sendfile(c->fd, file->file->fd, &offset, size);
```

这个系统调用是零拷贝的关键：`sendfile(out_fd, in_fd, offset, count)` 直接把内核页缓存中的数据拷贝到 socket 缓冲区，**完全绕过用户空间**。数据路径是：

```
磁盘 → 内核页缓存 → socket 缓冲区 → 网卡
```

对比传统的 read + write：

```
磁盘 → 内核页缓存 → 用户缓冲区 → 内核 socket 缓冲区 → 网卡
```

少了两次数据拷贝（用户态读/写）和四次上下文切换。

`sendfile` 返回 0 表示文件被截断，返回 -1 根据 errno 决定是重试（EAGAIN/EINTR）还是报错。Nginx 还限制单次 sendfile 大小在 2GB - page_size（`NGX_SENDFILE_MAXSIZE`），这是 Linux 内核对 sendfile 的隐式限制。

## direct I/O 与缓冲区对齐

当文件启用 `O_DIRECT`（direct I/O）时，事情变得复杂。Linux 要求 direct I/O 的读写缓冲区必须对齐到文件系统扇区边界（通常是 512 字节，XFS 上可能是 4096 字节），否则 `sendfile` 直接返回 EINVAL。

`ngx_output_chain_align_file_buf()` 处理这个对齐：

```c
size = (size_t) (in->file_pos - (in->file_pos & ~(ctx->alignment - 1)));
if (size == 0 && bsize >= ctx->bufs.size) {
    return NGX_DECLINED;  /* already aligned */
}
```

如果文件偏移未对齐，它会分配一个对齐的临时 buf，读取未对齐的部分填充，—这被称为"撕裂"（tearing），确实引入了一次额外拷贝，但只在开头和结尾。

`ngx_output_chain_get_buf()` 分配 buf 时，如果检测到 directio 模式，会调用 `ngx_pmemalign()` 分配对齐内存：

```c
if (ctx->directio) {
    b->start = ngx_pmemalign(ctx->pool, size, (size_t) ctx->alignment);
}
```

`ngx_output_chain_copy_buf()` 在 directio + 未对齐的情况下还会临时关闭文件的 O_DIRECT 标志完成读取后再恢复，—这些细节保证了零拷贝和 direct I/O 可以配合使用。

## 上游响应与临时文件缓冲

当上游服务器响应体超过 `proxy_temp_file_write_size`（默认 8KB）或缓冲区满时，Nginx 会溢出到临时文件。调用了 `ngx_write_chain_to_temp_file()`：

```c
ssize_t
ngx_write_chain_to_temp_file(ngx_temp_file_t *tf, ngx_chain_t *chain)
{
    if (tf->file.fd == NGX_INVALID_FILE) {
        if (ngx_create_temp_file(...) != NGX_OK) return rc;
    }
    return ngx_write_chain_to_file(&tf->file, chain, tf->offset, tf->pool);
}
```

之后从临时文件再次读出的数据，在 `ngx_output_chain()` 中会走 sendfile 路径，—前提是 `sendfile` 和 `directio` 配置得当。这里也是 tag 机制发挥作用的地方：临时文件 buf 的 tag 来自 upstream 模块，和后续 filter 产生的 buf tag 不同，回收时能正确区分。

## filter 链中的 buf 复用

Nginx filter 模块可以修改、替换或丢弃 buf，但尽量不产生新的 buf 分配。最典型的例子是 `ngx_http_copy_filter_module`：它在 filter 链中负责调用 `ngx_output_chain()`，如果上游来的数据已经在内存中且无需对齐，就直接把 `ngx_chain_t` 节点的 `buf` 指针传给下一个 filter，不做任何数据拷贝。

甚至在 `ngx_http_output_filter` 内部，`ngx_writev` 写完后调用 `ngx_chain_update_sent()` 只移动指针，不释放 buf：

```c
if (ngx_buf_in_memory(in->buf)) {
    in->buf->pos = in->buf->last;
}
if (in->buf->in_file) {
    in->buf->file_pos = in->buf->file_last;
}
```

配合 `ngx_chain_update_chains()` 的 tag 匹配机制，已发送的 buf 被归还到 free 链，下一个请求的同类 filter 直接复用，无需再 malloc/free 内存。

## 总结

Nginx 的缓冲系统可以概括为：**一个抽象（ngx_buf_t），两种路径（sendfile / buffer copy）**。sendfile 是零拷路径，适用于静态文件，CPU 占用几乎为零；buffer copy 路径在必要时使用，但通过 buf 池、tag 回收、filter 链指针传递等手段，把不得不做的拷贝降到最少。

tag 机制是整个 pool 的灵魂，—它让不同模块的 buf 在同一个 busy 链上和平共处，谁的孩子谁回收，不需要全局 GC 或引用计数。

## 参考

- Nginx 1.24.x 源码：`src/core/ngx_buf.h`、`src/core/ngx_buf.c`
- `src/core/ngx_output_chain.c` ， 输出链分帧与对齐
- `src/os/unix/ngx_linux_sendfile_chain.c` ， sendfile 零拷贝实现
- `src/core/ngx_file.c` ， `ngx_write_chain_to_temp_file`
- Linux man page：`sendfile(2)`、`open(2)` O_DIRECT

## 下一篇预告

（十）：Filter 链与响应体处理。讲一整个 response body 从 upstream 到 socket 的 filter 链：copy filter、dechunk filter、gzip filter、header filter，以及 `ngx_http_output_filter()` 如何驱动整条链。
