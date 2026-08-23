+++
date = '2026-08-23T10:07:06+08:00'
draft = false
title = 'PR_SET_MDWE：把可执行权限变成单向门'
author = 'JekYUlll'
lastmod = '2026-08-23T10:07:06+08:00'
tags = ['mdwe', 'w-x', 'memory-protection']
categories = ['linux']
+++

W^X 常被说成一句口号：一段内存要么可写，要么可执行。问题是，普通程序仍能自己调用 `mprotect()` 改权限。攻击者只要接管控制流，现成的 `mprotect(RW -> RX)` 也可能变成利用链的一环。

Linux 6.3 加入了 `PR_SET_MDWE`。MDWE 是 Memory-Deny-Write-Execute 的缩写，它把权限规则放进内核，而且一旦打开就关不掉。这个 API 很小，脾气却很硬，撞上规则的系统调用会直接报错。

## 它管的不是某一次 mmap

最直观的 W^X 检查是拒绝 `PROT_WRITE | PROT_EXEC`。MDWE 还多做了一件事：原本不可执行的 VMA，之后也不能获得 `PROT_EXEC`。

所以这段常见的 JIT 流程会失败：

```c
void *p = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
/* 往 p 写入机器码 */
mprotect(p, size, PROT_READ | PROT_EXEC);  /* MDWE 开启后返回 -1 */
```

中间先改成只读也没用。内核最初的提交专门列出了 `RW -> R -> RX`，最后一步仍会被拒绝。它防的是“获得可执行权限”，不是只盯着同一时刻有没有 W 和 X。

但已有的可执行映射可以继续保持可执行。比如一个 VMA 原来是 `RX`，再次调用 `mprotect()` 保持 `RX` 是合法的。在 arm64 上，动态加载器给已有代码页增加 `PROT_BTI` 也能通过。这个细节解决了 systemd 旧方案的麻烦。

### 内核其实只做两个判断

原始补丁把检查收在 `map_deny_write_exec()` 里。去掉外围代码后，逻辑可以压成下面几行：

```c
if ((new_flags & VM_EXEC) && (new_flags & VM_WRITE))
    return -EACCES;

if (!(old_flags & VM_EXEC) && (new_flags & VM_EXEC))
    return -EACCES;
```

第一条挡住同时可写、可执行的映射。第二条把 X 变成单向权限：创建 VMA 时可以直接申请 `RX`，但一个没有 X 的旧 VMA 不能在后面补上 X。权限从 `RX` 收紧到 `R` 没问题，只是这段映射再也回不到 `RX`。

这比记录“某个虚拟地址是否写过”简单得多，也便宜得多。代价是语义只落在 VMA 上。地址被解除映射以后，内核不会替已经消失的 VMA 保存权限历史。

### 为什么不继续用 seccomp 过滤

systemd 的 `MemoryDenyWriteExecute=` 早期靠 seccomp BPF 拦系统调用。过滤器只能看到这次 `mprotect()` 的参数，不知道 VMA 以前是什么权限，于是只能粗暴地拒绝所有带 `PROT_EXEC` 的 `mprotect()`。

arm64 的 Branch Target Identification 需要把已有的可执行映射从 `PROT_EXEC` 调整为 `PROT_EXEC | PROT_BTI`。旧过滤器会误伤这条合法路径。MDWE 在 `mm/mprotect.c` 里检查旧 VMA 和新 flags，能分清“保留 X”和“第一次拿到 X”。systemd 后来也改为在内核支持时优先调用 `PR_SET_MDWE`。

## 用 prctl 锁住当前地址空间

调用只需要一个 flag：

```c
prctl(PR_SET_MDWE, PR_MDWE_REFUSE_EXEC_GAIN, 0L, 0L, 0L);
```

内核把状态记在调用进程的 `mm_struct` 上，因此共享地址空间的线程都会受影响。默认情况下，`fork()` 出来的子进程也继承它。Linux 6.6 又加了 `PR_MDWE_NO_INHERIT`，需要在第一次设置时和 `PR_MDWE_REFUSE_EXEC_GAIN` 一起传入，子进程才不会继承。

mask 不能事后改回去，连后补 `PR_MDWE_NO_INHERIT` 也不行。做兼容检测时可以调用 `PR_GET_MDWE`；老内核不认识 `PR_SET_MDWE`，会以 `EINVAL` 失败。用户态头文件太旧也没关系，几个 UAPI 数值可以在源码里做条件定义。

### 开关放在哪一行

没有运行时代码生成的服务，可以在 `main()` 刚开始时设置。再晚就有空窗：程序先解析了不可信输入，之后才打开 MDWE，前面的执行路径仍可能被利用。

带插件系统的程序要多查一步。普通 ELF 代码段可以直接映射成 `RX`，不受影响；但 libffi closure、JIT 编译器或手写 trampoline 可能依赖 `RW -> RX` 或 `RWX`。这类路径会在 `mmap()` 或 `mprotect()` 处收到 `EACCES`。我更倾向于让进程启动失败并打印原因，别检测到不支持后悄悄降级。安全策略静默失效最难排查。

如果父进程只是一个通用 launcher，自己要开 MDWE，启动的子任务却不一定兼容，可以使用 Linux 6.6 的 `PR_MDWE_NO_INHERIT`。这个选择必须和主 flag 一次定好。反过来，专门拉起同类 worker 的服务通常就该保留默认继承，免得每个子进程都漏掉一次调用。

## 跑一个能看见拒绝结果的程序

