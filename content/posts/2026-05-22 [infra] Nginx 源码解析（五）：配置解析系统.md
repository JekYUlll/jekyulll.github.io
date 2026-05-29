+++
title = 'Nginx 源码解析（五）：配置解析系统'
date = '2026-05-22T12:00:00+08:00'
weight = 8
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'config', 'c']
+++

Nginx 的配置系统不是简单的 INI 解析，，它是一整套声明式 DSL，ngx_conf_parse() 递归地喂给 ngx_conf_handler()，后者根据指令名匹配模块、校验上下文和参数，最后调用模块注册的 set 回调填充配置结构体。今天把这套机制拆干净。

<!--more-->

## ngx_command_t：模块的指令清单

每个模块通过 commands 数组告诉解析器自己能处理哪些指令。结构体定义在 ngx_conf_file.h：

```c
struct ngx_command_s {
    ngx_str_t             name;     // 指令名，如 "listen"
    ngx_uint_t            type;     // 类型位掩码
    char               *(*set)(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
    ngx_uint_t            conf;     // 配置层级偏移（main/srv/loc）
    ngx_uint_t            offset;   // 在配置结构体中的字段偏移
    void                 *post;     // 后处理钩子
};
```

`set` 是回调函数指针，，指令被匹配成功后由 ngx_conf_handler() 调用。Nginx 内置了几十个通用 set 函数：`ngx_conf_set_flag_slot()`、`ngx_conf_set_str_slot()`、`ngx_conf_set_num_slot()`、`ngx_conf_set_enum_slot()` 等等。多数模块直接复用这些，offset 指向配置结构体里对应字段，set 负责根据 args 里的值做类型转换然后写入。

`conf` 和 `offset` 配合使用。conf 标明指令属于哪个配置层级（main_conf / srv_conf / loc_conf），offset 标明在当前层级配置结构体中的字段偏移。用 offsetof + 指针运算，比任何配置中心都快。

`post` 用于后处理钩子，典型的用法是 ngx_conf_post_t 绑定 ngx_conf_check_num_bounds() 做数值范围校验，或者 ngx_conf_deprecated_t 做旧指令迁移。

最后用 `ngx_null_command` 作为哨兵结尾。

## 类型位掩码：指令在哪、要几个参数

type 字段是一个 32 位位掩码，低 8 位表示参数数量：

```c
#define NGX_CONF_NOARGS      0x00000001
#define NGX_CONF_TAKE1       0x00000002
// ... 一直到 TAKE7
#define NGX_CONF_TAKE12      (NGX_CONF_TAKE1|NGX_CONF_TAKE2)  // 1或2个参数
#define NGX_CONF_MAX_ARGS    8
```

高 8 位是上下文约束位：

```c
#define NGX_MAIN_CONF        0x01000000
#define NGX_ANY_CONF         0xFF000000
```

HTTP 系列在 ngx_http_config.h 定义：

```c
#define NGX_HTTP_MAIN_CONF   0x02000000
#define NGX_HTTP_SRV_CONF    0x04000000
#define NGX_HTTP_LOC_CONF    0x08000000
#define NGX_HTTP_UPS_CONF    0x10000000
#define NGX_HTTP_SIF_CONF    0x20000000
#define NGX_HTTP_LIF_CONF    0x40000000
#define NGX_HTTP_LMT_CONF    0x80000000
```

中间还有 NGX_CONF_BLOCK (0x100) 表示指令带 `{}` 块，NGX_CONF_FLAG (0x200) 表示值是 on/off，NGX_CONF_ANY (0x400) 表示接受任意参数。

比如 `http {}` 的定义是 `NGX_MAIN_CONF|NGX_CONF_BLOCK`，`server {}` 是 `NGX_HTTP_MAIN_CONF|NGX_CONF_BLOCK`。`listen` 是 `NGX_HTTP_SRV_CONF|NGX_CONF_TAKE1`。

ngx_conf_handler() 里有一大段校验：

```c
if (!(cmd->type & NGX_CONF_BLOCK) && last != NGX_OK) {
    // 不是块指令但遇到了 "{"
    ngx_conf_log_error(..., "directive \"%s\" is not terminated by \";\"");
    return NGX_ERROR;
}
if ((cmd->type & NGX_CONF_BLOCK) && last != NGX_CONF_BLOCK_START) {
    // 是块指令但没看到 "{"
    ngx_conf_log_error(..., "directive \"%s\" has no opening \"{\"");
    return NGX_ERROR;
}
// 参数数量校验
if (!(cmd->type & NGX_CONF_ANY)) { ... }
```

