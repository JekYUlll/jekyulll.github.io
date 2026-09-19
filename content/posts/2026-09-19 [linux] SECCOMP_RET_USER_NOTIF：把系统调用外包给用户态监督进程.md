+++
date = '2026-09-19T10:12:39+08:00'
draft = false
title = 'SECCOMP_RET_USER_NOTIF：把系统调用外包给用户态监督进程'
author = 'JekYUlll'
lastmod = '2026-09-19T10:12:39+08:00'
tags = ['seccomp', 'syscall', 'container', 'sandbox']
categories = ['linux']
+++

容器里的进程想创建设备节点，内核只检查初始 user namespace 的权限，回一个 EPERM；想挂载一块磁盘，同样没门。这些请求经常是安全的，容器管理器也乐于放行，问题是它没有观察点：进程发起的系统调用要么通过、要么失败，运行时全程不知情。

seccomp 过滤器能看到所有系统调用，但它落在内核的 BPF 虚拟机里，不能解引用指针，看不到路径字符串，也查不了文件状态。"这个调用号禁止出现"这类静态规则是它的主场；"先读参数再决定"它做不到。内核 5.0 补上了这一块：新的返回动作 SECCOMP_RET_USER_NOTIF，命中时系统调用不执行，触发线程挂起，一条通知送到用户态监督进程（supervisor）手里。

## 一条通知的完整流程

链路只有四个动作。第一，目标进程装载过滤器时带上 SECCOMP_FILTER_FLAG_NEW_LISTENER，seccomp() 返回一个 listener fd。这个 fd 属于过滤器本身：之后 fork 出的进程同样受它管辖，通知都排到同一个 fd 上；内核文档还提到它的读写做过同步，多个读者并发取通知是安全的。

第二，listener fd 得交给 supervisor。走 UNIX socket 的 SCM_RIGHTS 是标准做法，man page 里也列了 pidfd_getfd 这条捷径。crun 把前者固化成约定：容器配置里写注解 `run.oci.seccomp.receiver=PATH`（或环境变量 `RUN_OCI_SECCOMP_RECEIVER`），运行时就把 listener fd 发到指定 socket 上，conmon 或外部程序接住它。

第三，supervisor 用 SECCOMP_IOCTL_NOTIF_RECV 阻塞读一条 struct seccomp_notif：系统调用号、六个参数寄存器、目标线程 pid，以及这场通知的 cookie（id 字段）。指针参数内核不会替你读，supervisor 要自己打开 `/proc/<pid>/mem`，按参数里的地址 pread 出来。

第四，处理完写 struct seccomp_notif_resp 回应答，三种走向。填 val 和 error 就是伪造返回值，内核不执行这次调用；置上 SECCOMP_USER_NOTIF_FLAG_CONTINUE，表示看过了，交回内核照常执行；需要产生文件描述符的调用（open、socket、accept），用 SECCOMP_IOCTL_NOTIF_ADDFD 把 supervisor 的 fd 直接装进目标的 fd 表。

时间线：通知机制进内核是 5.0（2019），CONTINUE 是 5.5，ADDFD 是 5.9，让"注入 fd 和发应答"一步原子完成的 SECCOMP_ADDFD_FLAG_SEND 是 5.14，5.19 又加了 WAIT_KILLABLE_RECV，让目标在被通知期间忽略非致命信号，直到 supervisor 发出应答。libseccomp 从 2.5.0 起提供 `seccomp_notify_alloc/receive/respond` 这组封装。

## 谁在用：从 LXD 到 crun

最早规模化使用它的是 LXD 团队，2017 年提出想法，Tycho Andersen 完成内核侧实现，Christian Brauner 补上 CONTINUE，Sargun Dhillon 加上 ADDFD。LXD 拿它模拟两类系统调用：mknod 创建设备节点、mount 挂载文件系统。容器里发起 mknod，判断和创建都在 supervisor 侧完成；mount 请求则可以由管理员指定一个 FUSE 程序代劳，用非特权的方式把文件系统带进容器。

crun（Podman 使用的 OCI 运行时）把处置逻辑做成了插件：seccomp_notify.c 只负责收通知，交给插件决定。附带的 mknod 插件展示了典型写法：进入目标的 user namespace 和 mount namespace，在目标路径创建占位文件，再把宿主的真实设备 bind mount 上去。可注入的设备写死五个：/dev/null、/dev/zero、/dev/full、/dev/random、/dev/urandom，其余一律回 EPERM。

