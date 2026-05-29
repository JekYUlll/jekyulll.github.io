+++
title = 'Nginx 源码解析（十）：定时器系统'
date = '2026-05-29T17:40:19+08:00'
draft = false
author = 'JekYUlll'
categories = ['infra']
tags = ['nginx-source', 'nginx', 'timer', 'c']
+++

前面我们分析了 Nginx 事件循环的核心骨架，，`ngx_process_events_and_timers()` 是每个 worker 进程里的主循环函数。不过之前一直留了一个坑：传给 `epoll_wait` 的超时时间 timer 是怎么算出来的？超时后事件怎么处理？

这一篇就来填这个坑。Nginx 的定时器系统用红黑树管理所有事件超时，逻辑写得很紧凑，总共不到 300 行。

<!--more-->

## 为什么是红黑树，不是最小堆？

读代码之前有个问题值得先聊一下：定时器调度最经典的数据结构是最小堆（min-heap），插入 O(log n)、取最值 O(1)、空间紧凑，为什么 Nginx 选择了红黑树？

这个问题没有官方文档解释，我们从源码里的线索来推。Nginx 红黑树的插入操作在 `ngx_rbtree.c`，我们看 `ngx_rbtree_insert_timer_value`：

```c
void
ngx_rbtree_insert_timer_value(ngx_rbtree_node_t *temp,
    ngx_rbtree_node_t *node, ngx_rbtree_node_t *sentinel)
{
    ngx_rbtree_node_t  **p;

    for ( ;; ) {
        /*
         * Timer values
         * 1) are spread in small range, usually several minutes,
         * 2) and overflow each 49 days, if milliseconds are stored in 32 bits.
         * The comparison takes into account that overflow.
         */
        p = ((ngx_rbtree_key_int_t) (node->key - temp->key) < 0)
            ? &temp->left : &temp->right;

        if (*p == sentinel) {
            break;
        }
        temp = *p;
    }
    // ... attach node
}
```

注意注释里两条信息：

1. 定时器键值集中在几分钟的小范围内（"spread in small range, usually several minutes"）
2. `ngx_rbtree_key_t` 是 `ngx_uint_t`（32 位），溢出周期约 49 天

**最关键的不同在于键值比较用差值而不是直接大小**。`(ngx_rbtree_key_int_t)(node->key - temp->key) < 0` 这个写法是有意为之的，，它处理了 key 的溢出场景。因为 `ngx_current_msec` 是单调递增的毫秒数，每 49 天循环一次（2^32 ms ≈ 49.7 天），如果两个 key 一个在溢出前一个在溢出后，直接比较大小会出问题。用有符号差值就能正确处理：只要两个 key 的差距不超过 2^31 毫秒（约 24.8 天），差值符号就能反推出正确的顺序。

另外，红黑树支持**升序遍历**（通过 `ngx_rbtree_next`），这在某些调试和统计场景下有用。最小堆虽然取最值 O(1)，但遍历所有节点需要额外排序。Nginx 是 2002 年开始写的，当时红黑树的实现参考了《算法导论》，对 Igor Sysoev 来说是顺手的选择。

## ngx_rbtree_t：环绕哨兵的红黑树

Nginx 的红黑树定义在 `src/core/ngx_rbtree.h`：

```c
struct ngx_rbtree_node_s {
    ngx_rbtree_key_t       key;       // 定时器超时时间（毫秒）
    ngx_rbtree_node_t     *left;
    ngx_rbtree_node_t     *right;
    ngx_rbtree_node_t     *parent;
    u_char                 color;     // 0=黑, 1=红
    u_char                 data;      // 节点数据（极少使用）
};

struct ngx_rbtree_s {
    ngx_rbtree_node_t     *root;
    ngx_rbtree_node_t     *sentinel;  // 哨兵节点（代替 NULL）
    ngx_rbtree_insert_pt   insert;    // 插入策略函数指针
};
```

红黑树的标准性质不赘述了（根黑、叶黑、红子黑、黑高一致），Nginx 实现里有个特殊设计：**哨兵节点**。所有叶子节点的 left/right 不指向 NULL，而是指向全局的 `ngx_event_timer_sentinel` 节点。这避免了大量的 NULL 判断，左右旋和查找时统一处理 sentinel。

