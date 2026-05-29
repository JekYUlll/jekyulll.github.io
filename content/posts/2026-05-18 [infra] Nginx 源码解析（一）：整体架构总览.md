+++
title = 'Nginx 源码解析（一）：整体架构总览'
date = '2026-05-18T12:00:00+08:00'
weight = 1
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'architecture', 'c']
+++

去年生产环境一个诡异的 TIME_WAIT 问题把我引进了 Nginx 源码，读完之后最大的感受是：代码写得比《Unix 网络编程》还干净。这个系列我会从整体架构到底层细节，逐层拆解 Nginx 1.24.x 的源码。

<!--more-->

## 代码树结构

```
src/
├── core/        # 核心基础设施：内存池、线程、配置文件解析、模块系统、主循环
├── event/       # 事件驱动框架：epoll/kqueue/select/iocp 各实现
│   └── modules/ # 事件模块扩展（如 accept 锁）
├── http/        # HTTP 核心 + 模块：upstream、proxy、fastcgi、headers 等
│   └── modules/ # 200+ 个 HTTP 模块
├── stream/      # 四层 TCP/UDP 代理框架（1.9.0 引入）
├── mail/        # 邮件代理（IMAP/POP3/SMTP）
├── os/          # OS 抽象层
│   └── unix/    # Linux/FreeBSD 系统调用封装（POSIX 实现在这里）
└── misc/        # Google Perftools 等杂项
```

`src/core/` 是所有模块的基础，你可以把它看作一个微型操作系统内核，内存池（`ngx_pool_t`）、原子操作、红黑树、队列、配置文件解析器、命令行参数解析全在这里。不管你写 HTTP 模块还是 Stream 模块，`ngx_core.h` 是你第一个要 include 的头文件。

`src/os/unix/` 封装了所有平台差异：`ngx_read_file()`、`ngx_write_file()`、`ngx_shm_open()` 等。Nginx 没有用 autoconf 那套抽象，而是直接写了 `ngx_linux_init()`、`ngx_freebsd_init()` 这样的平台初始化函数。

`src/event/` 是 Nginx 高性能的根基。`ngx_epoll_module.c` 实现 epoll 事件循环，`ngx_event_timer.c` 管理定时器（用红黑树维护）。

## 核心类型系统

Nginx 没有用 C 标准库的类型，它自己包了一层。看 `ngx_core.h` 能找到：

```c
// 基础类型：只在 64 位下保证 4/8 字节
typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;

// 字符串：带长度，杜绝 strlen 遍历
typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

// 动态数组：内存从 pool 分配，自动扩容
typedef struct {
    void        *elts;
    ngx_uint_t   nelts;
    size_t       size;
    ngx_uint_t   nalloc;
    ngx_pool_t  *pool;
} ngx_array_t;

// 链表：分片存储，避免巨量小内存碎片
typedef struct ngx_list_part_s {
    void             *elts;
    ngx_uint_t        nelts;
    ngx_list_part_t  *next;
} ngx_list_part_t;

typedef struct {
    ngx_list_part_t  *last;
    ngx_list_part_t   part;
    size_t            size;
    ngx_uint_t        nalloc;
    ngx_pool_t       *pool;
} ngx_list_t;

// 侵入式双向链表，offsetof 定位宿主
struct ngx_queue_s {
    ngx_queue_t  *prev;
    ngx_queue_t  *next;
};

// 红黑树：定时器、缓存使用
struct ngx_rbtree_s {
    ngx_rbtree_node_t     *root;
    ngx_rbtree_node_t     *sentinel;
    ngx_rbtree_insert_pt   insert;
};
```

`ngx_str_t` 是最常见的类型，所有字符串操作都带着长度跑，没有 `strlen`。`ngx_array_t` 和 `ngx_list_t` 的区别：array 是一整块连续内存（像 C++ vector），list 是分段链表（适合未知总数、分批写入的场景）。`ngx_queue_t` 是侵入式链表，container_of 宏定位宿主结构体，`ngx_rbtree_t` 用于定时器管理。

## 模块体系

Nginx 的模块抽象设计得非常简洁。每个模块都是一个 `ngx_module_t`：

