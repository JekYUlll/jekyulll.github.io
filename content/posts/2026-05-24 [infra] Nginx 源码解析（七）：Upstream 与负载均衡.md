+++
title = 'Nginx 源码解析（七）：Upstream 与负载均衡'
date = '2026-05-24T12:00:00+08:00'
lastmod = '2026-05-24T12:00:00+08:00'
weight = 6
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'upstream', 'c']
+++

Nginx 反向代理的核心是 upstream 模块。它负责从上游服务器池中选出一个 peer，建立 TCP 连接，转发请求，接收响应，然后回传给客户端。整个流程涉及连接管理、超时控制、负载均衡调度和事件驱动的 I/O 管理。

<!--more-->

## Upstream 核心结构

反向代理的入口是 `ngx_http_upstream_t`，定义在 `ngx_http_upstream.h` 第 321 行：

```c
struct ngx_http_upstream_s {
    ngx_http_upstream_handler_pt  read_event_handler;
    ngx_http_upstream_handler_pt  write_event_handler;

    ngx_peer_connection_t         peer;

    ngx_event_pipe_t             *pipe;          // body 管道（缓冲模式用）

    ngx_chain_t                  *request_bufs;  // 待发送的请求数据

    ngx_http_upstream_conf_t     *conf;          // 运行时配置
    ngx_http_upstream_srv_conf_t *upstream;      // 上游服务器组配置

    ngx_http_upstream_resolved_t *resolved;      // DNS 解析结果

    ngx_buf_t                     buffer;        // 上游响应读缓冲

    // 模块回调：每个协议模块（proxy/fastcgi/scgi/uwsgi）实现
    ngx_int_t (*create_request)(ngx_http_request_t *r);
    ngx_int_t (*process_header)(ngx_http_request_t *r);
    void      (*finalize_request)(ngx_http_request_t *r, ngx_int_t rc);

    ngx_uint_t  buffering:1;   // 启用缓冲模式
    ngx_uint_t  keepalive:1;   // 上游 keepalive
    ngx_uint_t  request_sent:1;// 请求是否已发出
    ngx_uint_t  header_sent:1; // 响应头是否已发给客户端
};
```

几个关键字段：

- `read_event_handler` / `write_event_handler`：这一对函数指针构成了 upstream 事件驱动的核心。它们随着请求生命周期不断切换。
- `peer`：`ngx_peer_connection_t` 类型，封装了上游的 socket 连接以及 `get`/`free` 回调函数指针。
- `conf`：指向 `ngx_http_upstream_conf_t`，包含所有超时和缓冲配置。
- `resolved`：当上游地址由运行时 DNS 动态解析时使用。`upstream` 则是配置阶段定义的服务器组。
- `create_request` / `process_header` / `finalize_request`：协议模块通过这三个回调实现差异化的请求构建和响应解析。

`ngx_http_upstream_conf_t`（第 149 行）包含了所有调优参数：

```c
typedef struct {
    ngx_http_upstream_srv_conf_t *upstream;

    ngx_msec_t  connect_timeout;   // 连接超时
    ngx_msec_t  send_timeout;      // 发送超时
    ngx_msec_t  read_timeout;      // 读取超时

    size_t      send_lowat;        // 发送低水位标记
    size_t      buffer_size;       // 读缓冲大小（默认 4k/8k）
    size_t      limit_rate;        // 限速

    size_t      busy_buffers_size; // 忙缓冲区上限
    size_t      max_temp_file_size;// 最大临时文件大小

    ngx_bufs_t  bufs;              // 缓冲数量和大小
    ngx_flag_t  buffering;         // 是否缓冲上游响应
    ngx_flag_t  request_buffering; // 是否缓冲请求体

    // ... cache、SSL 等
} ngx_http_upstream_conf_t;
```

配置文件中的 `proxy_connect_timeout`、`proxy_send_timeout`、`proxy_read_timeout`、`proxy_buffer_size` 最终都映射到这个结构体。

## 请求生命周期

一个 upstream 请求从初始化到结束，经历以下状态机：

```
ngx_http_upstream_init()
  └─ ngx_http_upstream_init_request()
       ├─ 如果 u->resolved 为空：通过 u->conf->upstream 找到服务器组
       ├─ 否则：DNS 解析或 sockaddr 直接连接
       └─ ngx_http_upstream_connect()
            ├─ ngx_event_connect_peer() → 调用 peer.get 选 peer + connect()
            ├─ 设置 write_event_handler = send_request_handler
            ├─ 设置 read_event_handler = process_header
            └─ ngx_http_upstream_send_request()
                 ├─ ngx_http_upstream_send_request_body()
                 ├─ 请求发完后：
                 │    u->write_event_handler = dummy
                 │    ngx_add_timer(c->read, read_timeout)
                 └─ ngx_http_upstream_process_header()
                      ├─ recv() 读取响应
                      ├─ u->process_header() ← 模块回调（解析 header）
                      └─ ngx_http_upstream_send_response()
                           ├─ 缓冲模式: ngx_event_pipe() 管道
                           └─ 非缓冲: input_filter 逐块转发
                                ngx_http_upstream_finalize_request()
```

