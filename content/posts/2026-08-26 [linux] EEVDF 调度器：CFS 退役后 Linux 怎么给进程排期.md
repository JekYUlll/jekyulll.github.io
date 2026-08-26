+++
date = '2026-08-26T10:03:55+08:00'
draft = false
title = 'EEVDF 调度器：CFS 退役后 Linux 怎么给进程排期'
author = 'JekYUlll'
lastmod = '2026-08-26T10:03:55+08:00'
tags = ['eevdf', 'scheduler', 'linux-kernel']
categories = ['linux']
+++

Linux 的默认 CPU 调度器在 6.6 换掉了。跑了十六年的 CFS（完全公平调度器）被 EEVDF 取代，全称 Earliest Eligible Virtual Deadline First，最早合格虚拟截止时间优先。名字绕口，但替换理由很直接：CFS 只会选虚拟运行时间最小的进程，它不知道谁急。EEVDF 在这套公平机制上加了"截止时间"概念，让等不起的任务能插队，同时不破坏按权重分 CPU 的承诺。

这篇文章用内核源码和两个能跑的小程序把 EEVDF 讲清楚，重点在 6.12 补完的两个能力：睡眠任务的 lag 衰减，和用户态自定义时间片。

## CFS 的问题：公平但不感知紧迫

CFS 的核心是每个进程一个虚拟运行时间 vruntime。进程跑得越多 vruntime 越大，调度器每次选最小的那个。nice 值决定 vruntime 的增长速率：nice 0 的权重是 1024，nice 10 只有 110，所以 nice 10 的进程虽然跑得少，vruntime 却涨得快，这就是它拿不到 CPU 的原因。

这套机制公平，但有个盲区。一个刚被网卡唤醒、需要在几毫秒内处理的进程，和一个刚从睡眠中醒来的批处理任务，在 CFS 眼里没有区别，谁的 vruntime 小谁上。内核社区为这个缺口折腾过 latency-nice 补丁，目标就是给 CFS 补上延迟感知；Peter Zijlstra 的结论是别叠了，直接换成 1995 年 Ion Stoica 和 Hussein Abdel-Wahab 论文里的 EEVDF。

## lag 和虚拟截止时间：怎么既公平又能插队

EEVDF 保留 vruntime 和权重体系，新增两个概念：lag 和虚拟截止时间。

lag 衡量进程"欠了多少 CPU"。论文定义 lag = S - s，其中 S 是理想情况下进程应得的服务时间，s 是实际拿到的。内核里换算成虚拟时间：`lag = w * (V - v)`，V 是运行队列的加权平均虚拟时间，v 是进程自己的 vruntime。lag 为正说明进程被亏待了，欠它的；为负说明它占便宜了。所有可运行进程的 lag 之和恒为 0，这是守恒的。

进程只有 lag >= 0 才有资格被选，这叫 eligible。有资格之后，比的是虚拟截止时间：

```c
/*
 * EEVDF: vd_i = ve_i + r_i / w_i
 */
se->deadline = se->vruntime + calc_delta_fair(se->slice, se);
```

这是 v6.12 `kernel/sched/fair.c` 里 `update_deadline()` 的原文。截止时间等于当前 vruntime 加上"以虚拟时间计价的时间片"。注意 `calc_delta_fair(slice, se)` 会把墙钟时间片按权重换算成虚拟时间，所以权重越大、时间片越短的进程截止时间越早，越先被选。选进程的代码在 `pick_eevdf()`，核心是沿着红黑树找"有资格且截止时间最早"的节点：

```c
/* Pick the leftmost entity if it's eligible */
if (se && entity_eligible(cfs_rq, se)) {
	best = se;
	goto found;
}

/* Heap search for the EEVD entity */
while (node) {
	struct rb_node *left = node->rb_left;

	/*
	 * Eligible entities in left subtree are always better
	 * choices, since they have earlier deadlines.
	 */
	if (left && vruntime_eligible(cfs_rq,
				__node_2_se(left)->min_vruntime)) {
		node = left;
		continue;
	}
	...
```

树节点缓存了子树的 min_vruntime，搜索时先检查左子树里有没有合格节点，剪掉不可能有候选的分支，最坏 O(log n)。

直觉理解：截止时间就是把"我接下来想要的时间片"翻译成虚拟时间后的到期点。短时间片的任务截止时间靠前，所以低延迟任务被满足；但 eligible 门槛保证了它不能无限插队，用多了 lag 变负就出局。公平和响应性在这个机制里是同一个公式的两面。

eligible 在代码里不是直接算 lag，而是比较 vruntime 和队列加权平均：`avg_vruntime() > se->vruntime` 就合格，因为 lag = w * (V - v)，V >= v 和 lag >= 0 是同一件事。lag 之和恒为零还有个推论：一个进程多拿的，必然是别人少拿的。论文里花了不少篇幅讨论进程退出时怎么把这笔 lag 遗产分给剩余进程，Linux 没有实现这套分配，进程退出时守恒就被打破了，换来的是实现简单。系统里有大量短命进程时这是可以接受的误差。

## 睡眠逃债：6.12 的延迟出队

