
[现代C++的内存模型](https://zhuanlan.zhihu.com/p/382372072)。--神文  
[自底向上理解memory_order](https://zhuanlan.zhihu.com/p/682286231)。  
[大白话C++之：一文搞懂C++多线程内存模型(Memory Order)](https://blog.csdn.net/sinat_38293503/article/details/134612152)。

> *时钟周期*也称为*振荡周期*，定义为时钟频率的倒数。时钟周期是计算机中最基本的、最小的时间单位。在一个时钟周期内，CPU仅完成一个最基本的动作。时钟周期表示了*SDRAM*所能运行的最高频率。

> 如果没有Cache，CPU每执行一条指令，都要去内存取下一条，而执行一条指令也就几个时钟周期（几ns），而取指令却要上百个时钟周期，这将导致CPU大部分时间都在等待状态，进而导致执行效率低下。

> C++ 内存模型（Memory Model）定义了程序在多线程环境中如何访问和共享内存，它为程序的正确性、并发性和可移植性提供了保证。C++ 内存模型主要通过原子操作、内存序列（Memory Ordering）、同步和锁等机制来规范线程之间的内存访问行为。

---

# CPU 的五级流水线

> CPU 将指令执行分解成5个部分，分别是：IF 取指令，ID 译码，EX 执行，MEM 访问内存，WB 写回。

| 内存顺序模型         | 描述                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------- |
| memory_order_seq_cst | 顺序一致(sequentially consistent ordering)，只有该值满足sC顺序一致性，原子操作默认使用该值。 |
| memory_order_relaxed | 松散(relaxed ordering)                                                                       |
| memory_order_consume | 获取发布(acquire-release ordering)                                                           |
| memory_order_acquire | 获取发布(acquire-release ordering)                                                           |
| memory_order_release | 获取发布(acquire-release ordering)                                                           |
| memory_order_acq_rel | 获取发布(acquire-release ordering)                                                           |

与编译器优化有关：

```cpp
//reordering 重排示例代码
int A = 0, B = 0;
void foo()
{
    A = B + 1;  //(1)
    B = 1;      //(2)
}
```
```asm
// g++ -std=c++11 -O2 -S test.cpp
// 编译器重排后的代码
// 注意第一句汇编，已经将B最初的值存到了
// 寄存器eax，而后将该eax的值加1，再赋给A
movl  B(%rip), %eax
movl  $1, B(%rip)          // B = 1
addl  $1, %eax             // A = B + 1
movl  %eax, A(%rip)
```

```cpp
// Invention示例代码
// 原始代码
if( cond ) x = 42;

// 优化后代码
r1 = x;// read what's there
x = 42;// oops: optimistic write is not conditional
if( !cond)// check if we guessed wrong
    x = r1;// oops: back-out write is not SC
```

对于内存读写来说，读写顺序需要严格按照代码顺序，即要求如下（符号`<p`表示程序代码顺序，符号`<m`表示内存的读写顺序）：

```cpp
// 顺序一致的要求
/* Load→Load */
/*若按代码顺序，a变量的读取先于b变量，
则内存顺序也需要先读a再读b
后面的规则同理。*/
if L(a) <p L(b) ⇒ L(a) <m L(b)

/* Load→Store */
if L(a) <p S(b) ⇒ L(a) <m S(b)

/* Store→Store */
if S(a) <p S(b) ⇒ S(a) <m S(b)

 /* Store→Load */
if S(a) <p L(b) ⇒ S(a) <m L(b)
```

顺序一致这么严格，其显然会限制编译器和CPU的优化，所以业界提出了很多宽松的模型，例如在X86中使用的TSO（Total Store Order）便允许某些条件下的重排。

`memory_order_acquire`：对于使用该枚举值的load操作，不允许该load之后的操作重排到load之前。  
`memory_order_release`：使用该枚举值的store操作，不允许store之前的操作重排到store之后。

> 现代C++（包括Java）都是使用了SC-DRF(Sequential consistency for data race free)。在SC-DRF模型下，程序员只要不写出Race Condition的代码，编译器和CPU便能保证程序的执行结果与顺序一致相同。因而，内存模型就如同程序员与编译器/CPU之间的契约，需要彼此遵守承诺。C++的内存模型默认为SC-DRF，此外还支持更宽松的非SC-DRF的模型。

> C++内存模型借鉴lock/unlock，引入了两个等效的概念，Acquire（类似lock）和Release（类似unlock），这两个都是单方向的屏障（One-way Barriers: acquire barrier, release barrier）。
