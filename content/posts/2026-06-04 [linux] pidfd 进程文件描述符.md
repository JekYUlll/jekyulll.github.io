+++
date = '2026-06-04T10:05:14+08:00'
draft = false
title = 'pidfd：用文件描述符管理进程的新范式'
author = 'JekYUlll'
lastmod = '2026-06-04T10:05:14+08:00'
tags = ['pidfd', 'process-management', 'clone3', 'waitid']
categories = ['linux']
+++

## 背景

Unix 用整数 PID 管理进程，几十年没变过。`kill(pid, SIGTERM)` 靠一个 `int` 来定位目标。问题是 PID 会回收。

进程 A 退出后，内核把这个 PID 还给池子。新进程 B 可能拿到同一个 PID。如果进程 C 手里还攥着旧 PID，`kill(pid, SIGKILL)` 就打错人了。这不是理论 bug，Linux 上好几个 CVE 都跟 PID 回收竞态有关。

2018 年，Christian Brauner 开始往内核里塞 pidfd：把进程引用做成文件描述符。跟 socket fd、eventfd 一样，pidfd 是一个内核保证只指向这一个进程的句柄。目标进程退出后，fd 变成可读状态（poll 能感知），但句柄不会指向任何新进程。

这是把「整数标签」升级成「稳定引用」。

## 核心原理

### 不是 `/proc/pid` 的替代品

打开 `/proc/<pid>` 目录也能拿到一个 fd。但那个 fd 不能 poll，不能用 `waitid()`，procfd 不承载进程生命周期语义。pidfd 走的是匿名 inode（anon_inode），内核为每个 pidfd 创建一个只存在于内存中的文件描述符，背后挂的是 `struct pid`。

为什么不是 `struct task_struct`？`task_struct` 太大，线程退出后不能 pin 住。`struct pid` 轻量得多，即使进程变成僵尸也还在。

### API 全景

pidfd 不是一个系统调用，是一组 API，跨了 5 个内核版本逐步补齐：

| 内核版本 | 系统调用 / 能力 | 做了什么 |
|----------|-----------------|----------|
| 5.1 | `pidfd_send_signal()` | 向 pidfd 发信号，不存在 PID 回收竞态 |
| 5.2 | `clone()` + `CLONE_PIDFD` | fork 子进程时顺带拿回 pidfd |
| 5.3 | `pidfd_open()`, `poll()`, `clone3()` | 为已有进程开 pidfd；可以 poll/select/epoll 等待进程退出 |
| 5.4 | `waitid()` + `P_PIDFD` | 通过 pidfd 回收子进程退出状态 |
| 5.6 | `pidfd_getfd()` | 从目标进程偷一个 fd 到自己进程 |
| 6.9 | `PIDFD_THREAD` | 创建线程级 pidfd（之前只支持进程级） |
| 6.13 | `PIDFD_SELF`, `PIDFD_GET_INFO` | 自引用 + 直接从 pidfd 拿进程信息，绕过 `/proc` |

### pidfd_open 是怎么工作的

```c
int pidfd = syscall(SYS_pidfd_open, target_pid, 0);
```

内核通过 `target_pid` 找到 `struct pid`，创建一个匿名 inode，返回 fd。这个 fd 设置了 `O_CLOEXEC`，exec 新程序时自动关闭，防止泄漏到子进程。

之后你可以做几件事：

- `poll(&pfd, 1, -1)`：阻塞直到目标进程退出（`POLLIN`），僵尸被回收后触发 `POLLHUP`
- `pidfd_send_signal(pidfd, SIGTERM, NULL, 0)`：精确发信号，不会打错进程
- `waitid(P_PIDFD, pidfd, &info, WEXITED, NULL)`：回收子进程退出状态，不需要知道 PID
- `pidfd_getfd(pidfd, target_fd, 0)`：从目标进程复制一个 fd 过来，需要 ptrace 权限

### pidfd_getfd：偷 fd

这是整个 API 里最黑的一个。传统 Unix 传递 fd 靠 `SCM_RIGHTS`（Unix domain socket 传文件描述符），要求发送方配合、双方之间有 socket 连接。`pidfd_getfd` 不要求目标进程配合：

```c
// 你是一个 supervisor，想看看 target_pid 的 fd 3 是什么
int pidfd = syscall(SYS_pidfd_open, target_pid, 0);
int stolen_fd = syscall(SYS_pidfd_getfd, pidfd, 3, 0);
// 现在 stolen_fd 指向 target_pid 的 fd 3 背后的同一个文件/socket
```

权限检查走 ptrace 模型：调用者需要对目标进程有 `PTRACE_MODE_ATTACH_REALCREDS` 权限。root 天然有，普通用户需要同一 UID 且不被 Yama LSM 拦截。

最直接的应用场景：supervisor 进程在 fork 之后、exec 之前，帮子进程 bind 特权端口、设置 socket 选项、打开特殊文件，然后 exec 成低权限业务进程。目标进程不需要写一行协作代码。

### waitid(P_PIDFD)：解决「不能 wait 别人的子进程」

传统 `waitpid()` 只能回收自己的直接子进程。你不能 `waitpid(别人的子进程的 PID)`。pidfd 可以通过 `SCM_RIGHTS` 在进程间传递，这让非父进程也能 wait。

Android 的 LMKD（Low Memory Killer Daemon）就靠这个：system_server 把自己的 pidfd 发给 lmkd，lmkd 通过 `waitid(P_PIDFD, ...)` 精确等待 system_server 退出。传统 PID 做不到，因为 PID 会被回收。