```c
struct ngx_module_s {
    ngx_uint_t            ctx_index;   // 同类模块中的索引
    ngx_uint_t            index;       // 全局模块索引
    char                 *name;
    ngx_uint_t            version;
    const char           *signature;   // 模块兼容性签名
    void                 *ctx;         // 模块上下文（类型相关）
    ngx_command_t        *commands;    // 配置指令表
    ngx_uint_t            type;        // NGX_CORE_MODULE / NGX_HTTP_MODULE ...
    // 生命周期回调
    ngx_int_t           (*init_master)(ngx_log_t *log);
    ngx_int_t           (*init_module)(ngx_cycle_t *cycle);
    ngx_int_t           (*init_process)(ngx_cycle_t *cycle);
    void                (*exit_process)(ngx_cycle_t *cycle);
    void                (*exit_master)(ngx_cycle_t *cycle);
};
```

全局模块数组定义在 `ngx_modules.c`（编译时由 auto/modules 脚本生成），每个 cycle 初始化时拷贝一份到 `cycle->modules`：

```c
// ngx_cycle_t 中的模块字段
ngx_module_t            **modules;    // 模块指针数组
ngx_uint_t                modules_n;  // 模块总数
```

`type` 决定了模块属于哪个子系统。`ctx` 指针类型随 type 变化，`NGX_CORE_MODULE` 时是 `ngx_core_module_t`，`NGX_HTTP_MODULE` 时是 `ngx_http_module_t`。模块通过 `commands` 数组暴露配置指令，配置文件解析器遍历所有模块的 commands 来处理指令。

## 核心数据结构关系

我读源码时最先做的事情就是在纸上画这四个结构体之间的关系图。画完之后 Nginx 的设计思路基本就清楚了。

四个关键结构体构成 Nginx 的骨架：

```
ngx_cycle_t (全局上下文)
 ├── conf_ctx              → 各模块配置树
 ├── modules / modules_n   → 模块列表
 ├── connections           → 连接数组（预分配）
 ├── read_events           → 读事件数组
 ├── write_events          → 写事件数组
 ├── listening             → 监听端口列表
 ├── pool                  → 内存池
 ├── open_files            → 已打开文件列表
 └── shared_memory         → 共享内存段
```

`ngx_connection_t` 包含 `read` 和 `write` 两个事件指针，每个连接对应一个读事件和一个写事件。这三个结构体在 cycle 中是预分配的连续数组，通过 `fd` 索引直接定位（`c = cycle->files[fd]`），这是 Nginx 能 O(1) 处理海量连接的关键。

## 启动流程：main() 详解

`src/core/nginx.c` 的入口流程：

```
main()
 ├─ ngx_strerror_init()        // 初始化错误码映射表
 ├─ ngx_get_options()          // 解析命令行参数 (-c, -p, -s, -g...)
 ├─ ngx_time_init()            // 初始化时间缓存
 ├─ ngx_log_init()             // 初始化日志系统
 ├─ 栈上分配 init_cycle        // 临时 cycle 用于启动阶段
 ├─ ngx_create_pool()          // 创建 1024 字节内存池
 ├─ ngx_save_argv()            // 保存 argv 参数
 ├─ ngx_process_options()      // 解析 prefix/conf_file/error_log
 ├─ ngx_os_init()              // OS 层初始化（pagesize、cpu 数等）
 ├─ ngx_preinit_modules()      // 计算 max_module，初始化模块索引
 ├─ ngx_init_cycle(&init_cycle)// ← 核心：解析配置，创建真实 cycle
 │   ├─ ngx_cycle_modules()    // 复制模块数组到 cycle
 │   ├─ 遍历所有模块配置创建   // 每个模块创建自己的配置上下文
 │   ├─ ngx_conf_parse()       // 解析 nginx.conf
 │   ├─ 打开 listen socket     // 创建监听 socket
 │   ├─ 初始化共享内存         // 各模块分配共享内存
 │   └─ ngx_init_modules()     // 调用每个模块的 init_module
 │
 ├─ ccf = ngx_get_conf()       // 读取 core 配置
 ├─ ngx_init_signals()         // 注册信号处理器
 ├─ ngx_daemon()               // daemonize（配置指定）
 ├─ ngx_create_pidfile()       // 写 PID 文件
 │
 └─ 分支:
     ├─ ngx_single_process_cycle(cycle) // 单进程模式
     └─ ngx_master_process_cycle(cycle) // Master-Worker 模式
         ├─ fork() workers
         └─ 每个 worker 进入:
            ngx_worker_process_cycle()
             └─ ngx_process_events_and_timers()
                 └─ ngx_epoll_process_events() ← 事件循环
```

