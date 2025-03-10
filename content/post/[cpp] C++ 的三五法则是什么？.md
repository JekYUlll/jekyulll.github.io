+++
date = '2024-08-26T18:05:47+08:00'
draft = false
title = 'C++ 的三五法则是什么？'
author = 'JekYUlll'
lastmod = '2024-08-26T18:05:47+08:00'
tags = ['cpp']
categories = ['cpp']
+++

三五法则（Rule of Three/Five/Zero）。  

“三法则”主要适用于 C++98/03 标准下的资源管理。在使用动态内存或其他资源时，如果类需要显式地管理资源，通常需要实现以下三个特殊成员函数：  
1. 拷贝构造函数（Copy Constructor）：用于复制对象时分配新资源。  
2. 拷贝赋值运算符（Copy Assignment Operator）：用于对象赋值时释放旧资源并分配新资源。  
3. 析构函数（Destructor）：用于对象销毁时释放资源。  
4. 
随着 C++11 引入了移动语义和右值引用，"五法则"扩展了“三法则”，增加了两个新的特殊成员函数：  
1. 移动构造函数（Move Constructor）：用于移动对象时“窃取”资源，而不是复制。  
2. 移动赋值运算符（Move Assignment Operator）：用于对象赋值时“窃取”资源，而不是复制。
