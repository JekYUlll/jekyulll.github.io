+++
date = '2025-01-01T14:05:47+08:00'
draft = false
title = '分段锁技术详解及 C++ 实现'
author = 'JekYUlll'
lastmod = '2025-01-01T14:05:47+08:00'
tags = ['cpp','algorithm']
categories = ['cpp']
+++

分段锁（Segmented Locking）是一种用于优化多线程访问共享资源时锁粒度的技术。它通过将资源分成多个小段，并为每段分配独立的锁，来减少锁的争用，从而提升并发性能。

分段锁通过减少锁粒度，让多个线程可以同时访问不同的段，从而显著提高性能。这种方法常见于 **哈希表**、**数据库索引** 和其他高并发系统中。

---

#### **基本原理**

1. **划分资源**：
   将容器划分为多个独立的段（`segment`），每段可以包含一部分数据。例如，一个哈希表可以按哈希值将数据分配到多个桶（`bucket`），每个桶代表一个段。

2. **独立加锁**：
   每个段都有一个独立的锁（如 `std::mutex` 或 `std::shared_mutex`），对该段的数据操作时，只需要锁定对应的段即可，其他段不受影响。

3. **映射规则**：
   通过某种映射规则（如哈希函数）将操作定位到特定的段。这种映射规则应尽可能均匀，以避免*热点问题*（即某些段过于频繁被访问，导致锁竞争）。

---

#### **适用场景**

- **高并发读写**：如多线程访问的大型哈希表、数据库索引。
- **热点数据分散**：通过分段减少单点锁的争用，提升性能。
- **读多写少**：可以结合 `std::shared_mutex` 提供共享锁和独占锁，进一步优化读性能。

注：**负载不均风险**：如果映射规则不合理，可能导致某些段成为热点(eg. 热点桶)，影响性能。

---

下面通过一个线程安全的哈希表（`ThreadSafeHashMap`）来展示分段锁的实现(用`std::vector`简单模拟)。

1. 将哈希表分为多个桶（`bucket`），每个桶独立管理其数据。
2. 使用哈希函数将键映射到对应的桶。
3. 为每个桶分配一个 `std::mutex` 来保护数据。
4. 对于读操作，只锁定对应的桶，支持并行读取。
5. 对于写操作，也只锁定对应的桶，减少锁的范围。

```cpp
#include <iostream>
#include <vector>
#include <mutex>
#include <shared_mutex>
#include <thread>
#include <functional>

template <typename Key, typename Value>
class ThreadSafeHashMap {
private:
    struct Bucket {
        std::shared_mutex mtx; // 每个桶的独立锁
        std::vector<std::pair<Key, Value>> data;
    };

    std::vector<Bucket> buckets;
    size_t num_buckets;

    // 哈希函数，将键映射到对应的桶
    size_t hash(const Key& key) const {
        return std::hash<Key>{}(key) % num_buckets;
    }

public:
    ThreadSafeHashMap(size_t num_buckets = 16) : num_buckets(num_buckets) {
        buckets.resize(num_buckets);
    }

    // 插入操作，按桶分段加锁
    void insert(const Key& key, const Value& value) {
        size_t index = hash(key);
        std::unique_lock<std::shared_mutex> lock(buckets[index].mtx);
        buckets[index].data.push_back({key, value});
    }

    // 查找操作，按桶分段加锁
    bool find(const Key& key, Value& value) {
        size_t index = hash(key);
        std::shared_lock<std::shared_mutex> lock(buckets[index].mtx);
        for (const auto& pair : buckets[index].data) {
            if (pair.first == key) {
                value = pair.second;
                return true;
            }
        }
        return false;
    }

    // 删除操作，按桶分段加锁
    bool erase(const Key& key) {
        size_t index = hash(key);
        std::unique_lock<std::shared_mutex> lock(buckets[index].mtx);
        auto& bucket = buckets[index].data;
        for (auto it = bucket.begin(); it != bucket.end(); ++it) {
            if (it->first == key) {
                bucket.erase(it);
                return true;
            }
        }
        return false;
    }
};

int main() {
    ThreadSafeHashMap<int, std::string> map;

    // 多线程插入数据
    std::thread t1([&]() { map.insert(1, "one"); });
    std::thread t2([&]() { map.insert(2, "two"); });
    std::thread t3([&]() { map.insert(3, "three"); });

    t1.join();
    t2.join();
    t3.join();

    // 查找数据
    std::string value;
    if (map.find(2, value)) {
        std::cout << "Found: " << value << std::endl;
    }

    // 删除数据
    map.erase(2);

    return 0;
}
```
像 [Intel TBB](https://github.com/uxlfoundation/oneTBB) 等并发库提供了更加高效的线程安全容器。  

> TBB(Thread Building Blocks)是英特尔发布的一个库，全称为 Threading Building Blocks。TBB 获得过 17 届 Jolt Productivity Awards，是一套 C++ 模板库。

```bash
sudo apt-get install libtbb-dev  # Ubuntu/Debian
sudo yum install tbb-devel       # CentOS/Red Hat

./vcpkg install tbb
conan install tbb
brew install tbb
```