注意 `init_cycle` 是栈上分配的临时变量，`ngx_init_cycle()` 在读取完配置文件、完成所有初始化后，返回一个从 pool 分配的新的 `cycle` 指针（或复用旧 cycle 的 pool 重新创建）。`ngx_is_init_cycle()` 宏通过检查 `conf_ctx == NULL` 来判断一个 cycle 是不是临时的 init_cycle。

## 整体数据流

```
Client → [TCP 连接]
         ↓
  socket() → bind() → listen()
         ↓
  epoll_wait() 返回 EPOLLIN
         ↓
  ngx_event_accept()    ← src/event/ngx_event_accept.c
         ↓
  accept() 新连接 → 分配 ngx_connection_t
         ↓
  添加读事件到 epoll
         ↓
  epoll_wait() 返回可读
         ↓
  ngx_http_init_request() ← 读取请求行
         ↓
  11 个 HTTP 阶段 (NGX_HTTP_*_PHASE):
    ├─ POST_READ → SERVER_REWRITE → FIND_CONFIG
    ├─ REWRITE → REWRITE_LAST → PREACCESS → ACCESS
    ├─ ACCESS_LAST → CONTENT → LOG
         ↓
  HTTP header 经过 filter 链:
    ngx_http_header_filter → ngx_http_not_modified_filter
    → ngx_http_range_header_filter → ... → ngx_http_write_filter
         ↓
  如果是反向代理进入 upstream:
    ngx_http_upstream_init → connect() → send request
    → receive response → body filter → client
         ↓
  ngx_http_finalize_request() → 关闭连接或 keepalive
```

Nginx 处理一个 HTTP 请求的完整生命周期：accept 一个连接后，读事件被注册到 epoll。epoll 触发读事件 → 解析请求行和 header → 经过 11 个 HTTP 处理阶段 → 内容生成通过 filter 链逐层处理（gzip、charset、sub_filter 等）→ 如果是 upstream 则向上游发起请求 → 最后写回客户端。

整个数据流里，**事件驱动是贯穿始终的主线**。从 accept 到 close，所有 I/O 都是非阻塞的，通过 epoll 回调驱动。worker 进程永远在 `ngx_process_events_and_timers()` 这个循环里转，处理完一个事件又开始下一轮 epoll_wait。

## 总结

Nginx 源码约 18 万行 C 代码，覆盖了从四个层级看下去：基础类型 → 核心数据结构 → 模块体系 → 进程模型。理解 `ngx_cycle_t` 等于掌握了 Nginx 的全局配置上下文，理解 `ngx_module_t` 等于知道如何扩展它，理解事件驱动模型就等于明白了它为什么能撑住百万并发。

读 Nginx 源码不需要一次读完所有文件。我自己是按这个顺序啃的：先看 `ngx_core.h` 弄清楚基础类型 → 看 `ngx_cycle_t` 和 `ngx_module_t` 理解全局骨架 → 然后直接看 `nginx.c` 的 main() 走一遍启动流程 → 再深入到 `ngx_epoll_module.c` 看事件循环。这个系列也会按同样的顺序展开。第一篇文章是全景图，后续每篇聚焦一个子系统的实现细节。

---

**下一篇预告：** Nginx 源码解析（二）：内存池 ngx_pool_t 实现

内存池是 Nginx 底层最精彩的设计之一。下一篇我会详细拆解 `ngx_create_pool()`、`ngx_palloc()` 和 `ngx_destroy_pool()` 的实现，看 Nginx 如何用极其简单的设计做到零碎片、零泄漏。

## 参考

- Nginx 1.24.x 源码: `/tmp/nginx-src/src/core/ngx_cycle.h`, `/tmp/nginx-src/src/core/ngx_module.h`, `/tmp/nginx-src/src/core/nginx.c`, `/tmp/nginx-src/src/core/ngx_core.h`
- Nginx 官方开发指南: https://nginx.org/en/docs/dev/development_guide.html
- src/core/ngx_string.h ， ngx_str_t 定义
- src/core/ngx_array.h ， ngx_array_t 定义
- src/core/ngx_list.h ， ngx_list_t 定义
- src/core/ngx_queue.h ， ngx_queue_t 定义
- src/core/ngx_rbtree.h ， ngx_rbtree_t 定义
- src/core/ngx_connection.h ， ngx_connection_t 定义
