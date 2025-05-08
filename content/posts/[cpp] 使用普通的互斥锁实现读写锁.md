+++
date = '2025-03-10T12:05:47+08:00'
draft = false
title = '【AI】使用普通的互斥锁实现读写锁'
author = 'JekYUlll'
lastmod = '2025-03-10T12:05:47+08:00'
tags = ['cpp','algorithm']
categories = ['cpp']
+++

核心在于通过**状态管理**和**锁的组合**来模拟读写锁的“读共享、写独占”特性。以下是实现思路和具体方法：

---

### 一、实现原理
读写锁的核心规则是：  
1. **读读共享**：允许多个读线程并发访问。  
2. **读写互斥**：读线程和写线程不能同时访问。  
3. **写写互斥**：同一时间只能有一个写线程访问。  

使用普通互斥锁（`std::mutex`）和计数器可以实现这一逻辑：  
• **读计数器**：统计当前活跃的读线程数量。  
• **写互斥锁**：确保写操作的独占性。  
• **状态保护锁**：保护读计数器和写锁状态的原子性。

---

### 二、实现步骤
#### 1. 定义关键成员变量
```cpp
#include <mutex>
#include <condition_variable>

class ReadWriteLock {
private:
    std::mutex counter_mutex;    // 保护读计数器和写标志
    std::mutex write_mutex;      // 写操作的独占锁
    int reader_count = 0;        // 当前活跃的读线程数量
    bool write_pending = false;  // 是否有写线程在等待
    std::condition_variable read_cv, write_cv;
};
```

#### 2. 读锁的获取与释放
• **获取读锁**：  
  当无写线程运行时，允许读线程进入；若存在写线程等待，则阻塞新读线程（避免写饥饿）。
```cpp
void read_lock() {
    std::unique_lock<std::mutex> lock(counter_mutex);
    // 等待直到没有写线程在等待或运行
    read_cv.wait(lock, [this] { return !write_pending; });
    reader_count++;
    if (reader_count == 1) {
        write_mutex.lock();  // 第一个读线程获取写锁，阻止写操作
    }
}
```

• **释放读锁**：  
  减少读计数器，若最后一个读线程退出，则释放写锁并通知可能的等待写线程。
```cpp
void read_unlock() {
    std::unique_lock<std::mutex> lock(counter_mutex);
    reader_count--;
    if (reader_count == 0) {
        write_mutex.unlock();
        write_cv.notify_one();  // 通知等待的写线程
    }
}
```

#### 3. 写锁的获取与释放
• **获取写锁**：  
  设置写等待标志，等待所有读线程退出后获取写锁。
```cpp
void write_lock() {
    std::unique_lock<std::mutex> lock(counter_mutex);
    write_pending = true;        // 标记有写线程等待
    write_cv.wait(lock, [this] { return reader_count == 0; }); // 等待读线程退出
    write_mutex.lock();          // 获取写锁
}
```

• **释放写锁**：  
  释放写锁并重置写等待标志，唤醒可能的读或写线程。
```cpp
void write_unlock() {
    {
        std::unique_lock<std::mutex> lock(counter_mutex);
        write_pending = false;
    }
    write_mutex.unlock();
    read_cv.notify_all();  // 唤醒等待的读线程
}
```

---

### 三、关键点与注意事项
1. **避免写线程饥饿**：  
   通过`write_pending`标志，确保在有写线程等待时，新读线程会被阻塞。  
2. **原子性保护**：  
   所有对`reader_count`和`write_pending`的修改必须通过`counter_mutex`保护。  
3. **条件变量的使用**：  
   `read_cv`和`write_cv`用于协调读/写线程的状态切换，避免忙等待。  
4. **锁的粒度**：  
   写操作通过`write_mutex`实现独占，读操作通过共享计数器实现并发。

---

### 四、潜在问题与优化
• **性能问题**：与标准库的`std::shared_mutex`相比，此实现可能因频繁锁竞争导致性能下降。  
• **死锁风险**：需确保锁的获取顺序一致（如先`counter_mutex`再`write_mutex`）。  
• **扩展性**：可引入优先级策略（如写优先）来优化公平性。

---

### 五、面试回答示例
“可以通过两个互斥锁和一个读计数器实现读写锁：  
1. **写锁**：使用一个互斥锁（`write_mutex`）保证写操作的独占性。  
2. **读计数器**：统计活跃读线程数量，第一个读线程获取写锁，最后一个释放。  
3. **状态协调**：通过条件变量和标志位避免写线程饥饿，例如在有写等待时阻塞新读线程。”

这一实现体现了对互斥锁组合使用和线程同步机制的理解，适合在面试中展示底层设计能力。