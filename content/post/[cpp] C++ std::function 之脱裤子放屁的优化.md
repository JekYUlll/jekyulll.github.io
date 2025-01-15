+++
date = '2025-01-15T12:05:47+08:00'
draft = false
title = 'C++ std::function 之脱裤子放屁的优化'
author = 'JekYUlll'
lastmod = '2025-01-15T12:05:47+08:00'
tags = ['cpp', 'morden']
categories = ['cpp']
+++

看到一句话：  

> `std::function` 很强大，但是代价也很高，在创建函数对象的时候总是会有 `new` 操作的。虽然通常情况下影响不是很高，但是总觉得这是没必要的。  

于是草草找一下资料，看看有没有隐藏的性能优化。

---

### `std::function` 的实现

[`std::function`](https://zh.cppreference.com/w/cpp/utility/functional/function) 是一个可变参类模板，是一个通用的函数包装器（Polymorphic function wrapper）。  
通过类型擦除（type erasure）机制，将具体类型的可调用对象封装到一个统一的接口中。  

> 其实例可以存储、复制和调用任何可复制构造的可调用目标，包括普通函数、成员函数、类对象（重载了operator()的类的对象）、Lambda表达式等。是对C++现有的可调用实体的一种类型安全的包裹（相比而言，函数指针这种可调用实体，是类型不安全的）。 -- [STL源码分析之std::function](https://zhuanlan.zhihu.com/p/560964284)


```cpp
template<typename _Res, typename... _ArgTypes>
class function<_Res(_ArgTypes...)>
  : public _Maybe_unary_or_binary_function<_Res, _ArgTypes...>
  , private _Function_base
{
private:
    using _Invoker_type = _Res (*)(const _Any_data&, _ArgTypes&&...);
    _Invoker_type _M_invoker;
// ...
};
```

`std::function` 的内部有两个部分：

- 一个指向实际存储区域的指针：存储实际的可调用对象（函数对象、lambda、函数指针等）。
- 一个*接口表*（vtable 等效机制）：存储操作函数（如调用函数、复制、销毁等）的地址。
- 
其类型擦除通过接口表的方式实现，类似于虚函数机制，但它通常采用静态接口表和手动的动态分配来支持多种类型的可调用对象。

---

### 性能分析

[关于std function和lambda function的性能调试 --法号桑菜](https://zhuanlan.zhihu.com/p/370563773)。  
[Avoiding The Performance Hazzards of std::function](https://blog.demofox.org/2015/02/25/avoiding-the-performance-hazzards-of-stdfunction/)。

> There are two performance implications of using `std::function` that might surprise you:
> 1. When calling a `std::function`, it does a virtual function call.
> 2. When assigning a lambda with significant captures to a `std::function`, it will do a dynamic memory allocation!

一是`std::function` 会使用虚函数调用，有开销。二是将lambda 赋给`std::function`的时候，如果捕获内容较，会需要额外的动态内存分配。

第二点也就是：  
`std::function` 对小型的可调用对象会使用“**小对象优化**（Small Object Optimization, SOO）”，避免动态分配堆内存。但如果对象超过了实现中的小对象优化阈值，则会触发堆分配（`new` 操作）。  

---

### 一些可能有用的优化

1. 手动使用模板代替 `std::function`：

```cpp
template <typename Callable>
void invoke(Callable f) {
    f();
}
```
直接在编译期确定类型，避免了类型擦除和动态分配。  
缺点就是使用场景受限于编译期类型，灵活性不如 `std::function`。

2. 向 `std::function` 传递 lambda 的时候使用 `std::ref()` / `std::cref()` 

> `std::ref()` and `std::cref()` return reference wrappers (costant ref wrapper in the cref case) which can hold arbitrary types as references. If you put your large capture lambda into one of these functions and give it to `std::function`, there’s a `std::function` constructor which is able to take this reference, and use that instead of allocating more memory.

```cpp
array i;
auto A = [=]() -> int {
    return (i[0] + i[1] * i[2] + i[3]) ^ i[4];
};
// no allocation, std::function stores a reference to A instead of A itself
function fA(ref(A));
```

3. 使用 `std::variant`：

通过 `std::variant` 直接存储无类型擦除的函数对象。似乎有点跑题，在此处作用有限。  
`std::variant` 的内存是静态分配的：其大小是所有可能存储的类型大小的最大值，避免了堆分配的开销。

```cpp
struct PrintHello {
    void operator()() const { std::cout << "Hello, Struct!" << std::endl; }
};

using Callable = std::variant<void(*)(), PrintHello>;

void invoke(const Callable& f) {
    std::visit([](const auto& func) { func(); }, f);
}

int main() {
    invoke([]() { std::cout << "Hello, Function Pointer!" << std::endl; });  // 函数指针
    invoke(PrintHello{});  // 自定义的函数对象
    return 0;
}
```

4. Stack Allocation: 给 `std::function` 一个自定义分配器...

5. Lambda 的 `+` 

一个无捕获的 lambda 表达式不依赖于任何外部状态，可以被隐式转换为函数指针：

```cpp
auto lambda = []() { std::cout << "Hello, Lambda!" << std::endl; };
void (*funcPtr)() = lambda; // 无需显式转换
funcPtr(); // 调用函数指针，输出 "Hello, Lambda!"
```
在 lambda 表达式前使用`+`，会强制将 lambda 转换为函数指针：

```cpp
void invoke(void (*func)()) {
    func();
}

int main() {
    invoke(+[]() { std::cout << "Hello, Lambda!" << std::endl; }); // 显式转换为函数指针
}
```

---

All in all: 

![确实](/images/stdfunction.png)


