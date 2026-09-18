+++
date = '2026-09-18T10:15:05+08:00'
draft = false
title = 'MPTCP：把一条 TCP 连接铺在多条路径上'
author = 'JekYUlll'
lastmod = '2026-09-18T10:15:05+08:00'
tags = ['mptcp', 'tcp', 'kernel', 'networking']
categories = ['linux']
+++

你从家出门，手机从 Wi-Fi 切到蜂窝网络，正在听的歌不会断。播放器没做任何重连，干活的是 iPhone 从 iOS 7 起就在用的 MPTCP：一条 TCP 连接同时挂在多条路径上，任意一条断了，数据从另一条继续走。

Linux 这边，MPTCP 从内核 5.6 进主线，协议编号是 RFC 8684，也就是 MPTCPv1，上游只实现了这一版（更早的 RFC 6824 已经废弃）。它的冷门程度和实用性不成比例，API 改动小到只需要换 socket 的第三个参数，剩下的代码一行不用动。

## 一条连接，若干条子流

MPTCP 没有推倒重来，内核实现拿普通 TCP 当积木：一条 MPTCP 连接由若干条 **subflow（子流）** 组成，每条子流就是一条真正的 TCP 连接，跑在某个网络接口上。连接建立时，SYN 里多加一个 **MP_CAPABLE** 选项，声明自己支持多路径，双方交换 key 之后连接立起来。之后想增减路径，服务端用 ADD_ADDR、REMOVE_ADDR 选项通告地址变化，客户端据此建新子流或者拆掉旧的。

协商失败的处理才是设计里最见功夫的地方。对端不支持 MPTCP，或者中间设备把 MP_CAPABLE 选项剥掉，回来的 SYN+ACK 就不会带这个选项，连接当场降级成普通 TCP 继续跑，应用无感。换句话说，打开 MPTCP 的最低收益是零损失：最坏情况你得到的就是一条普通 TCP 连接。

降级行为还有两个旋钮，较新的内核文档里能查到。`net.mptcp.syn_retrans_before_tcp_fallback` 默认 2：SYN 重传两次还协商不上，后面的重传就不再带 MPTCP 选项。`net.mptcp.blackhole_timeout` 默认 3600 秒：探测到有防火墙黑洞式丢 MPTCP 包时，临时降级这条连接。

## 最小实验：socket 第三个参数换成 262

先确认环境。内核要编译了 CONFIG_MPTCP 和 CONFIG_MPTCP_IPV6，Ubuntu 24.04 自带的 6.8 内核就有；再用 sysctl 确认没被关掉：

```bash
$ cat /proc/sys/net/mptcp/enabled
1
```

然后写个回显服务器。唯一特别的地方是 socket() 的协议参数用 IPPROTO_MPTCP（就是 262）：

```c
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void) {
    int lfd = socket(AF_INET, SOCK_STREAM, IPPROTO_MPTCP);
    if (lfd < 0) { perror("socket"); return 1; }

    /* 重跑 demo 时避免上一轮的 TIME_WAIT 挡住端口 */
    int one = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(9000);
    if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) { perror("bind"); return 1; }
    if (listen(lfd, 4) < 0) { perror("listen"); return 1; }

    printf("listening on 127.0.0.1:9000 (IPPROTO_MPTCP=%d)\n", IPPROTO_MPTCP);
    fflush(stdout);

    int cfd = accept(lfd, NULL, NULL);
    if (cfd < 0) { perror("accept"); return 1; }
    char buf[64] = {0};
    ssize_t n = read(cfd, buf, sizeof(buf) - 1);
    printf("server read %zd bytes: %s\n", n, buf);
    write(cfd, "pong", 4);
    close(cfd);
    close(lfd);
    return 0;
}
```

客户端同样只改这一个参数，连上之后用 SO_PROTOCOL 问内核：这个 socket 到底是什么类型。

```c
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void) {
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_MPTCP);
    if (fd < 0) { perror("socket"); return 1; }

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(9000);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) { perror("connect"); return 1; }

    write(fd, "ping", 4);
    char buf[64] = {0};
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    printf("client read %zd bytes: %s\n", n, buf);

    int proto = 0;
    socklen_t len = sizeof(proto);
    getsockopt(fd, SOL_SOCKET, SO_PROTOCOL, &proto, &len);
    printf("SO_PROTOCOL = %d (TCP=%d, MPTCP=%d)\n", proto, IPPROTO_TCP, IPPROTO_MPTCP);
    close(fd);
    return 0;
}
```