lag 只在进程可运行时有意义，但进程睡觉时内核保留它的 lag，醒来从保留值接着算。这里有个两难：lag 留得太久，睡了一天的进程还背着昨天的债；立刻清零，进程就能在时间片末尾（lag 为负）睡一小觉逃掉惩罚。

CFS 时代的解法是 sleeper bonus 和 START_DEBIT，本质是打补丁。EEVDF 在 6.12 换了个干净的思路：延迟出队（delayed dequeue）。不合适的进程睡眠时先不出队，留在队列里标记为 sched_delayed，它的 lag 按虚拟时间的流逝慢慢衰减；lag 涨回正值才真正移出队列。结果：短睡逃不掉债（lag 衰减很少），长睡被原谅（lag 按虚拟时间衰减到 0 就出队）。正 lag 则无限期保留，直到进程再次运行。

这也解释了为什么说 6.12 才"完成"EEVDF：6.6 只是把选择算法换掉，睡眠任务的 lag 处理还是老一套。延迟出队动的是 enqueue/dequeue 骨架本身，睡眠任务不再立刻从红黑树摘掉，配合 ENQUEUE_DELAYED、DELAY_ZERO 等特性开关控制。连锁反应不少，最典型的是 PELT 负载统计：延迟出队的任务还挂在队列上，但又不占 CPU，6.12 里专门有补丁修正这种统计偏差。收益是公平性不再依赖"睡多久"这类启发式判断。

## 用户态自定义时间片：sched_setattr 的新用法

6.12 之前，普通进程没法告诉内核自己想要多长的时间片。6.12 合入的 commit `857b158dc5e8` 让 `sched_attr.sched_runtime` 对 SCHED_NORMAL 进程生效，内核会 clamp 到 0.1ms 到 100ms：

```c
} else if (fair_policy(policy)) {
	p->static_prio = NICE_TO_PRIO(attr->sched_nice);
	if (attr->sched_runtime) {
		p->se.custom_slice = 1;
		p->se.slice = clamp_t(u64, attr->sched_runtime,
				      NSEC_PER_MSEC/10,   /* HZ=1000 * 10 */
				      NSEC_PER_MSEC*100); /* HZ=100  / 10 */
	} else {
		p->se.custom_slice = 0;
		p->se.slice = sysctl_sched_base_slice;
	}
}
```

配套的 `85e511df3cec` 让短时间片任务在唤醒时可以直接抢占长时间片任务（PREEMPT_SHORT 特性），否则自定义 slice 只影响"空闲时选谁"，唤醒还得等当前任务跑完。commit 里的测试数据：500us slice 的 cyclictest 和 100ms slice 的 massive_intr 混跑，打开 PREEMPT_SHORT 后 cyclictest 的最大延迟从 25-44ms 收敛到 33-38ms 的稳定区间，累计延迟从约 300 秒降到约 248 秒；代价是长时间片任务切换次数变多、累计延迟略升。pick_eevdf() 里还有个 RUN_TO_PARITY 开关：当前进程的 vlag 恰好等于 deadline 时直接续跑，省一次不必要的切换。

另外 base slice 不是固定 750us 全局生效。默认的 SCHED_TUNABLESCALING_LOG 模式下会乘上 (1 + ilog2(ncpus))：16 核机器上就是 750us * 5 = 3.75ms。这也是为什么同样负载在不同核数的机器上，调度粒度观感不一样。

写个最小程序试试，把时间片设为 1ms：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <linux/sched.h>
#include <linux/sched/types.h>
#include <sys/syscall.h>
#include <unistd.h>

static int sched_setattr(pid_t pid, const struct sched_attr *attr,
			 unsigned int flags)
{
	return syscall(SYS_sched_setattr, pid, attr, flags);
}
static int sched_getattr(pid_t pid, struct sched_attr *attr,
			 unsigned int size, unsigned int flags)
{
	return syscall(SYS_sched_getattr, pid, attr, size, flags);
}

