+++
date = '2026-05-26T01:07:21+08:00'
draft = false
title = 'Linux memfd 秘闻：内存文件描述符的秘密区域'
author = 'JekYUlll'
lastmod = '2026-05-26T01:07:21+08:00'
tags = ['linux']
categories = ['linux']
+++

## 背景

在 Linux 系统编程中，"一切皆文件"是一个核心哲学。但有些场景下，你并不想真正落盘——你需要的只是一个**存在于内存中的临时文件**，用完即焚，不留痕迹。

传统做法是：在 `/tmp` 下创建临时文件，使用完后 `unlink`。但这条路有隐患：残留在磁盘或 swap 分区中的数据可能被恢复；文件路径可在 `/proc` 中被窥探；而且涉及真实的文件系统操作，有性能开销。

更严重的是安全场景。想象一下：你的程序持有私钥、会话 Token 或加密内存中的敏感数据。即使进程空间被保护，内核依然可以通过 direct map 访问任何物理页面。如果内核被攻破（ROP 攻击、Spectre 侧信道），敏感数据瞬间裸奔。

Linux 提供了两个内核特性来解决这两个层次的问题：

- **`memfd_create()`** —— 纯内存匿名文件，不落盘，无路径暴露（自 Linux 3.17）
- **`memfd_secret()`** —— 秘密内存区域，连内核都看不到（自 Linux 5.14）

前者是"隐形文件"，后者是"内核都看不见的保险柜"。

## 核心原理

### memfd_create：匿名的内存文件

`memfd_create()` 创建一个完全驻留在 RAM 中的匿名文件，返回一个文件描述符。它看起来、用起来都像普通文件——可以 `ftruncate`、`mmap`、`write`、`read`、`splice`——但它的存储后端是匿名页面，而非磁盘上的 inode。

关键特性：

- **自动清理**：所有引用关闭后，文件自动释放，无需手动 unlink
- **进程间共享**：可通过 UNIX domain socket 传递 fd，或在 `fork` 后共享
- **文件封印（File Sealing）**：通过 `MFD_ALLOW_SEALING` 标志启用，配合 `fcntl(F_ADD_SEALS)` 防止误修改
- **HugeTLB 支持**：从 Linux 4.14 起可用 `MFD_HUGETLB` 使用大页

fd 在 `/proc/self/fd/` 中显示为 `memfd:<name> (deleted)`——它已被 unlink，不再有文件系统路径。

### memfd_secret：连内核都看不见的秘密内存

`memfd_secret()` 是 `memfd_create()` 在安全维度上的超集。它创建的文件描述符对应的物理页面会被**从内核的 direct map 中移除**。这意味着：

- 内核在直接映射虚拟地址空间中再也看不到这些页面
- 侧信道攻击无法通过内核读取这些区域
- `get_user_pages()` 拒绝返回这些页面的指针
- 无法通过 DMA 访问
- 这些页面的指针**不能**传给任何系统调用（内核根本不知道它们的虚拟地址）

实现原理：`memfd_secret()` 背后的 `mm/secretmem.c` 使用 `set_direct_map_invalid_noflush()` 在页面分配时解除内核 direct map 的映射。页面被锁定在内存中（类似 `mlock()`），防止被 swap 到磁盘。当存在活跃的 secret memory 用户时，休眠（hibernation）被自动禁用。

### API 对比

| 特性 | `memfd_create()` | `memfd_secret()` |
|---|---|---|
| 引入内核版本 | 3.17 | 5.14 |
| 存储后端 | 匿名页面（RAM） | 匿名页面（RAM） |
| 内核可见性 | 内核可通过 direct map 访问 | 从内核 direct map 移除 |
| 文件封印 | 可选 (`MFD_ALLOW_SEALING`) | 不支持 |
| 页面锁定 | 无 | 自动 mlock（受限 `RLIMIT_MEMLOCK`） |
| Swap 保护 | 允许 swap | 禁止 swap |
| 休眠影响 | 无 | 有活动区域时禁用休眠 |
| glibc wrapper | 有（glibc 2.27+） | 无（需 `syscall(SYS_memfd_secret)`） |
| 启动启用 | 默认可用 | Linux 6.5 后默认启用；之前需 `secretmem_enable=1` |
| 典型用途 | 文件描述符传递、共享内存、文件封印 | 密钥存储、敏感数据隔离、防内核泄漏 |

