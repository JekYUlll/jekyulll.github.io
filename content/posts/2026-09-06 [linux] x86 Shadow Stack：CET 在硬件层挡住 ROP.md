+++
date = '2026-09-06T10:10:04+08:00'
draft = false
title = 'x86 Shadow Stack：CET 在硬件层挡住 ROP'
author = 'JekYUlll'
lastmod = '2026-09-06T10:10:04+08:00'
tags = ['cet', 'shadow-stack', 'rop']
categories = ['linux']
+++

缓冲区溢出攻击里最值钱的东西是返回地址。它躺在栈上，离攻击者能越界写的数据只有几个字节远，被改写之后 ret 指令会把它当跳板。金丝雀、NX、ASLR 都能绕：canary 泄露一次就废，NX 只是把攻击者逼到 ROP 而不是 shellcode，ASLR 靠一个地址泄露也能拆。软件防御的共同问题是，返回地址和普通数据住在同一块内存里，防御代码没法区分哪次写是合法的。

CET 的 shadow stack 换了个思路：把返回地址的副本放到攻击者写不到的地方，由 CPU 自己维护。CET 其实有两半，shadow stack 管 ret，IBT（indirect branch tracking）管间接跳转必须落在 ENDBR 指令上。Linux 用户态实际落地的是前半，内核文档写得明白：目前只支持用户态 shadow stack 加内核自身 IBT。这篇文章只讲 shadow stack。它和之前写过的 mseal、PR_SET_MDWE 是一条思路的两端：那边把内核里的权限单向化，这边把返回地址的校验直接交给硬件。

## 机制：CALL 双写，RET 比对

开了 shadow stack 之后，每条 CALL 会同时把返回地址压进普通栈和一块独立内存；RET 时 CPU 弹出两份做比较，不一致就抛 control-protection exception（#CP）。内核把 #CP 转成 SIGSEGV，si_code 是 SEGV_CPERR，在 glibc 头文件里枚举值是 10。攻击者改写的是普通栈那份，shadow 那份对不上，进程当场死掉。

这个检查发生在每一条 RET 上，这一点对 ROP 是致命的。ROP 的链子由一个接一个的 ret gadget 拼成，每个 gadget 结尾都靠 ret 跳到下一个，等于链条上每个环节都要过一遍比对，第一跳就断。

那块独立内存由内核分配，大小按 MIN(RLIMIT_STACK, 4GB) 来。页表项是只读加 dirty 的组合，普通 store 指令写不进去，只有 CPU 内部 CALL/RET 的路径能更新（还有一条专门的 WRSS 指令，默认关）。攻击者就算拿到任意地址写，也够不着这份副本。

特征状态按线程管理，接口是 x86 专用的 arch_prctl 系统调用：ARCH_SHSTK_ENABLE（0x5001）、STATUS（0x5005）、LOCK（0x5003）。主线程开起来之后，clone 出来的线程自动继承；pthread 新线程由内核另分配 shadow stack。fork 走页表 dirty 位的 COW。exec 会清掉状态，由新程序决定是否再开。信号处理内核也包了：进 handler 前把旧 SSP 指针按 token 格式（最高位置 1）压进 shadow stack，sigreturn 时校验，restorer 地址也会压一份，避免 sigreturn 路径自己触发 #CP。

## 落地：内核 6.6 + glibc 2.39

硬件侧，Intel Tiger Lake（2020）和 AMD Zen 3（2021）之后的 x86 CPU 基本都带，Linux 下看 `/proc/cpuinfo` 的 `user_shstk`。内核 6.6（2023 年 10 月 29 日发布）合入用户态 shadow stack，配置项 CONFIG_X86_USER_SHADOW_STACK，启动参数 `nousershstk` 可以关掉。

内核特意不解析 ELF 里的 CET 标记。用 `gcc -fcf-protection=full` 编译的二进制会带 GNU property note，`readelf -n` 能看到 `x86 feature: IBT, SHSTK`，但按内核文档，标记只是声明"我准备好了"，激活必须由 loader 调 arch_prctl 完成。

glibc 2.39（2024 年 1 月 31 日发布）补上了这步：configure 加 `--enable-cet` 后，动态链接器在启动早期检查二进制和所有依赖库的标记，全带才开。Ubuntu 和 Fedora 早把 `-fcf-protection` 设为默认编译参数，系统里大部分二进制都带标记，但标记不等于运行时开启。开不开属于发行版策略，还得放到全进程的上下文里评估，所以内核只提供机制，决定权在 loader；二进制自己还能用 ARCH_SHSTK_LOCK 锁死状态，防止后续代码（比如 dlopen 进来的不可信模块）偷偷关掉保护。

