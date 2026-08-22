+++
date = '2026-08-22T10:00:59+08:00'
draft = false
title = 'ublk：把 Linux 块设备驱动搬到用户态'
author = 'JekYUlll'
lastmod = '2026-08-22T10:00:59+08:00'
tags = ['ublk', 'io-uring', 'block-device']
categories = ['linux']
+++

写一个虚拟块设备，最大的麻烦是驱动要进内核，读写逻辑反而通常很简单。一次越界访问就可能拖垮整台机器，调试还得在内核工具链里兜圈子。Linux 6.0 加入的 ublk 换了个分工：内核保留块层入口，设备逻辑交给普通用户态进程。

这条路并不神秘，也不保证换成用户态就会更快。ublk 解决的是驱动边界问题。qcow2 映射、远端存储协议、测试用故障注入都可以使用现成库实现，服务进程也能独立升级；代价是每个 I/O 都要跨过内核与用户态，队列、缓冲区和崩溃恢复一个都不能糊弄。

## 三个设备节点，各管一件事

加载 `ublk_drv` 后，系统先提供全局控制节点 `/dev/ublk-control`。用户态服务通过它发送添加、设置参数、启动、停止和删除设备等命令。添加成功会出现 `/dev/ublkcN`，这是服务进程使用的字符设备；启动成功后才会出现 `/dev/ublkbN`，文件系统、数据库和 `fio` 看到的是后一个块设备。队列数、深度和最大请求缓冲区会在创建阶段与驱动协商，基本设备信息一旦确定就不能临时改。

控制面的顺序不能乱：`ADD_DEV` 创建字符设备，`SET_PARAMS` 固定容量和队列限制，服务线程准备好 `io_uring` 与映射区后，`START_DEV` 才把块设备暴露给系统。停止时顺序反过来。内核不理解后端是文件、网络还是一段现场计算出来的数据，它只负责把块层请求交过去，再接回完成结果。

```text
mkfs / mount / database
          |
      /dev/ublkbN
          |
   blk-mq + ublk_drv
          |
 IORING_OP_URING_CMD
          |
      ublk server
          |
 file / NVMe / network / custom logic
```

这和 FUSE 不是同一层。FUSE 把文件系统操作交给用户态，ublk 提供的是扇区读写语义。你甚至可以先在 ublk 设备上做一层 device-mapper，再格式化成 ext4，块层上方的组件不需要知道后端住在用户态。

## `(queue_id, tag)` 怎么跑完一次 I/O

ublk 的数据面建立在 blk-mq 和 `IORING_OP_URING_CMD` 上。每个请求由 `(queue_id, tag)` 唯一标识，服务线程提前为各个 tag 提交 `UBLK_U_IO_FETCH_REQ`。请求到达后，驱动把操作类型、偏移、长度和标志写入一块 `mmap` 共享的 `ublksrv_io_desc` 数组，再完成对应的 io_uring 请求来通知服务。

服务根据 tag 直接索引描述符，不用在链表里找请求。后端读写结束后，它提交 `UBLK_U_IO_COMMIT_AND_FETCH_REQ`：同一个命令既归还上次结果，也继续等待下一次请求。少提交一个命令看起来只是小优化，但队列持续繁忙时，这正好省掉一轮提交动作。

一个常见结构是每个硬件队列配一个线程和一个 ring。线程收到前端请求后，仍可把真正的 `pread`、网络收发或 SPDK 操作异步提交出去。这里最容易写错的是完成事件的归属：来自 ublk 驱动的通知和后端 I/O 的完成都可能出现在 ring 中，服务必须用 `user_data` 或分开的 ring 区分，不能看见 CQE 就当成同一种完成。

## 先把缓冲区账算清楚

默认模式会给每个 `(queue_id, tag)` 准备 I/O 缓冲区。写请求先从块层页复制到服务缓冲区，读请求完成后再反向复制。预分配上限可以粗略按 `队列数 × queue_depth × max_io_buf_bytes` 算；2 个队列、深度 128、单请求上限 1 MiB，就是 256 MiB。

下面的小工具只做预算，不接触设备。它的用处很朴素：改队列参数之前先看一眼，别为了压测把常驻内存翻上去还不知道原因。

