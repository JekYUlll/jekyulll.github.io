+++
title = 'Nginx 源码解析（十二）：模块系统与动态加载'
date = '2026-05-29T12:00:00+08:00'
weight = 12
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'module-system', 'c']
+++

系列开篇就说过，Nginx 的所有功能都是模块提供的。前面的文章你看到了 HTTP 模块、Event 模块、Upstream 模块在各自领域的工作方式，现在是时候把视角拉回到模块系统本身，，看看 Nginx 的模块到底长什么样，静态模块怎么初始化，动态模块又是如何通过 `dlopen` 加载进来的。

这篇文章会深入 `ngx_module_t` 的每一个字段，拆解 `ngx_modules.c` 的生成逻辑、`ngx_count_modules()` 的索引分配、`ngx_load_module()` 的动态加载路径，以及模块 `commands` 数组如何驱动配置文件解析器。

<!--more-->

## ngx_module_t：模块的完整形态

每个 Nginx 模块本质就是一个 `ngx_module_t` 结构体变量。定义在 `src/core/ngx_module.h`：

```c
struct ngx_module_s {
    ngx_uint_t            ctx_index;     // 同类模块中的序号
    ngx_uint_t            index;         // 全局数组中的序号

    char                 *name;          // 模块名

    ngx_uint_t            spare0;        // 保留字段
    ngx_uint_t            spare1;

    ngx_uint_t            version;       // nginx_version，用于兼容性检查
    const char           *signature;     // NGX_MODULE_SIGNATURE 字符串

    void                 *ctx;           // 上下文指针，类型随 type 变化
    ngx_command_t        *commands;      // 配置指令表
    ngx_uint_t            type;          // 模块类型

    // 生命周期回调函数指针（共 7 个）
    ngx_int_t           (*init_master)(ngx_log_t *log);
    ngx_int_t           (*init_module)(ngx_cycle_t *cycle);
    ngx_int_t           (*init_process)(ngx_cycle_t *cycle);
    ngx_int_t           (*init_thread)(ngx_cycle_t *cycle);
    void                (*exit_thread)(ngx_cycle_t *cycle);
    void                (*exit_process)(ngx_cycle_t *cycle);
    void                (*exit_master)(ngx_cycle_t *cycle);

    // 保留钩子（spare_hook0~7），留给模块扩展
    uintptr_t             spare_hook0;
    uintptr_t             spare_hook1;
    uintptr_t             spare_hook2;
    uintptr_t             spare_hook3;
    uintptr_t             spare_hook4;
    uintptr_t             spare_hook5;
    uintptr_t             spare_hook6;
    uintptr_t             spare_hook7;
};
```

看起来字段很多，但核心就三块：**元数据**（ctx_index/index/name/version/signature/type）、**配置接口**（ctx/commands）、**生命周期回调**（7 个 init/exit 函数）。

`NGX_MODULE_V1` 宏为前 6 个字段提供默认值，，index 和 ctx_index 初始化为 `NGX_MODULE_UNSET_INDEX`（即 `(ngx_uint_t)-1`），version 设为 `nginx_version`，signature 设为 `NGX_MODULE_SIGNATURE`。后面 7 个回调函数 `NULL`，8 个 spare_hook `0`，由 `NGX_MODULE_V1_PADDING` 补齐。看实际模块的声明就简洁多了：

```c
ngx_module_t  ngx_core_module = {
    NGX_MODULE_V1,
    &ngx_core_module_ctx,       /* module context */
    ngx_core_commands,          /* module directives */
    NGX_CORE_MODULE,            /* module type */
    NULL,                       /* init master */
    NULL,                       /* init module */
    NULL,                       /* init process */
    NULL,                       /* init thread */
    NULL,                       /* exit thread */
    NULL,                       /* exit process */
    NULL,                       /* exit master */
    NGX_MODULE_V1_PADDING
};
```

## 模块类型：五大分类

`type` 字段决定了模块归属的子系统。每个类型的值是用 ASCII 字符拼出来的整数，，方便调试时从内存里直接读出类型名：