这里有个连带约束：全链都带标记是前提。`--enable-cet` 构建下 dlopen 一个没标记的库会直接报错，permissive 模式则静默关掉 shadow stack 继续跑。库生态不齐，默认开就是把崩溃风险推给所有人。

## 实测：同一份溢出代码，开与不开

我在一台 Ubuntu 24.04（内核 6.8，glibc 2.39-0ubuntu8.7，CPU 带 user_shstk）上跑了个最小 demo。程序从 stdin 读 payload，拷进 8 字节的缓冲区。gcc 13 的 `-O0` 布局下缓冲区起点到返回地址正好 16 字节（8 字节 buf + 8 字节 saved rbp），payload 就是 16 个 `A` 加上 win() 的地址（小端）。编译用 `-no-pie` 固定地址、`-fno-stack-protector` 去掉 canary，演示需要，真实攻击里这些要靠泄露补齐。

```c
// rop_demo.c：对比 shadow stack 开关前后的行为
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdint.h>
#include <signal.h>
#include <sys/syscall.h>

#define ARCH_SHSTK_STATUS 0x5005
#define ARCH_SHSTK_SHSTK  (1ULL << 0)

static void win(void) {
    char msg[] = "[+] win() 被执行：返回地址被劫持成功\n";
    write(1, msg, strlen(msg));
    _exit(0);
}

// 打印 shadow stack 状态；用 write 而不是 printf，避免信号处理里重入
static void print_features(const char *tag) {
    uint64_t f = 0;
    char buf[128];
    int n;
    if (syscall(SYS_arch_prctl, ARCH_SHSTK_STATUS, &f) == 0)
        n = snprintf(buf, sizeof buf, "%s: SHSTK=%s (features=0x%llx)\n",
                     tag, (f & ARCH_SHSTK_SHSTK) ? "ON" : "off",
                     (unsigned long long)f);
    else
        n = snprintf(buf, sizeof buf, "%s: ARCH_SHSTK_STATUS 失败\n", tag);
    write(1, buf, n);
}

static void on_segv(int sig, siginfo_t *si, void *ctx) {
    const char *msg;
    if (si->si_code == SEGV_CPERR)   // #CP：shadow stack 比对失败
        msg = "[-] SIGSEGV (si_code=SEGV_CPERR)：返回地址不一致，shadow stack 拦下了劫持\n";
    else
        msg = "[-] SIGSEGV (其它原因)\n";
    write(1, msg, strlen(msg));
    _exit(128 + sig);
}

static void vuln(const char *s) {
    char buf[8];
    strcpy(buf, s);           // 无边界拷贝，攻击者数据可以盖过返回地址
    char ok[] = "[-] vuln() 正常返回\n";
    write(1, ok, strlen(ok));
}

int main(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_segv;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL);
    setbuf(stdout, NULL);   // 关缓冲，输出顺序和实际执行一致

    print_features("main 入口");

    char input[256];
    ssize_t n = read(0, input, sizeof input - 1);   // payload 从 stdin 读
    if (n <= 0) return 1;
    input[n] = '\0';
    printf("[*] win() 地址: %p\n", (void *)win);

    vuln(input);
    char done[] = "[-] main() 正常结束\n";
    write(1, done, strlen(done));
    return 0;
}
```

编译一个带 CET 标记的版本，payload 里的 win() 地址让程序自己打印出来再拼：

```bash
gcc -O0 -fno-stack-protector -no-pie -fcf-protection=full -o rop_cet rop_demo.c
readelf -n rop_cet | grep SHSTK   # 确认二进制带上了 CET 标记
python3 - <<'PY'
import re, struct, subprocess
out = subprocess.run(['./rop_cet'], input=b'X', capture_output=True).stdout.decode()
addr = int(re.search(r'win\(\) 地址: (0x[0-9a-f]+)', out).group(1), 16)
open('payload.bin', 'wb').write(b'A' * 16 + struct.pack('<Q', addr))
PY
```

Ubuntu 的 gcc 默认就带 `-fcf-protection`，不加参数编译出来的产物同样带标记，这里显式写上是让命令在其他发行版上也能复现。readelf 会输出 `Properties: x86 feature: IBT, SHSTK`。

直接跑，返回地址劫持成功：

```text
$ ./rop_cet < payload.bin
main 入口: SHSTK=off (features=0x0)
[*] win() 地址: 0x401276
[-] vuln() 正常返回
[+] win() 被执行：返回地址被劫持成功
```

