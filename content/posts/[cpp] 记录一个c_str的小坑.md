+++
date = '2025-02-23T12:05:47+08:00'
draft = false
title = '如何让函数安全返回 std::string 的 c_str'
author = 'JekYUlll'
lastmod = '2025-02-23T12:05:47+08:00'
tags = ['cpp']
categories = ['cpp']
+++

观察以下这段明显错误的代码：

```cpp
const char* get_c() {
    std::string s = "hello world";
    return s.c_str();
}

int main() {
    printf("danger : %s\n", get_c());
    return 0;
}
```
字符串`s`是一个函数内部的临时对象，返回的`const char*`实际上是一个指针。函数结束后`s`会析构，而指针理论上会变成悬空的。  
实际上正确打印出了：
```bash
danger : hello world
```
(实际上只是因为该段内存没有被立刻覆盖，理论上是不安全的)

查看一下汇编：  

1. 构造 `s`：
```ass
leaq    .LC0(%rip), %rcx         ; 加载 "hello world" 地址到 %rcx
leaq    -64(%rbp), %rax          ; 栈上分配 s 的内存
call    _ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEC1IS3_EEPKcRKS3_ ; 调用构造函数
```
- 字符串 `"hello world"` 存储在 `.rodata` 只读数据段（.LC0）。
- s 在栈上构造，通过 SSO 直接存储字符串内容。

2. 获取 `c_str()`：
```ass
leaq    -64(%rbp), %rax
call    _ZNKSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE5c_strEv@PLT
movq    %rax, %rbx               ; 将 c_str() 指针保存到 %rbx
```
3. 析构 `s`：

```ass
leaq    -64(%rbp), %rax
movq    %rax, %rdi
call    _ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEED1Ev@PLT ; 调用析构函数
```

对于较短的字符串（如 "`hello world`"），`std::string` 可能使用 短字符串优化（SSO），将数据直接存储在对象内部的栈空间中，而非堆内存。  
在`get_c`中，`s`是在栈上分配的，当函数返回时，栈空间可能未被其他数据覆盖，所以字符串内容仍然保留。此时调用printf，可能仍然能读取到原来的数据，但这只是巧合，属于未定义行为的表现。


---

### 前情提要

写 webserver 的时候，设计了一个配置加载类用于加载配置文件。  
*eg*.  
```toml
[redis]
host = "127.0.0.1"
port = 6379
password = "donotpanic"
db = 0
```
逻辑差不多长这样：  
```cpp
Config Config::_instance;
std::unordered_map<std::string, std::string> Config::_configMap;

std::string Config::GetConfig(const std::string& key) {
    auto it = _configMap.find(key);
    if (it != _configMap.end()) {
        return it->second;
    }
    LOG_W("Config '{}' not found", key);
    return "";
}
```

我想让其返回`c_str`，直接返回是不行的。添加一个`static`的`string`作为cache即可。
```cpp
const char* Config::GetConfig(const std::string& key) {
    static std::string cache;
    auto it = _configMap.find(key);
    if (it != _configMap.end()) {
        cache = it->second;
        return cache.c_str;
    }
    LOG_W("Config '{}' not found", key);
    return nullptr;
}
```

