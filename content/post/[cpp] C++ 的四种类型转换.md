+++
date = '2024-09-28T18:05:47+08:00'
draft = false
title = 'C++ 的四种类型转换'
author = 'JekYUlll'
lastmod = '2025-02-28T18:05:47+08:00'
tags = ['cpp']
categories = ['cpp']
+++

思考了一下`reinterpret_cast`和强转的区别？这段非常易懂：  
C 语言的类型转换实际上包含各种转换方式，是 `static_cast` 跟 `reinterpret_cast` 等的父操作。  
- 一类是从逻辑意义上读取原有的值，然后到新的变量类型生成一个新值。（可以称为**显式类型转换**，简称显转）  
- 一类是完全保持原有值的内存表达方式，用新的变量类型来解读这段内存区域。（可以称为**强制类型转换**，简称强转）  
这两个用法实际的动作完全不同，但在 C 语言中是同一种写法。所以到了C++，就把前一种写法写成 `static_cast`，后一种写法写成 `reinterpret_cast`。  
`reinterpret_cast` 仅作用于编译时，可以保证不改变内存区域的内容。

- dynamic_cast：这是 C 里面不存在的转型方式，用来在带有虚函数的“动态对象”继承树里进行指针或引用的类型转换。比如，假设我们有对象基类 `Shape` 和派生类 `Circle` 和 `Rectangle`：如果有 `Shape` 指针 `ptr`，我们可以使用 `dynamic_cast<Circle*>(ptr)` 尝试把它转型成 `Circle*`。系统会进行需要的类型检查，并在转型成功时返回一个非空指针，返回空指针则表示失败（如当 `ptr` 实际指向的不是 `Circle`，而是 `Rectangle`）。
- static_cast：这是一种在很多认为较安全的场景下的“静态”转型方式。你可以使用它在不同的数值类型之间进行转换，如从 `long` 到 `int`，或者从 `long long` 到 `double`——当转换有可能有精度损失时，就不能使用隐式类型转换，而得明确使用转型了。你也可以使用它把一个 `void*` 转成一个实际类型的指针（如 `int*`）。你还可以用它把基类的指针转成派生类的指针，前提条件是你能确认这个基类的指针确实指向一个派生类的对象。显然，对于这最后一种场景 static_cast 不如 dynamic_cast 安全，但由于不需要进行运行期的检查，它的性能比 `dynamic_cast` 要高，在很多情况下是个空操作。
- const_cast：这种转型方式潜在就不那么安全了。它的目的是去掉一个指针或引用类型的 `const` 或 `volatile` 修饰，如从 `const char*` 转换到 `char*`。这种转型的一种常见用途是把一个 C++ 的指针传递到一个 `const` 不正确的 C 接口里去，比如 C 接口在该用 `const char*` 时使用了 `char*`。注意这种转型只是为了“欺骗”类型系统，让代码能通过编译。如果你通过 `const_cast` 操作指针或引用去修改一个 `const` 对象，这仍然是错误的，是未定义行为，可能会导致奇怪的意外结果。
- reinterpret_cast：这是最不安全的对数据进行“重新解释”的转型方式，用来在不相关的类型之间进行类型转换，如把指针转换成 `uintptr_t`。这种转换有可能得到错误的结果，比如，在存在多继承的情况下，如要把基类指针转成派生类指针，使用 `static_cast` 和使用 `reinterpret_cast` 可能会得到不同的结果：前者会进行偏移量的调整，而后者真的只是简单粗暴的硬转而已，因此结果通常是错的。又如，根据 C++ 的严格别名规则，**如果你用 `char` 或 `byte` 之外类型的指针访问并非该类型的对象（如通过 `int*` 访问 `double` 对象），会导致未定义行为**。

`dynamic_cast` 和 `static_cast` 都能用于继承的情况下，比较容易混淆：

### **`dynamic_cast`**

仅适用于多态类型（即具有虚函数的类）的转换。

**用途**  
   - 用于类继承层次间的**安全向下转型**（从基类指针/引用转换为派生类指针/引用）。
   - 支持**交叉转换**（同一继承体系中不同分支的类之间的转换，如兄弟类转换）。
   - 运行时检查类型安全性，若转换失败：
     - 对指针返回`nullptr`；
     - 对引用抛出`std::bad_cast`异常。

   ```cpp
   class Base { virtual void foo() {} };
   class Derived : public Base {};

   Base* pb = new Derived;
   Derived* pd = dynamic_cast<Derived*>(pb); // 安全转换，返回有效指针

   Base* pb2 = new Base;
   Derived* pd2 = dynamic_cast<Derived*>(pb2); // 失败，返回nullptr
   ```

---

### **`static_cast`**

**用途**  
   - **非多态类型转换**：如基本数据类型转换（`int`→`double`）。
   - **上行转换**（派生类→基类），效果与隐式转换相同。
   - 显式强制转换（如`void*`→具体类型指针）。
   - 不进行运行时检查。

   - 向下转型时若实际对象类型不匹配，可能导致未定义行为（如访问非法内存）。
   - 不支持交叉转换（编译报错）。

   ```cpp
   int a = 5;
   double b = static_cast<double>(a); // 基本类型转换

   Base* base_ptr = new Derived;
   Derived* derived_ptr = static_cast<Derived*>(base_ptr); // 不安全，假设base_ptr实际指向Derived对象
   ```

| **特性**         | **`dynamic_cast`**                      | **`static_cast`**                  |
| ---------------- | --------------------------------------- | ---------------------------------- |
| **安全性**       | 运行时类型检查，失败返回`nullptr`或异常 | 无运行时检查，依赖程序员判断       |
| **适用场景**     | 多态类的向下转型、交叉转换              | 非多态转换、上行转换、基本类型转换 |
| **虚函数要求**   | 必须存在虚函数（RTTI依赖）              | 无要求                             |
| **性能开销**     | 较高（运行时类型查询）                  | 无额外开销（编译时完成）           |
| **转换失败处理** | 指针返回`nullptr`，引用抛出异常         | 未定义行为（可能崩溃或数据损坏）   |

1. **优先使用`static_cast`的场景**  
   - 类型转换明确安全（如上行转换或数值类型转换）。
   - 需要高性能且能确保转换正确性时（如游戏开发中的内联优化）。

2. **必须使用`dynamic_cast`的场景**  
   - 多态类的向下转型，尤其是无法确定基类指针实际指向的对象类型时。
   - 需要避免因类型错误导致程序崩溃（如框架中动态加载插件）。

- **`const_cast`与`reinterpret_cast`**：  
  - `const_cast`用于移除或添加`const`限定符。
  - `reinterpret_cast`用于无关类型之间的危险转换（如指针→整数），慎用。