```c
#define NGX_CORE_MODULE      0x45524F43  /* "CORE" */
#define NGX_EVENT_MODULE     0x544E5645  /* "EVNT" */
#define NGX_HTTP_MODULE      0x50545448  /* "HTTP" */
#define NGX_MAIL_MODULE      0x4C49414D  /* "MAIL" */
#define NGX_STREAM_MODULE    0x4d525453  /* "STRM" */
```

- NGX_CORE_MODULE：核心模块，如 `ngx_core_module`、`ngx_http_module`、`ngx_events_module`。它们不处理具体业务，而是负责创建、初始化子系统的配置上下文。
- NGX_EVENT_MODULE：事件处理模块，如 `ngx_epoll_module`、`ngx_kqueue_module`。它们实现事件驱动机制。
- NGX_HTTP_MODULE：HTTP 处理模块，数量最多（200+），涵盖 proxy、fastcgi、headers、gzip 等。
- NGX_MAIL_MODULE / NGX_STREAM_MODULE：邮件代理和四层 TCP/UDP 代理模块。

`ctx` 指针的具体类型随 `type` 变化，，`NGX_CORE_MODULE` 时是 `ngx_core_module_t`（包含 `create_conf` 和 `init_conf` 函数指针），`NGX_HTTP_MODULE` 时是 `ngx_http_module_t`（包含 8 个钩子：create_main_conf、create_srv_conf、create_loc_conf 等）。

## ngx_modules：全局模块数组

所有静态链接的模块在编译时被 auto/modules 脚本生成到 `ngx_modules.c`：

```c
// 编译时自动生成，形如：
ngx_module_t *ngx_modules[] = {
    &ngx_core_module,
    &ngx_errlog_module,
    &ngx_conf_module,
    &ngx_regex_module,
    &ngx_events_module,
    &ngx_event_core_module,
    &ngx_epoll_module,
    &ngx_http_module,
    &ngx_http_core_module,
    &ngx_http_upstream_module,
    // ... 所有静态模块
    NULL
};

char *ngx_module_names[] = {
    "ngx_core_module",
    "ngx_errlog_module",
    // ...
    NULL
};
```

数组以 NULL 结尾。这个数组在 `ngx_preinit_modules()` 中完成初始化：

```c
ngx_int_t ngx_preinit_modules(void) {
    ngx_uint_t i;
    for (i = 0; ngx_modules[i]; i++) {
        ngx_modules[i]->index = i;          // 全局数组索引
        ngx_modules[i]->name = ngx_module_names[i];
    }
    ngx_modules_n = i;                      // 静态模块总数
    ngx_max_module = ngx_modules_n + NGX_MAX_DYNAMIC_MODULES;  // 预留 128 个动态模块位
    return NGX_OK;
}
```

关键设计：`ngx_max_module = 静态模块数 + 128`。`NGX_MAX_DYNAMIC_MODULES` 这个硬上限（128）意味着一个 Nginx 进程最多额外加载 128 个动态 .so 模块。

每个 cycle 初始化时，`ngx_cycle_modules()` 从 `ngx_modules` 拷贝到 `cycle->modules`：

```c
ngx_int_t ngx_cycle_modules(ngx_cycle_t *cycle) {
    cycle->modules = ngx_pcalloc(cycle->pool,
        (ngx_max_module + 1) * sizeof(ngx_module_t *));
    ngx_memcpy(cycle->modules, ngx_modules,
               ngx_modules_n * sizeof(ngx_module_t *));
    cycle->modules_n = ngx_modules_n;
    return NGX_OK;
}
```

为什么拷贝而不是直接用全局数组？因为动态加载的模块需插入到 `cycle->modules` 中，不能修改只读的全局 `ngx_modules[]`。

## 两个索引：index 与 ctx_index

这是很多初读 Nginx 源码的人容易混淆的地方：

