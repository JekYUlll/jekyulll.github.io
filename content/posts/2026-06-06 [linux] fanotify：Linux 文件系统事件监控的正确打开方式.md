+++
date = '2026-06-06T10:09:24+08:00'
draft = false
title = 'fanotify：Linux 文件系统事件监控的正确打开方式'
author = 'JekYUlll'
lastmod = '2026-06-08T21:16:03+08:00'
tags = ['fanotify', 'filesystem', 'security', 'monitoring']
categories = ['linux']
+++

## 背景

Linux 上想监控文件系统事件，大多数人第一反应是 inotify。`inotify_add_watch()` 给目录加个 watch，然后 `read()` 等事件来。

但 inotify 有几个让人抓狂的限制。

第一，你得逐个目录加 watch。想监控整个 `/home`？先递归遍历所有子目录，每个都加一个 watch。光遍历就要命，更别提文件和目录随时在创建和删除，watch 永远赶不上变化。目录刚创建完，inotify 还没来得及加 watch，文件已经写进去了。

第二，inotify 没有 PID。你知道有人写了文件，但不知道是谁写的。想追踪进程，只能从 `/proc` 倒查，这是个经典的 TOCTOU 竞态。

第三，inotify 只能事后通知。等 `IN_CLOSE_WRITE` 到达用户态，数据早就落盘了。想在文件被读取前拦截，不可能。

fanotify 就是来解决这三个问题的。它 2010 年随 Linux 2.6.36 合入主线，最初只有一个用途：给杀毒软件做 on-access 扫描。但十几年下来，fanotify 长出了大量新能力：create/delete/move 事件、文件路径解析、甚至能直接在用户态决定一个 `open()` 是放行还是拒绝。

## 工作方式

fanotify 和 inotify 的根本区别在监控粒度。

inotify 监控的是单个 inode，你得显式告诉内核"帮我看着这个目录"。fanotify 监控的是挂载点或整个文件系统，一句 `fanotify_mark()` 搞定一切。

### 三步走模型

fanotify 的使用流程很简单：

```
fanotify_init()   → 创建通知组，返回一个 fd
fanotify_mark()   → 往通知组注册要监控的对象和事件
read(fd)          → 阻塞读事件
write(fd)         → 对权限事件做出允许/拒绝的响应
close(fd)         → 清理
```

其中有几个关键设计。

通知组（notification group）：`fanotify_init()` 返回的 fd 是个内核对象，所有注册的 mark 共享同一个事件队列。多个进程可以各自创建通知组监控同一个文件系统，互不干扰。

mark 的 masks 是双层的：每个 mark 维护两个 bitmask，mark mask（要产生什么事件）和 ignore mask（抑制什么事件）。缓存类应用可以用它精细控制事件流：文件第一次被修改时通知，之后在 close 前都忽略。

权限事件的特殊点：接到 `FAN_OPEN_PERM` 时，发起 `open()` 的进程被内核暂停，等你用 `write()` 回一个 `FAN_ALLOW` 或 `FAN_DENY`。选后者，`open()` 直接返回 `EPERM`，文件根本没被打开。

### 三种通知级别

`fanotify_init()` 的 `flags` 里有一个必选的 class 参数：

| class | 能拦截？ | 典型用途 |
|-------|---------|---------|
| `FAN_CLASS_NOTIF` | 否 | 审计日志、文件同步 |
| `FAN_CLASS_CONTENT` | 是 | 杀毒扫描、DLP |
| `FAN_CLASS_PRE_CONTENT` | 是 | 分级存储管理（HSM） |

`FAN_CLASS_PRE_CONTENT` 最特殊。一个挂载点只能有一个进程持有。它在其他 class 之前收到事件，常用于在文件内容最终确定前做预处理。我们日常写监控工具选 `FAN_CLASS_NOTIF` 就够了。

### FAN_MARK_MOUNT vs FAN_MARK_FILESYSTEM

用 `fanotify_mark()` 注册目标时，有两个标记决定了监控范围：

| 标记 | 范围 | 内核要求 |
|------|------|---------|
| `FAN_MARK_MOUNT` | 单个挂载点下的所有文件和目录 | 所有支持 fanotify 的内核 |
| `FAN_MARK_FILESYSTEM` | 整个文件系统，包括所有挂载点 | Linux 4.20+ |

`FAN_MARK_MOUNT` 是最常见的用法。给 `/` 加个 mark，根挂载点下的所有文件操作都会产生事件。但 `/proc`、`/sys` 等独立挂载点不受影响。

`FAN_MARK_FILESYSTEM` 范围更大。比如你给 `/usr` 加 mark，就算 `/usr` 后来被 bind mount 到别处，事件照常产生。适合"一网打尽"的场景。

### 事件结构体拆解

从 fanotify fd 读出来的数据长这样：

