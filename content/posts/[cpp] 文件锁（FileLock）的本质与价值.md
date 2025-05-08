+++
date = '2025-03-26T04:05:47+08:00'
draft = false
title = '文件锁（FileLock）的本质与价值'
author = 'JekYUlll'
lastmod = '2025-03-26T04:05:47+08:00'
tags = ['cpp', 'linux']
categories = ['cpp']
+++

在多进程/多线程环境中，Mutex针对的是程序内部的内存数据结构（如链表、哈希表），无法直接控制外部资源（如磁盘文件）。例如，线程A通过Mutex保护一个缓存队列，但若另一个进程直接修改磁盘上的对应文件，Mutex无法拦截。此时可以用文件锁来解决。

文件锁的核心逻辑是通过独占标记协调资源访问。

**典型应用场景**：  
1. **配置文件的原子性更新**  
   多个服务实例同时修改同一配置文件时，未加锁会导致最后的写入覆盖先前内容（例如Nginx配置热更新）  
2. **日志文件的顺序写入**  
   多线程日志系统中，不加锁可能引发日志行交错（如Apache日志滚动的并发问题）  
3. **分布式系统的资源协调**  
   在无中心化锁服务时，通过共享存储（如NFS）的文件锁实现分布式锁（类似ZooKeeper的临时节点机制）  

---

### C++实现文件锁的三种范式  
#### **方案一：操作系统原生API（工业级方案）**  
**适用场景**：需要高可靠性、跨平台兼容的生产环境  
```cpp
#include <string>
#include <fcntl.h>    // Linux
#include <windows.h>  // Windows

class NativeFileLock {
public:
    explicit NativeFileLock(const std::string& path) : lock_path(path) {}

    bool acquire() {
#ifdef _WIN32
        // Windows通过独占模式创建文件实现锁
        h_file = CreateFileA(lock_path.c_str(), GENERIC_WRITE, 0, 
                           nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_HIDDEN, nullptr);
        return h_file != INVALID_HANDLE_VALUE;
#else
        // Linux使用fcntl记录锁
        fd = open(lock_path.c_str(), O_RDWR | O_CREAT, 0644);
        if (fd == -1) return false;

        flock lock_struct{};
        lock_struct.l_type = F_WRLCK;  // 排他锁
        lock_struct.l_whence = SEEK_SET;
        return fcntl(fd, F_SETLK, &lock_struct) != -1;
#endif
    }

    void release() {
#ifdef _WIN32
        CloseHandle(h_file);
        DeleteFileA(lock_path.c_str());
#else
        close(fd);
        unlink(lock_path.c_str());
#endif
    }

private:
#ifdef _WIN32
    HANDLE h_file = INVALID_HANDLE_VALUE;
#else
    int fd = -1;
#endif
    std::string lock_path;
};
```
**技术要点**：  
• Windows通过`CREATE_ALWAYS`+隐藏属性实现原子创建  
• Linux使用`fcntl`的记录锁，支持对文件部分区域加锁  
• 必须处理进程崩溃后的锁残留（通过`unlink/DeleteFile`物理删除锁文件）  

---

#### **方案二：基于文件系统标记（轻量级方案）**  
**适用场景**：快速实现、非高并发场景  
```cpp
#include <fstream>
#include <filesystem>

class MarkerFileLock {
public:
    explicit MarkerFileLock(const std::string& path) : lock_path(path) {}

    bool try_lock() {
        if (std::filesystem::exists(lock_path)) return false;
        std::ofstream temp(lock_path);
        return temp.is_open();  // 文件创建成功即视为获得锁
    }

    void unlock() { 
        std::filesystem::remove(lock_path); 
    }

private:
    std::string lock_path;
};
```
**局限性**：  
• 无法检测锁文件被手动删除的意外情况  
• 进程崩溃可能导致死锁（需额外守护进程清理）  

---

#### **方案三：内存映射+原子操作（高性能方案）**  
**适用场景**：需要微秒级响应的关键系统  
```cpp
#include <sys/mman.h>
#include <atomic>

class MMapLock {
public:
    MMapLock(const char* path) {
        fd = open(path, O_RDWR | O_CREAT, 0644);
        ftruncate(fd, sizeof(int));  // 扩展文件大小
        addr = mmap(nullptr, sizeof(int), PROT_READ | PROT_WRITE, 
                   MAP_SHARED, fd, 0);
        counter = reinterpret_cast<std::atomic<int>*>(addr);
    }

    bool lock() {
        int expected = 0;
        return counter->compare_exchange_strong(expected, 1, 
               std::memory_order_acquire);
    }

    void unlock() {
        counter->store(0, std::memory_order_release);
        munmap(addr, sizeof(int));
        close(fd);
    }

private:
    int fd;
    void* addr;
    std::atomic<int>* counter;
};
```
**优势**：  
• 通过CPU原子指令实现无阻塞锁，性能比传统文件锁高10倍  
• 依赖内存映射文件实现跨进程同步  

---

### 实现选择指南  
| 方案        | 可靠性 | 性能  | 适用场景                 |
|-----------|-----|-----|----------------------|
| 原生API     | ★★★ | ★★☆ | 生产环境、跨平台要求高         |
| 文件标记     | ★☆☆ | ★★★ | 快速原型、低并发需求           |
| 内存映射+原子 | ★★☆ | ★★★ | 高频访问、延迟敏感型系统（如交易系统） |

**避坑建议**：  
1. **避免网络文件系统**（如NFS）——锁机制可能因网络延迟失效  
2. **设置超时退避**——防止死锁（参考Java的`tryLock(timeout)`）  
3. **锁文件路径规范化**——建议使用`/var/lock/`等专用目录  
