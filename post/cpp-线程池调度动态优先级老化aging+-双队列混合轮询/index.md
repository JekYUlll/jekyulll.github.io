
C++中线程池一般使用队列（`std::queue`）配合外部的`std::condition_variable`，或者手动构建阻塞队列（BlockQueue）来设计。  

而需要使用任务优先级的时候，一般使用大根堆/小根堆的优先级队列`std::priority_queue`来实现。  

那么问题来了，在任务优先级比较不均的时候，怎么避免低优先级任务的长时间饥饿呢？

为了实现动态公平调度：

### 解决方案：动态优先级老化（Aging）+ 双队列混合轮询

#### 1. 核心思路
- **优先级动态调整**：任务在队列中等待时间越长，其有效优先级逐渐升高。
- **混合轮询**：每处理一定数量的高优先级任务后，强制处理低优先级任务。

#### 2. 实现步骤

**a. 任务结构定义**
记录任务的初始优先级和入队时间：
```cpp
#include <chrono>

struct Task {
    int base_priority; // 初始优先级
    std::chrono::steady_clock::time_point enqueue_time;
    std::function<void()> job;

    // 计算动态优先级（等待时间越长，优先级越高）
    int dynamic_priority() const {
        auto now = std::chrono::steady_clock::now();
        auto wait_time = std::chrono::duration_cast<std::chrono::seconds>(now - enqueue_time).count();
        return base_priority + static_cast<int>(wait_time * 0.1); // 老化系数可调
    }

    // 重载比较运算符（实际比较动态优先级）
    bool operator<(const Task& other) const {
        return this->dynamic_priority() < other.dynamic_priority(); 
    }
};
```

**b. 线程池类设计**
```cpp
#include <queue>
#include <vector>
#include <thread>
#include <mutex>
#include <condition_variable>

class ThreadPool {
public:
    ThreadPool(size_t threads, size_t high_freq = 5) 
        : high_processing_count(0), high_freq_(high_freq) {
        for(size_t i = 0; i < threads; ++i) {
            workers.emplace_back([this] { worker_loop(); });
        }
    }

    void add_task(int priority, std::function<void()> task) {
        {
            std::unique_lock<std::mutex> lock(queue_mutex);
            queue.emplace(Task{priority, std::chrono::steady_clock::now(), task});
        }
        condition.notify_one();
    }

    ~ThreadPool() { /* ... 省略资源回收代码 ... */ }

private:
    std::mutex queue_mutex;
    std::condition_variable condition;
    std::priority_queue<Task> queue; // 主队列（动态优先级）
    std::queue<std::function<void()>> low_priority_queue; // 辅助队列
    std::vector<std::thread> workers;
    
    // 轮询控制
    std::atomic<int> high_processing_count;
    const int high_freq_;

    void worker_loop() {
        while(true) {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lock(queue_mutex);
                condition.wait(lock, [this] { return !queue.empty(); });

                // 动态老化：每处理high_freq_个高优任务后强制处理低优
                if(++high_processing_count % high_freq_ == 0 && 
                   !low_priority_queue.empty()) {
                    task = low_priority_queue.front();
                    low_priority_queue.pop();
                } else {
                    task = queue.top().job;
                    queue.pop();
                }
            }
            if(task) task();
        }
    }
};
```

#### 3. 关键优化点
- **动态优先级计算**：在`dynamic_priority()`中通过等待时间提升优先级，老化系数（0.1）可根据场景调整
- **混合调度策略**：
  - 使用原子计数器`high_processing_count`跟踪连续处理的高优先级任务数量
  - 每处理`high_freq_`（可配置）个高优先级任务后，强制从低优先级队列取任务
- **双队列设计**：
  - 主队列仍为优先级队列，但优先级动态变化
  - 单独的低优先级队列确保最低保障

#### 4. 扩展建议
- **时间阈值兜底**：可添加最大等待时间监控，对超时任务直接提升到最高优先级
- **优先级区间划分**：将任务分为URGENT/HIGH/NORMAL等级别，不同级别采用不同老化系数
- **负载感知**：根据系统负载动态调整high_freq_参数

该方法在保证高优先级任务及时响应的同时，通过动态优先级和强制轮询机制有效防止低优先级任务饥饿。

---

线程池常见实现：[基于C++11实现线程池 - Skykey的文章 - 知乎](https://zhuanlan.zhihu.com/p/367309864)。

[C++ 并发编程（从C++11到C++17）](https://paul.pub/cpp-concurrency/)。  
[货比三家：C++ 中的 task based 并发](https://segmentfault.com/a/1190000002706259)。