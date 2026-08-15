+++
date = '2026-08-15T10:00:46+08:00'
draft = false
title = 'statmount 与 listmount：不用再解析 /proc/self/mountinfo 了'
author = 'JekYUlll'
lastmod = '2026-08-15T10:00:46+08:00'
tags = ['statmount', 'listmount', 'mount-namespace']
categories = ['linux']
+++

Linux 上想知道系统挂了哪些文件系统，通常只能读 `/proc/self/mountinfo`。这个接口可靠，也活了很多年，但它毕竟是一份面向文本解析器的协议。容器运行时、监控程序和存储工具都得重复写一套 parser。

Linux 6.8 加入了 `listmount(2)` 和 `statmount(2)`。前者枚举 mount ID，后者按 ID 取结构化属性。它们没有让 `mountinfo` 失效，却给只支持新内核的程序留了一条省心很多的路。

## `/proc/self/mountinfo` 难伺候的地方

一行 `mountinfo` 至少有 mount ID、父 ID、设备号、文件系统内根路径、挂载点和挂载选项。中间还有数量不固定的可选字段，遇到单独的 `-` 才切到文件系统类型、来源和 superblock 选项。

路径里的空格等字符使用八进制转义。可选字段以后还会增加，正确的 parser 必须跳过不认识的 tag。再加上 per-mount 选项和 per-superblock 选项不能混，随手 `split(' ')` 的版本迟早会出错。我见到这类文本接口时，第一反应总是先找现成库，而不是再造一个半对的解析器。

`mountinfo` 里的旧 mount ID 还有复用问题。挂载被卸载后，内核可以把这个 ID 交给另一个挂载。把 ID 缓存很久，再拿它当稳定身份，风险不小。

## `listmount` 管拓扑，`statmount` 管属性

这两个系统调用故意拆开。

`listmount()` 接收 `struct mnt_id_req`，返回某个挂载子树中的一批 64 位 mount ID。把 `mnt_id` 设成 `LSMT_ROOT` 就从当前 mount namespace 的根开始。缓冲区装满后，将本批最后一个 ID 写进 `req.param`，下一次调用会接着枚举。

`statmount()` 再查询单个 ID。调用者在 `req.param` 里按位声明想拿哪些字段，例如：

- `STATMOUNT_MNT_BASIC`：自身 ID、父 ID、mount attributes 和传播关系；
- `STATMOUNT_MNT_ROOT`、`STATMOUNT_MNT_POINT`：文件系统内根路径与挂载点；
- `STATMOUNT_FS_TYPE`：`ext4`、`proc`、`tmpfs` 这类文件系统类型；
- `STATMOUNT_SB_BASIC`：设备号、superblock magic 和 flags。

这种 mask 设计很像 `statx()`。返回结果里的 `mask` 才是字段有效性的最终依据，内核可能不支持某个新字段，所以不能把“请求过”当成“拿到了”。

### 字符串为什么是偏移量

`struct statmount` 的固定字段后面跟着柔性数组 `char str[]`。`fs_type`、`mnt_root` 和 `mnt_point` 不是指针，而是相对 `str` 的偏移量：

```c
printf("%s\n", sm->str + sm->mnt_point);
```

这样整个结果只占一块连续缓冲区，内核也不用向用户空间写入地址。如果缓冲区放不下字符串，`statmount()` 返回 `EOVERFLOW`。生产代码应扩大缓冲区后重试；下面的短程序固定用 4096 字节，错误时直接报告。

新 API 使用不会在系统生命周期内复用的 64 位 mount ID。旧 ID 仍保留在 `mnt_id_old` 等字段中，方便程序和 `/proc/*/mountinfo` 对照。两套 ID 不要混着做父子关系。

### 手上已有路径时，不必先枚举

很多程序不是想扫描整棵树，只是想知道 `/var/lib/data` 落在哪个挂载上。Linux 6.8 同时给 `statx()` 加了 `STATX_MNT_ID_UNIQUE`。它返回的 `stx_mnt_id` 与 `listmount()` 使用的是同一套 64 位 ID，可以直接交给 `statmount()`：

```c
struct statx stx;
if (statx(AT_FDCWD, path, AT_SYMLINK_NOFOLLOW,
          STATX_MNT_ID_UNIQUE, &stx) == 0 &&
    (stx.stx_mask & STATX_MNT_ID_UNIQUE)) {
    show_mount(stx.stx_mnt_id);
}
```

这条路径更适合磁盘用量检查、容器卷诊断之类的定点查询。它省掉一次全量枚举，也避开了“根据字符串猜某条 mountinfo 记录”的麻烦。`STATX_MNT_ID` 返回的是可能复用的旧 ID，少了 `_UNIQUE` 就不能拿去调用 `statmount()`，名字只差一个后缀，语义却完全不同。

### ABI 里有两个小坑

`mnt_id_req.size` 是内核识别结构体版本的长度字段，不是随手填个数字。示例明确写 `MNT_ID_REQ_SIZE_VER0`，表示只使用最初的 24 字节布局。后来的 header 给结构体追加了 mount namespace 字段，新程序仍可用 VER0 查询当前 namespace，这对“新用户态跑旧内核”很重要。

mask 也不要图省事填 `UINT_MAX`。man page 明确留了将某些 bit 用作缓冲区扩展的可能，未来的非零位未必仍表示“多取一个字段”。列出真正需要的 `STATMOUNT_*`，然后检查返回的 `sm->mask`。这种写法啰嗦几行，但版本升级时不会突然改变请求含义。

## 用 70 行 C 列出当前挂载