## 代码实战

### 示例 1：使用 memfd_create 创建匿名内存文件

```c
#define _GNU_SOURCE
#include <err.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(void) {
    int fd;
    char *addr;
    const char *msg = "Hello from secret memory!";

    /* 创建一个允许封印的匿名内存文件 */
    fd = memfd_create("my_secret_buf", MFD_ALLOW_SEALING);
    if (fd == -1)
        err(EXIT_FAILURE, "memfd_create");

    /* 设置文件大小 */
    if (ftruncate(fd, 4096) == -1)
        err(EXIT_FAILURE, "ftruncate");

    /* 映射到进程地址空间 */
    addr = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (addr == MAP_FAILED)
        err(EXIT_FAILURE, "mmap");

    /* 写入数据 */
    memcpy(addr, msg, strlen(msg) + 1);
    printf("Wrote: %s\n", addr);

    /* 添加封印防止收缩/写入（可选） */
    if (fcntl(fd, F_ADD_SEALS, F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL) == -1)
        err(EXIT_FAILURE, "fcntl");

    /* 关闭 fd 不会销毁映射，但去掉映射后自动释放 */
    printf("fd: %d, /proc/%d/fd/%d -> memfd:my_secret_buf (deleted)\n",
           fd, getpid(), fd);

    munmap(addr, 4096);
    close(fd);
    return 0;
}
```

编译：`gcc -o memfd_demo memfd_demo.c`

### 示例 2：使用 memfd_secret 创建秘密内存区域

```c
#define _GNU_SOURCE
#include <err.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

/* glibc 没有 memfd_secret wrapper，需要自己 syscall */
#ifndef SYS_memfd_secret
#define SYS_memfd_secret 447  /* x86_64 */
#endif

int main(void) {
    int fd;
    char *secret_buf;
    const char *key = "ThisIsATopSecretKey-2026";

    /* 创建秘密内存文件描述符 */
    fd = syscall(SYS_memfd_secret, FD_CLOEXEC);
    if (fd == -1)
        err(EXIT_FAILURE, "memfd_secret (kernel >= 5.14, secretmem_enable?)");

    /* 设置大小（受 RLIMIT_MEMLOCK 限制） */
    if (ftruncate(fd, 4096) == -1)
        err(EXIT_FAILURE, "ftruncate");

    /* 映射到进程地址空间 */
    secret_buf = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (secret_buf == MAP_FAILED)
        err(EXIT_FAILURE, "mmap");

    /* 写入敏感数据——内核 direct map 看不到这里 */
    memcpy(secret_buf, key, strlen(key) + 1);
    printf("Secret key stored at %p: %s\n", (void *)secret_buf, secret_buf);

    /* 注意：secret_buf 中的指针不能传给 read()/write() 等系统调用 */
    /* 尝试 write(fd, secret_buf, 16) 会失败，因为内核无法访问该内存 */

    printf("Success: kernel can't read this memory via direct map!\n");

    /* 清理 */
    munmap(secret_buf, 4096);
    close(fd);
    return 0;
}
```

编译：`gcc -o secret_demo secret_demo.c`

运行前确认内核支持：
```bash
# 检查内核版本
uname -r    # 需 >= 5.14

# 如果内核 < 6.5，需要内核参数
cat /proc/cmdline | grep secretmem_enable
```

## 生态现状

### 使用 memfd 的知名项目