不允许的就直接 NGX_ERROR 退出了。

## ngx_conf_t：解析上下文

ngx_conf_file.h 中：

```c
struct ngx_conf_s {
    char                 *name;
    ngx_array_t          *args;       // 当前指令的参数数组
    ngx_cycle_t          *cycle;
    ngx_pool_t           *pool;       // 永久内存池
    ngx_pool_t           *temp_pool;  // 临时内存池（每次解析完清空）
    ngx_conf_file_t      *conf_file;  // 当前文件指针/行号
    ngx_log_t            *log;
    void                 *ctx;        // 当前配置上下文指针
    ngx_uint_t            module_type;// 当前模块类型过滤
    ngx_uint_t            cmd_type;   // 当前允许的指令类型
    ngx_conf_handler_pt   handler;    // 自定义处理器（如 types 块）
    void                 *handler_conf;
};
```

ngx_init_cycle() 里初始化这个结构：

```c
ngx_memzero(&conf, sizeof(ngx_conf_t));
conf.args = ngx_array_create(pool, 10, sizeof(ngx_str_t));
conf.temp_pool = ngx_create_pool(NGX_CYCLE_POOL_SIZE, log);
conf.ctx = cycle->conf_ctx;
conf.cycle = cycle;
conf.pool = pool;
conf.log = log;
conf.module_type = NGX_CORE_MODULE;   // 先只处理 CORE 模块
conf.cmd_type = NGX_MAIN_CONF;       // 最顶层上下文
```

temp_pool 在每次配置解析循环后可以被释放，pool 则持续到整个 cycle 生命周期。args 动态增长，填完一次指令后由 ngx_conf_read_token() 重置 nelts = 0。

## ngx_conf_parse()：递归解析器

ngx_conf_file.c 的 ngx_conf_parse() 是整个配置系统的入口。它分为三种模式：

- **parse_file**：打开配置文件，分配缓冲区，初始化行号
- **parse_block**：进入块上下文（`{ }` 内部），递归调用自身
- **parse_param**：解析 -g 命令行参数

核心循环：

```c
for ( ;; ) {
    rc = ngx_conf_read_token(cf);
    // 返回值含义：
    //   NGX_ERROR              -> 解析错误
    //   NGX_OK                 -> 普通指令，以 ";" 结束
    //   NGX_CONF_BLOCK_START   -> 块开始，以 "{" 结束
    //   NGX_CONF_BLOCK_DONE    -> 块结束 "}"
    //   NGX_CONF_FILE_DONE     -> 文件结束

    if (rc == NGX_CONF_BLOCK_DONE) { goto done; }
    if (rc == NGX_CONF_FILE_DONE)  { goto done; }

    // 检查自定义 handler（如 types 块）
    if (cf->handler) {
        rv = (*cf->handler)(cf, NULL, cf->handler_conf);
        ...
    }

    rc = ngx_conf_handler(cf, rc);
}
```

遇到 `NGX_CONF_BLOCK_START` 时，ngx_conf_handler() 里某个模块的 set 回调会在下层修改 cf->ctx、cf->cmd_type、cf->module_type，然后再次调用 ngx_conf_parse() 处理块内容。典型的例子是 http 模块：

```
http {  // ngx_http_module 的 set 回调：
        //   1. 分配 ngx_http_conf_ctx_t
        //   2. 调用所有 HTTP 模块的 create_main_conf / create_srv_conf
        //   3. 设置 cf->ctx = 新分配的 ctx
        //   4. 设置 cf->cmd_type = NGX_HTTP_MAIN_CONF
        //   5. 调用 ngx_conf_parse(cf, NULL) 递归处理块内内容
  server { ... }  // 同上，嵌套
  location { ... } // 同上，再嵌套
}
```

ngx_conf_read_token() 是词法分析器，逐字符解析，处理引号、注释、变量展开（$ 前缀）。4KB 缓冲区（NGX_CONF_BUFFER），读不满时回退到 start 并继续读磁盘。

## ngx_conf_handler()：指令匹配与分发

