+++
date = '2025-04-29T21:05:47+08:00'
draft = false
title = '[linux] TODO 守护进程，setsid，Linux三个id，权限'
author = 'JekYUlll'
lastmod = '2025-04-29T21:05:47+08:00'
tags = ['opengl', 'graphics', 'game']
categories = ['game']
+++


复习：[【Linux】守护进程（ Daemon）的定义，作用，创建流程](https://blog.csdn.net/JMW1407/article/details/108412836)。

[Linux 命令之 `locale` -- 设置和显示程序运行的语言环境](https://blog.csdn.net/liaowenxiong/article/details/116401524)。  
> 使用 `locale` 命令来设置和显示程序运行的语言环境，`locale` 会根据计算机用户所使用的语言，所在国家或者地区，以及当地的文化传统定义一个软件运行时的语言环境。  
> `locale` 由ANSI C提供支持。`locale` 的命名规则为`<语言>_<地区>.<字符集编码>`。

[深刻理解——real user id, effective user id, saved user id in Linux](https://blog.csdn.net/fmeng23/article/details/23115989)。  
[Linux进程权限的研究——real user id, effective user id, saved set-user-id](https://blog.csdn.net/ybxuwei/article/details/23563423)。

> 调用setsid的进程不是一个进程组的组长，此函数创建一个新的会话期。

```bash
echo $UID
```

