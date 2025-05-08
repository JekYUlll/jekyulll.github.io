+++
date = '2025-04-21T18:05:47+08:00'
draft = false
title = 'C++ 中的乐观锁和悲观锁'
author = 'JekYUlll'
lastmod = '2025-04-21T18:05:47+08:00'
tags = ['cpp']
categories = ['cpp']
+++

> **悲观锁**（Pessimistic Lock）是一种假设冲突会频繁发生的锁机制。每次数据访问时，都会先加锁，直到操作完成后才释放锁，这样可以确保在锁持有期间，其他线程无法访问这段数据，从而避免了并发冲突。
> **乐观锁**（Optimistic Lock）是一种假设冲突不会频繁发生的锁机制。每次数据访问时，不会加锁，而是在更新数据时检查是否有其他线程修改过数据。如果检测到冲突（数据被其他线程修改过），则重试操作或报错。适用于读多写少的场景。  

乐观锁通常实现方式有以下两种：  
- **版本号机制**：每次读取数据时，读取一个版本号，更新数据时，检查版本号是否变化，如果没有变化，则更新成功，否则重试。
- **时间戳机制**：类似版本号机制，通过时间戳来检测数据是否被修改。
 
- 悲观锁性能较低，因为每次操作都需要加锁和解锁。
- 乐观锁性能较高，但在高并发写操作下可能会频繁重试，影响性能。

- 悲观锁适用于并发冲突高、数据一致性要求严格的场景。
- 乐观锁适用于并发冲突低、读多写少的场景。

---

C++乐观锁实现方式：使用 **CAS（Compare-And-Swap）** 或 `std::atomic`：

```cpp
#include <iostream>
#include <thread>
#include <atomic>

std::atomic<int> sharedData(0);

void optimisticTask(int id) {
    for (int i = 0; i < 5; ++i) {
        int oldValue;
        int newValue;
        do {
            oldValue = sharedData.load();        // 读取当前值
            newValue = oldValue + 1;             // 本地计算
        } while (!sharedData.compare_exchange_weak(oldValue, newValue));
        
        std::cout << "Thread " << id << " updated sharedData to " << newValue << std::endl;
    }
}

int main() {
    std::thread t1(optimisticTask, 1);
    std::thread t2(optimisticTask, 2);

    t1.join();
    t2.join();

    std::cout << "Final sharedData: " << sharedData << std::endl;
    return 0;
}
```

`compare_exchange_weak` 可能会在无冲突时也失败（为性能优化），可以换成 `compare_exchange_strong` 更稳定。

| 特性       | 悲观锁                          | 乐观锁                                |
|------------|----------------------------------|----------------------------------------|
| 开销       | 较大（加锁解锁）                 | 较小（无锁，靠 CAS）                  |
| 并发性能   | 低（锁竞争激烈时性能下降）       | 高（冲突少时效率高）                  |
| 适用场景   | 冲突频繁的情况（例如写多读少）   | 冲突较少的情况（例如读多写少）        |
| 实现方式   | `std::mutex`, `std::lock_guard` | `std::atomic`, `compare_exchange_*`    |
