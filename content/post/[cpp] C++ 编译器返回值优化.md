+++
date = '2024-12-23T08:42:29+08:00'
draft = false
title = 'C++ 编译器返回值优化'
author = 'JekYUlll'
lastmod = '2024-12-23T08:42:29+08:00'
tags = ['cpp', 'compiler']
categories = ['cpp']
+++

### 背景

在 C++ 中，当函数返回一个对象时，编译器通常需要进行对象的拷贝或移动操作。例如：

```cpp
SomeClass createObject() {
    SomeClass obj;
    // 设置 obj 的一些成员
    return obj;  // 返回一个对象
}
```

在没有优化的情况下，`obj` 被返回时，编译器可能会执行一次拷贝构造或移动构造操作，甚至可能是两次（先拷贝到临时对象，再从临时对象拷贝到目标变量）。这些额外的拷贝或移动操作会导致性能下降。

为了减少这种不必要的开销，现代 C++ 编译器通常会进行优化，减少返回值时的拷贝或移动，使用如 **RVO** 和 **NRVO** 的优化策略。

### 1. RVO（Return Value Optimization，返回值优化）

RVO（Return Value Optimization，返回值优化），编译器可以直接在目标变量的位置构造返回值，减少不必要的对象拷贝和内存开销。

```cpp
SomeClass createObject() {
    SomeClass obj;   // 局部对象
    return obj;      // 返回该对象
}
```

在没有优化的情况下，`obj` 被返回时，编译器可能会做两次操作：  
1. 将 `obj` 拷贝或移动到一个临时对象中。
2. 将临时对象拷贝或移动到调用者的目标变量。

RVO 的核心思想是，在函数返回临时对象时，编译器可以<u>直接将返回值构造到调用者的接收变量中</u>，而无需通过中间的临时对象进行拷贝或移动。

```cpp
int main() {
    SomeClass obj = createObject();  // RVO 优化将直接构造在 obj 中
}
```

RVO 只适用于临时对象返回的场景，对于具名对象（有名称的局部对象），编译器一般不能直接应用 RVO。返回具名对象时，编译器会尝试应用 NRVO（Named Return Value Optimization，命名返回值优化），以减少不必要的拷贝或移动。
```cpp
SomeClass createObject() {
    SomeClass obj;   // 具名局部变量
    return obj;      // 这里不能使用 RVO
}
```

编译器行为：
- **GCC/Clang**：启用优化选项（如 `-O2` 或 `-O3`）时，编译器会自动应用 RVO 来优化返回临时对象的代码。
- **MSVC**：在 Visual Studio 中，编译器会自动应用 RVO，并且它通常比 GCC 和 Clang 更早地进行这种优化。

### 2. NRVO（Named Return Value Optimization，命名返回值优化）

NRVO 可以看作是 RVO 的一种扩展。  
它仅在返回的是具名对象时有效。具体来说，当函数返回一个具名的局部变量时，NRVO 允许编译器直接将该局部变量的位置“转移”到调用者的接收变量中，而不需要进行拷贝或移动。

---

# 拓展

[从函数中返回stl容器开销很大吗？](https://zhuanlan.zhihu.com/p/656372497)

禁用 NRVO 优化的情况下：
```cpp
struct X {
    X() { puts("X()"); }
    X(const X&) { puts("X(const X&)"); }
    X(X&&)noexcept { puts("X(X&&)"); }
    ~X() {puts("~X()");}
};

X func() {
    X x;
    puts("-----------");
    return x;
}

int main() {
    auto result = func();
}
```
输出：
```
X()
-----------
X(X&&)
~X()
X(X&&)
~X()
~X()
```

如果启用了 命名返回值优化（NRVO），编译器可以直接将 `x` 移动到返回值位置，而无需额外的构造操作。  
由于 `x` 是一个左值，标准情况下会调用拷贝构造函数，但在返回时，由于是函数返回值（即返回局部变量），且需要将返回值传递给 `result`（通过移动语义优化），C++ 编译器通常会选择移动构造。
