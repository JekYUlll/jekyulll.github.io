+++
title = 'Nginx 源码解析（六）：HTTP 模块与请求处理'
date = '2026-05-29T17:24:00+08:00'
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'http', 'c']
+++

前五篇我们从整体架构走到事件驱动和配置系统，现在终于到了最核心的 HTTP 处理层。这一篇拆解 Nginx 1.24.x 的 HTTP 模块体系与请求处理全流程，，从 `ngx_http_module_t` 接口到 11 阶段处理引擎，从请求解析到 filter 链。HTTP 模块是 Nginx 最重要（也最大）的子系统，全部源码约 7 万行，分布在 `src/http/`。

<!--more-->

## ngx_http_module_t：HTTP 模块的上下文接口

每个 HTTP 模块的 `ctx` 指针指向 `ngx_http_module_t`（定义在 `ngx_http_config.h`）：

```c
typedef struct {
    ngx_int_t   (*preconfiguration)(ngx_conf_t *cf);
    ngx_int_t   (*postconfiguration)(ngx_conf_t *cf);

    void       *(*create_main_conf)(ngx_conf_t *cf);
    char       *(*init_main_conf)(ngx_conf_t *cf, void *conf);

    void       *(*create_srv_conf)(ngx_conf_t *cf);
    char       *(*merge_srv_conf)(ngx_conf_t *cf, void *prev, void *conf);

    void       *(*create_loc_conf)(ngx_conf_t *cf);
    char       *(*merge_loc_conf)(ngx_conf_t *cf, void *prev, void *conf);
} ngx_http_module_t;
```

这 8 个回调在 `ngx_http_block()`（`ngx_http.c` 第 122 行）中被精确调度。`ngx_http_block` 是 `http {}` 配置块的处理器，其流程是：

1. 统计模块数，`ngx_count_modules()` 计算所有 `NGX_HTTP_MODULE` 类型的模块数，赋值给 `ngx_http_max_module`。
2. 创建三组配置数组，`main_conf` / `srv_conf` / `loc_conf`，每个数组大小 = `ngx_http_max_module`。
3. 调用 `create_main_conf/ create_srv_conf/ create_loc_conf`，按所有 HTTP 模块的 `ctx_index` 分配配置结构体。
4. 调用 `preconfiguration`，各模块在配置解析前注册变量、header handlers 等。
5. `ngx_conf_parse()` 解析 `http {}` 内部，遍历 `server {}` / `location {}`，触发各模块的 commands 处理。
6. 调用 `init_main_conf` + `merge_srv_conf` + `merge_loc_conf`，初始化主配置，合并上下层配置。
7. 构建 location 树 + 初始化 phase 引擎，为请求路由做准备。
8. 调用 `postconfiguration`，模块在此阶段注册 phase handlers、注册 filter 链。
9. `ngx_http_init_phase_handlers()`，将各阶段 handlers 展平为运行时 phase engine 数组。

其中 `create_srv_conf` 和 `create_loc_conf` 在每个 server/location 块解析时被调用，`merge_*_conf` 函数将上层默认值与下层配置合并。这个"三段式创建 + 合并"的设计是 Nginx 配置层叠（CSS 式继承）的基础。

## ngx_http_core_main_conf_t：Phase 引擎的宿主

`ngx_http_core_module` 提供核心 HTTP 功能，它的 main_conf 类型 `ngx_http_core_main_conf_t`（`ngx_http_core_module.h` 第 152 行）是 phase 引擎的直接宿主：

```c
typedef struct {
    ngx_array_t                servers;
    ngx_http_phase_engine_t    phase_engine;
    ngx_hash_t                 headers_in_hash;
    ngx_hash_t                 variables_hash;
    ngx_array_t                variables;
    ngx_array_t                prefix_variables;
    ngx_http_phase_t           phases[NGX_HTTP_LOG_PHASE + 1];
    // ...
} ngx_http_core_main_conf_t;
```

关键字段：
- **`phases[]`**，11 个阶段的数组，每个元素是一个 `ngx_http_phase_t`（内部只有一个 `ngx_array_t handlers`，存放该阶段的 handler 函数指针）。
- `phase_engine`，`ngx_http_phase_engine_t`，初始化后是一个扁平的 handlers 数组，运行时逐级分发。
- `servers`，所有 `server {}` 的配置数组。
- `headers_in_hash`，用 Hash 表加速请求头查找。

