+++
date = '2025-06-08T20:05:47+08:00'
draft = false
title = '容器化技术之 Linux namespace'
author = 'JekYUlll'
lastmod = '2025-06-08T20:05:47+08:00'
tags = ['linux', 'os']
categories = ['linux']
+++

Linux namespace做一层资源隔离，使里面的进程/进程组看起来拥有自己的独立资源。  

PID namespace 中的 `init` 进程（PID=1）需要正确处理子进程的僵尸状态，否则会导致资源泄漏。

有多种namespace:   
- PID Namespace（CLONE_NEWPID）：不同 namespace 中的进程可以拥有相同的 PID  
- Network Namespace（CLONE_NEWNET）：隔离网络栈，包括网络设备、IP 地址、端口、路由表以及防火墙规则
- Mount Namespace（CLONE_NEWNS）：隔离文件系统挂载点  
- User Namespace（CLONE_NEWUSER）：隔离用户和组 ID 空间，允许同一个用户在不同 namespace 中拥有不同的权限  
- ...

Docker 容器默认会使用以下 namespace：  
- PID：隔离进程树。
- NET：提供独立的网络栈。
- IPC：隔离进程间通信。
- UTS：设置独立的主机名。
- MOUNT：隔离文件系统挂载点。
- USER：用于映射容器内的 root 用户到宿主机的普通用户。

每个进程的 namespace 信息都存储在/proc/[pid]/ns目录下：
```bash
ls -l /proc/self/ns
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 cgroup -> 'cgroup:[4026531835]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 ipc -> 'ipc:[4026531839]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 mnt -> 'mnt:[4026531840]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 net -> 'net:[4026531956]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 pid -> 'pid:[4026531836]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 pid_for_children -> 'pid:[4026531836]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 user -> 'user:[4026531837]'
# lrwxrwxrwx 1 user user 0 Jun 10 12:00 uts -> 'uts:[4026531838]'
```

### 如何创建？

1. 使用`unshare`命令创建 namespace：
```bash
# 创建新的挂载点和PID namespace，并在其中启动bash
unshare --mount --pid --fork bash

# 在新的namespace中查看PID
echo $$  # 输出通常为1，表示当前bash是新namespace中的第一个进程

# 查看当前namespace中的进程
ps aux
```

2. 使用`clone()`系统调用
```C++
#define _GNU_SOURCE
#include <sched.h>
#include <unistd.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <stdio.h>

// 子进程执行的函数
static int child_func(void *arg) {
    // 在新的UTS namespace中设置主机名
    sethostname("container", 9);
    // 输出当前进程ID和主机名
    printf("子进程PID: %d\n", getpid());
    printf("主机名: %s\n", "container");
    // 执行/bin/bash
    execlp("/bin/bash", "bash", NULL);
    return 1;
}

int main() {
    const int STACK_SIZE = 65536; // 为子进程分配栈空间
    char *stack = malloc(STACK_SIZE);
    if (!stack) {
        perror("内存分配失败");
        return 1;
    }
    // 设置栈顶（栈是向下增长的）
    char *stack_top = stack + STACK_SIZE;
    // 创建新的UTS和PID namespace，并启动子进程
    pid_t pid = clone(child_func, stack_top, 
                     CLONE_NEWUTS | CLONE_NEWPID | SIGCHLD, NULL);
    if (pid == -1) {
        perror("clone失败");
        return 1;
    }
    // 等待子进程结束
    waitpid(pid, NULL, 0);
    free(stack);
    return 0;
}
```

3. 使用`setns()`加入现有 namespace  
加入另一个进程的网络 namespace：
```C++
#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <unistd.h>
#include <stdio.h>

int main() {
    // 打开目标进程的网络namespace文件
    int fd = open("/proc/1234/ns/net", O_RDONLY);
    if (fd == -1) {
        perror("打开namespace文件失败");
        return 1;
    }
    // 加入目标namespace
    if (setns(fd, CLONE_NEWNET) == -1) {
        perror("加入namespace失败");
        return 1;
    }
    close(fd);
    // 执行需要在目标namespace中运行的命令
    execlp("ip", "ip", "addr", NULL);
    return 0;
}
```

4. 使用`nsenter`命令（简化版`setns()`）
```bash
sudo nsenter --target 1234 --net ip addr
```