2024 年的 bypass4netns 是另一个方向。rootless 容器网络要经过 slirp4netns 之类的用户态中继，吞吐上不去；它拦截容器的 connect/bind 等系统调用，用 ADDFD 把宿主网络命名空间里建好的 socket 换进容器进程的 fd 表。论文报告吞吐比原始 rootless 方案快 30 倍以上，而且不依赖 LD_PRELOAD，静态链接的程序也能用。

## 写一个最小 supervisor

文档看一圈不如自己跑一遍。下面的程序 fork 出目标进程；目标装好过滤器后把 listener fd 的编号通过 socketpair 告诉父进程，父进程用 pidfd_getfd 取一份副本，然后处理全部通知。目标进程做五次尝试，supervisor 给出四种处置：替它创建目录、CONTINUE 放行、伪造 EPERM、注入 fd。

```c
/* notify_demo.c — seccomp 用户态通知最小示例（x86-64，内核 >= 5.14）
 *
 * 目标进程做五次"体检"，每次命运都由 supervisor 决定：
 *   mkdir("/tmp/snp-demo/emulated")  由 supervisor 替它创建（mode 0700）
 *   mkdir("./snp-rel")               回复 CONTINUE，内核正常执行
 *   mkdir("/snp-denied")             伪造 EPERM
 *   open("/snp-magic/hello")         注入 supervisor 的 fd 作为返回值
 *   open("/snp-magic/denied")        伪造 EPERM
 *
 * 编译：gcc -Wall -Wextra -O2 -o notify_demo notify_demo.c
 * 运行：./notify_demo
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef SECCOMP_USER_NOTIF_FLAG_CONTINUE
#define SECCOMP_USER_NOTIF_FLAG_CONTINUE (1UL << 0)
#endif
#ifndef SECCOMP_ADDFD_FLAG_SEND
#define SECCOMP_ADDFD_FLAG_SEND (1UL << 1)
#endif

#define PAYLOAD_PATH "/tmp/snp-demo-payload.txt"

static int seccomp(unsigned int op, unsigned int flags, void *args)
{
	return syscall(SYS_seccomp, op, flags, args);
}

/* 目标侧：装过滤器。mkdir/mkdirat、open/openat 触发通知，其余放行。 */
static int install_notify_filter(void)
{
	struct sock_filter filter[] = {
		/* 先校验 audit arch，再看系统调用号（不同 ABI 编号会撞车） */
		BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
			 offsetof(struct seccomp_data, arch)),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),

		BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
			 offsetof(struct seccomp_data, nr)),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_mkdirat, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_mkdir, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_openat, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_open, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),

		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
	};
	struct sock_fprog prog = {
		.len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
		.filter = filter,
	};

	if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == -1) {
		perror("prctl(PR_SET_NO_NEW_PRIVS)");
		exit(1);
	}
	return seccomp(SECCOMP_SET_MODE_FILTER,
		       SECCOMP_FILTER_FLAG_NEW_LISTENER, &prog);
}

/* 目标进程：报告 listener fd 的编号，等父进程取走，然后干活。 */
static void target(int control)
{
	char buf[128];
	ssize_t n;
	int fd, notify_fd;

	notify_fd = install_notify_filter();
	if (notify_fd == -1) {
		perror("seccomp");
		exit(1);
	}
	if (write(control, &notify_fd, sizeof(notify_fd)) !=
	    (ssize_t)sizeof(notify_fd))
		exit(1);
	n = read(control, buf, 1); /* 等父进程确认取走 */
	(void)n;
	close(control);
	close(notify_fd);

	if (chdir("/tmp/snp-demo") == -1)
		exit(1);

	if (mkdir("/tmp/snp-demo/emulated", 0777) == 0)
		printf("T: mkdir(emulated) 成功\n");
	else
		perror("T: mkdir(emulated)");
	if (mkdir("./snp-rel", 0755) == 0)
		printf("T: mkdir(./snp-rel) 成功\n");
	else
		perror("T: mkdir(./snp-rel)");
	if (mkdir("/snp-denied", 0755) == -1)
		printf("T: mkdir(/snp-denied) 被拒绝：%s\n", strerror(errno));

	{
		struct stat st;

		if (stat("/tmp/snp-demo/emulated", &st) == 0)
			printf("T: emulated 的 mode = 0%o（请求的是 0777）\n",
			       st.st_mode & 0777);
	}

	printf("T: 打开 /snp-magic/hello（磁盘上没有这个路径）\n");
	fd = open("/snp-magic/hello", O_RDONLY);
	if (fd >= 0) {
		n = read(fd, buf, sizeof(buf) - 1);
		if (n > 0) {
			buf[n] = '\0';
			printf("T: 读到: %s", buf);
		}
		close(fd);
	} else {
		perror("T: open(/snp-magic/hello)");
	}
	if (open("/snp-magic/denied", O_RDONLY) == -1)
		printf("T: open(/snp-magic/denied) 被拒绝：%s\n",
		       strerror(errno));

	printf("T: 结束\n");
	exit(0);
}

/* 读通知 id 是否仍然有效：目标还在、系统调用还挂在那里 */
static bool id_valid(int notify_fd, uint64_t id)
{
	return ioctl(notify_fd, SECCOMP_IOCTL_NOTIF_ID_VALID, &id) == 0;
}

/* 从目标内存里读指针参数。读前读后都验一次 id，内容按不可信输入处理。 */
static bool read_target_path(int notify_fd, const struct seccomp_notif *req,
			     int arg, char *path, size_t size)
{
	char mem_path[64];
	int mfd;
	ssize_t n;

	snprintf(mem_path, sizeof(mem_path), "/proc/%d/mem", req->pid);
	mfd = open(mem_path, O_RDONLY | O_CLOEXEC);
	if (mfd == -1)
		return false;
	if (!id_valid(notify_fd, req->id)) {
		close(mfd);
		return false;
	}
	n = pread(mfd, path, size - 1, (off_t)req->data.args[arg]);
	close(mfd);
	if (n <= 0)
		return false;
	path[n] = '\0';
	if (!id_valid(notify_fd, req->id))
		return false; /* 目标可能已被信号打断，读到的内存不再可信 */
	return strnlen(path, (size_t)n) < (size_t)n;
}

/* supervisor 主循环：一条通知一条通知地处理 */
static void handle(int notify_fd)
{
	for (;;) {
		struct seccomp_notif req;
		struct seccomp_notif_resp resp;
		char path[PATH_MAX];
		int mkdir_call, arg, tfd;

		memset(&req, 0, sizeof(req));
		if (ioctl(notify_fd, SECCOMP_IOCTL_NOTIF_RECV, &req) == -1) {
			if (errno == EINTR)
				continue;
			perror("S: NOTIF_RECV");
			break;
		}

		mkdir_call = (req.data.nr == __NR_mkdir ||
			      req.data.nr == __NR_mkdirat);
		arg = (req.data.nr == __NR_openat ||
		       req.data.nr == __NR_mkdirat) ? 1 : 0;
		if (!read_target_path(notify_fd, &req, arg, path,
				      sizeof(path))) {
			memset(&resp, 0, sizeof(resp));
			resp.id = req.id;
			resp.error = -EINVAL;
			ioctl(notify_fd, SECCOMP_IOCTL_NOTIF_SEND, &resp);
			continue;
		}

		memset(&resp, 0, sizeof(resp));
		resp.id = req.id;

		if (mkdir_call && strncmp(path, "/tmp/snp-demo/", 14) == 0) {
			printf("S: 收到 mkdir(\"%s\")，替目标创建\n", path);
			if (mkdir(path, 0700) == 0) {
				printf("S: 创建完成，mode 用 supervisor 的 0700\n");
			} else {
				resp.error = -errno; /* 失败就把 errno 原样带回 */
			}
		} else if (strcmp(path, "/snp-magic/hello") == 0) {
			struct seccomp_notif_addfd addfd = { 0 };
			int src = open(PAYLOAD_PATH, O_RDONLY);

			printf("S: \"%s\" 不存在，把 supervisor 的 fd 塞给目标\n",
			       path);
			if (src == -1) {
				resp.error = -errno;
				goto send;
			}
			addfd.id = req.id;
			addfd.flags = SECCOMP_ADDFD_FLAG_SEND; /* 注入+应答原子 */
			addfd.srcfd = (uint32_t)src;
			addfd.newfd = 0;
			addfd.newfd_flags = O_CLOEXEC;
			tfd = ioctl(notify_fd, SECCOMP_IOCTL_NOTIF_ADDFD,
				    &addfd);
			if (tfd < 0) {
				resp.error = -errno;
				goto send;
			}
			printf("S: 注入完成，目标侧 fd 编号 = %d\n", tfd);
			close(src);
			continue; /* FLAG_SEND 已送出应答，不必再 SEND */
		} else if (strncmp(path, "/tmp/", 5) == 0 ||
			   strncmp(path, "./", 2) == 0) {
			printf("S: \"%s\" 放行（CONTINUE）\n", path);
			resp.flags = SECCOMP_USER_NOTIF_FLAG_CONTINUE;
		} else {
			printf("S: 拒绝 \"%s\"（EPERM）\n", path);
			resp.error = -EPERM;
		}

send:
		if (ioctl(notify_fd, SECCOMP_IOCTL_NOTIF_SEND, &resp) == -1 &&
		    errno != ENOENT)
			perror("S: NOTIF_SEND");
	}
}

/* 目标退出后 RECV 不会自己返回（内核不报错，只静静阻塞），
 * 所以装个 SIGCHLD 处理函数把 supervisor 从 ioctl 里打断。 */
static void on_sigchld(int sig)
{
	const char msg[] = "S: 目标已退出，收工\n";
	ssize_t wr;

	(void)sig;
	wr = write(STDOUT_FILENO, msg, sizeof(msg) - 1);
	(void)wr;
	_exit(0);
}

int main(void)
{
	struct sigaction sa;
	int control[2];
	pid_t child;
	int fd_num = -1, pidfd, notify_fd;
	char ack = 'x';
	ssize_t n;

	setvbuf(stdout, NULL, _IONBF, 0);

	/* 准备可重复运行的环境 */
	rmdir("/tmp/snp-demo/emulated");
	rmdir("/tmp/snp-demo/snp-rel");
	if (mkdir("/tmp/snp-demo", 0755) == -1 && errno != EEXIST) {
		perror("mkdir(/tmp/snp-demo)");
		return 1;
	}
	{
		int p = open(PAYLOAD_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
		const char content[] = "message from supervisor, via injected fd\n";

		if (p == -1)
			return 1;
		n = write(p, content, sizeof(content) - 1);
		(void)n;
		close(p);
	}

	if (socketpair(AF_UNIX, SOCK_STREAM, 0, control) == -1)
		return 1;
	child = fork();
	if (child == -1)
		return 1;
	if (child == 0)
		target(control[0]);
	close(control[0]);

	/* 拿到目标手里的 listener fd 编号，用 pidfd_getfd 复制过来 */
	if (read(control[1], &fd_num, sizeof(fd_num)) !=
	    (ssize_t)sizeof(fd_num))
		return 1;
	pidfd = syscall(SYS_pidfd_open, child, 0);
	if (pidfd < 0) {
		perror("pidfd_open");
		return 1;
	}
	notify_fd = syscall(SYS_pidfd_getfd, pidfd, fd_num, 0);
	close(pidfd);
	if (notify_fd < 0) {
		perror("pidfd_getfd");
		return 1;
	}
	n = write(control[1], &ack, 1); /* 告诉目标：取到了 */
	(void)n;
	close(control[1]);

	sa.sa_handler = on_sigchld;
	sa.sa_flags = 0;
	sigemptyset(&sa.sa_mask);
	sigaction(SIGCHLD, &sa, NULL);

	printf("S: 已拿到 listener fd，开始处理通知\n");
	handle(notify_fd);

	wait(NULL);
	return 0;
}
```

