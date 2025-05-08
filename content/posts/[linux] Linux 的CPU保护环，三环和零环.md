+++
date = '2025-03-30T04:05:47+08:00'
draft = false
title = 'Linux 的CPU保护环，三环和零环'
author = 'JekYUlll'
lastmod = '2025-03-30T04:05:47+08:00'
tags = ['linux', 'os']
categories = ['linux']
+++

Linux系统中的“三环”和“零环”概念源自CPU的**保护环**（Protection Rings）机制，是操作系统实现权限隔离和安全保护的核心设计。  

x86保护环的完整结构为四层，但实际仅Ring 0和Ring 3被广泛使用：

- **零环**（Ring 0）：
	- 又称内核态，是CPU权限最高的运行模式。操作系统内核运行于此环，可直接访问硬件资源（如CPU、内存、I/O设备），执行特权指令（如修改内存映射、中断处理等）。例如，Linux内核的进程调度、内存管理和设备驱动均在此层级运行。
	- 零环可直接控制硬件，而三环的代码若试图执行特权指令（如直接读写磁盘），CPU会触发异常（如General Protection Fault），强制终止非法操作。这种设计避免了用户程序破坏系统稳定性。
- **三环**（Ring 3）：
	- 又称用户态，是权限最低的层级。普通应用程序运行于此环，仅能通过系统调用（Syscall）请求内核服务，无法直接操作硬件。例如，用户启动的文本编辑器、浏览器等程序均受此限制。
	- 用户程序通过系统调用或硬件中断从三环切换到零环。例如，当程序调用`open()`函数打开文件时，会触发软中断（如`int 0x80`或`syscall`指令），内核接管执行文件操作，完成后返回用户态。

[CPU的运行环, 特权级与保护](https://blog.csdn.net/youyou1543724847/article/details/85048490)。  
[原文 ——CPU的运行环, 特权级与保护](https://blog.csdn.net/farmwang/article/details/50094959)。  
[Linux内核开发之hook系统调用](https://blog.csdn.net/qq_26962739/article/details/133133574)。  
[三环进入零环的细节（KiFastCallEntry函数分析）](https://www.cnblogs.com/onetrainee/p/11707130.html)。  
[系统调用之_KUSER_SHARED_DATA](https://blog.csdn.net/wxy_xx1/article/details/142953401)。