```c
typedef struct {
    ngx_http_phase_handler_t  *handlers;
    ngx_uint_t                 server_rewrite_index;
    ngx_uint_t                 location_rewrite_index;
} ngx_http_phase_engine_t;
```

## 11 阶段处理模型

Phase 引擎是 Nginx HTTP 处理的核心调度机制。定义了 11 个阶段（`ngx_http_phases` 枚举，`ngx_http_core_module.h` 第 107 行）：

```c
typedef enum {
    NGX_HTTP_POST_READ_PHASE = 0,      // 读取请求后 | realip 等
    NGX_HTTP_SERVER_REWRITE_PHASE,     // server 级别 rewrite
    NGX_HTTP_FIND_CONFIG_PHASE,        // location 匹配 ← 特殊阶段
    NGX_HTTP_REWRITE_PHASE,            // location 级别 rewrite
    NGX_HTTP_POST_REWRITE_PHASE,       // rewrite 后跳回 FIND_CONFIG
    NGX_HTTP_PREACCESS_PHASE,          // 访问前检查 | limit_conn/limit_req
    NGX_HTTP_ACCESS_PHASE,             // 访问控制 | auth_basic/auth_request
    NGX_HTTP_POST_ACCESS_PHASE,        // 访问后 | satisfy any 逻辑
    NGX_HTTP_PRECONTENT_PHASE,         // 内容生成前 | try_files/mirror
    NGX_HTTP_CONTENT_PHASE,            // 内容生成 | proxy_pass/fastcgi/static
    NGX_HTTP_LOG_PHASE                 // 日志记录
} ngx_http_phases;
```

`FIND_CONFIG_PHASE` 和 `POST_REWRITE_PHASE` 是"无用户 handler"的纯检查阶段，其余阶段可以注册多个 handler。`LOG_PHASE` 在请求完成时调用，不参与主逻辑。

### Phase handler 结构

```c
struct ngx_http_phase_handler_s {
    ngx_http_phase_handler_pt  checker;   // 阶段检查函数
    ngx_http_handler_pt        handler;   // 用户 handler
    ngx_uint_t                 next;      // 下一阶段索引
};
```

每个 phase handler 有一个 **checker 函数**和一个 handler 函数。checker 决定何时调用 handler、何时跳过、何时进入下一阶段。关键 checker 函数（`ngx_http_core_module.c`）：

- `ngx_http_core_generic_phase`，通用 checker。调用 handler；若返回 `NGX_OK` 跳到 `next`；`NGX_DECLINED` 跳到下一个 handler；`NGX_AGAIN/DONE` 暂停等待 I/O。
- `ngx_http_core_rewrite_phase`，循环调用该阶段所有 rewrite handler。
- `ngx_http_core_find_config_phase`，location 匹配专用。
- `ngx_http_core_post_rewrite_phase`，rewrite 后如果 URI 变了，跳回 `FIND_CONFIG_PHASE`。
- `ngx_http_core_access_phase`，访问控制循环。
- `ngx_http_core_post_access_phase`，`satisfy any` 逻辑。
- `ngx_http_core_content_phase`，内容生成，优先用 `r->content_handler`。

### Phase 引擎初始化

`ngx_http_init_phase_handlers()`（`ngx_http.c` 第 454 行）将所有阶段 handler 展平为一个一维数组：

```c
for (i = 0; i < NGX_HTTP_LOG_PHASE; i++) {
    switch (i) {
    case NGX_HTTP_SERVER_REWRITE_PHASE:
        checker = ngx_http_core_rewrite_phase;
        break;
    case NGX_HTTP_FIND_CONFIG_PHASE:
        ph->checker = ngx_http_core_find_config_phase;
        n++; ph++; continue;
    // ... 每个阶段分配不同的 checker
    }
    for (j = nelts - 1; j >= 0; j--) {
        ph->checker = checker;
        ph->handler = h[j];
        ph->next = n;    // 跳到下一阶段的起始索引
        ph++;
    }
}
```