编译运行（x86-64，内核 5.14 以上）：

```bash
gcc -Wall -Wextra -O2 -o notify_demo notify_demo.c
./notify_demo
```

6.8 内核上的真实输出（T:/S: 分别来自目标和 supervisor，交错顺序视调度而定）：

```
S: 已拿到 listener fd，开始处理通知
S: 收到 mkdir("/tmp/snp-demo/emulated")，替目标创建
S: 创建完成，mode 用 supervisor 的 0700
T: mkdir(emulated) 成功
S: "./snp-rel" 放行（CONTINUE）
T: mkdir(./snp-rel) 成功
S: 拒绝 "/snp-denied"（EPERM）
T: mkdir(/snp-denied) 被拒绝：Operation not permitted
T: emulated 的 mode = 0700（请求的是 0777）
T: 打开 /snp-magic/hello（磁盘上没有这个路径）
S: "/snp-magic/hello" 不存在，把 supervisor 的 fd 塞给目标
S: 注入完成，目标侧 fd 编号 = 3
T: 读到: message from supervisor, via injected fd
S: 拒绝 "/snp-magic/denied"（EPERM）
T: open(/snp-magic/denied) 被拒绝：Operation not permitted
T: 结束
S: 目标已退出，收工
```

对着输出看三个细节。目标请求的 mode 是 0777，最后 stat 出来的是 0700：目录不是它建的，supervisor 按自己的意愿用 0700 创建。"/snp-magic/hello" 在磁盘上不存在，目标却拿到了一个可读的 fd，内容来自 supervisor 打开的文件，这也是 bypass4netns 换 socket 用的同一招。CONTINUE 那条最不起眼，却是误拦的解药：过滤器按系统调用号粗筛，必然会拦到一些无需代劳的调用，CONTINUE 把它们交回内核。