- **`index`**：模块在全局 `cycle->modules[]` 数组中的位置。在 `ngx_preinit_modules()` 中被赋值为数组下标 i。对动态模块来说，由 `ngx_add_module()` 调用 `ngx_module_index()` 分配。这个索引用于访问 `cycle->conf_ctx[index]` 获取模块的配置结构体指针。

- **`ctx_index`**：模块在同一类型模块内的序号。比如所有类型为 `NGX_HTTP_MODULE` 的模块，ctx_index 分别是 0、1、2……由 `ngx_count_modules()` 分配。这个索引用于 HTTP 模块的 phase handlers 数组查找。

`ngx_count_modules()` 的逻辑值得一看：

```c
ngx_int_t ngx_count_modules(ngx_cycle_t *cycle, ngx_uint_t type) {
    // 遍历 cycle->modules，跳过不同 type 的模块
    // 对 ctx_index == NGX_MODULE_UNSET_INDEX 的模块分配新 ctx_index
    // 对已经分配过的（如旧 cycle 遗留的）保留原值
    // 最后遍历 old_cycle 确保返回的数够大
    cycle->modules_used = 1;  // 标记：不再允许加载新模块
    return max + 1;
}
```

这里有个精妙的设计：`cycle->modules_used = 1`。一旦模块被计数标记为 used，后续的 `load_module` 指令就会被拒绝（`ngx_load_module()` 中首先检查 `cf->cycle->modules_used`）。确保在配置解析期间动态加载的模块必须在任何 `ngx_count_modules()` 调用之前完成。

## ngx_init_modules()：模块初始化

所有模块的 `init_module` 回调在配置解析完成后统一调用：

```c
ngx_int_t ngx_init_modules(ngx_cycle_t *cycle) {
    ngx_uint_t i;
    for (i = 0; cycle->modules[i]; i++) {
        if (cycle->modules[i]->init_module) {
            if (cycle->modules[i]->init_module(cycle) != NGX_OK) {
                return NGX_ERROR;
            }
        }
    }
    return NGX_OK;
}
```

这个调用序列发生在 `ngx_init_cycle()` 内部，在配置解析完成、监听 socket 创建之后。`init_module` 回调通常用于初始化与配置相关的全局状态，，比如 ngx_http_module 的 init_module 会初始化 HTTP 的 phase handlers。`init_process` 则在每个 worker 进程 fork 后调用，用于初始化进程级资源（如连接池、定时器）。

`init_master` 在主进程中调用，`exit_master` / `exit_process` 则对应退出清理。整个模块生命周期环环相扣。

## 动态模块加载：ngx_load_module()

从 Nginx 1.9.11 开始支持动态模块。配置写法很简单：

```
load_module modules/ngx_http_image_filter_module.so;
```

背后是 `ngx_load_module()` 函数（`src/core/nginx.c`）：

```c
static char *ngx_load_module(ngx_conf_t *cf, ngx_command_t *cmd, void *conf) {
    void          *handle;
    char         **names, **order;
    ngx_str_t     *value, file;
    ngx_module_t  *module, **modules;

    if (cf->cycle->modules_used) {
        return "is specified too late";
    }

    file = value[1];
    ngx_conf_full_name(cf->cycle, &file, 0);  // 补全路径

    handle = dlopen(file.data, RTLD_NOW | RTLD_GLOBAL);
    if (handle == NULL) {
        return NGX_CONF_ERROR;
    }

    // 注册 cleanup，cycle 销毁时自动 dlclose
    cln = ngx_pool_cleanup_add(cf->cycle->pool, 0);
    cln->handler = ngx_unload_module;
    cln->data = handle;

    // 从 .so 中解析三个符号
    modules = dlsym(handle, "ngx_modules");       // 模块数组
    names   = dlsym(handle, "ngx_module_names");   // 模块名数组
    order   = dlsym(handle, "ngx_module_order");   // 优先顺序（可选）

    for (i = 0; modules[i]; i++) {
        module = modules[i];
        module->name = names[i];
        ngx_add_module(cf, &file, module, order);
    }
    return NGX_CONF_OK;
}
```