这里 handler 按阶段内逆序放入数组：因为同一个阶段多个 handler 按正向执行，但 `next` 指针指向所有 handler 之后的索引，保证串行。

### Phase 引擎运行时

`ngx_http_handler()` 设置 `r->write_event_handler = ngx_http_core_run_phases`，然后立即调用它：

```c
void ngx_http_core_run_phases(ngx_http_request_t *r) {
    ngx_http_phase_handler_t *ph;
    ngx_http_core_main_conf_t *cmcf;

    cmcf = ngx_http_get_module_main_conf(r, ngx_http_core_module);
    ph = cmcf->phase_engine.handlers;

    while (ph[r->phase_handler].checker) {
        rc = ph[r->phase_handler].checker(r, &ph[r->phase_handler]);
        if (rc == NGX_OK) return;  // 等待 I/O
    }
}
```

核心逻辑：从 `r->phase_handler` 当前索引开始，调用 checker → checker 决定是否调用 handler → checker 返回 `NGX_OK` 说明挂起等待事件 → 返回事件循环。下次可写时重新进入（`write_event_handler` 被再次触发），从同样的 `phase_handler` 索引继续执行。这个"可重入"设计保证了非阻塞 I/O 下的正确状态恢复。

## ngx_http_request_t：请求的完整上下文

`ngx_http_request_t`（`ngx_http_request.h` 第 377 行）是请求生命周期中最重要的结构体，约 200 多个字段：

```c
struct ngx_http_request_s {
    uint32_t                          signature;      // "HTTP" 魔数

    ngx_connection_t                 *connection;     // TCP 连接

    void                            **ctx;            // 各模块私有上下文
    void                            **main_conf;      // main 级别配置
    void                            **srv_conf;       // server 级别配置
    void                            **loc_conf;       // location 级别配置

    ngx_http_event_handler_pt         read_event_handler;
    ngx_http_event_handler_pt         write_event_handler;

    ngx_pool_t                       *pool;           // 请求内存池
    ngx_buf_t                        *header_in;      // header 缓冲区

    ngx_http_headers_in_t             headers_in;     // 请求头
    ngx_http_headers_out_t            headers_out;    // 响应头

    ngx_uint_t                        method;         // HTTP 方法
    ngx_uint_t                        http_version;

    ngx_str_t                         request_line;   // 完整请求行
    ngx_str_t                         uri;
    ngx_str_t                         args;
    ngx_str_t                         exten;          // 文件扩展名

    ngx_int_t                         phase_handler;  // 当前 phase 索引
    ngx_http_handler_pt               content_handler;// 内容处理器

    ngx_http_variable_value_t        *variables;      // 变量表

    unsigned                          keepalive:1;
    unsigned                          header_sent:1;
    unsigned                          request_complete:1;
    // ... 大量 bit 字段
};
```

关键点：
- **`ctx[module.ctx_index]`**，各模块通过 `ngx_http_get_module_ctx(r, module)` 获取自己的请求上下文数据。
- `main_conf/srv_conf/loc_conf`，三级配置指针数组，运行时通过 `ngx_http_get_module_*_conf(r, module)` 访问。
- `headers_in`，已解析的请求头，Host、User-Agent 等常用字段有专用指针，其余存 `headers` 链表中。
- `headers_out`，响应头，content_type、location、status 等。
- `variables`，变量表数组，与 `ngx_http_core_main_conf_t.variables` 一一对应。
- `phase_handler`，当前 phase engine 索引，实现"中断后恢复"。
- `pool`，请求级内存池，请求结束自动释放。

## HTTP 请求生命周期

从连接建立到请求结束，核心路径在 `ngx_http_request.c`：

