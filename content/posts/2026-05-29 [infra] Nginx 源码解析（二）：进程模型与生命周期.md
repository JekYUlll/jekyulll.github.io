+++
date = '2026-05-29T17:40:00+08:00'
draft = false
title = 'Nginx 源码解析（二）：进程模型与生命周期'
author = 'JekYUlll'
lastmod = '2026-05-29T17:40:09+08:00'
tags = ['nginx-source', 'nginx', 'process-model', 'c']
categories = ['infra']
+++

上一篇文章从宏观架构角度剖析了 Nginx 多进程模型的优势。本篇将深入 Nginx 1.24.x 源码，逐行拆解进程的生命周期，从 `main()` 入口到 Master 主循环，再到 Worker 工作循环，看看这些进程如何被创建、管理、通信以及优雅退出。

---

#### 一、进程类型定义

Nginx 通过 `src/os/unix/ngx_process_cycle.h` 中的一组宏定义进程类型：

```c
#define NGX_PROCESS_SINGLE     0
#define NGX_PROCESS_MASTER     1
#define NGX_PROCESS_SIGNALLER  2
#define NGX_PROCESS_WORKER     3
#define NGX_PROCESS_HELPER     4
```

全局变量 `ngx_process` 标记当前进程的角色。Master 进程初始值为 `NGX_PROCESS_SINGLE`（0），在 `main()` 中检测到 `ccf->master` 开启后提升为 `NGX_PROCESS_MASTER`；Worker fork 成功后立即设为 `NGX_PROCESS_WORKER`。这套标识配合 `ngx_signal_handler()` 中的 switch 分支，实现了同一套信号处理函数在不同角色下的差异化行为。

---

#### 二、main()：入口处的三件大事

`src/core/nginx.c` 中的 `main()` 函数在调用 `ngx_master_process_cycle()` 之前做了三项关键准备：

**1. 初始化信号处理器**

```c
if (ngx_init_signals(cycle->log) != NGX_OK) {
    return 1;
}
```

`ngx_init_signals()` 遍历 `signals[]` 数组，为每个信号注册 `sa_sigaction` 回调。SIGPIPE 和 SIGSYS 注册为 `SIG_IGN`（忽略），其余均绑定 `ngx_signal_handler`。Nginx 使用 `SA_SIGINFO` 标志位，确保 handler 能接收到 `siginfo_t` 结构体，从而获取发送信号的 PID。

**2. 守护进程化**

```c
if (!ngx_inherited && ccf->daemon) {
    if (ngx_daemon(cycle->log) != NGX_OK) {
        return 1;
    }
    ngx_daemonized = 1;
}
```

`src/os/unix/ngx_daemon.c` 中的 `ngx_daemon()` 实现标准的 Unix 守护进程流程：

- `fork()` 后父进程 `exit(0)`，子进程继续
- 调用 `setsid()` 创建新会话，脱离控制终端
- `umask(0)` 重置文件权限掩码
- 打开 `/dev/null`，通过 `dup2()` 将 stdin/stdout 重定向至此

**3. 分支选择**

```c
if (ngx_process == NGX_PROCESS_SINGLE) {
    ngx_single_process_cycle(cycle);
} else {
    ngx_master_process_cycle(cycle);
}
```

单进程模式（`master_process off`）下走 `ngx_single_process_cycle()`，该函数将 Master 和 Worker 职责合并到一个进程中运行，主要用于调试。生产环境走 `ngx_master_process_cycle()`。

---

#### 三、ngx_master_process_cycle()：Master 主循环

`src/os/unix/ngx_process_cycle.c` 中的 `ngx_master_process_cycle()` 是 Nginx 的核心控制循环。它的工作流可以概括为：阻塞信号 → fork 子进程 → 信号驱动的事件循环。

**3.1 信号掩码初始化**

```c
sigemptyset(&set);
sigaddset(&set, SIGCHLD);
sigaddset(&set, SIGALRM);
sigaddset(&set, SIGIO);
sigaddset(&set, SIGINT);
sigaddset(&set, ngx_signal_value(NGX_RECONFIGURE_SIGNAL));  // SIGHUP
sigaddset(&set, ngx_signal_value(NGX_REOPEN_SIGNAL));       // SIGUSR1
sigaddset(&set, ngx_signal_value(NGX_NOACCEPT_SIGNAL));     // SIGWINCH
sigaddset(&set, ngx_signal_value(NGX_TERMINATE_SIGNAL));    // SIGTERM
sigaddset(&set, ngx_signal_value(NGX_SHUTDOWN_SIGNAL));     // SIGQUIT
sigaddset(&set, ngx_signal_value(NGX_CHANGEBIN_SIGNAL));    // SIGUSR2

sigprocmask(SIG_BLOCK, &set, NULL);
```

