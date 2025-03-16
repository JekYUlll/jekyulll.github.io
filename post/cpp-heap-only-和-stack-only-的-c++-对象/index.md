
## 为什么需要限制对象的创建位置？

例如一个需要**手动控制生命周期**的数据库连接池，不希望随便在栈上创建一个然后自动销毁吧。又或者写了一个轻量级的临时计算工具类，如果每次都在堆上创建，性能反而会下降。  
这时候就需要用一些技巧来限制对象的创建和销毁。

• ✅ 明确生命周期管理
• ✅ 避免资源泄漏
• ✅ 提升关键路径性能
• ✅ 强制使用最佳实践

---

## 一、Heap Only：必须用new创建

#### 方法1：私有化析构函数
```cpp
class HeapOnly {
public:
    static HeapOnly* Create() {
        return new HeapOnly(); // 工厂方法
    }
    
    void Suicide() { delete this; } // 起个中二的名字提醒要手动释放

private:
    ~HeapOnly() {} // 关键！栈对象无法自动调用
    HeapOnly() {}  // 私有构造
};
```
**原理**：栈对象在离开作用域时会自动调用析构函数，如果析构是私有的，编译器直接报错。必须通过`new`创建，手动调用释放。

#### 方法2：C++11，用`= delete`
```cpp
class HeapOnly {
public:
    static HeapOnly* Create() { return new HeapOnly; }

    // 直接禁用拷贝构造和赋值
    HeapOnly(const HeapOnly&) = delete;
    HeapOnly& operator=(const HeapOnly&) = delete;

private:
    HeapOnly() = default;
};
```

### 应用场景
1. **单例模式**（比如全局配置管理器）
2. **需要多态的对象**（比如动物基类派生出猫狗子类）
3. **重量级资源**（比如线程池、网络连接池）
4. **延迟初始化**的对象（按需创建）

eg. 游戏引擎中的资源管理器，所有贴图、模型都通过`ResourceManager::LoadTexture()`这类工厂方法创建，确保统一管理。

---

## 二、Stack Only：禁止`new`出来的对象

#### 方法1：删除new运算符
```cpp
class StackOnly {
public:
    static StackOnly Create() { return StackOnly(); }
    
    // 重点在这两行！
    void* operator new(size_t) = delete;
    void operator delete(void*) = delete;

private:
    StackOnly() = default;
};
```
**效果**：`new StackOnly()`，编译器直接报错："尝试引用已删除的函数"。

#### 方法2：RAII
```cpp
class FileHandler {
public:
    FileHandler(const char* path) { 
        file = fopen(path, "r"); 
    }
    
    ~FileHandler() { 
        if(file) fclose(file); 
    }
    
    // 禁用堆分配
    void* operator new(size_t) = delete;

private:
    FILE* file;
};
```
**实际意义**：用的时候直接在栈上创建，离开作用域自动关闭文件。

### 应用场景
1. **RAII资源管理**（锁、文件句柄、智能指针）
2. **轻量临时对象**（比如3D向量、矩阵运算）
3. **高频创建销毁的小对象**（比如游戏中的粒子效果）
4. **保证线程安全的对象**（栈对象不会跨线程共享）

eg. 多线程中的`std::lock_guard`，必须直接在栈上创建才能确保锁的自动释放，防止死锁。

---

## 三、使用场景

| **考虑因素**       | **选堆对象**                          | **选栈对象**                      |
|--------------------|---------------------------------------|-----------------------------------|
| 生命周期           | 需要长期存在或跨作用域                | 随用随毁，自动清理                |
| 对象大小           | 大型对象（比如超过1MB）               | 小型对象（建议不超过几十KB）       |
| 性能要求           | 对内存分配速度不敏感                  | 高频创建/销毁时性能敏感            |
| 多态需求           | 需要基类指针操作不同子类              | 通常不需要                        |
| 资源安全           | 需要手动管理                          | 依赖RAII自动管理                   |

不确定该用哪个时，优先考虑栈对象（更安全）。
