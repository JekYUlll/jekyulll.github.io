
[C++的POD以及如何判断是否POD - cheeto的文章 - 知乎](https://zhuanlan.zhihu.com/p/17003601237)。

---

在C++11及以后的版本中，POD类型（Plain Old Data）的定义被细化为两个核心概念：  
**平凡类型**（Trivial Type）和**标准布局类型**（Standard Layout Type）。当类型为Trivial && Standard Layout时才能被认为是POD。

### 平凡类型（Trivial Type）

满足以下条件：

- 默认构造函数：没有用户定义的构造函数，即使用默认构造函数。
- 默认拷贝构造函数：没有用户定义的拷贝构造函数。
- 默认析构函数：没有用户定义的析构函数。
- 默认赋值操作符：没有用户定义的拷贝赋值和移动赋值操作符。

对于平凡类型，编译器会为其提供默认的构造、拷贝和析构行为，无需用户显式定义。

比如说以下`Trivial`，即使它有构造函数和析构函数 只要不是用户自定义而是`default`也可以
```cpp
struct Trivial {
 int a;
 Trivial() = default;  // 默认构造函数
 ~Trivial() = default; // 默认析构函数
};
```

### 标准布局类型（Standard Layout Type）

满足以下条件：

- 无虚函数：它没有虚函数。
- 无虚基类：它没有虚基类。
- 成员变量顺序：它的成员变量是按声明顺序排列的。
- 
直接用`std::is_standard_layout_v`判断即可
```cpp
#define Print(x) std::cout << x << '\n'
struct safe {
 int m;
};
struct unsafe_cons {
 unsafe_cons(unsafe_cons const &) {}
};
struct unsafe_vir {
 virtual void foo();
};
template <typename T> class unsafe_tem {
 T data;
};
struct Trivial {
 int a;
 Trivial() = default;  // 默认构造函数
 ~Trivial() = default; // 默认析构函数
};
struct StandardLayout {
 char a; // 1 byte
 int b;  // 4 bytes
};

// 用于检查是否为 POD 类型
template <typename T> struct is_pod {
 static constexpr bool value =
 std::is_trivial<T>::value && std::is_standard_layout<T>::value &&
 std::is_trivially_default_constructible<T>::value;
};
void test1() {
 Print(is_pod<int>::value);                           // 1
 Print(is_pod<std::string>::value);                   // 0
 Print(is_pod<Trivial>::value);                       // 1
 Print(is_pod<StandardLayout>::value);             // 1
 Print(std::is_trivial<Trivial>::value);              // 1
 Print(std::is_trivial<StandardLayout>::value);    // 1
 Print(std::is_standard_layout_v<StandardLayout>); // 1
}
```

这里的标准布局的判定反而没有这么严格

我说的严格指的是
```cpp
struct A{ 
    char a;  // 1 byte
    int b;   // 4 bytes
};
和
struct B{ 
 int b;   // 4 bytes
    char a;  // 1 byte
 
};
```
这种

`struct A` 会在 `char` 后插入填充字节，以满足 `int` 的对齐要求。
`struct B` 的内存布局使得结构体末尾需要填充字节，确保结构体的总大小满足 4 字节对齐要求。

### 小结

也就是说填充不影响POD的判定 而是成员变量顺序发生了改变才不算POD。

POD 类型的定义主要关注**是否有特殊的构造、析构或拷贝操作**，以及**成员变量的顺序是否保持一致**。

**如何判断是否POD**
```cpp
// 用于检查是否为 POD 类型
// 使用例is_pod<int>::value
template <typename T> struct is_pod {
 static constexpr bool value =
 std::is_trivial<T>::value && std::is_standard_layout<T>::value &&
 std::is_trivially_default_constructible<T>::value;
};
```

---

### 拓展

对于平凡类型的 `class` 和 `struct`，它们在内存布局、对象拷贝、传递给 C 函数等操作中，几乎没有区别。因此，可以像使用 C 语言中的结构体一样使用它们。
```cpp
struct Point {
    int x, y;
};

int main() {
    Point p = {1, 2};
    // 使用 reinterpret_cast 强制转换
    void* ptr = &p;  // void* 指针指向结构体
    // 使用 reinterpret_cast 强制转换为 Point* 类型
    Point* p2 = reinterpret_cast<Point*>(ptr);
    // 访问成员
    std::cout << "x: " << p2->x << ", y: " << p2->y << std::endl;
    return 0;
}
```
