
复习：[【Linux】守护进程（ Daemon）的定义，作用，创建流程](https://blog.csdn.net/JMW1407/article/details/108412836)。

编写守护进程的一般步骤步骤：

1. 在父进程中执行`fork`并`exit`退出；
2. 在子进程中调用`setsid`函数创建新的会话；
3. 在子进程中调用`chdir`函数，让根目录`/`成为子进程的工作目录；
4. 在子进程中调用`umask`函数，设置进程的`umask`为`0`；
5. 在子进程中关闭任何不需要的文件描述符。

[Linux—umask（创建文件时的掩码）用法详解](https://blog.csdn.net/Change_Improve/article/details/106107317)。

[深刻理解——real user id, effective user id, saved user id in Linux](https://blog.csdn.net/fmeng23/article/details/23115989)。  
[Linux进程权限的研究——real user id, effective user id, saved set-user-id](https://blog.csdn.net/ybxuwei/article/details/23563423)。

> 调用setsid的进程不是一个进程组的组长，此函数创建一个新的会话期。