编译运行：

```bash
$ gcc -Wall -Wextra -o mptcp_server mptcp_server.c
$ gcc -Wall -Wextra -o mptcp_client mptcp_client.c
$ ./mptcp_server &
listening on 127.0.0.1:9000 (IPPROTO_MPTCP=262)
$ ./mptcp_client
client read 4 bytes: pong
SO_PROTOCOL = 262 (TCP=6, MPTCP=262)
```

SO_PROTOCOL 返回 262 而不是 6，这就是内核给 MPTCP socket 发的身份证。连接还没断的时候，ss -M 能看到这对 socket（两条 ESTAB 分别是客户端和服务器两端）：

```bash
$ ss -M
State Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
ESTAB 0      0          127.0.0.1:60348    127.0.0.1:9000
ESTAB 964    0          127.0.0.1:9000     127.0.0.1:60348
```

默认每条连接最多挂 2 条子流，想看或者想改这个限制：

```bash
$ ip mptcp limits show
add_addr_accepted 0 subflows 2
```

通告更多地址需要 root 权限，走 netlink 配 endpoint：

```bash
# ip mptcp endpoint add 10.0.0.2 dev eth1 subflow
```

生产代码里对创建失败的兜底，官方给的建议是运行时检查而不是编译期判断（编译机和运行机的内核可能完全不同）：

```c
int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_MPTCP);
if (fd < 0 && (errno == EINVAL || errno == EPROTONOSUPPORT || errno == ENOPROTOOPT))
    fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
```

三个错误码对应三种情况：内核老于 5.6（EINVAL）、编译时没开 MPTCP（EPROTONOSUPPORT）、被 sysctl 关了（ENOPROTOOPT）。老 libc 里没有 IPPROTO_MPTCP 这个宏的话，自己 #define 成 262 即可。

## 降级验证：对着普通 TCP 服务器连

把服务器源码里 socket() 的第三个参数从 IPPROTO_MPTCP 改成 0，重编重跑。客户端一行不改，照样收到 pong，输出和上面完全一样。这就是降级：本端 socket 从创建起就是 MPTCP 类型（SO_PROTOCOL 仍然是 262），但子流协商失败后，数据在一条普通 TCP 连接上跑。

真实网络上想确认自己到底有没有用上 MPTCP，有个现成的服务 check.mptcp.dev。同一段 Python，分别用 TCP 和 MPTCP socket 请求它的 443 端口：

```python
import socket, ssl

def fetch(proto: int) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto)  # 6 = TCP, 262 = MPTCP
    s.settimeout(15)
    tls = ssl.create_default_context().wrap_socket(s, server_hostname="check.mptcp.dev")
    tls.connect(("check.mptcp.dev", 443))
    tls.sendall(b"GET / HTTP/1.1\r\nHost: check.mptcp.dev\r\n"
                b"User-Agent: curl/8.5.0\r\nConnection: close\r\n\r\n")
    buf = b""
    while True:
        chunk = tls.recv(4096)
        if not chunk:
            break
        buf += chunk
    tls.close()
    return buf.split(b"\r\n\r\n", 1)[-1].decode().strip()

print("plain TCP:", fetch(socket.IPPROTO_TCP))
print("MPTCP    :", fetch(262))
```

这台机器上跑出来的结果：

```bash
plain TCP: You are not using MPTCP.
MPTCP    : You are using MPTCP.
```

## 给现成程序套上 MPTCP

重写应用不是必须的。社区给了三层拦截 socket 创建的办法：

- mptcpize 用 LD_PRELOAD 把程序里的 socket(AF_INET, SOCK_STREAM) 全部改写成 MPTCP，`mptcpize run ./your-app` 直接跑。Ubuntu 24.04 的 universe 仓库里就有（包名 mptcpd，0.12-4）。
- Go 程序不走 libc，LD_PRELOAD 无效，官方给的是环境变量：`GODEBUG=multipathtcp=1`（Go 1.21 起）。
- eBPF：内核 6.6 起可以给 cgroup 挂程序，把这个组里新建的 socket 换成 MPTCP，bcc 工具集里的 mptcpify 就是这个路子。