下面的程序先准备一个 `RW` 页，再打开 MDWE。随后它分别尝试创建 `RWX` 映射、把 `RW` 改成 `RX`，以及把合法的 `RX` 改成 `RWX`。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <unistd.h>

#ifndef PR_SET_MDWE
#define PR_SET_MDWE 65
#endif
#ifndef PR_GET_MDWE
#define PR_GET_MDWE 66
#endif
#ifndef PR_MDWE_REFUSE_EXEC_GAIN
#define PR_MDWE_REFUSE_EXEC_GAIN (1UL << 0)
#endif

static int expect_denied(const char *name, int rc)
{
    if (rc == 0) {
        fprintf(stderr, "%s: unexpectedly succeeded\n", name);
        return 1;
    }
    printf("%s: denied, errno=%d (%s)\n", name, errno, strerror(errno));
    return errno == EACCES ? 0 : 1;
}

int main(void)
{
    size_t size = (size_t)sysconf(_SC_PAGESIZE);
    void *rw = mmap(NULL, size, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (rw == MAP_FAILED) {
        perror("mmap(RW)");
        return 1;
    }

    if (prctl(PR_SET_MDWE, PR_MDWE_REFUSE_EXEC_GAIN, 0L, 0L, 0L) == -1) {
        perror("PR_SET_MDWE (need Linux 6.3+)");
        return 1;
    }
    printf("MDWE mask: 0x%x\n", prctl(PR_GET_MDWE, 0L, 0L, 0L, 0L));

    errno = 0;
    void *rwx = mmap(NULL, size, PROT_READ | PROT_WRITE | PROT_EXEC,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    int bad = expect_denied("mmap(RWX)", rwx == MAP_FAILED ? -1 : 0);
    if (rwx != MAP_FAILED)
        munmap(rwx, size);

    errno = 0;
    bad |= expect_denied("mprotect(RW -> RX)",
                         mprotect(rw, size, PROT_READ | PROT_EXEC));

    void *rx = mmap(NULL, size, PROT_READ | PROT_EXEC,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (rx == MAP_FAILED) {
        perror("mmap(RX)");
        return 1;
    }
    puts("mmap(RX): allowed");

    errno = 0;
    bad |= expect_denied("mprotect(RX -> RWX)",
                         mprotect(rx, size, PROT_READ | PROT_WRITE | PROT_EXEC));

    munmap(rx, size);
    munmap(rw, size);
    return bad;
}
```

编译和运行：

```bash
cc -std=c11 -Wall -Wextra -Wpedantic -O2 mdwe_demo.c -o mdwe_demo
./mdwe_demo
```

我在 Linux 6.8 上得到：

```text
MDWE mask: 0x1
mmap(RWX): denied, errno=13 (Permission denied)
mprotect(RW -> RX): denied, errno=13 (Permission denied)
mmap(RX): allowed
mprotect(RX -> RWX): denied, errno=13 (Permission denied)
```

## 这道门也有边界

MDWE 检查的是 VMA 权限变化，不是内存内容的完整履历。内核自测里有一个看着很怪的合法操作：用 `MAP_FIXED` 替换旧映射，再在同一虚拟地址创建新的 `RX` 映射。旧 VMA 已被解除，新映射从一开始就是可执行，所以检查会放行。

同理，MDWE 没有表达“同一份后备存储绝不能通过两个别名分别以 RW 和 RX 映射”这种更强的策略。它会砍掉最常见的 `RWX` 和 `mprotect` 提权路径，但不是代码完整性、CFI 或 LSM 的替代品。

攻击面也得说清。MDWE 能阻止把数据页改成代码页，能拒绝直接申请 `RWX`，却挡不住只复用现有代码的 ROP 或 JOP。程序已经存在可利用的内存越界时，MDWE 也不会修好那个越界。它只管进程的内存权限；seccomp、只读重定位、控制流保护和正常的输入校验仍要单独做。具体放哪些，要按程序面对的输入和可接受的兼容代价来选。

排错时先看 `errno`。策略拒绝映射或改权限时，`mmap()` 与 `mprotect()` 返回 `EACCES`；`prctl()` 的 mask 不合法、内核不认识该操作时则常见 `EINVAL`。把这两类错误混成一句“内存申请失败”，运维现场基本只能靠猜。

处理不可信网络包或文件、又要长期运行的守护进程，如果没有 JIT，就尽早打开 MDWE。JavaScript 引擎、JVM 和需要持续生成机器码的运行时不能直接照抄；先确认它们采用权限切换还是双映射方案。安全开关一旦和运行时模型打架，结果通常不是更安全，而是服务启动后立刻崩掉。

我喜欢这个 API 的地方正在于它很窄。它没有许诺解决所有内存执行问题，只把一条危险的权限迁移路径变成内核拒绝，而且调用者无法反悔。

## 参考

- [Linux man-pages: PR_SET_MDWE(2const)](https://man7.org/linux/man-pages/man2/pr_set_mdwe.2const.html)
- [Linux man-pages: PR_GET_MDWE(2const)](https://man7.org/linux/man-pages/man2/pr_get_mdwe.2const.html)
- [Linux 6.3 原始提交：mm: implement memory-deny-write-execute as a prctl](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=b507808ebce23561d4ff8c2aa1fb949fe402bc61)
- [Linux 内核 MDWE selftest](https://github.com/torvalds/linux/blob/master/tools/testing/selftests/mm/mdwe_test.c)
- [systemd 使用 PR_SET_MDWE 的修改](https://github.com/systemd/systemd/pull/25276)
