
##### [图解Linux进程优先级](https://mp.weixin.qq.com/s?__biz=MzkyNDIyNzU4Mg==&mid=2247484435&idx=1&sn=b6f30489cf388f1024d6883abb8237c8&chksm=c1d84682f6afcf94c1af14678d5401d558d2b728f0e6c853dbe4efe02c12dfa147369ceee13f#rd)

*实时优先级*用于实时应用程序，如硬实时任务和实时控制系统，而*普通优先级*用于非实时应用程序。

- **实时进程**：动态优先级为0-99的进程，采用*实时调度算法*调度。
- **普通进程**：动态优先级为100-139的进程，采用*完全公平调度算法*调度。

[Linux进程调度之完全公平调度（压箱底的干货分享）](https://mp.weixin.qq.com/s?__biz=MzkyNDIyNzU4Mg==&mid=2247484458&idx=1&sn=e4e64c006d4d822c6e7c184ab50540c1&chksm=c1d846bbf6afcfad20af0a7132eca1e3fd3c765ea5d4ee4134985b2b03f2461c207239fcc208#rd)。完全公平调度，CFS (Completely Fair Scheduler) 。

**nice值**：是用于调整普通进程优先级的参数。范围：`-20`-`19`。

```c
task_struct {
......
int             prio; 			// prio（动态优先级）
int             static_prio;	// static_prio（静态优先级）
int             normal_prio;	// normal_prio（归一化优先级）
unsigned int    rt_priority; 	// rt_priority（实时优先级）
};
```

1. `prio`（动态优先级）  
   动态优先级，有效优先级，调度器最终使用的优先级数值，范围0-139，值越小，优先级越高。
2. `static_prio`（静态优先级）  
   静态优先级，采用`SCHED_NORMAL`和`SCHED_BATCH`调度策略的进程（即普通进程）用于计算动态优先级的，范围100-139。
   prio = static_prio = nice + DEFAULT_PRIO = nice + 120
3. `normal_prio`（归一化优先级）  
   用于计算`prio`的中间变量，不需要太关心。
4. `rt_priority`（实时优先级）  
   实时优先级，采用`SCHED_FIFO`和`SCHED_RR`调度策略进程（即实时进程）用于计算动态优先级，范围0-99。
   prio = MAX_RT_PRIO - 1 - rt_prio = 100 - 1 - rt_priority;

实时优先级数值越大，得到的动态优先级数值越小，优先级越高。

`ps -elf`命令查看进程优先级。`PRI`：进程优先级，数值越小，优先级越高。（并非动态优先级）`NI`：nice值。

`SCHED_FIFO`（先进先出调度）和`SCHED_RR`（时间片轮转调度），这些策略可以通过`sched_setscheduler()`系统调用（头文件`<sched.h>`）来设置：

```cpp
struct sched_param param;
    // 设置优先级为最高优先级
    param.sched_priority = sched_get_priority_max(SCHED_FIFO);
    // 设置调度策略为SCHED_FIFO
    if (sched_setscheduler(getpid(), SCHED_FIFO, &param) == -1) {
        std::cerr << "无法设置实时调度策略" << std::endl;
        return 1;
    }
```