Master 进程将所有关键信号加入阻塞集，然后调用 `sigprocmask(SIG_BLOCK)` 统一阻塞。线程随后进入 `sigsuspend(&set)`，以原子方式释放阻塞并等待信号。这是典型的信号驱动事件循环模式。

**3.2 启动 Worker 进程**

```c
ccf = (ngx_core_conf_t *) ngx_get_conf(cycle->conf_ctx, ngx_core_module);
ngx_start_worker_processes(cycle, ccf->worker_processes, NGX_PROCESS_RESPAWN);
ngx_start_cache_manager_processes(cycle, 0);
```

`ngx_start_worker_processes()` 循环调用 `ngx_spawn_process()`，为每个 Worker 传入回调 `ngx_worker_process_cycle`。`NGX_PROCESS_RESPAWN` 标记告诉进程管理系统：该 Worker 崩溃后应自动重新 spawn。

**3.3 信号驱动的主循环**

```c
for ( ;; ) {
    sigsuspend(&set);          // 挂起等待信号

    if (ngx_reap) {
        live = ngx_reap_children(cycle);   // SIGCHLD → 回收子进程
    }

    if (ngx_terminate) {
        ngx_signal_worker_processes(cycle, SIGTERM);  // 终止
        if (delay > 1000) ngx_signal_worker_processes(cycle, SIGKILL);
    }

    if (ngx_quit) {
        ngx_signal_worker_processes(cycle, SIGQUIT);  // 优雅关闭
        ngx_close_listening_sockets(cycle);
    }

    if (ngx_reconfigure) {
        cycle = ngx_init_cycle(cycle);      // SIGHUP → 热加载配置
        ngx_start_worker_processes(cycle, ..., NGX_PROCESS_JUST_RESPAWN);
        ngx_signal_worker_processes(cycle, SIGQUIT);  // 通知旧 Worker 退出
    }

    if (ngx_reopen) {
        ngx_reopen_files(cycle, ccf->user);    // SIGUSR1 → 重新打开日志
    }

    if (ngx_change_binary) {
        ngx_new_binary = ngx_exec_new_binary(cycle, ngx_argv);  // SIGUSR2 → 平滑升级
    }

    if (ngx_noaccept) {
        ngx_signal_worker_processes(cycle, SIGQUIT);  // SIGWINCH → 停止接受连接
    }
}
```

每个 `sig_atomic_t` 全局变量（`ngx_reap`、`ngx_terminate` 等）都在信号处理函数中被置位，主循环在 `sigsuspend` 返回后逐一检查。这种设计保证了信号处理的原子性与主循环的执行安全。

---

#### 四、ngx_spawn_process()：进程创建的底层细节

`src/os/unix/ngx_process.c` 中的 `ngx_spawn_process()` 是 Nginx 创建子进程的唯一入口。其核心流程如下：

```c
pid = fork();

switch (pid) {
case -1:  // fork 失败，关闭 channel
    ngx_close_channel(ngx_processes[s].channel, cycle->log);
    return NGX_INVALID_PID;

case 0:   // 子进程
    ngx_parent = ngx_pid;
    ngx_pid = ngx_getpid();
    proc(cycle, data);    // 调用 proc 回调，不会返回
    break;

default:  // 父进程（Master）
    break;
}

ngx_processes[s].pid = pid;
ngx_processes[s].exited = 0;
// 根据 respawn 类型设置 flags...
```

关键前置操作：fork 之前，Master 通过 `socketpair(AF_UNIX, SOCK_STREAM, 0, ...)` 创建一对 Unix 域 socket（`channel[0]` 和 `channel[1]`），用于父子进程间的带外通信（如通知新的监听 socket）。子进程持有 `channel[1]`，Master 持有 `channel[0]`，后者通过 `fcntl(FIOASYNC)` 设为异步 I/O 模式。

**processes 数组管理**：`ngx_processes[NGX_MAX_PROCESSES]` 是一个静态数组（最大 1024 个槽位），每个槽位包含 pid、状态、channel、回调函数指针等。`ngx_last_process` 追踪最后一个有效槽位，崩溃回收后的槽位置 `pid = -1`，可被后续 spawn 重用。

---

#### 五、ngx_worker_process_cycle()：Worker 工作循环

Worker 进程被 fork 后进入 `ngx_worker_process_cycle()`：