ngx_conf_handler() 是解析器的核心调度函数。它遍历所有已加载模块的 commands 数组，匹配指令名：

```c
name = cf->args->elts;

for (i = 0; cf->cycle->modules[i]; i++) {
    cmd = cf->cycle->modules[i]->commands;
    if (cmd == NULL) continue;

    for (; cmd->name.len; cmd++) {
        if (name->len != cmd->name.len) continue;
        if (ngx_strcmp(name->data, cmd->name.data) != 0) continue;

        // 匹配成功，开始权限校验
        found = 1;

        // 1. 模块类型校验
        if (cf->cycle->modules[i]->type != NGX_CONF_MODULE
            && cf->cycle->modules[i]->type != cf->module_type)
            continue;

        // 2. 指令上下文校验（cmd_type 位掩码）
        if (!(cmd->type & cf->cmd_type))
            continue;

        // 3. 参数数量校验（前面说过的位掩码匹配）
        // ...

        // 4. 定位配置结构体
        conf = resolve_conf(cf, cmd, module_index);

        // 5. 调用 set 回调
        rv = cmd->set(cf, cmd, conf);
        ...
    }
}
```

定位 conf 的逻辑值得细看，—三种情况：

- **NGX_DIRECT_CONF**：直接取 `((void**)cf->ctx)[module->index]`（用于 core 模块）
- **NGX_MAIN_CONF**：取 `&((void**)cf->ctx)[module->index]`（指针的指针，因为 HTTP 模块要存储的是 ctx->main_conf 这个级别）
- **HTTP_SRV/LOC_CONF**：`confp = *(void**)((char*)cf->ctx + cmd->conf)` 然后取 `confp[module->ctx_index]`

第三种是关键：cmd->conf 存的是 offsetof(ngx_http_conf_ctx_t, srv_conf) 或 loc_conf，然后通过 ctx_index（按模块类型排的索引，不是全局 index）拿到该模块的配置指针。

## void ****conf_ctx：四级指针的疯狂

Nginx 的配置上下文是一个四级指针：

```c
cycle->conf_ctx = ngx_pcalloc(pool, ngx_max_module * sizeof(void *));
```

`cycle->conf_ctx` 是 `void **`（数组，每个元素是一个模块的配置）。对于 core 模块，每个元素直接指向该模块的配置结构体。

对于 HTTP 模块，元素指向 `ngx_http_conf_ctx_t`：

```c
typedef struct {
    void        **main_conf;  // void* 数组，每个 HTTP 模块一个
    void        **srv_conf;   // void* 数组
    void        **loc_conf;   // void* 数组
} ngx_http_conf_ctx_t;
```

所以取一个 HTTP 模块在 server 级别的配置：`cycle->conf_ctx[ngx_http_module.index]->srv_conf[ngx_http_proxy_module.ctx_index]`。

翻译成指针级数：`cycle->conf_ctx` 是 `void**`，取 index 得到 `ngx_http_conf_ctx_t*`，取 srv_conf 得到 `void**`，取 ctx_index 得到配置实体。所以 ngx_http_conf_ctx_t* 本身是 `void*`，放回数组：`void* conf_ctx[] -> void* (ngx_http_conf_ctx_t*) -> void** (srv_conf[]) -> void* (proxy_module 的配置)`。

四级指针就此成型。不熟悉 C 的人看着头皮发麻，但内存布局其实极其规整。

## 辅助类型：enum 和 bitmask

ngx_conf_enum_t 把字符串映射到整数：

```c
typedef struct {
    ngx_str_t      name;
    ngx_uint_t     value;
} ngx_conf_enum_t;

// 使用示例 — ngx_http_core_module 中：
static ngx_conf_enum_t  ngx_http_core_keepalive[] = {
    { ngx_string("off"), 0 },
    { ngx_string("on"),  1 },
    { ngx_null_string, 0 }
};
```

ngx_conf_set_enum_slot() 遍历这个表，匹配字符串后把 value 写入 offset 指向的字段。

ngx_conf_bitmask_t 类似，但支持 OR 叠加：

```c
typedef struct {
    ngx_str_t      name;
    ngx_uint_t     mask;
} ngx_conf_bitmask_t;

// 用于限流算法等可叠加选项
```

## 变量系统：编译时与运行时

ngx_http_variable_t 定义在 ngx_http_variables.h：

