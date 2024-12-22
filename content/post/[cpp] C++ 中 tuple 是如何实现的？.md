+++
date = '2024-09-28T18:05:47+08:00'
draft = false
title = 'C++ 中 tuple 是如何实现的？'
author = 'JekYUlll'
lastmod = '2024-09-28T18:05:47+08:00'
tags = ['cpp', 'template']
categories = ['cpp']
+++

`tuple`本身就是一种结构体，但是是一个模板类。利用`形参包`(Parameter pack)。[C++ std::tuple的原理及简易实现](https://zhuanlan.zhihu.com/p/715025973)，靠着模板元的递归实现的，相当抽象。  

```cpp
template<typename...Args>
struct tuple;

// 当元组中没有元素时，递归结束
template<>
struct tuple<> {
    constexpr tuple() noexcept = default;
    constexpr tuple(const tuple &) noexcept {};
    constexpr tuple &operator=(const tuple &) = default;
};

// 当元组中有一个或多个元素时，将第一个元素的类型分离出来，并通过继承，将剩下的元素作为另一个元组处理。
template<typename head, typename...Args>
struct tuple<head, Args...> : tuple<Args...> {
    using base_ = tuple<Args...>;

    template<typename head_, typename...Args_>
    constexpr tuple(head_ &&val, Args_ &&...args) :
        head_val_(std::forward<head_>(val)), base_(std::forward<Args_>(args)...) {}

    tuple_val_<head> head_val_;
};
```

[类模板实参推导（CTAD）(C++17 起)](https://zh.cppreference.com/w/cpp/language/class_template_argument_deduction)。