服务端什么都不用配。客户端主动请求就用 MPTCP，客户端没请求，内核 accept 出来的就是普通 TCP socket，对服务端几乎零成本。多路径的配置成本都在客户端侧。

## 调度器和路径管理器要不要动

连接立起来之后，包发往哪条子流由 packet scheduler 决定，子流的建删和地址通告由 path manager 决定，两个都能换。

调度器走 `net.mptcp.scheduler` 选，默认值 default，较新的内核里还有 available_schedulers 能列出当前已注册的调度器。主线源码里目前注册的也只有 default 一个，想上的自定义策略还在补丁阶段：2024 年起社区在给 MPTCP 加 bpf_iter、kfunc 和 BPF 包调度器（bpf_mptcp_sched_ops），代码在 mptcp_net-next 开发树里迭代，还没合进主线。现在 BPF 能实际用上的，是前面说的 cgroup 层拦截 socket 创建。

路径管理器用 `net.mptcp.path_manager` 切内核版和用户态版（mptcpd），旧的 pm_type 从 6.15 起标记废弃。内核版一套规则管全局，用户态版能按连接定制，代价是常驻一个 daemon 处理 netlink 事件。

另有个容易看漏的细节：`net.mptcp.stale_loss_cnt` 默认 4，某条子流连续 4 个重传周期没有任何成功交付，就被标记为 stale，调度器不再往上放新数据。调大能在劣质链路上多榨一点，调小可以更快切到备用路径。

## 谁在跑 MPTCP

最大规模的部署在苹果。iOS 7 起 Siri 默认开 MPTCP，理由很朴素：用户经常一边往外走一边喊 Siri，走到门口 Wi-Fi 就断了。后来 Apple Maps、Apple Music（iOS 13 起）也默认启用，第三方应用里 iOS 版 Firefox 133 默认开。macOS 也支持，但只做客户端。

Cloudflare 2023 年写过一篇实测博客，给过一份现状盘点：像样的实现只有两个，Linux 内核（5.6 起，但"现实中你至少需要 6.1"）和苹果（iOS 7 起）；常见组合是 Linux 当服务端、苹果设备当客户端；另外旧文档过时严重，以 mptcp.dev 和内核源码为准。

同一篇里他们还标了两组坑：MPTCP 和 kTLS 目前不兼容；部分 setsockopt 还没打通，比如 TCP_USER_TIMEOUT。依赖这些特性的应用上生产前要自己验证一遍。协议头开销大约每包 1%（mptcp.dev FAQ 的说法），用它换路径冗余和吞吐，这笔账因场景而异。

## 什么时候值得打开

服务端可以放心打开，收益的大头在客户端侧：

- 移动场景收益最直接，Wi-Fi 和蜂窝之间切换不断连，语音、语音助手这类交互式流量体感最明显。
- 双上行环境（两条运营商线路、链路冗余）也值得试，故障切换对应用透明。
- 单路径内网服务开不开一个样，你得到的还是普通 TCP，不用折腾。

想再往里看，check.mptcp.dev 和 mptcp.dev 的 setup 页是好入口；程序里想自己观察子流状态，内核文档里 SOL_MPTCP（284）那组 socket 选项（MPTCP_INFO、MPTCP_SUBFLOW_ADDRS）够用了。

## 参考

- Linux 内核 MPTCP 文档：https://docs.kernel.org/networking/mptcp.html
- 内核 MPTCP sysctl 文档：https://docs.kernel.org/networking/mptcp-sysctl.html
- Multipath TCP for Linux（Setup）：https://www.mptcp.dev/setup.html
- Multipath TCP for Linux（FAQ）：https://www.mptcp.dev/faq.html
- Multipath TCP for Linux（App 支持列表）：https://www.mptcp.dev/apps.html
- Cloudflare：Multi-Path TCP: revolutionizing connectivity, one path at a time：https://blog.cloudflare.com/multi-path-tcp-revolutionizing-connectivity-one-path-at-a-time/
- LWN：bpf: Add mptcp_subflow bpf_iter support：https://lwn.net/Articles/1014990/
- mptcp_net-next 开发树：https://github.com/multipath-tcp/mptcp_net-next
- check.mptcp.dev：https://check.mptcp.dev