`ngx_http_upstream_init()`（第 507 行）是 CONTENT 阶段的入口。它先删除客户端读定时器（防止客户端超时影响上游），如果是 CLEAR_EVENT 模式还要重新注册写事件，然后调用 `ngx_http_upstream_init_request()`。

`ngx_http_upstream_init_request()`（第 544 行）做了几件重要的事：

1. 检查缓存命中（`ngx_http_upstream_cache()`），命中则直接返回缓存内容。
2. 调用 `u->create_request(r)` 构建请求数据，放入 `u->request_bufs`。
3. 找上游服务器组：若 `u->resolved` 非空则 DNS 解析后连接，否则通过 `u->conf->upstream` 找到配置的 upstream 块，调用 `uscf->peer.init(r, uscf)` 初始化 peer 选择器。
4. 最后调用 `ngx_http_upstream_connect()`。

## ngx_http_upstream_connect

连接建立的核心函数（第 1507 行）：

```c
static void
ngx_http_upstream_connect(ngx_http_request_t *r, ngx_http_upstream_t *u)
{
    rc = ngx_event_connect_peer(&u->peer);  // 选 peer + socket connect

    if (rc == NGX_BUSY) {
        ngx_http_upstream_next(r, u, NGX_HTTP_UPSTREAM_FT_NOLIVE);
        return;
    }

    c = u->peer.connection;
    c->write->handler = ngx_http_upstream_handler;
    c->read->handler = ngx_http_upstream_handler;

    u->write_event_handler = ngx_http_upstream_send_request_handler;
    u->read_event_handler = ngx_http_upstream_process_header;

    if (rc == NGX_AGAIN) {
        ngx_add_timer(c->write, u->conf->connect_timeout);
        return;
    }

    ngx_http_upstream_send_request(r, u, 1);
}
```

注意这里设置了 `c->write->handler` 和 `c->read->handler` 都为 `ngx_http_upstream_handler`。这个统一的 dispatcher 再根据 `u->write_event_handler` / `u->read_event_handler` 派发到具体的阶段处理函数。这种两层事件回调的设计让 upstream 可以在请求生命周期的不同阶段切换不同的处理逻辑。

`ngx_event_connect_peer()` 会先调用 `pc->get(pc, pc->data)` 从负载均衡模块选出 peer，然后 `connect(sockfd, addr, addrlen)` 发起非阻塞连接。返回 `NGX_AGAIN` 表示连接在建立中（TCP 三次握手尚未完成），此时注册 `connect_timeout` 定时器。

## Round-Robin 负载均衡

RR 轮询是 Nginx 默认的负载均衡算法。初始化函数在 `ngx_http_upstream_round_robin.c` 第 30 行：

### ngx_http_upstream_init_round_robin

```c
ngx_int_t
ngx_http_upstream_init_round_robin(ngx_conf_t *cf,
    ngx_http_upstream_srv_conf_t *us)
{
    // 统计非 backup 服务器的数量、总权重、可用数
    for (i = 0; i < us->servers->nelts; i++) {
        if (server[i].backup) continue;
        n += server[i].naddrs;
        w += server[i].naddrs * server[i].weight;
        if (!server[i].down) t += server[i].naddrs;
    }

    peers->number = n;
    peers->weighted = (w != n);    // 权重是否不均匀
    peers->total_weight = w;
    peers->single = (n == 1);      // 只有一台则优化

    // 初始化每个 peer
    for (i = 0; i < us->servers->nelts; i++) {
        for (j = 0; j < server[i].naddrs; j++) {
            peer[n].weight = server[i].weight;
            peer[n].effective_weight = server[i].weight;
            peer[n].current_weight = 6;
            peer[n].max_fails = server[i].max_fails;
            // ...
        }
    }

    // 同样处理 backup 服务器，链接到 peers->next
    peers->next = backup;
}
```

注意 `peers->single = (n == 1)` 这个优化：如果只有一个 peer，选 peer 时避免遍历，直接判断是否 down。

### 平滑加权轮询算法

当有多个 peer 时，`ngx_http_upstream_get_round_robin_peer()`（第 430 行）调用内部的 `ngx_http_upstream_get_peer()`（第 521 行）实现平滑加权轮询：