初始化宏：

```c
#define ngx_rbtree_init(tree, s, i)  \
    ngx_rbtree_sentinel_init(s);     \
    (tree)->root = s;                 \
    (tree)->sentinel = s;             \
    (tree)->insert = i
```

空树时 root 指向 sentinel 自身。第一个节点插入时会直接成为根节点并涂黑：

```c
if (*root == sentinel) {
    node->parent = NULL;
    node->left = sentinel;
    node->right = sentinel;
    ngx_rbt_black(node);
    *root = node;
    return;
}
```

### 插入与删除

`ngx_rbtree_insert()` 的逻辑分三步：

1. 空树则直接设置为根
2. 调用 `tree->insert` 函数指针执行二叉搜索树插入（定位插入位置）
3. 调整父子颜色，必要时左右旋，保持红黑树性质

对于定时器系统，`insert` 指针是 `ngx_rbtree_insert_timer_value`，，它用有符号差值比较 key 来处理溢出。

`ngx_rbtree_delete()` 的删除逻辑更复杂一些。它先找一个替代节点 `subst`（被删节点最多只有一个子节点时用自身，有两个子节点时用右子树的最小节点），然后调整颜色，最后执行删除修复（`while` 循环调整红黑性质）。

Nginx 在 DEBUG 模式下会把已删除节点的指针清零：

```c
#if (NGX_DEBUG)
    ev->timer.left = NULL;
    ev->timer.right = NULL;
    ev->timer.parent = NULL;
#endif
```

找最小节点的操作 `ngx_rbtree_min` 是个内联函数，一直往左走：

```c
static ngx_inline ngx_rbtree_node_t *
ngx_rbtree_min(ngx_rbtree_node_t *node, ngx_rbtree_node_t *sentinel)
{
    while (node->left != sentinel) {
        node = node->left;
    }
    return node;
}
```

这就是定时器查找最近超时事件的核心操作。

## 定时器事件结构

回头看 `ngx_event_t` 的定义。在 `src/event/ngx_event.h` 中：

```c
struct ngx_event_s {
    void            *data;
    // ... 各种标志位 ...
    unsigned         timedout:1;    // 标记：事件已超时
    unsigned         timer_set:1;   // 标记：事件已加入定时器树
    // ...
    ngx_log_t       *log;
    ngx_rbtree_node_t   timer;      // 红黑树节点（嵌入，不是指针！）
    ngx_queue_t      queue;        // 用于 posted event 队列
    ngx_event_handler_pt  handler;  // 事件回调函数
};
```

注意 `timer` 字段：它不是指针，而是 `ngx_rbtree_node_t` 结构体**嵌入**在 `ngx_event_t` 中。这意味着每个事件对象自身的 `timer.key` 字段就是它在红黑树中的键值（超时时间的毫秒值）。用 `ngx_rbtree_data()` 宏可以从红黑树节点指针反推出宿主 `ngx_event_t`：

```c
#define ngx_rbtree_data(node, type, link) \
    (type *) ((u_char *) (node) - offsetof(type, link))

// 使用示例：
ev = ngx_rbtree_data(node, ngx_event_t, timer);
```

这也说明了为什么 Nginx 用红黑树而非最小堆的另一个原因：节点嵌入在事件对象里，红黑树天然支持修改 key 后的重新插入（先删后插），而最小堆中更新一个节点的 key 需要上浮/下沉操作，实现上不够通用。

## ngx_add_timer / ngx_del_timer

这两个操作是 `static ngx_inline` 函数，定义在 `ngx_event_timer.h`：

