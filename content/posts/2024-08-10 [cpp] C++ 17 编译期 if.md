+++
date = '2024-08-10T18:05:47+08:00'
draft = false
title = 'C++ 17 编译期 if'
author = 'JekYUlll'
lastmod = '2024-08-10T18:05:47+08:00'
tags = ['cpp', 'morden cpp']
categories = ['cpp']
+++

[C++17编译期if](https://www.bilibili.com/video/BV1Eb42177a8/?spm_id_from=333.1007.tianma.1-1-1.click&vd_source=9b0b9cbfd8c349b95b4776bd10953f3a)：`constexpr`。  

用例：不加`constexpr`会编译出错，因为必有一种情况是语法错误的。如果`T`为`X`类型，则内部没有`y_func()`。

```cpp
template<typename T>
void f(T t) {
	// 判断类型
	if constexpr (std::is_same_v<T, X>) {
		t.x_func();
	} else { // 此处若为 "舍弃语句"，不会参加编译。但会检查语法错误(但不会检查模板的实例化)。而预处理器if(#if)如果舍弃，完全不检查。
		t.y_func();
	}
}
```

返回类型推导：C++14后可以用`auto`作为函数返回值，但所有表达式必须推导出相同的返回类型(不能在不同情况下返回不同的类型，例如`int`和`float`)。但如果在判断的地方使用`constexpr`，能通过编译(因为是在编译期判断的)。

```cpp
auto func() {
	if constexpr (...) {
		return 1.0f;
	} else {
		return 0;
	}
}
```