流程三步走：

1. dlopen：使用 `RTLD_NOW | RTLD_GLOBAL` 标志加载 .so。`RTLD_NOW` 表示立即解析所有未定义符号，有链接错误马上报出来而不是推到运行时。**`RTLD_GLOBAL`** 是关键，，它把 .so 中的符号提升到全局符号表，后续加载的其它 .so 也能看到。但这也是双刃剑（见下文符号冲突）。

2. dlsym 提取模块符号：从 .so 中查找 `ngx_modules`、`ngx_module_names`、`ngx_module_order` 三个全局符号。注意这里的 `ngx_modules` 是 .so 内部定义的数组，不是主程序的静态数组。

3. ngx_add_module 逐个注册：对 .so 导出的每个模块，检查版本和签名兼容性，检查是否重名，分配全局 index 和 ctx_index，插入 `cycle->modules` 数组。

`ngx_dlopen.c` 对 `dlerror()` 做了简单的封装：

```c
char *ngx_dlerror(void) {
    char *err = (char *) dlerror();
    return (err == NULL) ? "" : err;
}
```

### 版本与签名检查

`ngx_add_module()` 最关键的前两步检查：

```c
// 版本检查：必须与 nginx_version 一致
if (module->version != nginx_version) {
    ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
        "module \"%V\" version %ui instead of %ui",
        file, module->version, (ngx_uint_t) nginx_version);
    return NGX_ERROR;
}

// 签名检查：二进制兼容性
if (ngx_strcmp(module->signature, NGX_MODULE_SIGNATURE) != 0) {
    ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
        "module \"%V\" is not binary compatible", file);
    return NGX_ERROR;
}
```

版本检查确保模块是为相同主版本编译的。签名检查则细粒度得多，，`NGX_MODULE_SIGNATURE` 是一个在编译时通过宏拼接的字符串，包含 35 个标志位：指针大小（4/8）、原子类型大小、time_t 大小、kqueue/epoll/inet6/linux 特性的启用状态、PCRE/SSL/GZIP 支持等。任意差异都会导致签名不匹配。这避免了把一个在 `--with-pcre` 下编译的模块加载到没有 PCRE 的主程序中。

### 符号冲突风险与隔离

`dlopen` 使用了 `RTLD_GLOBAL`，这意味着 .so 中定义的所有全局符号都会进入主程序的动态符号表。如果两个 .so 定义了同名函数（比如都有 `ngx_http_module_ctx`），后加载的那个会覆盖前面的。这在实际生产中是真实风险。

Nginx 的缓解策略很有限：

- `ngx_add_module()` 会检查模块名是否重复，但这是针对 `module->name` 字符串，不是 C 符号。
- 模块作者应尽量使用 `static` 限定内部符号，导出符号最好加上模块特定的前缀。
- `ngx_module_order` 允许配置模块加载顺序，间接缓解部分依赖问题。

本质上 Nginx 并没有实现真正的符号隔离，，这是 C 语言动态链接的固有局限。如果你需要完全的模块隔离，可以考虑 `RTLD_LOCAL` 配合手动导出，但 Nginx 选择了 `RTLD_GLOBAL` 以简化模块间符号共享。

## commands 数组：配置驱动的引擎

模块通过 `commands` 数组告诉配置解析器自己能处理哪些指令。`ngx_command_t` 的结构：

```c
struct ngx_command_s {
    ngx_str_t             name;       // 指令名（如 "worker_processes"）
    ngx_uint_t            type;       // 指令类型 + 位置掩码
    char               *(*set)(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
    ngx_uint_t            conf;       // 所属配置结构体偏移
    ngx_uint_t            offset;     // 字段在结构体中的偏移量
    void                 *post;       // 后处理回调或辅助数据
};
```

配置解析器在读取 `nginx.conf` 时，每当遇到一个指令名，就在所有模块的 `commands` 数组中线性查找。找到后调用 `set` 函数指针，传入当前 `ngx_conf_t` 上下文和待处理的配置结构体。

