+++
date = '2025-01-09T20:05:47+08:00'
draft = false
title = '从场景解析 C++ shared_from_this'
author = 'JekYUlll'
lastmod = '2025-01-09T20:05:47+08:00'
tags = ['cpp']
categories = ['cpp']
+++

---

[智能指针之shared_ptr易错点05](https://blog.csdn.net/weixin_44517656/article/details/114208041)。   
[掌握C++ 智能指针的自我引用：深入解析 `shared_from_this` 和 `weak_from_this`](https://zhuanlan.zhihu.com/p/701343248)。   
[C++之shared_from_this用法以及类自引用this指针陷阱](https://blog.csdn.net/weixin_44834554/article/details/131589849)。

---

**思考**：
> 设计一个树的节点的时候，如果使用智能指针：用一个`std::vector<shared_ptr<TreeNode>>`来存储子节点，为避免循环引用，用`weak_ptr<TreeNode>`来存储自身的父节点指针。  
> 那添加子节点的时候，怎么把自身的`shared_ptr`赋值给子节点存储的父节点指针呢？

两个错误做法：

1. 使用`std::make_shared<TreeNode<T>>(*this)`来创建一个新的`shared_ptr`，然后赋值给子节点存储的父节点指针。
```cpp
void addChild(std::shared_ptr<TreeNode<T>> child) {
    // 使用 make_shared 来创建子节点并设置父节点
    child->setParent(std::make_shared<TreeNode<T>>(*this));  // 错误的做法
    children.push_back(child);
}
```
使用 `std::make_shared<TreeNode<T>>(*this)` 时，实际上是对当前对象的 拷贝构造（调用拷贝构造函数）来创建一个新的 `TreeNode<T>` 对象。这意味着你将当前节点的状态（但不是智能指针）拷贝到一个新的对象中，而新对象的生命周期由 `std::shared_ptr` 管理。

2. 使用 `std::shared_ptr<TreeNode<T>> ptr(this)` 把裸指针 `this` 包装为 `shared_ptr`。

将 `this` 传递给 `std::shared_ptr<TreeNode<T>>` 会导致新创建的 `shared_ptr` 管理一个裸指针，而裸指针的生命周期没有由智能指针控制。

引出一个常规问题，从裸指针创建 `shared_ptr` 的隐患：

- 当 `shared_ptr` 的引用计数归零时，它会释放它所管理的对象。如果裸指针在此时继续存在，它仍然会指向原来的内存地址。但这时该内存已被释放，裸指针成为了*悬空指针*，也就是所谓的*野指针*。
- 如果裸指针指向的内存已经被释放（例如，该指针原本由 `delete` 或 `delete[]` 释放），然后你用这个裸指针创建 `shared_ptr`，那么 `shared_ptr` 仍然会管理这个已经释放的内存区域。这会导致访问已释放内存（悬空指针）或*双重释放内存*的问题（如果 `shared_ptr` 销毁时再次释放内存）。
- 裸指针可能指向一个栈上的对象：如果裸指针指向一个栈上分配的对象，并且你用它创建 `shared_ptr`，那么 `shared_ptr` 会试图在引用计数归零时释放这个栈上对象的内存。然而，栈上对象的生命周期由栈帧的销毁来管理，而 `shared_ptr` 并不清楚这一点。这将导致程序的未定义行为。

**案例**：

```cpp
class TestB
{
public:
	TestB(){
		cout << "TestB create" << endl;
	}
	~TestB(){
		cout << "TestB destory" << endl;
	}
	shared_ptr<TestB> getSharedFromThis() { 
		return  shared_ptr<TestB> (this); 
	}
};
int main(){
	{
		shared_ptr<TestB> ptr3(new TestB());
		shared_ptr<TestB> ptr4 = ptr3->getSharedFromThis();
		cout << "ptr2 count: " << ptr3.use_count() << " ptr4 count: " << ptr4.use_count() << endl;
		//输出：ptr2 count: 1 ptr4 count: 1 然后会崩溃因为重复释放
	}
	cin.get();
	return 0;
}
```

如何会导致`shared_ptr`指向同一个对象，但是不共享引用计数器？  
是因为裸指针与`shared_ptr`混用，如果我们用一个裸指针初始化或者赋值给`shared_ptr`指针时，在`shared_ptr`内部生成一个计数器，当另外一个`shared_ptr`不用`share_ptr`赋值或者初始化的话，再次将一个裸指针赋值给另外一个`shared_ptr`时，又一次生成一个计数器，两个计数器不共享。

---

### `shared_ptr`实现原理：

`shared_ptr` 从 `_Ptr_base` 继承了 `element_type` 和 `_Ref_count_base` 类型的两个成员变量。
```cpp
template<class _Ty>class _Ptr_base
{ 
private: 
        element_type * _Ptr{
            ptr
        }; // 指向资源的指针 
        _Ref_count_base * _Rep{
            ptr
        }; // 指向资源引用计数的指针
};
```
`_Ref_count_base` 中定义了原子类型的变量 `_Uses` 和 `_Weaks`，它们分别记录资源的引用个数和资源观察者的个数。
```cpp
class __declspec(novtable) _Ref_count_base
{ 
    private:
         _Atomic_counter_t _Uses;//记录资源引用个数 
         _Atomic_counter_t _Weaks;//记录观察者个数
}
```

### 从 `this` 构造智能指针的正确做法
```cpp
class MyClass: enable_shared_from_this<MyClass>//必须继承enable_shared_from_this
{
public:
    shared_ptr<MyClass> getself()
    {
        return shared_from_this();
   }
};
```

`shared_from_this` 是 C++11 中引入的功能，允许对象在继承了 `std::enable_shared_from_this` 的情况下，安全地生成自身的 `std::shared_ptr` 实例，而不会创建新的控制块（reference counting block）。这样可以避免悬垂指针的问题，特别是在对象的成员函数中使用时，可以确保对象在使用期间不被销毁。

`std::enable_shared_from_this<T>` 内部维护了一个 `std::weak_ptr<T>`。当第一个 `std::shared_ptr<T>` 开始管理该对象时，这个 `weak_ptr` 被初始化。之后，当 `shared_from_this()` 被调用时，它将基于这个已经存在的 `weak_ptr` 返回一个新的 `std::shared_ptr<T>`，这个新的 `shared_ptr` 与原有的 `shared_ptr` 共享对对象的所有权。
```cpp
    shared_ptr<_Tp>
      shared_from_this()
      { return shared_ptr<_Tp>(this->_M_weak_this); }

      shared_ptr<const _Tp>
      shared_from_this() const
      { return shared_ptr<const _Tp>(this->_M_weak_this); }

      mutable weak_ptr<_Tp>  _M_weak_this;
```

---

**实践**：

实现这个 `TreeNode` 类的时候，`shared_from_this` 解析不出来(似乎是因为模板导致的 clangd 语法解析失败)。

![语法](/images/ERRORshared_from_this.png)

改为 `this->shared_from_this()` 后报错消失，因为 `shared_from_this` 实际上是当前父类 `enable_shared_from_this` 的成员函数。

最终实现：

```cpp
template <typename T>
class TreeNode : public std::enable_shared_from_this<TreeNode<T>> {
public:
    TreeNode(T value) : value(value) {}

    void setParent(std::weak_ptr<TreeNode<T>> parent) {
        this->parent = parent;
    }

    void addChild(std::shared_ptr<TreeNode<T>> child) {
        child->setParent(this->shared_from_this());
        children.push_back(child);
    }

    T getValue() const {
        return value;
    }

    std::shared_ptr<TreeNode<T>> getParent() const {
        return parent.lock();
    }

    const std::vector<std::shared_ptr<TreeNode<T>>>& getChildren() const {
        return children;
    }

private:
    T value;
    std::vector<std::shared_ptr<TreeNode<T>>> children;
    std::weak_ptr<TreeNode<T>> parent;
};
```