```c
static ngx_inline void
ngx_event_add_timer(ngx_event_t *ev, ngx_msec_t timer)
{
    ngx_msec_t      key;
    ngx_msec_int_t  diff;

    key = ngx_current_msec + timer;  // 绝对超时时间

    if (ev->timer_set) {
        /*
         * 如果新旧键值差小于 300ms，就不操作。
         * 这叫 lazy delay，对快速连接减少红黑树操作。
         */
        diff = (ngx_msec_int_t) (key - ev->timer.key);
        if (ngx_abs(diff) < NGX_TIMER_LAZY_DELAY) {
            return;
        }
        ngx_del_timer(ev);  // 差异太大，先删旧的
    }

    ev->timer.key = key;
    ngx_rbtree_insert(&ngx_event_timer_rbtree, &ev->timer);
    ev->timer_set = 1;
}

static ngx_inline void
ngx_event_del_timer(ngx_event_t *ev)
{
    ngx_rbtree_delete(&ngx_event_timer_rbtree, &ev->timer);
#if (NGX_DEBUG)
    ev->timer.left = NULL;
    ev->timer.right = NULL;
    ev->timer.parent = NULL;
#endif
    ev->timer_set = 0;
}
```

值得注意的细节：

**Lazy Delay 优化**。当一个事件已经有定时器，并且新的超时时间和原来的差距小于 `NGX_TIMER_LAZY_DELAY`（300ms），就直接跳过更新。这针对的是 HTTP keepalive 场景，，每次请求可能都会重新设置超时时间，但实际超时值并没有变化多少，没必要反复删除插入红黑树。注释里说得很清楚："allows to minimize the rbtree operations for fast connections"。

**绝对时间**。`key = ngx_current_msec + timer`。定时器树中存的是绝对超时毫秒值，而不是相对延迟。这使得 `ngx_event_find_timer()` 可以直接用 `node->key - ngx_current_msec` 计算剩余时间，也方便处理 49 天溢出。

宏别名使得调用代码更简洁：

```c
#define ngx_add_timer   ngx_event_add_timer
#define ngx_del_timer   ngx_event_del_timer
```

## ngx_event_timer_init：全局红黑树

在 `ngx_event_timer.c` 中，声明了两个全局变量：

```c
ngx_rbtree_t              ngx_event_timer_rbtree;
static ngx_rbtree_node_t  ngx_event_timer_sentinel;
```

初始化函数：

```c
ngx_int_t
ngx_event_timer_init(ngx_log_t *log)
{
    ngx_rbtree_init(&ngx_event_timer_rbtree, &ngx_event_timer_sentinel,
                    ngx_rbtree_insert_timer_value);
    return NGX_OK;
}
```

使用 `ngx_rbtree_insert_timer_value` 作为插入函数，—它用有符号差值比较 key，正确处理 32 位毫秒值的溢出。

## ngx_event_find_timer：找出最近超时

```c
ngx_msec_t
ngx_event_find_timer(void)
{
    ngx_msec_int_t      timer;
    ngx_rbtree_node_t  *node, *root, *sentinel;

    if (ngx_event_timer_rbtree.root == &ngx_event_timer_sentinel) {
        return NGX_TIMER_INFINITE;  // 空树，返回 -1
    }

    root = ngx_event_timer_rbtree.root;
    sentinel = ngx_event_timer_rbtree.sentinel;

    node = ngx_rbtree_min(root, sentinel);  // 最左节点 = 最小 key

    timer = (ngx_msec_int_t) (node->key - ngx_current_msec);

    return (ngx_msec_t) (timer > 0 ? timer : 0);  // 已超时的返回 0
}
```

逻辑很清晰：取最左节点（红黑树中 key 最小的节点）→ 和当前时间做差 → 如果已经超时了返回 0，否则返回剩余毫秒数。

这里返回的 timer 值就是传给 `epoll_wait` 的超时参数。如果所有定时器都还没到时间，epoll_wait 最多等 `timer` 毫秒后返回，然后处理超时事件。

注意 `NGX_TIMER_INFINITE` 定义为 `(ngx_msec_t) -1`，在 `ngx_process_events_and_timers` 中会判断：如果是 `NGX_TIMER_INFINITE`，传给 epoll_wait 的 timer 就是 -1（表示无限等待，直到有事件发生）。

## ngx_event_expire_timers：批量超时处理