## 细节：id 校验、信号打断与 CONTINUE 的边界

第一件容易忽略的是竞态。目标发起系统调用后挂在内核里可中断睡眠：信号一来，处理函数跑完，这次调用可能就被放弃了。supervisor 拿着刚才读到的 pid 和地址继续操作，可能踩进别的进程的内存，pid 还有被复用的风险。

防护手段是 SECCOMP_IOCTL_NOTIF_ID_VALID：读目标内存前后各校验一次 cookie，确认目标还挂在这条调用上。man page 里有两句话最好记牢：读内存必须夹在两次 id 校验之间；而 supervisor 往目标内存里写，"永远不能被当作安全操作"。demo 里的 read_target_path() 就是这个模式，读出来的字节按不可信输入处理，还要自己确认字符串完整。

第二件是重复通知。目标如果有 SA_RESTART 的信号处理函数，被打断的调用会被内核重启，supervisor 会为同一次调用收到第二条通知，第一条的应答以 ENOENT 失败。ENOENT 不代表出错，它属于正常流程。还有一个边角：supervisor 自己退出后，目标再触发通知模式的系统调用会拿到 ENOSYS，而不是一直挂着。

CONTINUE 单独说。它看起来像"放行开关"，但不能用来拼安全策略：从 supervisor 决定放行到内核真正执行之间有时间窗，目标可以重写参数指向的内容，检查时看到的是 /etc/passwd，执行时未必还是。man page 的措辞更重：这个机制"绝对不能用来实现安全策略"；它只适合"特权进程替低权限目标消除内核误伤"这个定位。