```c
struct ngx_http_variable_s {
    ngx_str_t                     name;         // 变量名，如 "http_host"
    ngx_http_set_variable_pt      set_handler;  // 写回调（极少用）
    ngx_http_get_variable_pt      get_handler;  // 读回调
    uintptr_t                     data;         // 传给回调的上下文数据
    ngx_uint_t                    flags;        // CHANGEABLE / NOCACHEABLE 等
    ngx_uint_t                    index;        // 编译时分配的索引
};
```

编译时变量在 ngx_http_variables_add_core_vars() 中注册：

```c
static ngx_http_variable_t  ngx_http_core_variables[] = {
    { ngx_string("http_host"), NULL, ngx_http_variable_header,
      offsetof(ngx_http_request_t, headers_in.host), 0, 0 },
    { ngx_string("http_user_agent"), NULL, ngx_http_variable_header,
      offsetof(ngx_http_request_t, headers_in.user_agent), 0, 0 },
    // ...
    ngx_http_null_variable
};
```

ngx_http_add_variable() 把变量插入哈希表并分配 index。运行时通过 ngx_http_get_indexed_variable(r, index) 按索引快速取值，—哈希查找只做一次，后续是 O(1) 数组访问。

`data` 字段通常是 offsetof 请求结构体的某个字段，get_handler 直接通过 data 偏移读取，省去字符串比较。

## 热加载：old_cycle 的内存复用

SIGHUP 时 master 进程重新调用 ngx_init_cycle(old_cycle)，传入旧的 cycle 作为基准。

ngx_init_cycle() 的关键逻辑：

1. 分配新 pool 和新 cycle，拷贝路径、日志配置等元数据
2. `cycle->old_cycle = old_cycle` 保存旧引用
3. 分配新 `cycle->conf_ctx`，调用核心模块的 create_conf
4. 设置 conf.ctx = cycle->conf_ctx，调用 ngx_conf_parse() 重新解析配置文件
5. 调用各模块的 init_conf 做后处理
6. 打开新文件、新监听 socket
7. **关闭旧文件中不再需要的 fd**（对比新旧 open_files、listening、shared_memory 列表）
8. 把旧 cycle 扔进 ngx_old_cycles 队列，30 秒后由 cleaner 事件释放

```c
// 分配旧 cycle 中已经存在的共享内存块：如果 tag 和 size 匹配就直接复用
if (oshm_zone[i].tag == shm_zone[n].tag
    && oshm_zone[i].shm.size == shm_zone[n].shm.size
    && !oshm_zone[i].noreuse)
{
    goto live_shm_zone;  // 保留，不 free
}
ngx_shm_free(&oshm_zone[i].shm);
```

这段逻辑保证热加载时共享内存不丢失、监听 socket 不断连。worker 进程用 old_cycle 的 socket 继续服务，只在新 cycle 准备就绪后平滑切换。

关于配置合并，每个 HTTP 模块的 merge_srv_conf / merge_loc_conf 回调负责从 `prev`（上一级的继承值）跟 `conf`（当前显式配置值）做合并，调用的宏如 `ngx_conf_merge_value(conf, prev, default)`。未显式配置的字段保持 NGX_CONF_UNSET 后继承上层，这条链一直走到最顶层。

## 下一篇预告

第六篇会切到事件处理体系，—ngx_event_actions 接口、epoll 事件循环的 ngx_epoll_process_events() 实现、EPOLLRDHUP 和 EPOLLEXCLUSIVE 的关键细节，以及定时器红黑树如何支撑百万并发。

## 参考

- /tmp/nginx-src/src/core/ngx_conf_file.h ， ngx_command_t, ngx_conf_t 定义
- /tmp/nginx-src/src/core/ngx_conf_file.c ， ngx_conf_parse, ngx_conf_handler, ngx_conf_read_token 实现
- /tmp/nginx-src/src/core/ngx_cycle.c ， ngx_init_cycle 配置加载与热加载
- /tmp/nginx-src/src/http/ngx_http_config.h ， ngx_http_conf_ctx_t 与 HTTP 配置层级常量
- /tmp/nginx-src/src/http/ngx_http_variables.h / .c ， 变量系统定义与核心变量注册
- [Nginx Development Guide](https://nginx.org/en/docs/dev/development_guide.html)