```c
void
ngx_event_expire_timers(void)
{
    ngx_event_t        *ev;
    ngx_rbtree_node_t  *node, *root, *sentinel;

    sentinel = ngx_event_timer_rbtree.sentinel;

    for ( ;; ) {
        root = ngx_event_timer_rbtree.root;

        if (root == sentinel) {
            return;  // 树空了
        }

        node = ngx_rbtree_min(root, sentinel);

        /* node->key > ngx_current_msec */
        if ((ngx_msec_int_t) (node->key - ngx_current_msec) > 0) {
            return;  // 所有未超时
        }

        ev = ngx_rbtree_data(node, ngx_event_t, timer);

        ngx_rbtree_delete(&ngx_event_timer_rbtree, &ev->timer);
        ev->timer_set = 0;
        ev->timedout = 1;            // 标记超时
        ev->handler(ev);             // 调用事件回调
    }
}
```

这是一个循环：每次取最小节点→如果已超时→从树中删除→设置 `timedout=1`→调用 `ev->handler(ev)`→继续下一个。因为红黑树是按 key 排好序的，最小节点就是最先超时的事件，每次取最左节点就行。

注意 `ev->timedout = 1` 这个字段。事件处理函数（比如连接的读写 handler）通过检查 `ev->timedout` 来判断是正常 I/O 事件触发还是超时触发，从而走不同的分支，—比如连接超时就关闭连接，或者重试。

## 事件循环中的整合

回到 `src/event/ngx_event.c` 的 `ngx_process_events_and_timers()` 函数，完整的定时器整合流程：

```c
void
ngx_process_events_and_timers(ngx_cycle_t *cycle)
{
    ngx_uint_t  flags;
    ngx_msec_t  timer, delta;

    if (ngx_timer_resolution) {
        // 使用 itimer 信号的话，epoll_wait 无限等待
        timer = NGX_TIMER_INFINITE;
        flags = 0;
    } else {
        // 正常路径：计算最近超时时间
        timer = ngx_event_find_timer();
        flags = NGX_UPDATE_TIME;

#if (NGX_WIN32)
        if (timer == NGX_TIMER_INFINITE || timer > 500) {
            timer = 500;  // Windows 信号处理限制
        }
#endif
    }

    // accept mutex 相关处理...
    if (ngx_use_accept_mutex) {
        // ... 可能缩短 timer ...
    }

    // 如果有待处理的下一次事件队列，立即处理，timer 设为 0
    if (!ngx_queue_empty(&ngx_posted_next_events)) {
        ngx_event_move_posted_next(cycle);
        timer = 0;
    }

    delta = ngx_current_msec;
    (void) ngx_process_events(cycle, timer, flags);  // ← epoll_wait 在这里
    delta = ngx_current_msec - delta;                 // 计算实际耗时

    // 处理 accept 后置事件
    ngx_event_process_posted(cycle, &ngx_posted_accept_events);

    if (ngx_accept_mutex_held) {
        ngx_shmtx_unlock(&ngx_accept_mutex);
    }

    ngx_event_expire_timers();       // ← 处理超时的定时器

    ngx_event_process_posted(cycle, &ngx_posted_events);  // 处理普通后置事件
}
```

完整的调用链：

```
ngx_process_events_and_timers()
  ├─ ngx_event_find_timer()           → 计算最近超时时间 timer
  ├─ ngx_process_events(timer)        → epoll_wait(epfd, events, nevents, timer)
  │   └─ ngx_time_update()            ← epoll_wait 返回后立即更新缓存时间
  │       └─ ngx_current_msec = ...   ← 更新全局毫秒时间
  ├─ ngx_event_expire_timers()        → 遍历红黑树，处理所有超时事件
  │   ├─ ngx_rbtree_min()             → 取最左节点（最近超时）
  │   ├─ node->key ≤ ngx_current_msec → 已超时
  │   ├─ ngx_rbtree_delete()          → 从树中移除
  │   └─ ev->handler(ev)              → 回调
  └─ ngx_event_process_posted()       → 处理后置事件队列
```

## ngx_timer_resolution 模式

当配置了 `timer_resolution` 指令时，Nginx 会使用 setitimer(ITIMER_REAL) 定时信号来定期中断 epoll_wait。这时 `ngx_event_find_timer()` 的返回值被忽略，epoll_wait 无限等待（`timer = NGX_TIMER_INFINITE`），由信号驱动时间更新。信号处理函数中设置 `ngx_event_timer_alarm = 1`，在 epoll 模块中检测到此标志后调用 `ngx_time_update()`。

