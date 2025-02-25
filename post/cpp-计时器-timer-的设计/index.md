
三种经典算法：小顶堆、红黑树、时间轮。  
Linux内核多采用时间轮处理中断定时器，而Nginx使用红黑树管理定时事件。

- *Redis*:		`usUntilEarliestTimer()`
- *Nginx*: 		`ngx_event_find_timer()` 红黑树
- *Skynet*:
- *Netty*: 		时间轮
- *Libevent*: 	最小堆
- *Linux*: 		时间轮

| **算法** | **插入**   | **删除**   | **触发效率**       | **适用场景**                     |
| -------- | ---------- | ---------- | ------------------ | -------------------------------- |
| 小顶堆   | `O(log n)` | `O(n)`     | 高（仅处理堆顶）   | 任务量大，无需频繁取消非堆顶任务 |
| 红黑树   | `O(log n)` | `O(log n)` | 中（遍历有序数据） | 需动态增删改任务                 |
| 时间轮   | `O(1)`     | `O(1)`     | 高（批量处理槽）   | 海量短周期任务，固定时间精度     |

---

### 一、小顶堆

- ​优先级队列结构，堆顶元素始终是最小的（即最近的到期时间）。
- ​插入和删除堆顶操作效率高，但删除任意节点效率低。

#### 复杂度
- 插入：`O(log n)`
- 删除堆顶：`O(log n)`
- 删除任意节点：`O(n)`

#### 适用场景
- 定时任务数量大，且**频繁触发最近任务**的场景。
- 不适用于需要频繁取消或修改非堆顶任务的场景。

```cpp
#include <queue>
#include <vector>
#include <functional>

struct Timer {
    int64_t expire;  // 到期时间戳
    std::function<void()> task;
};

// 小顶堆比较函数
struct Compare {
    bool operator()(const Timer& a, const Timer& b) {
        return a.expire > b.expire;
    }
};

std::priority_queue<Timer, std::vector<Timer>, Compare> min_heap;

// 添加定时任务
void add_timer(int64_t expire, std::function<void()> task) {
    min_heap.push({expire, task});
}

// 驱动逻辑（在事件循环中调用）
void check_expire(int64_t current_time) {
    while (!min_heap.empty() && min_heap.top().expire <= current_time) {
        auto task = min_heap.top().task;
        min_heap.pop();
        task();  // 执行任务
    }
}
```

---

### 二、红黑树

- 使用**有序容器**（如 `std::multimap`）管理定时任务，键为到期时间。
- 支持高效的**插入**、**删除**和**查找**操作。

#### 复杂度
- 插入、删除、查找：`O(log n)`

#### 适用场景
- 需要频繁**取消或修改定时任务**的场景。
- 适合时间跨度大或需要动态调整任务的场景。

```cpp
#include <map>
#include <functional>

struct Timer {
    int id;  // 唯一标识符，用于取消任务
    std::function<void()> task;
};

std::multimap<int64_t, Timer> timer_map;

// 添加定时任务
void add_timer(int id, int64_t expire, std::function<void()> task) {
    timer_map.insert({expire, {id, task}});
}

// 取消定时任务（需遍历）
void cancel_timer(int id) {
    for (auto it = timer_map.begin(); it != timer_map.end();) {
        if (it->second.id == id) {
            it = timer_map.erase(it);
        } else {
            ++it;
        }
    }
}

// 驱动逻辑
void check_expire(int64_t current_time) {
    auto it = timer_map.begin();
    while (it != timer_map.end() && it->first <= current_time) {
        it->second.task();  // 执行任务
        it = timer_map.erase(it);
    }
}
```

---

### 三、时间轮

> 其实可以理解为一种变相的哈希表。

- 将时间划分为多个槽（slot），每个槽对应一个时间间隔。
- 通过指针周期性移动触发当前槽的任务，**插入和删除操作高效**。

**分层**：  
如果时间轮的槽数有限，比如60个槽，每个槽代表1秒，那么最大只能处理60秒内的任务。超过这个时间的任务无法直接放置，所以需要分层来解决这个问题。  
- 比如，像钟表一样，有小时、分钟、秒的分层结构。当高层时间轮指针转动时，将任务降级到低层时间轮（类似钟表的进位机制）。

