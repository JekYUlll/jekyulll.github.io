+++
date = '2024-09-01T18:05:47+08:00'
draft = false
title = 'C++ 模板类型推导'
author = 'JekYUlll'
lastmod = '2024-09-01T18:05:47+08:00'
tags = ['cpp', 'morden cpp']
categories = ['cpp']
+++

[一篇文章学完 Effective Modern C++：条款 & 实践](https://zhuanlan.zhihu.com/p/649667647)：

## 条款1： 模板参数类型推导，引用折叠

```cpp
template<typename T>
void f(T&& param);

int x = 27;
const int cx = x;
const int& rx = x;

// 左值的情况
f(x);    // T 的类型为 int&, paramType 为 int&
f(cx);   // T 的类型为 const int&, paramType 为 const int&
f(rx);   // T 的类型为 const int&, paramType 为 const int&

// 右值的情况
f(27)    // T 的类型为 int, paramType 为 int&&
```

对于指向 `const` 对象的 `const` 指针的传递，仅有指针本身的常量性会被忽略：

```cpp
template<typename T>
void f(T param);

const char* const ptr = "Fun with pointers";

f(ptr);    // T 和 param 的类型均为 const char*
```

按值传递给函数模板的数组类型将退化为指针类型，但按引用传递却能推导出真正的数组类型：

```cpp
template<typename T>
void f(T& param);

const char name[] = "J. P. Briggs";

f(name);   // T 的类型为 const char[13], paramType 为 const char (&)[13]
```

利用声明数组引用这一能力可以创造出一个模板，用来推导出数组含有的元素个数：

```cpp
template<typename T, std::size_t N>
constexpr std::size_t arraySize(T (&)[N]) noexcept {
    return N;
} // constexpr 函数，表示这个函数可以在编译时计算结果

int arr[10];
std::size_t size = arraySize(arr); // size 的值是 10
```

函数类型同样也会退化成函数指针，并且和数组类型的规则类似：

```cpp
void someFunc(int, double);

template<typename T>
void f1(T param);

template<typename T>
void f2(T& param);

f1(someFunc);   // param 被推导为函数指针，具体类型为 void (*)(int, double)
f2(someFunc);   // param 被推导为函数引用，具体类型为 void (&)(int, double)
```

## 条款2： auto类型推导

## 条款3：理解 decltype

在 C++11 中，`decltype`的主要用途是声明返回值类型依赖于形参类型的函数模板，这需要用到返回值类型尾置语法(trailing return type syntax)：

```cpp
template<typename Container, typename Index>
auto authAndAccess(Container& c, Index i) -> decltype(c[i]) {
    authenticateUser();
    return c[i];
}
```

C++11 允许对单表达式的 lambda 的返回值实施类型推导，而 C++14 将这个允许范围扩张到了一切函数和一切 lambda，包括那些多表达式的。这就意味着在 C++14 中可以去掉返回值类型尾置语法，仅保留前导`auto`。  
但编译器会为`auto`指定为返回值类型的函数实施模板类型推导，这样就会留下隐患（例如忽略初始化表达的引用性），使用`decltype(auto)`来说明我们采用的是`decltype`的规则，就可以解决这个问题：

```cpp
template<typename Container, typename Index>
decltype(auto) authAndAccess(Container& c, Index i) {
    authenticateUser();
    return c[i];
}
```

在初始化表达式处也可以应用`decltype`类型推导规则：

```cpp
Widget w;
const Widget& cw = w;
auto myWidget1 = cw;            // auto 推导出类型为 Widget
decltype(auto) myWidget2 = cw;  // decltype 推导出类型为 const Widget&
```

在上述情形中，我们无法向函数传递右值容器，若想要采用一种既能绑定到左值也能绑定到右值的引用形参，就需要借助万能引用，并应用`std::forward`（参考条款 25）：

```cpp
template<typename Container, typename Index>
decltype(auto) authAndAccess(Container&& c, Index i) {
    authenticateUser();
    return std::forward<Container>(c)[i];
}
```