int main(void)
{
	struct sched_attr attr = {
		.size = sizeof attr,
		.sched_policy = SCHED_NORMAL,
		.sched_nice = 0,
		.sched_runtime = 1 * 1000 * 1000, /* 1ms 时间片 */
	};
	if (sched_setattr(0, &attr, 0) < 0) {
		perror("sched_setattr");
		return 1;
	}
	struct sched_attr got = { .size = sizeof got };
	sched_getattr(0, &got, sizeof got, 0);
	printf("sched_runtime=%llu ns\n", (unsigned long long)got.sched_runtime);
	return 0;
}
```

我在 6.8 内核上跑，输出 `sched_runtime=0 ns`，调用成功但 slice 没生效。这不奇怪，sched_runtime 对 fair 任务的支持是 6.12 才有的，6.8 的内核直接忽略了这个字段。想要看到效果得换 6.12+ 的发行版。

## 用 /proc 实测公平性

不动内核也能观察 EEVDF 干活。`/proc/<pid>/sched` 里有两个关键字段：`se.vruntime` 和 `se.sum_exec_runtime`，打印格式是"毫秒.微秒"，解析时要按这个单位算。下面这个程序把两个 busy loop 钉在同一个 CPU 上竞争，一个 nice 0 一个 nice 10，跑 5 秒后打印两个增量：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sched.h>

#define SAMPLES 5

static void pin_cpu0(void)
{
	cpu_set_t set;
	CPU_ZERO(&set);
	CPU_SET(0, &set);
	sched_setaffinity(0, sizeof set, &set);
}

static unsigned long long read_field(int pid, const char *field)
{
	char path[64], line[256];
	unsigned long long msec = 0, usec = 0;
	snprintf(path, sizeof path, "/proc/%d/sched", pid);
	FILE *f = fopen(path, "r");
	if (!f) return 0;
	while (fgets(line, sizeof line, f)) {
		if (strncmp(line, field, strlen(field)) == 0) {
			char *colon = strchr(line, ':');
			/* 打印格式是"毫秒.微秒"，转成 ns */
			if (colon && sscanf(colon + 1, "%llu.%llu", &msec, &usec) == 2)
				break;
		}
	}
	fclose(f);
	return msec * 1000000ULL + usec;
}

static void busy_loop(void)
{
	volatile unsigned long x = 0;
	for (;;) x += 1;
}

int main(void)
{
	pin_cpu0();
	pid_t p0 = fork();
	if (p0 == 0) { setpriority(PRIO_PROCESS, 0, 0); busy_loop(); }
	pid_t p10 = fork();
	if (p10 == 0) { setpriority(PRIO_PROCESS, 0, 10); busy_loop(); }

	usleep(300000);
	unsigned long long v0[SAMPLES], v10[SAMPLES], r0[SAMPLES], r10[SAMPLES];
	for (int i = 0; i < SAMPLES; i++) {
		v0[i]  = read_field(p0,  "se.vruntime");
		v10[i] = read_field(p10, "se.vruntime");
		r0[i]  = read_field(p0,  "se.sum_exec_runtime");
		r10[i] = read_field(p10, "se.sum_exec_runtime");
		sleep(1);
	}

	printf("vruntime 增量: nice0=%llu ms, nice10=%llu ms\n",
	       (v0[SAMPLES-1] - v0[0]) / 1000000,
	       (v10[SAMPLES-1] - v10[0]) / 1000000);
	printf("实际 CPU: nice0=%llu ms, nice10=%llu ms\n",
	       (r0[SAMPLES-1] - r0[0]) / 1000000,
	       (r10[SAMPLES-1] - r10[0]) / 1000000);

	kill(p0, SIGKILL); kill(p10, SIGKILL);
	waitpid(p0, NULL, 0); waitpid(p10, NULL, 0);
	return 0;
}
```

我在 6.8.0-136-generic 上的实测输出：

```text
vruntime 增量: nice0=3612 ms, nice10=3601 ms
实际 CPU: nice0=3612 ms, nice10=386 ms
```

两组数字放在一起就很有意思。nice 10 只拿到 386ms 的 CPU，占总量 9.7%，和权重比 110/(110+1024) = 9.7% 完全吻合。但它的 vruntime 增量是 3601ms，和 nice 0 几乎一样。因为 vruntime 的前进速率是实际运行时间乘 1024/权重：nice 10 跑 1ms 墙钟，vruntime 涨 9.31ms。这就是公平性的度量方式，两个进程的虚拟时钟以相同速率前进，谁欠了谁一目了然，EEVDF 只是在这个基础上加了截止时间来决定谁先跑。

## 可以马上试的三件事

1. 在 6.12+ 的机器上跑上面的 slice-test，把 sched_runtime 设成 1ms 再设成 50ms，配合 `sched_getattr` 读回确认 clamp 生效。
2. 看 `/sys/kernel/debug/sched/base_slice_ns`，这是 6.12 里 base slice 的调试接口，显示的是按核数缩放后的实际值：单核基准 750us，16 核机器上就是 3.75ms，`update_deadline()` 里每个没有自定义 slice 的进程都拿这个值当 r_i。
3. 用 `perf sched record` + `perf sched latency` 对比 EEVDF 和 SCHED_BATCH 下的调度延迟，直观感受 deadline 抢占的效果。

## 参考

- Linux 内核文档: EEVDF Scheduler: https://www.kernel.org/doc/html/next/scheduler/sched-eevdf.html
- LWN: Completing the EEVDF scheduler (2024-04): https://lwn.net/Articles/969062/
- LWN: sched: EEVDF using latency-nice (补丁系列, 2023-03): https://lwn.net/Articles/927530/
- 内核源码 v6.12: kernel/sched/fair.c (pick_eevdf / update_deadline / place_entity)
- 内核源码 v6.12: kernel/sched/debug.c (proc_sched_show_task)
- commit 857b158dc5e8: sched/eevdf: Use sched_attr::sched_runtime to set request/slice suggestion
- commit 85e511df3cec: sched/eevdf: Allow shorter slices to wakeup-preempt
- Stoica & Abdel-Wahab, Earliest Eligible Virtual Deadline First (1995): https://citeseerx.ist.psu.edu/document?repid=rep1&type=pdf&doi=805acf7726282721504c8f00575d91ebfd750564