```c
static ngx_http_upstream_rr_peer_t *
ngx_http_upstream_get_peer(ngx_http_upstream_rr_peer_data_t *rrp)
{
    best = NULL;
    total = 0;

    for (peer = rrp->peers->peer; peer; peer = peer->next) {
        if (peer->down) continue;

        // 跳过失败的 peer（fails >= max_fails 且在 fail_timeout 内）
        if (peer->max_fails && peer->fails >= peer->max_fails
            && now - peer->checked <= peer->fail_timeout)
            continue;

        // 跳过达到 max_conns 的 peer
        if (peer->max_conns && peer->conns >= peer->max_conns)
            continue;

        peer->current_weight += peer->effective_weight;
        total += peer->effective_weight;

        if (peer->effective_weight < peer->weight)
            peer->effective_weight++;

        if (best == NULL || peer->current_weight > best->current_weight)
            best = peer;
    }

    if (best == NULL) return NULL;

    best->current_weight -= total;

    return best;
}
```

这个算法的核心思想是 Google 的平滑加权轮询（SWRR）。关键步骤：

1. 每个 peer 的 `current_weight` 每次递增自己的 `effective_weight`。
2. 选出 `current_weight` 最大的 peer。
3. 被选中的 peer 的 `current_weight` 减去所有 peer 的 `effective_weight` 之和。

这样保证了权重高的 peer 被选中的次数更多，而且分配是平滑的，，不会出现连续 N 次都选同一个 peer 的情况。例如 A:weight=5, B:weight=1, C:weight=1，分配序列会是 A, A, A, B, A, C, A, ... 不会出现 AAAAA BC 这样的突发。

`peers->weighted` 标记在初始化时设置：如果所有 peer 权重相等（`w == n`），就不需要做加权计算，直接用简单轮询。

### ngx_http_upstream_free_round_robin_peer

请求结束后（第 599 行），free 函数处理失败统计：

```c
void
ngx_http_upstream_free_round_robin_peer(ngx_peer_connection_t *pc, void *data,
    ngx_uint_t state)
{
    peer = rrp->current;

    if (state & NGX_PEER_FAILED) {
        peer->fails++;
        peer->accessed = now;
        peer->checked = now;

        peer->effective_weight -= peer->weight / peer->max_fails;
        if (peer->effective_weight < 0)
            peer->effective_weight = 6;

        if (peer->fails >= peer->max_fails) {
            ngx_log_error(NGX_LOG_WARN, ...,
                          "upstream server temporarily disabled");
        }
    } else {
        // 成功：清除失败计数
        if (peer->accessed < peer->checked)
            peer->fails = 0;
    }

    peer->conns--;
}
```

失败时递减 `effective_weight`，这样在当前轮次中它的权重竞争力会降低。当失败次数超过 `max_fails` 且在 `fail_timeout` 内，该 peer 被临时禁用（`ngx_http_upstream_get_peer` 中会跳过）。

## 上下游事件管理

Upstream 模块中有一对关键的函数指针，它们随着生命周期不断切换：

```
上游事件（读上游响应）：
  connect 阶段：  → process_header         → process_upstream / process_non_buffered_upstream
下游事件（写客户端）：
  connect 阶段：  → send_request_handler    → process_downstream / process_non_buffered_downstream
```

这种"上下游非对称"的设计体现在：

- **写事件只关心上游**：`send_request_handler` 检查上游连接的写事件是否可写，把请求数据发过去。
- **读事件分两段**：前半段 `process_header` 读取并解析响应头，后半段根据缓冲模式切换到不同的 body 处理函数。

`ngx_http_upstream_handler` 是统一的事件分发入口：

```c
static void
ngx_http_upstream_handler(ngx_event_t *ev)
{
    ngx_http_upstream_t *u;

    u = r->upstream;

    if (ev->write)
        u->write_event_handler(r, u);
    else
        u->read_event_handler(r, u);
}
```

## 请求发送与响应接收

### ngx_http_upstream_send_request

第 2058 行，函数在连接建立后调用：

1. 检查连接是否已建立（`ngx_http_upstream_test_connect()`）。
2. 调用 `ngx_http_upstream_send_request_body()`：如果是第一次发送（`!u->request_sent`），输出 `u->request_bufs`；如果已经发送过，只输出剩余数据。
3. 返回 `NGX_AGAIN` 表示发送未完，注册 `send_timeout` 定时器，继续等待写事件。
4. 返回 `NGX_OK` 表示全部发完：将 `write_event_handler` 切换为 `ngx_http_upstream_dummy_handler`（空函数，不再处理写事件），然后启动 `read_timeout` 定时器。

```c
if (!u->request_body_sent) {
    u->request_body_sent = 1;
    ngx_add_timer(c->read, u->conf->read_timeout);

    if (c->read->ready) {
        ngx_http_upstream_process_header(r, u);
    }
}
```

### ngx_http_upstream_process_header

第 2350 行，读取上游响应头：