| 项目 | 用途 | 说明 |
|---|---|---|
| **systemd** | `memfd_create()` 实现日志传输、进程间文件传递 | systemd-journald 用 memfd 高效传递日志数据 |
| **QEMU** | `memfd_create()` 替代 `tmpfs` 文件用于 vhost-user 后端 | 减少文件系统操作，支持 `MFD_HUGETLB` 提升虚拟化性能 |
| **Docker / containerd** | `memfd_create()` 在容器启动时传递配置和文件 | 用 memfd 替代临时文件，避免在宿主机留下痕迹 |
| **GNOME / Wayland** | `memfd_create()` 用于 `wl_shm` 共享内存 | 替代传统的 `shm_open` 方式创建共享缓冲区 |
| **OpenSSL (secret memory preloader)** | `memfd_secret()` 保护私钥内存 | IBM 提供了 preloader 库，重定向 `OPENSSL_malloc` 到 secret memory |
| **WebKit / Chromium** | `memfd_create()` 用于隔离进程间内存共享 | 替代管道传递大块数据，减少拷贝 |
| **文件逃避型恶意软件** | `memfd_create()` 绕过文件扫描 | 恶意代码直接用 memfd 加载 payload，磁盘上不留痕迹——这是 memfd 被滥用的阴暗面 |

### memfd 的两个面孔

memfd 是一把双刃剑。合法场景下，它是高性能的进程间通信和内存安全工具；但在攻击者手中，`memfd_create()` 创建的匿名文件成为"无文件恶意软件"（fileless malware）的理想载体——直接在内存中执行 SHELLCODE，绕过基于文件的 AV 扫描。Sysdig、bpflock 等工具已支持检测来自 memfd 的可执行文件。

`memfd_secret()` 则始终专注于防御——它针对的是内核级攻击面，为私钥、密码、会话令牌提供硬件级的内存隔离。随着 Linux 6.5 后默认启用，它的采用率正在上升。

## 今日可执行动作

1. **在内核菜单中验证 memfd_secret 是否启用**：运行 `cat /proc/cmdline | grep -o secretmem_enable`；如果没有输出但内核版本 >= 6.5，则默认已启用。检查 `/proc/config.gz` 中 `CONFIG_SECRETMEM=y`。

2. **编译并运行上面的 `secret_demo.c`**：在你的 Linux 机器上验证 memfd_secret 是否能正常工作。如果返回 `ENOSYS`，说明内核编译时未启用或者需要 `secretmem_enable=1` 启动参数。

3. **检测系统中活跃的 memfd 文件**：运行 `ls -la /proc/*/fd/ 2>/dev/null | grep memfd` 查看哪些进程正在使用 memfd 文件。可以进一步结合 `lsof` 或自定义脚本监控 memfd 活动。

## 参考

- [memfd_create(2) - Linux man page (man7.org)](https://man7.org/linux/man-pages/man2/memfd_create.2.html)
- [memfd_secret(2) - Linux man page (man7.org)](https://man7.org/linux/man-pages/man2/memfd_secret.2.html)
- [mm: introduce memfd_secret system call (LWN.net, 2020)](https://lwn.net/Articles/838160/)
- [memfd_secret() in 5.14 (LWN.net, 2021)](https://lwn.net/Articles/865256/)
- [Linux 5.14 Can Create Secret Memory Areas With memfd_secret (Phoronix, 2021)](https://www.phoronix.com/news/Linux-5.14-memfd_secret)
- [Using Linux's memfd_secret syscall from the JVM with JEP-419](https://blog.arkey.fr/2022/05/16/linux_memfd_secret_with_jep-419/)
- [argv and memfd_secret](https://sanchda.github.io/2024/05/28/memfd_secret.html)
- [Linux Kernel memfd_secret patches (lore.kernel.org)](https://lore.kernel.org/linux-nvdimm/20201123113910.GC17833@gaia/T/)
- [Fileless Malware Detection with Sysdig Secure](https://www.sysdig.com/blog/fileless-malware-detection-sysdig-secure)
- [eBPF: Block Linux Fileless Payload Execution with BPF LSM](https://djalal.opendz.org/post/ebpf-block-linux-fileless-payload-execution-with-bpf-lsm/)