```c
static void
ngx_worker_process_cycle(ngx_cycle_t *cycle, void *data)
{
    ngx_int_t worker = (intptr_t) data;

    ngx_process = NGX_PROCESS_WORKER;
    ngx_worker = worker;

    ngx_worker_process_init(cycle, worker);

    ngx_setproctitle("worker process");

    for ( ;; ) {
        if (ngx_exiting) {
            if (ngx_event_no_timers_left() == NGX_OK) {
                ngx_worker_process_exit(cycle);
            }
        }

        ngx_process_events_and_timers(cycle);   // 事件驱动核心

        if (ngx_terminate) {
            ngx_worker_process_exit(cycle);
        }

        if (ngx_quit) {
            ngx_close_listening_sockets(cycle);
            ngx_set_shutdown_timer(cycle);
            // ... 优雅关闭
        }

        if (ngx_reopen) {
            ngx_reopen_files(cycle, -1);
        }
    }
}
```

**5.1 Worker 初始化（ngx_worker_process_init）**

初始化函数执行了多项关键操作：

1. 关闭不需要的文件描述符：遍历 `ngx_processes[]`，关闭除自身 `channel[1]` 之外所有进程的 channel fd。这一步确保 Worker 之间不会持有彼此的内部通信管道。
2. 解除信号阻塞：通过 `sigprocmask(SIG_SETMASK, &set)` 清空掩码，使 Worker 能够接收信号（Master 此前已阻塞了几乎所有信号）。
3. 权限降级：如果以 root 启动，Worker 会调用 `setgid()`、`setuid()` 降权到配置指定的 user/group。
4. CPU 亲和性绑定：调用 `ngx_setaffinity()` 将 Worker 绑定到指定 CPU 核心（如果有 `worker_cpu_affinity` 配置）。
5. 模块初始化回调：遍历所有已加载模块，调用其 `init_process` 钩子。
6. 注册 channel 事件：通过 `ngx_add_channel_event()` 将 channel fd 注册到事件驱动引擎，使 Worker 能接收来自 Master 的带外命令。

**5.2 事件驱动循环**

`ngx_process_events_and_timers()` 是 Worker 的核心函数（将在后续文章中深入），它封装了 epoll/poll/select 等 I/O 多路复用机制。Worker 进程在此函数中阻塞等待网络事件，每次返回后处理定时器到期事件。

---

#### 六、ngx_signal_handler()：统一信号分发

`ngx_signal_handler()` 通过 `ngx_process` 变量区分处理逻辑：

**Master / Single 进程**收到信号后的行为：

| 信号 | 行为 |
|------|------|
| SIGQUIT | `ngx_quit = 1`，优雅关闭 |
| SIGTERM / SIGINT | `ngx_terminate = 1`，强制退出 |
| SIGHUP | `ngx_reconfigure = 1`，重载配置 |
| SIGUSR1 | `ngx_reopen = 1`，重新打开日志文件 |
| SIGUSR2 | `ngx_change_binary = 1`，平滑升级二进制 |
| SIGWINCH | `ngx_noaccept = 1`，拒绝新连接 |
| SIGCHLD | `ngx_reap = 1` + 调用 `ngx_process_get_status()` |
| SIGALRM | `ngx_sigalrm = 1`，用于终止超时控制 |

**Worker / Helper 进程**的信号处理更简单：SIGTERM 触发立即退出，SIGQUIT 触发优雅关闭，SIGHUP、SIGUSR2、SIGIO 则被忽略（Worker 不处理重载和二进制升级）。

当 Master 需要给 Worker 发送指令时，它调用 `ngx_signal_worker_processes()`。对于 SIGQUIT/SIGTERM/SIGUSR1，Master 通过 channel 发送结构化的 `ngx_channel_t` 命令（`NGX_CMD_QUIT` / `NGX_CMD_TERMINATE` / `NGX_CMD_REOPEN`）；其他信号则直接 `kill()` 系统调用。Worker 端的 `ngx_channel_handler()` 解析命令并置位对应的全局变量。

---

#### 七、ngx_reap_children()：子进程回收与自动重生

当 Master 收到 SIGCHLD（子进程退出）时，`ngx_signal_handler()` 设置 `ngx_reap = 1` 并调用 `ngx_process_get_status()` 通过 `waitpid(-1, &status, WNOHANG)` 批量收集退出状态。

主循环随后调用 `ngx_reap_children()`：