截至 man-pages 6.18，glibc 还没有提供这两个调用的包装函数，只能走 `syscall()`。编译时也需要 Linux 6.8 或更新版本的 UAPI headers。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <asm/unistd.h>
#include <linux/mount.h>
#include <stdint.h>

#ifndef SYS_statmount
#define SYS_statmount __NR_statmount
#define SYS_listmount __NR_listmount
#endif
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#define BATCH 64
#define INFO_SIZE 4096

static int show_mount(uint64_t id)
{
    struct mnt_id_req req = {
        .size = MNT_ID_REQ_SIZE_VER0,
        .mnt_id = id,
        .param = STATMOUNT_MNT_BASIC | STATMOUNT_MNT_ROOT |
                 STATMOUNT_MNT_POINT | STATMOUNT_FS_TYPE,
    };
    char storage[INFO_SIZE] = {0};
    struct statmount *sm = (struct statmount *)storage;

    if (syscall(SYS_statmount, &req, sm, sizeof(storage), 0) == -1) {
        fprintf(stderr, "statmount(%llu): %s\n",
                (unsigned long long)id, strerror(errno));
        return -1;
    }

    printf("id=%llu parent=%llu type=%s root=%s point=%s\n",
           (unsigned long long)sm->mnt_id,
           (unsigned long long)sm->mnt_parent_id,
           sm->str + sm->fs_type,
           sm->str + sm->mnt_root,
           sm->str + sm->mnt_point);
    return 0;
}

int main(void)
{
    uint64_t ids[BATCH];
    uint64_t cursor = 0;

    for (;;) {
        struct mnt_id_req req = {
            .size = MNT_ID_REQ_SIZE_VER0,
            .mnt_id = LSMT_ROOT,
            .param = cursor,
        };
        long n = syscall(SYS_listmount, &req, ids, BATCH, 0);

        if (n == -1) {
            perror("listmount");
            return errno == ENOSYS ? 2 : 1;
        }
        for (long i = 0; i < n; ++i)
            show_mount(ids[i]);
        if (n < BATCH)
            break;
        cursor = ids[n - 1];
    }
    return 0;
}
```

编译和运行：

```bash
gcc -std=c17 -O2 -Wall -Wextra -Werror mountscan.c -o mountscan
./mountscan
```

我在 Ubuntu 24.04 的 6.8.0 内核上跑到的输出片段如下：

```text
id=4294967323 parent=4294967329 type=sysfs root=/ point=/sys
id=4294967324 parent=4294967329 type=proc root=/ point=/proc
id=4294967329 parent=4294967298 type=ext4 root=/ point=/
```

返回值是扁平 ID 列表，不是排好缩进的树。根据每条记录的 `mnt_parent_id` 建索引，才能重建 mount tree。若内核没有实现系统调用，程序会收到 `ENOSYS`；安全策略或 namespace 权限不允许访问时，还要处理 `EPERM`。

本文程序只查询调用者当前的 mount namespace，不会穿过容器边界看到宿主机。Linux 6.11 以后可以在请求里指定外部 namespace ID，但这条路有额外的权限检查。诊断工具最好把“当前 namespace 扫描”和“跨 namespace 扫描”做成两个明确模式，权限不足时也能给出说得明白的错误。

## 别把两次调用想成原子快照

`listmount()` 和随后的一串 `statmount()` 之间，另一个进程完全可能挂载或卸载文件系统。某个 ID 刚被列出，查询属性时已经消失，`statmount()` 就会返回 `ENOENT`。示例选择打印错误并继续，这比整次退出实用。

分页也有同样的问题。新挂载可能出现在两批之间，旧挂载可能被移除。需要强一致快照的程序仍得自己定义重试策略；大多数诊断工具接受一次近似一致的扫描即可。

固定 4096 字节也只是 demo 写法。打开 `STATMOUNT_MNT_OPTS` 或更新版本加入的 option array 后，长选项很容易触发 `EOVERFLOW`。比较稳妥的实现会从几 KB 开始翻倍，并给最大值设限，免得异常输入拖垮进程。

## 这个接口还在快速补齐

6.8 的初版只解决当前 mount namespace 的基本枚举和查询。Linux 6.11 增加了外部 mount namespace 查询以及反向枚举；6.12 的 nsfs ioctl 可以取得 namespace ID，并遍历进程可见的 mount namespaces。

字段也在继续长。6.13 加入文件系统 subtype、mount source 与结构化 option arrays，6.15 又加入 idmapped mount 的 UID/GID 映射和 `supported_mask`。request mask 与 `size` 字段让这些扩展不用破坏旧 ABI，新内核可以追加能力，旧程序仍按旧尺寸工作。

如果程序要覆盖大量旧发行版，我还是会用 libmount 或认真解析 `mountinfo`。如果部署基线已经是 Linux 6.8+，而且你只需要明确的几项属性，`listmount` 加 `statmount` 更顺手。少一套文本语法，少一批边角 bug，这个收益很实际。

## 参考

- Linux 6.8 变更记录：https://kernelnewbies.org/Linux_6.8
- `statmount(2)` manual：https://man7.org/linux/man-pages/man2/statmount.2.html
- `listmount(2)` manual：https://man7.org/linux/man-pages/man2/listmount.2.html
- `/proc/pid/mountinfo` 格式：https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html
- Linux UAPI `mount.h`：https://github.com/torvalds/linux/blob/master/include/uapi/linux/mount.h
- 内核示例 `samples/vfs/mountinfo.c`：https://github.com/torvalds/linux/blob/master/samples/vfs/mountinfo.c
- Christian Brauner 的 mount namespace 枚举示例：https://brauner.io/2024/12/16/list-all-mounts.html