```c
struct fanotify_event_metadata {
    __u32 event_len;       // 本事件总长度（含附加记录）
    __u8  vers;            // 必须是 FANOTIFY_METADATA_VERSION
    __u8  reserved;
    __u16 metadata_len;    // 本结构体的大小
    __aligned_u64 mask;    // 事件类型位图
    __s32 fd;              // 打开的文件描述符，或 FAN_NOFD
    __s32 pid;             // 触发进程的 PID（或 TID）
};
```

几个注意点：

- `fd` 如果不等于 `FAN_NOFD`，必须 close，否则泄漏。内核给这个 fd 打了 `FMODE_NONOTIFY` 标记，读写它不会触发新一轮 fanotify 事件（避免递归死锁）。
- 如果 `event_len > metadata_len`，说明后面还跟着附加信息记录，比如用 `FAN_REPORT_DFID_NAME` 时会有文件路径信息。
- 一次 `read()` 可能返回多个事件，用 `FAN_EVENT_OK()` 和 `FAN_EVENT_NEXT()` 宏遍历。

### 关键事件一览

fanotify 支持的事件比 inotify 多得多：

| 事件 | 含义 | 能否拦截 |
|------|------|---------|
| `FAN_OPEN` | 文件/目录被打开 | 是（`FAN_OPEN_PERM`） |
| `FAN_ACCESS` | 文件被读取 | 是（`FAN_ACCESS_PERM`） |
| `FAN_OPEN_EXEC` | 文件被执行 | 是（`FAN_OPEN_EXEC_PERM`） |
| `FAN_MODIFY` | 文件内容被修改 | 否 |
| `FAN_CLOSE_WRITE` | 可写文件被关闭 | 否 |
| `FAN_CREATE` | 子文件或目录被创建 | 否 |
| `FAN_DELETE` | 子文件或目录被删除 | 否 |
| `FAN_MOVED_FROM / _TO` | 文件被移入/移出 | 否 |

`FAN_OPEN_EXEC_PERM` 是个被低估的安全工具。它在 `execve()` 加载新进程镜像之前触发，你可以用它实现用户态的应用白名单，不需要写一行 LSM 内核模块。

## 代码

下面写一个完整的监控程序，跑起来就能看到系统上谁在读什么文件。

### 初始化

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/fanotify.h>
#include <unistd.h>

int main(void) {
    // FAN_CLASS_NOTIF: 只收通知，不做权限决策
    // FAN_CLOEXEC: fd 不在 exec 后泄漏
    int fan_fd = fanotify_init(
        FAN_CLASS_NOTIF | FAN_CLOEXEC,
        O_RDONLY | O_LARGEFILE
    );
    if (fan_fd == -1) {
        perror("fanotify_init");
        exit(EXIT_FAILURE);
    }

    // 监控根挂载点的所有文件和目录
    // FAN_EVENT_ON_CHILD: 目录下的子对象事件也上报
    // FAN_ONDIR: 上报目录自身事件
    uint64_t mask = FAN_EVENT_ON_CHILD | FAN_ONDIR
                  | FAN_OPEN | FAN_OPEN_EXEC
                  | FAN_ACCESS | FAN_MODIFY
                  | FAN_CLOSE_WRITE | FAN_CLOSE_NOWRITE;

    if (fanotify_mark(fan_fd, FAN_MARK_ADD | FAN_MARK_MOUNT,
                      mask, AT_FDCWD, "/") == -1) {
        perror("fanotify_mark");
        exit(EXIT_FAILURE);
    }

    printf("Monitoring / ... (Ctrl+C to stop)\n");
```

### 事件循环

```c
    char buf[8192];
    struct pollfd pfd = { .fd = fan_fd, .events = POLLIN };

    for (;;) {
        int ret = poll(&pfd, 1, -1);
        if (ret <= 0) continue;

        ssize_t n = read(fan_fd, buf, sizeof(buf));
        if (n <= 0) continue;

        struct fanotify_event_metadata *meta =
            (struct fanotify_event_metadata *)buf;

        while (FAN_EVENT_OK(meta, n)) {
            if (meta->fd != FAN_NOFD) {
                char path[PATH_MAX];
                snprintf(path, sizeof(path),
                         "/proc/self/fd/%d", meta->fd);
                char target[PATH_MAX];
                ssize_t len = readlink(path, target,
                                       sizeof(target) - 1);
                if (len != -1) {
                    target[len] = '\0';
                } else {
                    snprintf(target, sizeof(target),
                             "(deleted)");
                }

                printf("PID %5d  ", meta->pid);

                // 按事件类型输出标签
                if (meta->mask & FAN_OPEN_EXEC)
                    printf("[EXEC]  %s\n", target);
                else if (meta->mask & FAN_OPEN)
                    printf("[OPEN]  %s\n", target);
                else if (meta->mask & FAN_MODIFY)
                    printf("[WRITE] %s\n", target);
                else if (meta->mask & FAN_CLOSE_WRITE)
                    printf("[CLOSE] %s\n", target);
                else if (meta->mask & FAN_ACCESS)
                    printf("[READ]  %s\n", target);

                close(meta->fd);
            }

            meta = FAN_EVENT_NEXT(meta, n);
        }
    }

    close(fan_fd);
    return 0;
}
```

### 编译运行

```bash
gcc -o fmon fmon.c
sudo ./fmon
```

输出会是滚屏的实时事件：

```
PID  3521  [EXEC]  /usr/bin/cat
PID  3521  [OPEN]  /etc/passwd
PID  3521  [READ]  /etc/passwd
PID  3521  [CLOSE] /etc/passwd
```

### 权限拦截版（拦截所有未授权的 exec）

把 `FAN_CLASS_NOTIF` 换成 `FAN_CLASS_CONTENT`，加几行 `write()` 响应：

```c
// 修改 fanotify_init 调用：
int fan_fd = fanotify_init(
    FAN_CLASS_CONTENT | FAN_CLOEXEC,
    O_RDONLY | O_LARGEFILE
);