## 代码实战

### 示例 1：监控任意进程的退出

```c
#define _GNU_SOURCE
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

static int pidfd_open(pid_t pid, unsigned int flags) {
    return syscall(SYS_pidfd_open, pid, flags);
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <pid>\n", argv[0]);
        return 1;
    }

    int pidfd = pidfd_open(atoi(argv[1]), 0);
    if (pidfd == -1) { perror("pidfd_open"); return 1; }

    struct pollfd pfd = { .fd = pidfd, .events = POLLIN };
    printf("Waiting for PID %s to exit...\n", argv[1]);

    int ready = poll(&pfd, 1, -1);
    if (ready == -1) { perror("poll"); return 1; }

    if (pfd.revents & POLLIN)
        printf("Process %s has exited.\n", argv[1]);

    close(pidfd);
    return 0;
}
```

编译运行：`gcc -o pidwatch pidwatch.c && ./pidwatch 12345`，目标进程退出时立即返回。

### 示例 2：父进程精确回收子进程

```c
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

int main() {
    int pidfd = -1;

    pid_t pid = syscall(SYS_clone3,
        &(struct clone_args){
            .flags   = CLONE_PIDFD,
            .pidfd   = (uintptr_t)&pidfd,
            .exit_signal = SIGCHLD,
        }, sizeof(struct clone_args));

    if (pid == 0) {
        // child
        printf("[child] PID=%d, doing work...\n", getpid());
        sleep(2);
        _exit(42);
    }

    // parent: wait via pidfd, no PID needed
    siginfo_t info = {0};
    if (waitid(P_PIDFD, pidfd, &info, WEXITED) == -1) {
        perror("waitid");
        return 1;
    }

    printf("[parent] child exited with status %d\n", info.si_status);
    close(pidfd);
    return 0;
}
```

`clone3()` 在创建子进程的同时把 pidfd 填进变量。父进程用 `waitid(P_PIDFD, pidfd, ...)` 回收，不需要记住子进程 PID。

### 示例 3：用 poll 做非阻塞进程监控

```c
struct pollfd fds[2];

// fd 0: pidfd
fds[0].fd = pidfd;
fds[0].events = POLLIN;

// fd 1: stdin
fds[1].fd = STDIN_FILENO;
fds[1].events = POLLIN;

while (1) {
    int n = poll(fds, 2, -1);
    if (n == -1) { perror("poll"); break; }

    if (fds[0].revents & POLLIN) {
        printf("Target process exited.\n");
        break;
    }
    if (fds[1].revents & POLLIN) {
        char buf[256];
        read(STDIN_FILENO, buf, sizeof(buf));
        printf("User input: %s", buf);
    }
}
```

pidfd 能和其他 fd 混在一起 poll。不需要单独线程，不需要 `SIGCHLD` 信号处理。对事件驱动的进程管理器（systemd、容器 runtime）来说，这是质的提升。

## 生态现状

| 项目 | 用 pidfd 做什么 |
|------|----------------|
| systemd | 服务监控：用 pidfd 等待服务进程退出，替代传统 `waitpid()` 轮询 |
| D-Bus | 通过 pidfd 传递进程引用，避免 PID 竞态导致消息发错目标 |
| CRIU | 进程快照 / 恢复时用 pidfd 精确控制目标进程 |
| Android LMKD | 低内存杀进程：通过 pidfd 精确监控和结束目标进程 |
| bpftrace | 跟踪工具内部用 pidfd 引用目标进程 |
| Qt | 计划用 pidfd 做 `QProcess` 的底层实现，替代 PID 方案 |
| Rust mio | 事件驱动库正在接入 pidfd，进程退出成为可 poll 的事件源 |
| container runtimes | containerd/cri-o 用 pidfd 做容器进程生命周期管理 |

还有一个值得关注的组合：BPF + pidfd。ArthurChiao 的文章演示了 `pidfd_getfd()` 配合 `BPF_PROG_TYPE_SK_LOOKUP`，让多个进程共享同一个 listen socket。不是 SO_REUSEPORT 那种负载分散，而是真正共享同一个 socket 的 accept 队列。

## 今日可执行动作

1. 写一个 pidfd 版进程监控器：用 `pidfd_open()` + `poll()` 替代 `waitpid()`，监控 2-3 个子进程，各进程退出时打印日志和退出码
2. 尝试 pidfd_getfd：启动两个进程（A 打开一个 socket，B 用 pidfd_getfd 复制过来），在 B 中往偷来的 socket 写数据，验证 fd 共享
3. 看 systemd 源码里的 pidfd：`grep -r 'pidfd_open\|P_PIDFD' src/core/`，对比传统 `waitpid()` 的处理流程

## 参考

- [Completing the pidfd API — LWN.net](https://lwn.net/Articles/794707/)
- [Adding the pidfd abstraction to the kernel — LWN.net](https://lwn.net/Articles/801319/)
- [Two pidfd tweaks: PIDFD_GET_INFO and PIDFD_SELF — LWN.net](https://lwn.net/Articles/992991/)
- [pidfd_open(2) — Linux manual page](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)
- [pidfd_getfd(2) — Linux manual page](https://man7.org/linux/man-pages/man2/pidfd_getfd.2.html)
- [Pidfd and Socket-lookup BPF Illustrated — ArthurChiao](https://arthurchiao.art/blog/pidfd-and-socket-lookup-bpf-illustrated/)
