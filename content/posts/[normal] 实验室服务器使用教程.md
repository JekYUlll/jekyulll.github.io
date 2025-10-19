+++
date = '2025-10-18T21:05:47+08:00'
draft = false
title = '实验室服务器使用教程'
author = '张祝玙'
lastmod = '2025-10-18T21:05:47+08:00'
tags = ['linux', 'os']
categories = ['linux']
+++

## 一、前言

<span style="color: red;">**校外访问请通过校园 VPN**</span>

实验室近期新购置了一台深度学习服务器，供成员进行模型训练、仿真和计算任务。为了帮助大家快速上手、规范使用、避免资源浪费或系统损坏，特编写此教程。

---

## 二、什么是“深度学习服务器”

深度学习服务器是一台高性能计算机，通常配备多块 GPU（图形处理器）、大容量显存、内存和高速存储。
通常安装Linux系统 ，一般**不直接接显示器**，而是通过网络远程访问（SSH 或远程桌面）。

### 主要用途

* 深度学习训练（如 PyTorch、TensorFlow）；
* 大规模有限元仿真（Abaqus、ANSYS、COMSOL）；
* 数据分析、图像识别、模型优化等。

### 系统与软件环境

* 操作系统：Ubuntu 22.04 LTS
* 已安装软件：Anaconda、Python、JupyterLab
  
为避免cuda版本导致的问题，此处仅保证显卡驱动版本，cuda请根据需求自行安装。

---

## 三、账户与登录信息

### 账户申请

新用户请联系实验室管理员开通。

1. **账户命名规则**
   实验室统一以*姓名全拼*作为用户名，例如：

   * 张三 → *zhangsan*
   * 李四 → *lisi*

2. **初始密码**
   所有新账户的初始密码为：`123456`


### 手动修改密码（重要）

1. 登录服务器后（见后续教程），在终端输入：

   ```bash
   passwd
   ```

2. 系统会提示：

   ```bash
   Changing password for user zhangsan.
   (current) UNIX password:
   ```

   输入当前密码（即 `123456`）。

3. 接着输入新密码两次（系统不会显示输入内容，但实际上输进去了）：

   ```bash
   Enter new UNIX password:
   Retype new UNIX password:
   ```

4. 如果两次输入一致，会显示：

   ```bash
   password updated successfully
   ```

   表示修改成功。

---

## 四、连接方式

### （一）Windows 用户