// 只监控 OPEN_EXEC_PERM
fanotify_mark(fan_fd, FAN_MARK_ADD | FAN_MARK_MOUNT,
              FAN_EVENT_ON_CHILD | FAN_OPEN_EXEC_PERM,
              AT_FDCWD, "/");

// 事件循环中，遇到 FAN_OPEN_EXEC_PERM：
if (meta->mask & FAN_OPEN_EXEC_PERM) {
    struct fanotify_response resp = {
        .fd       = meta->fd,
        .response = FAN_DENY,  // 拒绝执行
    };
    write(fan_fd, &resp, sizeof(resp));
    printf("BLOCKED exec: /proc/self/fd/%d\n", meta->fd);
}
```

把 `FAN_DENY` 改成条件判断（比如只允许 `/usr/bin/` 下的文件执行），就是一个用户态应用白名单。

## 谁在用

fanotify 早已不是实验室玩具。以下项目直接依赖它：

| 项目 | 用途 | 使用的 fanotify 特性 |
|------|------|---------------------|
| ClamAV | on-access 病毒扫描 | `FAN_CLASS_CONTENT` + `FAN_OPEN_PERM` |
| fapolicyd（RHEL） | 应用完整性验证与白名单 | `FAN_OPEN_EXEC_PERM` |
| CrowdStrike / SentinelOne | Linux EDR agent | 全事件掩码 + `FAN_REPORT_DFID_NAME` |
| systemd-journald | 日志转发 | `FAN_MODIFY` 监控 journal 文件 |
| BCC / bpftrace | 可观测性集成 | fanotify + eBPF 混合方案 |

RHEL 的 fapolicyd 可以单独看。它用 `FAN_OPEN_EXEC_PERM` 在 `execve()` 前校验可执行文件的 SHA256 哈希和 RPM 签名，不通过的进程直接 `FAN_DENY`。这一套全程用户态，没碰一行 LSM 代码。

ClamAV 的 on-access 扫描是另一个典型用例。打开文件时，fanotify 把调用者挂起，ClamAV 拿到 fd 扫描内容，干净则 `FAN_ALLOW`，有毒则 `FAN_DENY`。用户看到的只是 `open()` 慢了 50ms，而不是文件被感染。

从内核版本来看，fanotify 还在快速演进：5.1 加了 create/delete/move 事件和 `FAN_REPORT_DFID_NAME`，5.9 加了 `FAN_REPORT_NAME` 简化路径获取，6.14 加了 `FAN_REPORT_MNT` 支持 mount namespace 标记。这个 API 远没到稳定期。

## 可以马上试的三件事

1. 跑一下上面的监控代码。把完整程序编译运行 10 秒，看看你的系统在这一小段窗口里产生了多少文件事件。数量通常会比直觉多。
2. 写一个拦截器，只放行 `/usr/bin/` 下的二进制执行，其余 `FAN_DENY`。验证效果：`sudo ./fmon_block` 后再开个终端，很多命令应该挂掉。
3. 阅读 `samples/fanotify/fs-monitor.c`。这是内核源码树里的官方示例，展示了 `FAN_FS_ERROR` 的用法。用 `git clone --depth=1 https://github.com/torvalds/linux` 拉一份源码就能看到。

## 参考

- [fanotify(7) - Arch manual pages](https://man.archlinux.org/man/fanotify.7.en)
- [fanotify_init(2) - Linux manual page](https://man7.org/linux/man-pages/man2/fanotify_init.2.html)
- [fanotify_mark(2) - Linux manual page](https://man7.org/linux/man-pages/man2/fanotify_mark.2.html)
- [Linux File Monitoring With Fanotify - Mathscantor's Blog (2025)](https://mathscantor.github.io/posts/linux-file-monitoring-with-fanotify/)
- [Linux fanotify for Real-Time Filesystem Security Monitoring - systemshardening.com (2026)](https://www.systemshardening.com/articles/linux/linux-fanotify-security-monitoring/)
- [File System Monitoring with fanotify - Linux Kernel docs](https://docs.kernel.org/admin-guide/filesystem-monitoring.html)