```
ngx_event_accept()
  → ngx_http_init_connection()       注册 wait_request_handler
  → epoll_wait 事件到达
  → ngx_http_wait_request_handler()  读事件触发
    → ngx_http_create_request()      分配 r，设置 main_conf/srv_conf/loc_conf
    → ngx_http_process_request_line() ↓
      → 循环 read + ngx_http_parse_request_line()
      → 成功：ngx_http_process_request_uri()
      → rev->handler = ngx_http_process_request_headers
    → ngx_http_process_request_headers() ↓
      → 循环 read + ngx_http_parse_header_line()
      → 全部 header 解析完：ngx_http_process_request()
    → ngx_http_process_request() ↓
      → SSL 验证等安全检查
      → c->read/write->handler = ngx_http_request_handler
      → ngx_http_handler()
    → ngx_http_handler() ↓
      → ngx_http_update_location_config()
      → r->write_event_handler = ngx_http_core_run_phases
      → ngx_http_core_run_phases()
    → phase 引擎遍历 ↓
      → checker 调度 handler
      → handler 调用 ngx_http_send_header() → filter 链
      → handler 调用 ngx_http_output_filter() → body filter 链
    → ngx_http_finalize_request() → 释放或 keepalive
```

### 请求行解析：ngx_http_parse_request_line()

`ngx_http_parse_request_line()`（`ngx_http_parse.c` 第 104 行）是一个约 500 行的有限状态机（FSM），纯手写无依赖。它逐字节扫描 `header_in` 缓冲区，用 `switch(state)` 在 `sw_start / sw_method / sw_spaces_before_uri / sw_after_slash_in_uri / sw_http_09 / sw_http_H / sw_http_HT / sw_http_HTT / sw_http_HTTP / sw_first_major_digit / sw_major_digit / sw_first_minor_digit / sw_minor_digit / sw_almost_done / sw_done` 等状态间转换。

解析结果直接写入 `r->uri_start / uri_end / args_start / method_end / http_protocol` 等指针，零拷贝，，所有字段都在 `header_in` 缓冲区内通过指针和长度表示。

### Header 解析

`ngx_http_process_request_headers()` 调用 `ngx_http_parse_header_line()` 逐行解析。解析完成后，用 `ngx_hash_find(&cmcf->headers_in_hash, ...)` 快速查找 header 名称对应的 `ngx_http_header_t`，调用其 handler。常见 header（如 Host、Connection）有专用处理函数。

## Filter 链：响应处理管道

Filter 是 Nginx HTTP 响应处理的责任链模式实现。全局有两个 filter 链入口：

```c
// ngx_http.h
extern ngx_http_output_header_filter_pt  ngx_http_top_header_filter;
extern ngx_http_output_body_filter_pt    ngx_http_top_body_filter;
```

Filter 模块在 `postconfiguration` 中注册自己，形成一个链表头插入，—`ngx_http_top_header_filter = my_filter`，内部保存旧值并在处理完自己的逻辑后调用旧 filter。

典型的 header filter 链：

```
ngx_http_send_header()
  → ngx_http_top_header_filter
    → ngx_http_not_modified_filter_module (处理 304)
    → ngx_http_range_header_filter_module (处理 Range)
    → ngx_http_gzip_filter_module (gzip 过滤)
    → ... 其他 filter
    → ngx_http_header_filter_module ← 最后一级，构造并输出响应行+头
      → ngx_http_write_filter_module  ← 写入 socket
```

`ngx_http_header_filter_init()` 设置 `ngx_http_top_header_filter = ngx_http_header_filter`，而 `ngx_http_header_filter()` 在 `ngx_http_header_filter_module.c` 第 624 行以扇出方式调用 `ngx_http_write_filter(r, &out)` 写入响应行和头部至 socket。

Body filter 链同理，模块可对响应体做 gzip 压缩、sub_filter 替换、SSI 处理等，最终全部汇入 `ngx_http_write_filter`。

`ngx_http_write_filter()`（`ngx_http_write_filter_module.c` 第 48 行）负责实际的 socket 写入，处理限速、链式缓冲区管理、部分写入重试等逻辑。

## 变量系统与 ngx_http_script_engine_t

Nginx 配置指令中的 `$variable` 插值由脚本引擎在运行时求值。`ngx_http_script_engine_t`（`ngx_http_script.h` 第 17 行）是一个字节码解释器：

```c
typedef struct {
    u_char                     *ip;    // 指令指针
    u_char                     *pos;   // 当前输出位置
    ngx_http_variable_value_t  *sp;    // 栈指针（操作数栈）
    ngx_str_t                   buf;   // 输出缓冲区
    ngx_str_t                   line;
    u_char                     *args;
    unsigned                    flushed:1;
    unsigned                    skip:1;
    unsigned                    quote:1;
    unsigned                    is_args:1;
    ngx_int_t                   status;
    ngx_http_request_t         *request;
} ngx_http_script_engine_t;
```

