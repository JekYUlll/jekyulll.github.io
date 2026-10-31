+++
date = '2026-10-31T19:16:24+08:00'
draft = false
title = 'rm -rf 之后，日志里到底还剩什么'
author = 'JekYUlll'
lastmod = '2026-10-31T19:16:24+08:00'
tags = ['auditd', 'filesystem', 'forensics', 'ext4']
categories = ['linux']
+++

十月的最后一天，实验室那台 16TB 的共享盘上有人跑了一条 `rm -rf`，删掉了自己整个项目目录。几小时后我带着 root 权限坐在那块盘前面，有 1GB 的 journal，有一整套恢复工具，最后花三个小时证明了它们全都救不回来。

那是一次抢救。这篇写的是预防：下次再出这种事，我希望"谁删的"在一分钟里出现。

## 在 ext4 上，删掉就是删掉

`unlink` 做三件事：把目录项标成空、把 inode 的 extent 树清空、把 dtime 写成当下时间。数据块回到空闲池，下一次写入就可能盖掉它们。

journal 是唯一的窗口，但它只有 1GB，而服务器每分钟都在往里写事务。我把 journal dump 出来搜过，原目录 inode 只剩一条墓碑：

```
Size 0 / Links 0 / dtime 2026-10-31 11:23:01
```

删除前的副本已经被后续事务冲掉了。ext4 也没有快照，所以这一刻之前没做的准备，事后补不回来。

只剩两条路：让它删不掉，或者让删除留下记录。

## chattr +a 比想象中好用

在一个目录上挂 append-only 属性：

```
$ chattr +a proj/
$ lsattr -d proj/
-----a--------e------- proj

$ rm -f proj/model.pt
rm: 无法删除 'proj/model.pt': 不允许的操作
$ rmdir proj
rmdir: 删除 'proj' 失败: 不允许的操作
$ echo x > proj/new.txt           # 照常成功
```

只能加，不能删、不能改名、不能删目录本身。对"只在里面写 checkpoint 和结果"的实验目录，代价几乎为零。

两个限制得说清楚：root 可以 `chattr -a` 撤掉，所以它防手滑不防人；另外编辑器保存文件常用 rename 覆盖，在 +a 目录里会失败，挂之前先拿你的工具链试一遍。

## auditd 能记到什么

大部分人的第一反应是 auditd，规则一行：

```
auditctl -w /data/liwenxi -p wa -k liwenxi
```