注意这个二进制带着 CET 标记，运行时却一点保护都没有：标记只是声明，glibc 默认不激活。用 glibc tunable 显式开启后，同一个二进制、同一个 payload，结局完全不同：

```bash
$ GLIBC_TUNABLES=glibc.cpu.hwcaps=SHSTK ./rop_cet < payload.bin
main 入口: SHSTK=ON (features=0x1)
[*] win() 地址: 0x401276
[-] vuln() 正常返回
[-] SIGSEGV (si_code=SEGV_CPERR)：返回地址不一致，shadow stack 拦下了劫持
```

同样的二进制、同样的 payload，差别只在开不开。进程启动时 features=0x1，vuln() 的 RET 弹出 shadow stack 上的真返回地址和普通栈上的 win() 地址一比，不一致，CPU 抛 #CP。运行期间看 /proc/self/status 也能确认：

```text
x86_Thread_features: shstk
x86_Thread_features_locked: shstk wrss
```

第二行说明 glibc 开完顺手把状态锁了（ARCH_SHSTK_LOCK），WRSS 被锁在关闭位，进程自己不能关掉保护或放开 WRSS，想解锁只能靠 ptrace。

顺带记一个我踩的坑：别在 main 里自己调 ARCH_SHSTK_ENABLE。开启后的第一个 RET 就会 #CP 崩溃，因为调用链里开启之前建立的帧在 shadow stack 上没有对应条目，RET 一弹就空栈违例。我第一版 demo 就是开完直接段错误。所以激活必须在进程最早的阶段、调用栈还没有历史帧的时候做，这正是动态链接器干的活。

## 为什么到现在还没默认全开

glibc 2.39 的策略是默认关，opt-in 开启。原因上面已经露出一半：它要求整个进程配合。二进制和依赖库全带标记只是底线，setjmp/longjmp、ucontext、手写汇编里直接 push/ret 的代码、JIT 生成代码、调试器，都可能在没有 shadow stack 意识时露馅。这套接口从内核补丁到 glibc 适配来回折腾了好几年，最后 6.6 和 2.39 才凑齐，也说明默认开启的包袱有多重。

Fedora 45 的变更提案（2025 年）计划系统级默认开：动态链接器对"二进制和依赖全部带 SHSTK 标记"的进程自动激活，不满足就静默不开，不兼容的应用用 per-app 配置 opt-out（`/etc/tunables.conf.d/` 里写 `glibc.cpu.x86_shstk=off`）。Ubuntu 24.04 这边我实测默认还是关的。想先体验，给单个命令加环境变量就行：

```bash
GLIBC_TUNABLES=glibc.cpu.hwcaps=SHSTK ./my_program
```

别全局 export。进程里如果有 JIT 或者没带标记的插件库，结局可能是 dlopen 报错或者直接崩，先拿小工具试。

## 可以马上试的三件事

1. 检查硬件和系统够不够格：`grep user_shstk /proc/cpuinfo`，内核要 6.6+（`uname -r`），glibc 要 2.39+（`ldd --version`）。
2. 看看系统里的二进制有没有"准备好"：`readelf -n /usr/bin/<程序> | grep -i shstk`。Ubuntu 默认带 -fcf-protection 编译，大部分会显示 `IBT, SHSTK`，注意这只是标记。
3. 把上面的 demo 编译跑一遍，同一个 payload 对比开与不开两种模式（默认运行 vs 加 `GLIBC_TUNABLES=glibc.cpu.hwcaps=SHSTK`），再用 `/proc/self/status` 验证特征行。

## 参考

- Kernel 文档：Control-flow Enforcement Technology (CET) Shadow Stack：https://docs.kernel.org/arch/x86/shstk.html
- KernelNewbies：Linux 6.6（2023-10-29 发布，含 x86 shadow stack）：https://kernelnewbies.org/Linux_6.6
- LWN：GNU C Library version 2.39（shadow stack 部分）：https://lwn.net/Articles/960309/
- LWN：User-space shadow stacks：https://lwn.net/Articles/926649/
- x86.lol：Hardening C Against ROP: Getting CET Shadow Stacks Working：https://x86.lol/generic/2024/09/23/user-shadow-stacks.html
- Phoronix：Glibc Updated For Recent Linux CET Shadow Stack Support：https://www.phoronix.com/news/Glibc-Intel-CET-Shadow-Stack
- Fedora 45 Change Proposal：Enable Shadow Stack by Default on x86_64 system-wide：https://discussion.fedoraproject.org/t/f45-change-proposal-enable-shadow-stack-by-default-on-x86-64-system-wide/195400
- arch_prctl(2) man page：https://man7.org/linux/man-pages/man2/arch_prctl.2.html