编译时，配置指令（如 `set $name value`、`if ($arg_xx)`、`rewrite ^ /path?$arg_y`）被编译为字节码数组。每个"字节码"是一个 `ngx_http_script_code_pt` 函数指针：

```c
typedef void (*ngx_http_script_code_pt)(ngx_http_script_engine_t *e);

typedef struct {
    ngx_http_script_code_pt     code;   // 执行函数
    uintptr_t                   len;    // 参数
} ngx_http_script_copy_code_t;

typedef struct {
    ngx_http_script_code_pt     code;
    uintptr_t                   index;  // 变量索引
} ngx_http_script_var_code_t;
```

常见的字节码类型有：
- `ngx_http_script_copy_code`，复制常数字符串到输出。
- `ngx_http_script_var_code`，读取变量值（`$name`），根据变量类型调用 `get_handler`。
- `ngx_http_script_var_handler_code`，运行时 set 操作（`set` 指令）。
- `ngx_http_script_regex_code`，正则匹配/替换（`if ($host ~* pattern)`、`rewrite` 的捕获组）。
- `ngx_http_script_if_code`，`if` 条件分支。
- `ngx_http_script_break_code`，`break` 终止 rewrite 循环。

运行时，`ngx_http_script_run()` 创建 `ngx_http_script_engine_t`，设置 `ip` 指向字节码起始地址，然后循环调用 `code()` 函数，—类似 JVM 的字节码解释器，用 ip 指针遍历指令、用 sp 栈管理中间结果。`if` 指令通过设置 `skip` 标志实现条件跳转；`rewrite` 指令通过修改 `r->uri` 并设置 `uri_changed` 标志触发 phase 引擎重入 `FIND_CONFIG_PHASE`。

## 总结

从 `ngx_http_module_t` 的 8 个回调接口，到 `ngx_http_core_main_conf_t` 管理的 11 阶段处理引擎，再到 `ngx_http_request_t` 的请求上下文、filter 链和脚本引擎，—Nginx 的 HTTP 子系统是一个高度模块化的分层架构。每个模块只需通过 `postconfiguration` 注册自己的 handler/filter，框架就会在正确的时机调度它。这个设计使得 Nginx 可以轻松扩展第三方模块（如 lua-nginx-module、nginx-vod-module）而不影响核心。

---

下一篇预告： Nginx 源码解析（七）：Upstream 模块与反向代理

Upstream 模块是 Nginx 反向代理能力的核心。下一篇将深入 `ngx_http_upstream_init` → 上游连接建立 → 请求转发 → 响应接收的全流程，以及负载均衡、健康检查的实现机制。

## 参考

- `/tmp/nginx-src/src/http/ngx_http_config.h` ， `ngx_http_module_t` 定义
- `/tmp/nginx-src/src/http/ngx_http.c` ， `ngx_http_block()` / `ngx_http_init_phase_handlers()`
- `/tmp/nginx-src/src/http/ngx_http_core_module.h` ， phase 枚举、phase handler 结构、`ngx_http_core_main_conf_t`
- `/tmp/nginx-src/src/http/ngx_http_core_module.c` ， phase checker 函数、`ngx_http_handler()`、`ngx_http_send_header()`、`ngx_http_output_filter()`
- `/tmp/nginx-src/src/http/ngx_http_request.h` ， `ngx_http_request_t` 定义
- `/tmp/nginx-src/src/http/ngx_http_request.c` ， 请求生命周期实现
- `/tmp/nginx-src/src/http/ngx_http_parse.c` ， `ngx_http_parse_request_line()`
- `/tmp/nginx-src/src/http/ngx_http_script.h` ， `ngx_http_script_engine_t`
- `/tmp/nginx-src/src/http/ngx_http_header_filter_module.c` ， header filter 实现
- `/tmp/nginx-src/src/http/ngx_http_write_filter_module.c` ， write filter 实现
- Nginx 官方开发指南: https://nginx.org/en/docs/dev/development_guide.html
