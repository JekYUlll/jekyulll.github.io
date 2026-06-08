+++
date = '2026-05-29T10:00:49+08:00'
draft = false
title = 'Linux Landlock LSM：无特权沙箱的原理与实战'
author = 'JekYUlll'
lastmod = '2026-06-08T21:16:09+08:00'
tags = ['landlock', 'security', 'lsm', 'sandbox']
categories = ['linux']
+++

Linux 上给普通进程做沙箱，一直不算顺手。seccomp-bpf 能限制系统调用，但得自己写 BPF 程序，端口号变了还得改过滤器。容器太笨重，bubblewrap 依赖 setuid 辅助。Firejail 倒是好用，但它的 setuid 二进制本身就是攻击面。Landlock 是内核直接提供的一条路：不需要 root，不需要 setuid，也不需要写 BPF。它在 5.13 合入主线，但使用率还不高。

## 背景

Landlock 是 Mickaël Salaün 在 2021 年合入主线的 Linux Security Module（LSM）。它要解决的问题很明确：允许任意进程，包括非特权进程，主动收窄自己的资源访问范围。注意“主动”这个词。Landlock 不是 SELinux/AppArmor 那类强制 MAC 策略，而是 self-sandboxing。

传统沙箱方案的问题：

| 方案 | 需要 root/setuid | 优势 | 硬伤 |
|------|------------------|------|------|
| 容器 (Docker/Podman) | 需要（daemon root） | 完整的命名空间隔离 | 启动慢，镜像大，资源重 |
| seccomp-bpf | 不需要（`prctl`） | 精细控制系统调用 | BPF 过滤器维护成本高 |
| bubblewrap | 需要 setuid | 轻量命名空间 | setuid 二进制是攻击面 |
| Firejail | 需要 setuid | 预置 1000+ profile | 同样依赖 setuid |
| Landlock | 不需要 | 零特权沙箱，纯系统调用 API | 仅限文件/网络，不隔离进程视图 |

Landlock 不替代 seccomp 或容器，它补的是中间那块空白：普通进程自己给自己加限制，不依赖外部工具。写好规则集，调用三个系统调用，然后权限就收紧。

## 工作机制

Landlock 的 API 很小，只有三个系统调用：

```
landlock_create_ruleset(2)  — 创建一个规则集，声明要限制哪些操作
landlock_add_rule(2)         — 向规则集添加一条具体规则（允许哪些路径/端口）
landlock_restrict_self(2)    — 把规则集绑定到当前线程（锁住）
```

锁住之后不可逆。没有"解除"的接口。这是设计上的故意选择：一旦你限制了自己，同一个进程无法放宽限制，只能被新线程继承。

### 规则类型（截至 ABI v6）

Landlock 的 ABI 版本在演进，每个版本追加新的能力：

| ABI | Linux 内核 | 新能力 |
|-----|-----------|--------|
| 1   | 5.13      | 基本文件系统限制（读写执行） |
| 2   | 5.19      | `LANDLOCK_ACCESS_FS_REFER`（跨目录 rename/link） |
| 3   | 6.2       | `LANDLOCK_ACCESS_FS_TRUNCATE`（截断文件） |
| 4   | 6.7       | 网络限制（TCP bind/connect） |
| 5   | 6.10      | `LANDLOCK_ACCESS_FS_IOCTL_DEV`（ioctl 限制） |
| 6   | 6.12      | IPC 作用域（abstract UNIX socket + 信号隔离） |

当前最新是 ABI v6（Linux 6.12+，2024 年底）。每个版本向后兼容。你在老内核上降级能力就行。

### 分层模型

Landlock 使用多层策略（layers）。每次调用 `landlock_restrict_self` 追加一层，最多 16 层。访问检查通过所有层的规则才放行。跟 SELinux 的 intersection 逻辑一样，不能的权限一层否决就能挡住。

```c
// 伪代码：Landlock 的访问检查逻辑
for each layer in domain {
    if not any_rule_allows(layer, path, operation) {
        return EACCES;
    }
}
return ALLOW;
```

锁住之前必须调用 `prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)`。这个操作会防止进程通过 `setuid`、`setcap` 等方式获得新特权。Landlock 要求你不能一边限制自己一边偷偷升级。

## 代码示例

下面这个 C 程序把进程限制在“只读 `/usr` + 读写 `/tmp` + 能连 GitHub HTTPS”的规则内。