#### 复杂度
- 插入、删除：`O(1)`（理想情况下）

#### 适用场景
- **海量定时任务**且**时间精度固定**的场景（如游戏技能冷却）、超大规模定时任务​（例如百万级连接的心跳检测）。
- 不适用于时间跨度极大或需要高精度动态调整的场景。

```cpp
// 单层的
#include <vector>
#include <list>
#include <functional>

const int WHEEL_SIZE = 60;  // 时间轮槽数（如60秒）

struct Timer {
    int rotation;  // 剩余轮数
    std::function<void()> task;
};

std::vector<std::list<Timer>> time_wheel(WHEEL_SIZE);
int current_slot = 0;

// 添加定时任务
void add_timer(int interval, std::function<void()> task) {
    int slots = interval % WHEEL_SIZE;
    int rotation = interval / WHEEL_SIZE;
    int pos = (current_slot + slots) % WHEEL_SIZE;
    time_wheel[pos].push_back({rotation, task});
}

// 驱动逻辑（每秒调用一次）
void tick() {
    auto& tasks = time_wheel[current_slot];
    auto it = tasks.begin();
    while (it != tasks.end()) {
        if (it->rotation > 0) {
            it->rotation--;
            ++it;
        } else {
            it->task();  // 执行任务
            it = tasks.erase(it);
        }
    }
    current_slot = (current_slot + 1) % WHEEL_SIZE;
}
```

```cpp
// 三层时间轮
#include <vector>
#include <list>
#include <functional>

// 时间轮层级定义
struct TimingWheel {
    int wheel_size;    // 槽数
    int current_slot;  // 当前槽位置
    int tick;          // 时间精度（单位：秒）
    std::vector<std::list<std::function<void()>>> slots;

    TimingWheel(int size, int tick_unit) 
        : wheel_size(size), current_slot(0), tick(tick_unit), slots(size) {}
};

// 三层时间轮：小时级、分钟级、秒级
TimingWheel hour_wheel(60, 3600);    // 1小时/槽，总范围60小时
TimingWheel minute_wheel(60, 60);    // 1分钟/槽，总范围60分钟
TimingWheel second_wheel(60, 1);     // 1秒/槽，总范围60秒

// 插入任务（假设时间单位为秒）
void add_task(int interval, const std::function<void()>& task) {
    if (interval < 60) {
        // 插入秒级时间轮
        int pos = (second_wheel.current_slot + interval) % 60;
        second_wheel.slots[pos].push_back(task);
    } else if (interval < 3600) {
        // 插入分钟级时间轮
        int pos = (minute_wheel.current_slot + interval / 60) % 60;
        minute_wheel.slots[pos].push_back(task);
    } else {
        // 插入小时级时间轮
        int pos = (hour_wheel.current_slot + interval / 3600) % 60;
        hour_wheel.slots[pos].push_back(task);
    }
}

// 驱动逻辑（每秒调用一次）
void tick() {
    // 处理秒级时间轮
    auto& second_tasks = second_wheel.slots[second_wheel.current_slot];
    for (auto& task : second_tasks) task();
    second_tasks.clear();
    second_wheel.current_slot = (second_wheel.current_slot + 1) % 60;

    // 每分钟触发分钟级时间轮迁移
    if (second_wheel.current_slot == 0) {
        auto& minute_tasks = minute_wheel.slots[minute_wheel.current_slot];
        for (auto& task : minute_tasks) {
            // 重新计算剩余时间并降级到秒级时间轮
            int remain_time = ...; // 根据业务逻辑计算剩余秒数
            add_task(remain_time, task);
        }
        minute_tasks.clear();
        minute_wheel.current_slot = (minute_wheel.current_slot + 1) % 60;
    }

    // 每小时触发小时级时间轮迁移（类似逻辑）
}
```

[c语言-手撕多级时间轮定时器(纯手写)](https://blog.csdn.net/weixin_45203607/article/details/127095268)。

---

**拓展**：  
- <u>redis延时队列如何实现？</u>
- 非活跃的连接自动断开如何实现？
- 主从节点随机心跳检测如何实现？
- 下单后30分钟内未付款自动取消订单如何实现？