```c
for (i = 0; i < ngx_last_process; i++) {
    if (ngx_processes[i].exited) {
        ngx_close_channel(ngx_processes[i].channel, cycle->log);

        if (ngx_processes[i].respawn       // 标记为可重生
            && !ngx_processes[i].exiting   // 非主动退出
            && !ngx_terminate              // 非终止流程
            && !ngx_quit)                  // 非关闭流程
        {
            ngx_spawn_process(cycle, ngx_processes[i].proc,
                              ngx_processes[i].data,
                              ngx_processes[i].name, i);
            ngx_pass_open_channel(cycle);
        }
    }
}
```

关键设计点：

- 自动重生：Worker 异常崩溃后，Master 自动重新 fork，保持配置的 Worker 数量不变。
- 退出代码检查：如果退出码为 2（致命错误），`ngx_process_get_status()` 会清除 `respawn` 标记，阻止无限重启。
- channel 清理：已退出进程的 channel socket 被关闭，并通过 `NGX_CMD_CLOSE_CHANNEL` 通知其他进程。
- 新二进制进程的特殊处理：如果退出的是新二进制进程（升级场景），Master 恢复原始 pid 文件。

---

#### 八、热加载配置：ngx_init_cycle() 与 old_cycle 机制

当 Master 收到 SIGHUP 时，`ngx_reconfigure = 1` 被置位，主循环调用：

```c
cycle = ngx_init_cycle(cycle);
```

`src/core/ngx_cycle.c` 中的 `ngx_init_cycle()` 接收当前的 `cycle`（作为 `old_cycle`），然后从头开始重新解析配置文件，创建新的 `ngx_cycle_t` 实例。如果新配置解析成功且所有模块初始化通过，则返回新 cycle；否则返回 `NULL`，继续使用旧 cycle，实现配置回滚。

热加载的完整流程：

1. Master 解析新配置 → `ngx_init_cycle()` 返回新 cycle
2. `ngx_cycle = cycle` 全局指针更新
3. 使用 `NGX_PROCESS_JUST_RESPAWN` spawn 新 Worker（新进程使用新配置监听 socket）
4. `ngx_msleep(100)` 等待新 Worker 就绪
5. 向旧 Worker 发送 SIGQUIT，优雅关闭
6. 旧 Worker 处理完现有连接后退出
7. 子进程退出触发 SIGCHLD → `ngx_reap_children()` 清理

这种设计实现了不停机的配置重载。新老配置共享一个端口时，通过 `SO_REUSEPORT` 或 `SO_REUSEADDR` 实现无缝切换。

---

#### 九、进程退出：优雅与强制

**优雅退出（SIGQUIT）**：

- Master → 向所有 Worker 发送 `NGX_CMD_QUIT` → 关闭监听 socket → 进入 `sigsuspend` 等待
- Worker → 关闭监听 socket → 设置 `ngx_exiting = 1` → 逐 event loop 检查 `ngx_event_no_timers_left()` → 所有连接关闭后 exit

**强制退出（SIGTERM）**：

- Master → 向 Worker 发送 SIGTERM → 设置定时器（50ms 起步，指数退避至 1s）→ 超时后升级为 SIGKILL
- Worker → 直接 `ngx_worker_process_exit()`，不等待连接处理完毕

Master 自身的退出函数 `ngx_master_process_exit()` 会：删除 pid 文件 → 调用模块的 `exit_master` 钩子 → 关闭监听 socket → 将日志引用保存到静态变量（防止 pool 销毁后日志丢失）→ 销毁 pool → exit(0)。

---

#### 结语

从 `main()` 的守护进程化，到 Master 的信号驱动循环，再到 Worker 的事件驱动循环，，Nginx 的进程生命周期处处体现了「简单性是可扩展性基石」的设计哲学。Master 不处理任何请求，仅负责管理与控制；Worker 则成为纯粹的事件驱动引擎，二者各司其职、高度内聚。理解这套模型，是深入 Nginx 事件驱动、共享内存、upstream 等高级机制的前提。

下一篇预告：Nginx 源码解析（三）：事件驱动框架与 epoll 封装

---

## 参考
- Nginx 1.24.x 源码：`src/os/unix/ngx_process_cycle.c` ， 进程生命周期核心
- Nginx 1.24.x 源码：`src/os/unix/ngx_process.c` ， 进程管理
- Nginx 1.24.x 源码：`src/core/nginx.c` ， main() 入口
- Nginx 1.24.x 源码：`src/os/unix/ngx_daemon.c` ， 守护进程化
- Nginx 1.24.x 源码：`src/os/unix/ngx_process_cycle.h` ， 进程类型定义
- Nginx 1.24.x 源码：`src/os/unix/ngx_process.h` ， 进程结构体定义