这种方式精确度由内核的 itimer 保证，适用于需要极精确计时的场景，但信号处理有额外开销。

## ngx_current_msec 与时间缓存

定时器系统依赖 `ngx_current_msec` 这个全局变量。它定义在 `ngx_times.c` 中：

```c
volatile ngx_msec_t  ngx_current_msec;
```

`ngx_time_update()` 每次通过 `gettimeofday` 或 `clock_gettime(CLOCK_MONOTONIC)` 更新它。关键点：**时间更新发生在 epoll_wait 返回之后、事件回调之前**。这保证了在同一轮事件循环中，所有事件看到的 `ngx_current_msec` 是一致的。

在 `src/event/modules/ngx_epoll_module.c` 中：

```c
events = epoll_wait(ep, event_list, (int) nevents, timer);
// ...
if (flags & NGX_UPDATE_TIME || ngx_event_timer_alarm) {
    ngx_time_update();
}
```

注意 `ngx_process_events_and_timers` 在调用 `ngx_process_events` 时传了 `flags = NGX_UPDATE_TIME`，所以每次 epoll_wait 返回后都会更新一次时间。然后事件处理函数和定时器过期检查都基于这个统一的时间戳进行比较。

`ngx_event_find_timer` 计算差值时用了 `ngx_current_msec`，`ngx_event_expire_timers` 判断超时也用了 `ngx_current_msec`，所以时间更新的时机直接决定了定时器的精度，—精度在毫秒级，但受限于事件循环的调度粒度。

## 关键设计归纳

| 方面 | 实现 | 说明 |
|------|------|------|
| 数据结构 | 红黑树（`ngx_rbtree_t`） | 嵌入在事件对象中的节点 |
| 键值 | 绝对超时毫秒数（`ngx_current_msec + timer`） | 每 49 天溢出，用有符号差值比较 |
| 查找最近超时 | `ngx_rbtree_min()` 取最左节点 | O(log n) |
| 插入 | `ngx_rbtree_insert()` | O(log n)，Lazy Delay 优化减少操作 |
| 删除 | `ngx_rbtree_delete()` | O(log n)，DEBUG 模式下清空指针 |
| 过期检查 | 循环取最左节点直到未超时 | 每次事件循环执行一次 |
| 时间源 | `ngx_current_msec`（`ngx_time_update()`更新） | epoll_wait 返回后立即更新 |
| 与事件循环集成 | `ngx_process_events_and_timers` | epoll_wait(timer) → expire_timers |

## 总结

Nginx 的定时器系统是事件循环的灵魂组件。红黑树的选择虽不如最小堆极致，但代码实现干净、通用，且天然支持溢出安全的键值比较和升序遍历。全系统加起来不过 300 行代码，却支撑了数百万并发连接的超时管理，—每次 `ngx_process_events_and_timers` 循环里，`epoll_wait` 拿到的 timer 精确到下一个即将超时的事件，`ngx_event_expire_timers` 则在 epoll 返回后一次性收走所有到期的定时器。

下一篇我们继续深入事件系统，看 Nginx 的 posted event 队列如何实现延迟事件处理。

---

**下一篇预告：** Nginx 源码解析（十一）：Posted Event 与延迟处理

## 参考

- `/tmp/nginx-src/src/core/ngx_rbtree.h` ， 红黑树定义
- `/tmp/nginx-src/src/core/ngx_rbtree.c` ， 红黑树实现
- `/tmp/nginx-src/src/event/ngx_event_timer.h` ， 定时器添加/删除 inline 函数
- `/tmp/nginx-src/src/event/ngx_event_timer.c` ， 定时器初始化、查找、过期处理
- `/tmp/nginx-src/src/event/ngx_event.c` ， `ngx_process_events_and_timers`
- `/tmp/nginx-src/src/event/ngx_event.h` ， `ngx_event_t` 结构体定义
- `/tmp/nginx-src/src/core/ngx_times.c` ， `ngx_time_update` 和 `ngx_current_msec`
- `/tmp/nginx-src/src/event/modules/ngx_epoll_module.c` ， epoll 中时间更新点