`-w` 挂目录是递归的，[audit.rules(7)](https://man7.org/linux/man-pages/man7/audit.rules.7.html) 的原话是"递归到目录树底部，遇到挂载点停止"。子目录里发生的事照样能抓到，这点我实测确认过。

一次删除会生成四到五条记录：

```
type=SYSCALL   msg=audit(...): syscall=263 comm="rm" exe="/usr/bin/rm" key="lab"
type=CWD       msg=audit(...): cwd="/tmp/dlw/lab/data/liwenxi"
type=PATH      msg=audit(...): item=0 name="/tmp/dlw/lab/data/liwenxi" nametype=PARENT
type=PATH      msg=audit(...): item=1 name="f0154.py" nametype=DELETE
type=PROCTITLE msg=audit(...): proctitle=726D002D726600...
```

真正值钱的是 `auid`。它在登录时写入，`su`、`sudo` 之后都不变，所以能追到最初那个登录用户，而不是只看到 `uid=0`。再配 `auditctl --loginuid-immutable`，这个字段就改不动了。

## 三万次删除，只记下一千三百条

问题在量。我造了三万个文件跑 `rm -rf`，测了几组。

| 场景 | 耗时 | 记下的 DELETE | 内核 lost |
|---|---|---|---|
| 不加规则 | 0.43s | 无 | 无 |
| watch + backlog 8192 + 不等待 | 0.70s | 1,324 | +44,976 |
| watch + backlog 65536 | 0.77s | 30,000 | 0 |

加一个 watch，删除慢了 63%。

第二行才是要命的。backlog 是内核里等着 auditd 取走的缓冲区数量，满了有两种行为：丢（`backlog_wait_time=0`），或者把进程挂住等（默认 60 秒）。丢的时候只有 `lost` 计数器加一，日志本身一声不吭。

三万次删除，记下一千三百二十四条。取证记录缺了 96%，而且它不会告诉你缺了。

第三行把 backlog 提到 65536 就不丢了，代价是 34MB 日志，每个被删文件约 1.1KB。

## 默认只留四十兆

auditd 出厂配置是 `max_log_file = 8`（MB）、`num_logs = 5`、`max_log_file_action = ROTATE`，总共 40MB。

一次三万文件的 `rm -rf` 就能把它冲干净。我实测时看着 `audit.log.1` 到 `.4` 在几秒内写满，最早那批记录直接被删掉。事后去翻，你翻到的是最后 40MB，不是全部。

打算拿 auditd 做取证，这两件事必须一起改：backlog 调大，日志要么调大要么实时转到另一台机器。

## 路径可能是假的

这条最反直觉。

```
$ rm /tmp/pt/sub/abs.txt
PATH: name="/tmp/pt/sub/abs.txt"    nametype=DELETE
PATH: item=0 name="/tmp/pt/sub/"    nametype=PARENT

$ cd /tmp/pt && rm sub/rel.txt
PATH: name="sub/rel.txt"            nametype=DELETE
PATH: item=0 name="sub/"            nametype=PARENT
```

两种都能还原，第二种拼上 cwd 就行。

但 `rm -rf` 不走这条路。coreutils 边走边 `unlinkat(dirfd, "文件名")`，dirfd 是它自己打开的目录句柄。audit 记下来是：

```
name="f0154.py"                              # 只剩文件名
item=0 name="/tmp/dlw/lab/data/liwenxi"      # 这是 cwd，不是文件真正的父目录
```

所有被删的文件都落在同一个"父目录"下，中间那层 `rgbtir_calibration_unified/code/pipeline/` 凭空消失。auditd 知道有人删了四千二百个名字，但说不出它们原来在哪。

要真实路径，必须在事件发生时把 dirfd 读成 `/proc/<pid>/fd/<n>`。SYSCALL 记录里有 `a0`（那个 fd 号），但进程一退出就查不到了。这是实时采集和事后翻日志的分水岭。

## fanotify 和 eBPF 是更合适的层

[fanotify](https://man7.org/linux/man-pages/man7/fanotify.7.html) 从 5.1 起就有 `FAN_DELETE`，挂一个 mount mark 就能拿到"某目录下有条目被删"，带 pid 和文件句柄，不用给每个目录写规则。有意思的是 `fanotify(7)` 手册页很多年一直写着"不支持 create/delete/move"，直到有人提 [bug](https://bugzilla.kernel.org/show_bug.cgi?id=209247) 才改过来。

eBPF 更直接，挂在 `tracepoint:syscalls:sys_enter_unlinkat` 上，一个事件一行，还能在内核里先聚合再吐出来，三万个删除只占一个 map 条目，不用把 34MB 写进磁盘。

两者的共同代价是要 `CAP_SYS_ADMIN`，而且持久化和轮转得自己解决。auditd 至少是发行版自带、开机自启、有现成的日志体系，这也是它一直被用的原因。

## 快照才是真解

前面所有方案都在回答"谁删的"，只有快照回答"怎么拿回来"。

ext4 没有快照。这台机器 `/data` 是整块盘直接 `mkfs.ext4`，没有 LVM，现在想加只能重建卷。如果是 btrfs 子卷或者 LVM thin pool，[snapper](http://snapper.io/) 可以每 15 分钟自动拍一张、留 24 小时，一次误删最多丢 15 分钟的改动，恢复是两条命令的事。

快照和备份不是一回事。备份防硬盘坏，快照防手。这台机器两样都没有，所以才有前面那三个小时。

## delwatch：一个 agent 写的工具

把上面这些接起来，我让 Hermes 写了个小工具。起因很具体：auditd 给的是原材料，不是答案。一次 `rm -rf` 吐出来 4.8MB 记录、4244 个事件、每个事件 5 条，你要在凌晨三点从里面读出"是谁"。

`delwatch` 一个文件、约 360 行 Python、不依赖第三方库，做三件事：按事件号把散落的记录拼回去、只留真正删掉东西的（`nametype=DELETE`）、按进程 rollup 成一行。输出是它在测试机上跑出来的原样，路径是我重建的目录结构。

```
$ delwatch import /var/log/audit/audit.log
4244 deletions ingested from 1 file(s)

$ delwatch who /tmp/dlw/lab/data/liwenxi/rgbtir_calibration_unified
2026-10-31 19:15:18  horeb  pid 3809675  rm  4244 names in 0.2s
  cmd     rm -rf rgbtir_calibration_unified
  cwd     /tmp/dlw/lab/data/liwenxi
  matched 1 by full path, 4243 while standing in a parent of the target
```

4.8MB 变成四行。`cmd` 是从 `PROCTITLE` 的十六进制解出来的，`auid` 翻成了用户名，`cwd` 说明它站在哪一层动的手。

它写得很老实。`who` 的匹配是宽的：只有 1 条记录带完整路径（被删目录本身那次 `rmdir`），另外 4243 条只能靠"当时的 cwd 是目标的父目录"来推断，所以输出里明写 `while standing in a parent of the target`，不假装那是精确命中。

它也不会替丢失遮掩。同一个工具跑那份丢事件的日志（三万个文件、backlog 320），报的是 `1735 names in 0.1s`。真实是 30000。它只知道落进日志的那部分，剩下的连存在过的痕迹都没有。所以任何拿它做的报告，第一步都该是确认 `auditctl -s` 里的 `lost` 是 0。

这东西是一次会话里写完的，中间改了三轮。第一版把 `PATH` 的 PARENT 当成真父目录，拼出来的路径全是错的；第二版按 pid 分组，但没处理跨文件轮转的事件；第三版才把置信度和 rollup 分开。要补的话，最该加的是实时模式，读 `/proc/<pid>/fd` 把 dirfd 解开，那样路径就不用猜了。