```c
/*
 * landlock_demo.c — 简单的 Landlock 沙箱示例
 * 编译：cc -o landlock_demo landlock_demo.c
 * 运行：./landlock_demo /bin/ls /usr
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <linux/landlock.h>
#include <sys/syscall.h>
#include <sys/prctl.h>

/* 如果 glibc 没导出，直接 syscall */
static inline int landlock_create_ruleset(
    const struct landlock_ruleset_attr *attr,
    size_t size, __u32 flags) {
    return syscall(__NR_landlock_create_ruleset, attr, size, flags);
}

static inline int landlock_add_rule(
    int fd, enum landlock_rule_type type,
    const void *rule, __u32 flags) {
    return syscall(__NR_landlock_add_rule, fd, type, rule, flags);
}

static inline int landlock_restrict_self(
    int fd, __u32 flags) {
    return syscall(__NR_landlock_restrict_self, fd, flags);
}

/* 检查 ABI 版本，兼容旧内核 */
static int get_abi(void) {
    int abi = landlock_create_ruleset(NULL, 0,
                    LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 0) {
        perror("Landlock 不支持（内核太旧或未开启）");
        exit(1);
    }
    return abi;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "用法: %s <命令> [参数...]\n", argv[0]);
        return 1;
    }

    int abi = get_abi();
    printf("Landlock ABI v%d\n", abi);

    /* 第一步：声明要限制哪些操作 */
    struct landlock_ruleset_attr attr = {
        .handled_access_fs =
            LANDLOCK_ACCESS_FS_EXECUTE |
            LANDLOCK_ACCESS_FS_WRITE_FILE |
            LANDLOCK_ACCESS_FS_READ_FILE |
            LANDLOCK_ACCESS_FS_READ_DIR |
            LANDLOCK_ACCESS_FS_REMOVE_DIR |
            LANDLOCK_ACCESS_FS_REMOVE_FILE |
            LANDLOCK_ACCESS_FS_MAKE_REG |
            LANDLOCK_ACCESS_FS_MAKE_DIR |
            LANDLOCK_ACCESS_FS_TRUNCATE,
    };

    /* 如果内核 >= ABI 4，加上网络限制 */
    if (abi >= 4) {
        attr.handled_access_net =
            LANDLOCK_ACCESS_NET_BIND_TCP |
            LANDLOCK_ACCESS_NET_CONNECT_TCP;
    }

    /* 第二步：创建规则集 */
    int ruleset_fd = landlock_create_ruleset(&attr, sizeof(attr), 0);
    if (ruleset_fd < 0) {
        perror("landlock_create_ruleset");
        return 1;
    }

    /* 第三步：添加文件系统规则 */
    /* 规则 A：允许读 + 执行 /usr */
    int usr_fd = open("/usr", O_PATH | O_CLOEXEC);
    if (usr_fd >= 0) {
        struct landlock_path_beneath_attr path = {
            .allowed_access = LANDLOCK_ACCESS_FS_EXECUTE |
                              LANDLOCK_ACCESS_FS_READ_FILE |
                              LANDLOCK_ACCESS_FS_READ_DIR,
            .parent_fd = usr_fd,
        };
        if (landlock_add_rule(ruleset_fd, LANDLOCK_RULE_PATH_BENEATH,
                              &path, 0))
            perror("add_rule /usr");
        close(usr_fd);
    }

    /* 规则 B：允许读 + 写 + 执行 /tmp */
    int tmp_fd = open("/tmp", O_PATH | O_CLOEXEC);
    if (tmp_fd >= 0) {
        struct landlock_path_beneath_attr path = {
            .allowed_access = LANDLOCK_ACCESS_FS_EXECUTE |
                              LANDLOCK_ACCESS_FS_WRITE_FILE |
                              LANDLOCK_ACCESS_FS_READ_FILE |
                              LANDLOCK_ACCESS_FS_READ_DIR |
                              LANDLOCK_ACCESS_FS_REMOVE_DIR |
                              LANDLOCK_ACCESS_FS_REMOVE_FILE |
                              LANDLOCK_ACCESS_FS_MAKE_REG |
                              LANDLOCK_ACCESS_FS_MAKE_DIR |
                              LANDLOCK_ACCESS_FS_TRUNCATE,
            .parent_fd = tmp_fd,
        };
        if (landlock_add_rule(ruleset_fd, LANDLOCK_RULE_PATH_BENEATH,
                              &path, 0))
            perror("add_rule /tmp");
        close(tmp_fd);
    }

    /* 第四步（内核 >= ABI 4）：添加网络规则 */
    if (abi >= 4) {
        /* 允许连接 GitHub HTTPS (443) */
        struct landlock_net_port_attr net = {
            .allowed_access = LANDLOCK_ACCESS_NET_CONNECT_TCP,
            .port = 443,   /* 网络字节序？不，主机字节序 */
        };
        if (landlock_add_rule(ruleset_fd, LANDLOCK_RULE_NET_PORT,
                              &net, 0))
            perror("add_rule net 443");
    }

    /* 第五步：先声明 no_new_privs，再锁住 */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) {
        perror("prctl(PR_SET_NO_NEW_PRIVS)");
        return 1;
    }

    if (landlock_restrict_self(ruleset_fd, 0)) {
        perror("landlock_restrict_self");
        return 1;
    }
    close(ruleset_fd);

    /* 锁住了！接下来运行的任何操作都在沙箱内 */
    printf("✅ 沙箱已激活，执行命令...\n\n");

    execvp(argv[1], &argv[1]);
    perror("execvp");
    return 1;
}
```

编译运行：

```bash
$ cc -o landlock_demo landlock_demo.c
$ ./landlock_demo /bin/ls /usr
```
行为对比（同样调 `open("/etc/shadow", O_RDONLY)`）：