`ngx_core_module` 的 `ngx_core_commands` 包含指令如 `daemon`、`worker_processes`、`pid`、`load_module` 等。加载动态模块的 `load_module` 本身就是一条指令，由 `ngx_core_commands` 配置并指向 `ngx_load_module`。

这种设计让 Nginx 的配置解析极度模块化，，新增一个功能只需要写一个新的 `ngx_module_t` 变量，填充 commands 数组和对应的 set 函数，配置解析器完全不需要修改。

## 系列总结

至此，这个 Nginx 源码解析系列全部 12 篇完成了。回顾整个系列的脉络：

1. **整体架构总览**，，代码树结构、核心类型系统、模块体系、启动流程、整体数据流。所有后续分析的基础全景图。

2. **进程模型与生命周期**，，Master-Worker 进程模型、`ngx_master_process_cycle` / `ngx_worker_process_cycle`、信号处理、平滑升级（USR2 + WINCH）和热加载。

3. **事件驱动核心**，—`ngx_process_events_and_timers()` 主循环、epoll 封装、定时器红黑树、事件驱动模型如何支撑百万并发。

4. 内存管理，，`ngx_pool_t` 内存池（`ngx_create_pool` / `ngx_palloc` / `ngx_destroy_pool`）、大内存块管理、小内存块的 pool 分配链和释放机制。

5. 配置解析系统，，`ngx_conf_parse()` 的词法/语法分析、指令分派、配置上下文栈、Nginx 指令三层作用域（main/srv/loc）。

6. **HTTP 模块与请求处理**，—11 个 HTTP 处理阶段、phase handlers 的注册与调用顺序、request 结构体生命管理周期。

7. Upstream 与负载均衡，，upstream 的 server 选择、健康检查、`ngx_http_upstream_init` 的上下游数据流、失败重试策略、`peer.get()` 接口抽象。

8. 连接管理，，`ngx_connection_t` 的设计、预分配连接池、`cycle->files[]` 的 fd->connection 映射、客户端连接和 upstream 连接的连接生命周期。

9. 事件模块与定时器，，`ngx_event_actions_t` 接口层（add/del/enable/disable）、`ngx_event_timer.c` 的红黑树实现、定时器事件分发、`ngx_usec` 时间缓存。

10. 共享内存与锁，，slab allocator 的分区与 page 管理、`ngx_shmtx_t` 自旋锁实现、accept 互斥锁与惊群解决。

11. 配置解析进阶与变量系统，，变量索引、`ngx_http_variable_t` 的 get/set 回调、变量哈希表的构建、SSI 变量、`$arg_XXX` 动态变量实现。

12. **模块系统与动态加载**，—`ngx_module_t` 完整结构、`ngx_modules` 全局数组、`ngx_count_modules()` 和 `ngx_init_modules()` 的初始流程、`dlopen`/`dlsym` 动态加载、版本签名检查、符号冲突风险。

从第一篇文章俯瞰全景，到后面每篇文章深入一个子系统的实现细节，再到最后一篇收束回模块系统这个架构核心，—读完这 12 篇，你应该能清晰地回答 Nginx 最本质的问题：一个 18 万行 C 代码的 Web 服务器，是如何在极简的抽象下实现极致性能与极致灵活性的。

## 参考

- Nginx 1.24.x 源码: `src/core/ngx_module.h` / `src/core/ngx_module.c` / `src/core/nginx.c` / `src/os/unix/ngx_dlopen.c`
- Nginx 官方开发指南: https://nginx.org/en/docs/dev/development_guide.html
- `src/core/ngx_conf_file.h` ， ngx_command_t 定义
- `src/event/ngx_event.h` ， NGX_EVENT_MODULE
- `src/http/ngx_http_config.h` ， NGX_HTTP_MODULE
- `src/stream/ngx_stream.h` ， NGX_STREAM_MODULE
- `src/mail/ngx_mail.h` ， NGX_MAIL_MODULE
