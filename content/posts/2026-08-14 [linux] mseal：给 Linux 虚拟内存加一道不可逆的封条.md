+++
date = '2026-08-14T10:07:53+08:00'
draft = false
title = 'mseal：给 Linux 虚拟内存加一道不可逆的封条'
author = 'JekYUlll'
lastmod = '2026-08-14T10:07:53+08:00'
tags = ['mseal', 'virtual-memory', 'hardening']
categories = ['linux']
+++

把一页内存改成只读，并不等于它会一直只读。只要进程还能调用 `mprotect()`，攻击者拿到足够强的内存破坏原语后，就可能把权限改回可写；也可以用 `munmap()` 腾出地址，再用 `MAP_FIXED` 塞进一段属性完全不同的映射。

Linux 6.10 加入的 `mseal()` 管的是这层问题。它不修改页面内容，也不替你决定 RWX 权限，而是冻结一段虚拟内存映射的元数据。调用成功后，没有 `munseal()`，封条会一直留到进程退出或执行 `execve()`。

## 只读权限还差在哪里

`mprotect(PROT_READ)` 约束的是 CPU 随后的访存行为。VMA，也就是 `vm_area_struct` 描述的映射本身，仍然可以被进程的内存管理系统调用修改。权限位、地址和范围都不是永久状态。

这类差别平时很难察觉。正常代码不会先把 `.text` 改成可写，再去覆盖指令；内存破坏漏洞却可能把本来不会出现的参数送进 `mprotect()` 或 `mremap()`。Trail of Bits 对 `mseal()` 的分析就把它放在这个威胁模型里：攻击者已有进程内执行能力，下一步想篡改映射布局或权限。

`mseal()` 成功后，内核给覆盖范围内的 VMA 加上 `VM_SEALED`。之后这些操作会受到拦截：

- `mprotect()` 和 `pkey_mprotect()` 不能再改保护位；
- `munmap()`、`mremap()` 不能删除、移动或缩放这段映射；
- `mmap(MAP_FIXED)` 不能覆盖它；
- 部分会丢弃匿名页内容的 `madvise()` 行为也会失败。

被拦住的调用通常返回 `EPERM`。但别把“返回失败”理解成跨多个 VMA 的事务。当前内核文档明确区分了行为：`munmap()` 是原子的，`mprotect()`、`pkey_mprotect()` 和 `madvise()` 跨多个 VMA 时可能已经改过前面的区域，`mmap()` 与 `mremap()` 的部分更新行为则没有保证。工程上最好一次封一个边界清楚的独立映射，后续也别拿大范围系统调用横跨它。

## 先定权限，再落锁

接口很短：

```c
int mseal(void *addr, size_t len, unsigned long flags);
```

`addr` 必须按页对齐，起点和终点都要落在已分配的 VMA 中，中间不能有空洞。内核会把 `len` 向上取整到页边界。`flags` 目前保留，必须传 `0`。重复封同一段映射不会报错，这个调用是幂等的。

顺序比接口更要紧。假设一段区域最终只允许读取，应当先写入数据，再执行 `mprotect(PROT_READ)`，最后调用 `mseal()`。如果在 RW 状态下直接封，普通写入仍然合法，此时你已经没法把它改成只读了。这种错误没有补救 API，只能结束进程。

下面的程序走完整流程。glibc 2.43 已经提供 `mseal()` 包装函数，不过许多发行版还在用更早的 glibc，所以示例直接调用系统调用。Linux 6.10 在这里用的系统调用号是 462，代码只为列出的 64 位 ABI 提供旧头文件兼容。

```c
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef SYS_mseal
# if __SIZEOF_POINTER__ != 8
#  error "mseal requires a 64-bit Linux ABI"
# elif defined(__x86_64__) || defined(__aarch64__) || defined(__riscv)
#  define SYS_mseal 462
# else
#  error "add the mseal syscall number for this architecture"
# endif
#endif

static int seal_memory(void *addr, size_t len)
{
    return (int)syscall(SYS_mseal, addr, len, 0UL);
}

int main(void)
{
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) {
        perror("sysconf");
        return 1;
    }

    char *p = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    strcpy(p, "sealed configuration v1");
    if (mprotect(p, (size_t)page, PROT_READ) == -1) {
        perror("mprotect(PROT_READ)");
        return 1;
    }

    if (seal_memory(p, (size_t)page) == -1) {
        if (errno == ENOSYS) {
            puts("kernel does not support mseal; need Linux 6.10+");
            munmap(p, (size_t)page);
            return 0;
        }
        perror("mseal");
        return 1;
    }

    printf("content: %s\n", p);

    errno = 0;
    if (mprotect(p, (size_t)page, PROT_READ | PROT_WRITE) == -1 &&
        errno == EPERM) {
        puts("mprotect blocked with EPERM");
    } else {
        fputs("unexpected: mprotect was not blocked\n", stderr);
        return 1;
    }

    errno = 0;
    if (munmap(p, (size_t)page) == -1 && errno == EPERM) {
        puts("munmap blocked with EPERM");
    } else {
        fputs("unexpected: munmap was not blocked\n", stderr);
        return 1;
    }

    return 0;
}
```