| 操作 | 无沙箱 | 有沙箱 |
|------|--------|--------|
| 读 `/etc/shadow` | 普通用户→拒绝（DAC） | 拒绝（Landlock 层先拦住） |
| 读 `/usr/bin/gcc` | 允许 | 允许（规则 A） |
| 写 `/tmp/test.txt` | 允许 | 允许（规则 B） |
| 连 example.com:80 | 允许 | 拒绝（没声明 80 端口） |
| 连 github.com:443 | 允许 | 允许（网络规则） |

`/etc/shadow` 本来就会被 DAC 拦住普通用户读取。Landlock 的价值在另一层：如果 DAC 配错了，比如权限被改成 644，Landlock 仍然会挡住。

### 关于 ABI 兼容

写产品级代码时一定要做 ABI 降级。上面的示例用了简单的 switch，更健壮的做法是：

```c
/* 最佳实践：按 ABI 版本降级能力 */
switch (abi) {
case 6:
    attr.scoped = LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET |
                  LANDLOCK_SCOPE_SIGNAL;
    __attribute__((fallthrough));
case 5:
    attr.handled_access_fs |= LANDLOCK_ACCESS_FS_IOCTL_DEV;
    __attribute__((fallthrough));
case 4:
    attr.handled_access_net = LANDLOCK_ACCESS_NET_BIND_TCP |
                              LANDLOCK_ACCESS_NET_CONNECT_TCP;
    __attribute__((fallthrough));
case 3:
    attr.handled_access_fs |= LANDLOCK_ACCESS_FS_TRUNCATE;
    __attribute__((fallthrough));
case 2:
    attr.handled_access_fs |= LANDLOCK_ACCESS_FS_REFER;
}
```

不这样做的话，在旧内核上 `landlock_create_ruleset` 会返回 `EINVAL` 或 `ENOSYS`。

## 使用现状

已经有几个项目在生产环境用 Landlock：

| 项目 | 用途 | 备注 |
|------|------|------|
| [landrun](https://github.com/Zouuup/landrun) | 通用 CLI 沙箱工具 | Go 实现，2025 年火过一波 |
| [nono](https://nono.sh/docs/cli/internals/landlock) | 无特权能力限制引擎 | 生产级使用 |
| [systemd](https://github.com/systemd/systemd) | service 的 `RestrictFileSystems=` | 从 v255 开始实验支持 |
| 内核 `samples/landlock/sandboxer.c` | 官方参考实现 | 功能完整的沙箱管理器 |
| [go-landlock](https://pkg.go.dev/github.com/landlock-lsm/go-landlock/landlock) | Go 语言的 Landlock 绑定 | 官方维护 |
| Firejail (待合并) | 已有 issue #5269 提议加入 | 还没合入 |

Landrun 值得看一下，用法很直接：

```bash
# 只允许读 /usr，允许连接 443 端口
landrun --ro /usr --net-connect 443 -- cmd
```

主要逻辑就是封装了上面那段 C 代码。跟 Firejail 的区别是：landrun 不需要 setuid，不需要安装 daemon，不修改 /proc/sys 配置。就是一个普通的 Go 二进制，任何用户都能跑。

## 可以马上做的事

1. 检查内核是否支持 Landlock：运行 `dmesg | grep landlock` 或 `journalctl -kb -g landlock`。如果没输出，检查 `/boot/config-*` 中 `CONFIG_SECURITY_LANDLOCK=y`。Debian/Ubuntu 的通用内核默认启用。Arch Linux 也是。

2. 跑官方的 sandboxer 示例：在 Linux 内核源码树里执行以下操作：
   ```bash
   git clone --depth=1 https://github.com/torvalds/linux.git /tmp/linux
   make -C /tmp/linux samples/landlock/sandboxer
   LL_FS_RO="/usr:/lib:/etc/ssl" LL_FS_RW="/tmp" \
       LL_TCP_CONNECT="443:80" ./samples/landlock/sandboxer bash
   ```
   试试在沙箱里 `rm /etc/hostname`。对比外面和里面的效果。

3. 试用 landrun：`go install github.com/Zouuup/landrun@latest`，然后：
   ```bash
   # 沙箱里跑 curl —— 只允许连 443 端口
   landrun --ro /usr:/etc --net-connect 443 -- curl https://github.com
   # 试试连 80 端口，应该被拒绝
   landrun --ro /usr:/etc --net-connect 443 -- curl http://example.com
   ```

## 参考

- Landlock 官网和文档: https://landlock.io/
- landlock(7) 手册页: https://man7.org/linux/man-pages/man7/landlock.7.html
- 内核用户空间 API 文档: https://docs.kernel.org/userspace-api/landlock.html
- 内核 LSM 文档: https://www.kernel.org/doc/html/v5.13/security/landlock.html
- 官方示例 sandboxer.c: https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/samples/landlock/sandboxer.c
- landrun GitHub: https://github.com/Zouuup/landrun
- LWN 文章 "Landlock LSM: unprivileged sandboxing": https://lwn.net/Articles/840419/