```c
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t number(const char *s) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(s, &end, 10);
    if (errno || !end || *end != '\0') {
        fprintf(stderr, "invalid number: %s\n", s);
        exit(2);
    }
    return (uint64_t)value;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s QUEUES DEPTH MAX_IO_BYTES\n", argv[0]);
        return 2;
    }

    uint64_t queues = number(argv[1]);
    uint64_t depth = number(argv[2]);
    uint64_t bytes = number(argv[3]);
    if (!queues || !depth || !bytes || queues > UINT64_MAX / depth ||
        queues * depth > UINT64_MAX / bytes) {
        fputs("zero or overflow\n", stderr);
        return 2;
    }

    uint64_t total = queues * depth * bytes;
    printf("buffer budget: %llu bytes (%.2f MiB)\n",
           (unsigned long long)total, total / 1048576.0);
    return 0;
}
```

```bash
cc -std=c11 -O2 -Wall -Wextra ublk-buffer-budget.c -o ublk-buffer-budget
./ublk-buffer-budget 2 128 1048576
# buffer budget: 268435456 bytes (256.00 MiB)
```

如果这笔内存不能接受，可以降低队列深度和单请求上限，也可以改用 user-copy 模式按请求分配。零拷贝是另一条路：ublk 能把请求页注册进 io_uring 的 fixed buffer 表，`UBLK_F_AUTO_BUF_REG` 还能自动处理注册与注销。零拷贝同时增加了权限与缓冲区生命周期约束。官方文档要求这类服务具备 `CAP_SYS_ADMIN` 且可信，因为错误的完成长度可能把未初始化的内核缓冲区暴露给调用方。

## 用文件后端跑通最小链路

最省事的练习是把普通文件作为后端。需要 Linux 6.0 以上内核、`CONFIG_BLK_DEV_UBLK` 和 ublksrv 提供的 `ublk` 命令。下面会格式化 `/dev/ublkb0`，只能在确认编号空闲的测试机上执行；已有设备时换一个明确的 ID。

```bash
sudo modprobe ublk_drv
truncate -s 256M /tmp/ublk-loop.img

sudo ublk add -t loop -f /tmp/ublk-loop.img
ublk list

sudo mkfs.ext4 /dev/ublkb0
sudo mkdir -p /mnt/ublk-demo
sudo mount /dev/ublkb0 /mnt/ublk-demo
printf 'hello from ublk\n' | sudo tee /mnt/ublk-demo/hello.txt
sudo cat /mnt/ublk-demo/hello.txt

sudo umount /mnt/ublk-demo
sudo ublk del -n 0
rm -f /tmp/ublk-loop.img
```

这段命令适合验证控制面和数据面，不是性能测试。要测吞吐和延迟，后端文件不能和测试目标互相绕回同一个薄弱磁盘，还要固定队列数、深度、块大小与 direct I/O。否则跑出来的数字只是在量页缓存或底层盘，和 ublk 本身关系不大。

## 服务崩了，块设备怎么办

用户态进程崩溃不会把内核一起带走，但上层 I/O 仍要有确定结果。开启 `UBLK_F_USER_RECOVERY` 后，服务退出时设备 ID 和 `/dev/ublkbN` 可以保留，驱动暂停设备，等待新进程重新打开字符设备并恢复队列。默认情况下，尚未交给用户态的请求会重新排队，已经交出去的请求会中止。

`UBLK_F_USER_RECOVERY_REISSUE` 会把已发出的请求也交给新进程重做。这对只读后端好用，对写请求却有重复执行风险；存储协议没有幂等保证时别开。另一种 `UBLK_F_USER_RECOVERY_FAIL_IO` 会让请求持续返回 I/O 错误，直到服务恢复。它不温柔，但比挂住调用线程更容易被监控系统发现。

## 我会在什么场景选 ublk

需要自定义块语义，又不想维护内核模块时，我会先看 ublk。它适合把 qcow2、远端协议或 SPDK bdev 接到本机标准块层，也适合构造延迟、坏块和短读写等测试设备。SPDK 已经提供 ublk target，可把用户态管理的 NVMe、RBD 或 malloc bdev 暴露成 `/dev/ublkbN`。

如果需求只是挂载一个普通镜像，`loop` 更省事；如果目标本来就是文件系统接口，先看 FUSE。选择 ublk，是为了把标准块设备接口、io_uring 队列和可替换的后端逻辑接到一起。代价也摆在桌面上：内存预算、权限边界和恢复语义都由服务作者负责。

## 参考

- Linux kernel documentation, Userspace block device driver: https://docs.kernel.org/block/ublk.html
- ublksrv project: https://github.com/ublk-org/ublksrv
- SPDK ublk Target: https://spdk.io/doc/ublk.html
- Jiri Pospisil, Creating virtual block devices with ublk: https://jpospisil.com/posts/2026-01-13-creating-virtual-block-devices-with-ublk