```c
for ( ;; ) {
    n = c->recv(c, u->buffer.last, u->buffer.end - u->buffer.last);

    if (n == NGX_AGAIN) {
        ngx_handle_read_event(c->read, 0);
        return;
    }

    u->buffer.last += n;
    rc = u->process_header(r);  // ← 模块回调

    if (rc == NGX_AGAIN)
        continue;               // 数据不够，继续读

    break;
}
```

`process_header` 由具体协议模块实现。例如 `ngx_http_proxy_module` 的 `ngx_http_proxy_process_status_line()` 解析 "HTTP/1.1 200 OK" 这样的状态行，然后 `ngx_http_proxy_process_header()` 逐个解析 header 字段。

响应头解析完成后，调用 `ngx_http_upstream_send_response()`（第 2993 行），根据 `u->buffering` 进入两种模式。

## 缓冲与无缓冲模式

### 缓冲模式

`buffering == 1`（默认，由 proxy_buffering on 控制）：

```c
u->read_event_handler = ngx_http_upstream_process_upstream;
r->write_event_handler = ngx_http_upstream_process_downstream;

// 创建 ngx_event_pipe_t 管道
p->upstream = u->peer.connection;
p->downstream = r->connection;
p->input_filter = u->input_filter;
// ...
```

`ngx_event_pipe()` 在上游和下游之间做缓冲转发。它会分配多个固定大小的 buffer（由 `proxy_buffers` 配置决定），从上游读取数据填充 buffer，当 buffer 满了或数据读完时向下游写出。如果数据量超过 `busy_buffers_size`，会写入临时文件。

### 无缓冲模式

`buffering == 0`（如 proxy_buffering off）：

```c
u->read_event_handler = ngx_http_upstream_process_non_buffered_upstream;
r->write_event_handler = ngx_http_upstream_process_non_buffered_downstream;
```

`ngx_http_upstream_process_non_buffered_request()` 每次读一个 buffer 的数据，立即调用 `input_filter`（`ngx_http_upstream_non_buffered_filter`），然后 `ngx_output_chain()` 直接写回客户端。没有二级缓冲和临时文件，延迟更低，但上游和下游的读写必须精确匹配。

## Keepalive 连接池

Upstream keepalive 由单独的 `ngx_http_upstream_keepalive_module` 实现（`src/http/modules/ngx_http_upstream_keepalive_module.c`）。它通过 `ngx_http_upstream_peer_t` 的回调机制接入 RR 调度器之后：

```
原始 RR:  get → connect → send → recv → free(close)
Keepalive: get → try_reuse(空闲连接池) → 失败则新建 → send → recv → free(归还到池)
```

keepalive 模块的 `get` 回调先从连接池中取出一个复用连接。如果池子为空，才调用原始 RR 的 `get` 去新建连接。`free` 回调不关闭连接，而是把它放回空闲连接池（最多保持 `keepalive_requests` 个请求或 `keepalive_timeout` 时间）。

keepalive 连接池的核心是 `ngx_queue_t` 队列，按 LRU 顺序维护空闲连接。取连接时从队首取，归还时放回队尾。`ngx_http_upstream_keepalive_close_handler` 负责在池满时淘汰最旧的连接。

## 总结

Upstream 模块是 Nginx 反向代理能力的中枢。它的设计有几个值得学习的地方：

1. **事件驱动的状态机**：通过随时切换的 `read_event_handler` / `write_event_handler` 函数指针，把复杂的生命周期管理变成了简单的状态切换，避免了大型 switch-case。
2. **可插拔的负载均衡**：`ngx_http_upstream_peer_t` 的 get/free 回调让不同算法可以无缝替换。RR 是默认实现，但 ip_hash、hash、least_conn、random 等都是同样的接口。
3. **平滑加权轮询算法**：Google 提出的 SWRR 算法实现简洁而优雅，—用 `current_weight += effective_weight` 做趋势积累，`current_weight -= total` 做全局修正，保证分布的平滑性。
4. **协议与框架的解耦**：`create_request` / `process_header` / `finalize_request` 三个回调让 proxy、fastcgi、uwsgi、scgi 等不同协议共享同一套连接管理、超时控制、负载均衡机制。

---
**下一篇预告：** Nginx 源码解析（八）：ngx_http_upstream_keepalive_module ， 连接池复用与长连接管理

## 参考

- Nginx 1.24.x 源码: `/tmp/nginx-src/src/http/ngx_http_upstream.h`, `/tmp/nginx-src/src/http/ngx_http_upstream.c`
- Round-Robin 实现: `/tmp/nginx-src/src/http/ngx_http_upstream_round_robin.h`, `/tmp/nginx-src/src/http/ngx_http_upstream_round_robin.c`
- Upstream 事件管理: `src/core/ngx_event_connect.h` ， ngx_peer_connection_t
- Nginx 官方开发指南: https://nginx.org/en/docs/dev/development_guide.html