## 反向用法：不用 ptrace 的进程注入

同一套机制反过来用，就是一个新的攻击面。2025 年 12 月 Outflank 公开了一个 PoC：注入器扮演父进程，子进程装好针对 openat 的过滤器后 execve 目标程序；目标动态链接器加载每个共享库的 openat 都会被父进程截住，父进程用 ADDFD 把 memfd 里的恶意 .so 冒充成它要找的库。库里的 IFUNC resolver 在符号解析前执行，和 2024 年 XZ 后门是同一类手法。

它不需要 LD_PRELOAD，不碰 procfs，也不用 process_vm_writev，而且无视 ptrace_scope：内核视角里这就是"监督进程替子进程模拟系统调用"的标准场景。作者注明了边界：注入代码与目标同 uid、同命名空间、同 LSM 标签，不构成提权；目标必须是动态链接的。方向性（父对子、特权对非特权）是这个接口的设计前提，但用法一旦被接到非预期的方向上，它照样成立。

## 参考

- man page seccomp_unotify(2): https://man7.org/linux/man-pages/man2/seccomp_unotify.2.html
- 内核文档 Seccomp BPF: https://docs.kernel.org/userspace-api/seccomp_filter.html
- Christian Brauner: Seccomp Notify - New Frontiers in Unprivileged Container Development: https://people.kernel.org/brauner/the-seccomp-notifier-new-frontiers-in-unprivileged-container-development
- Giuseppe Scrivano: Playing with seccomp notifications in the OCI runtime: https://scrivano.org/posts/2020-08-10-seccomp-notifications/
- crun seccomp_notify.c: https://github.com/containers/crun/blob/main/src/libcrun/seccomp_notify.c
- crun mknod 插件（Rust）: https://github.com/containers/crun/blob/main/contrib/seccomp-notify-plugin-rust/src/mknod.rs
- bypass4netns: Accelerating TCP/IP Communications in Rootless Containers: https://arxiv.org/abs/2402.00365
- Outflank: Linux Process Injection via Seccomp Notify: https://www.outflank.nl/blog/2025/12/09/seccomp-notify-injection/（镜像 https://kyleavery.com/posts/seccomp-notify-injection/）
- libseccomp v2.5.0 发布说明: https://github.com/seccomp/libseccomp/releases/tag/v2.5.0