编译不需要额外库：

```bash
gcc -std=c11 -O2 -Wall -Wextra mseal_demo.c -o mseal_demo
./mseal_demo
```

在支持 `mseal()` 的内核上，后两次修改应当分别打印 `mprotect blocked with EPERM` 和 `munmap blocked with EPERM`。旧内核会得到 `ENOSYS`，程序会说明需要 Linux 6.10 以上版本后正常退出。

## fork 会继承，降级要自己决定

封住的映射会被 `fork()` 出来的子进程继承，子进程同样不能改权限或解除映射。`execve()` 会替换整个进程映像，旧映射到这里才结束。这个行为很适合预派生 worker：父进程读入配置并完成校验，封好后再 fork，子进程拿到的是同一份无法改回可写的布局。

但预派生模型也容易放大错误。父进程一旦封错区域，所有子进程都会继承这个决定。热更新若依赖在原地址修改配置，就该换成“创建新映射、校验、封住、切换指针”的版本化方案，旧映射等持有它的进程退出后再消失。试图原地解封行不通。

示例在 `ENOSYS` 时返回成功，是为了让它能在旧内核上跑完兼容性检查。生产代码不能照抄这个策略。如果程序把内存封条当作安全边界的一部分，缺少系统调用就应当拒绝启动；如果它只是额外加固，可以记录一次清楚的告警后继续运行。静默降级最糟，运维看见进程活着，实际保护却没启用。

上线前，先在与你的分配器、JIT 和 checkpoint 工具一致的环境里压测。封条会阻止映射回收和移动，平时不走的退出、重载路径反而最容易暴露问题。先封一类生命周期最简单的只读表，再逐步扩大范围，比一上来处理整片堆或代码缓存稳得多。

## 不要封 malloc 返回的指针

封条改变了映射的寿命。最容易踩的坑，是对 `malloc()` 返回的某个对象调用 `mseal()`。分配器可能从 `brk` 堆里切出这块内存，也可能复用一段较大的匿名映射；它只承诺对象可以交给 `free()`，没承诺对象独占一个 VMA。

对象释放后，分配器还想复用、合并或收缩那段区域，封过的页却不能取消映射，也不能恢复写权限。结果可能是地址空间泄漏，也可能在后续分配时直接崩掉。内核文档对此给得很干脆：不要封 `malloc()` 返回的指针。

需要保存只读配置、校验后的策略表或 JIT 最终生成物时，单独 `mmap()` 整页更稳。先完成初始化和校验，再收紧权限并封住映射。这样 VMA 的所有权和寿命都摆在代码表面，不会暗中破坏分配器。

## 它封的是映射，不是所有写入通道

`mseal()` 不是“不可变内存”开关。映射若保留 `PROT_WRITE`，CPU 照样可以写；它也不负责阻断 `/proc/self/mem`、`ptrace(PTRACE_POKETEXT)` 或 `userfaultfd` 等路径。需要对付同机调试者或被接管的高权限进程，还得配合 seccomp、LSM、Yama 等限制。

我更愿意把它看成 W^X 策略的最后一道闩：W^X 决定当前能写还是能执行，`mseal()` 防止进程稍后反悔。它适合生命周期固定、最终权限明确的映射。只要对象仍需回收、扩容或换权限，就先别封。

2026 年 1 月发布的 glibc 2.43 已加入 `mseal()` 函数。新发行版可以直接用包装函数，兼容旧版时再保留上面的 `syscall()` 封装。我不会把它撒到每个只读映射上；封哪一段，先看那段 VMA 是否真的会活到进程结束。

## 参考

- Linux kernel documentation, Introduction of mseal: https://docs.kernel.org/userspace-api/mseal.html
- Linux 6.10 kselftest, `mseal_test.c`: https://github.com/torvalds/linux/blob/v6.10/tools/testing/selftests/mm/mseal_test.c
- Trail of Bits, A deep dive into Linux's new mseal syscall: https://blog.trailofbits.com/2024/10/25/a-deep-dive-into-linuxs-new-mseal-syscall/
- GNU C Library 2.43 release announcement: https://sourceware.org/pipermail/libc-announce/2026/000052.html
- GNU C Library manual, Memory Protection: https://sourceware.org/glibc/manual/2.43/html_node/Memory-Protection.html