此处推荐三个用于连接的终端工具（就是黑窗口，理论上使用cmd即可，但不够易用）：  
- [MobaXterm](https://mobaxterm.mobatek.net/)
- [XShell](https://www.netsarang.com/en/xshell/)
- [Terminus](https://termius.com/)

此处仅演示MobaXterm。

打开软件，点击左上角的Session

![moba1](images/moba1.png)

再点SSH，在下面的Remote host 填服务器地址，Specify username填你的账号名。

![moba2](images/moba2.png)

![moba3](images/moba3.png)

左边是服务器文件路径，可以按照权限操纵文件，新建个文件夹放代码之类的。

右边就是终端，可以对linux系统进行各种命令行操作。

### 用 VS Code 连接（主要使用方式）

Pycharm（以及其他Jetbrains的IDE）远程连接功能远不如VS Code，因此此处仅演示VS Code，也仅推荐使用VS Code。

1. 安装remote相关插件
   
![vscode1](images/vscode1.png)

2. 在侧边栏打开插件

![vscode2](images/vscode2.png)

3. 新建ssh连接

![vscode3](images/vscode3.png)

4. 选择路径

5. 在 VS Code 里使用终端：点击 New Terminal

![vscode5](images/vscode5.png)

---

### （二）macOS 用户

此处推荐[Terminus](https://termius.com/)，iPad都能用。

或者直接用mac自带终端也行：

**SSH连接**
打开“终端”：

```bash
ssh username@服务器IP
```

**文件传输**
可使用命令行：

```bash
scp localfile username@服务器IP:/home/username/
```

---

### （三）Linux 用户

推荐[WindTerm](https://github.com/kingToolbox/WindTerm)，本人用得最多的一个。

---

## 五、Anaconda 与 Python 环境管理

### 1. Anaconda 简介

Anaconda 是一个 Python 环境与包管理工具，可方便地创建独立环境、安装依赖，避免不同项目间的冲突。（miniconda是其精简版）  

此处管理员已预装系统级miniconda。

### 2. 常用命令

| 操作    | 命令示例                                 |
| ----- | ------------------------------------ |
| 创建新环境 | `conda create -n myenv python=3.10`  |
| 激活环境  | `conda activate myenv`               |
| 安装包   | `conda install numpy pandas pytorch` |
| 查看环境  | `conda env list`                     |
| 删除环境  | `conda remove -n myenv --all`        |

### 3. 使用 JupyterLab（可选）

在服务器上启动：

```bash
jupyter lab --no-browser --port=8888
```

然后在本地终端执行：

```bash
ssh -L 8888:localhost:8888 username@服务器IP
```

本地浏览器访问：

```txt
http://localhost:8888
```

---

## 六、远程桌面与图形化软件

### 1. 适用场景

用于运行图形化仿真软件（Abaqus、ANSYS、COMSOL、Matlab 等）。  
（如需使用请联系我，因为我也没配过这玩意）

### 2. 配置方式（由管理员完成）

* 启用 `xrdp` 服务；
* 为每个用户创建独立桌面会话。

### 3. 用户连接方式

| 系统      | 工具                       | 登录方式          |
| ------- | ------------------------ | ------------- |
| Windows | 远程桌面连接 (mstsc)           | 输入 IP、用户名、密码  |
| macOS   | Microsoft Remote Desktop | App Store 可下载 |
| Linux   | Remmina                  | 支持 RDP 协议     |

### 4. 注意事项

* 远程桌面会占用 GPU 和内存资源，不建议长时间挂起。
* 深度学习训练任务应通过命令行执行。

---

## 七、文件管理与备份

### 1. 目录说明

| 路径             | 用途             |
| -------------- | -------------- |
| `/home/username` | 用户个人目录         |
| `/data`          | 公共数据集或模型       |
| `/workspace`     | 项目工作区（可按课题分目录） |

---

## 八、服务器使用规范

1. 不要在系统目录（`/`、`/root`）下操作。
2. 运行长时间任务时请使用：

   ```bash
   nohup python train.py > log.txt 2>&1 &
   ```

   或在 tmux 会话中执行。
3. 未经沟通严禁重启服务器（如果你有权限的话）。
3. 任务结束后释放显存和进程（输入`ps -aux | grep 你的进程名`找你的进程，找到`pid`之后`kill -9 你的pid`）。
4. 请勿安装系统级软件，如需`sudo`权限请在群里询问。

---

## 九、常见问题（FAQ）

| 问题           | 解决方法                       |
| ------------ | -------------------------- |
| SSH 连不上      | 检查网络/VPN，确认端口22是否开放        |
| GPU 已占满      | 使用 `nvidia-smi` 查看使用者，协商使用 |
| Conda 包冲突    | 新建独立环境                     |
| Jupyter 无法访问 | 检查端口转发是否正确                 |
| 远程桌面卡顿       | 降低分辨率或关闭3D加速               |

---

## 十、附录

### 常用命令速查

```bash
nvidia-smi          # 查看GPU
pwd                 # 显示当前路径
ls -lh              # 列出文件
scp localfile user@ip:/home/user/   # 上传文件
conda create -n myenv python=3.10   # 创建新环境
```

### 管理员联系方式

![微信](images/wechat.jpg)

### 推荐学习资料

* Linux命令快速入门：[https://wangchujiang.com/linux-command/](https://wangchujiang.com/linux-command/)
* Anaconda官方文档：[https://docs.anaconda.com/](https://docs.anaconda.com/)
* PyTorch教程：[https://pytorch.org/tutorials/](https://pytorch.org/tutorials/)

---

### 参考文档

[实验室GPU管理神器Determined - 吕昱峰的文章 - 知乎](https://zhuanlan.zhihu.com/p/422462131)。

[实验室服务器管理经验 - PurRigiN的文章 - 知乎](https://zhuanlan.zhihu.com/p/1908296804832879701)。

[手把手教你如何连上实验室的服务器](https://blog.csdn.net/qq_38356397/article/details/103166234)。

[Mac下使用SSH连接远程Linux服务器](https://blog.csdn.net/qq_44773719/article/details/104352965)。

[实验室服务器使用教程（用户篇）](https://ajohn.top/article/bes1sa4i/)。

[yurizzzzz/TJU-ServerDoc 天津大学实验室服务器使用和管理](https://github.com/yurizzzzz/TJU-ServerDoc)。