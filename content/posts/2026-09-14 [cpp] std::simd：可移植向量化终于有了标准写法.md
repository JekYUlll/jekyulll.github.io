+++
date = '2026-09-14T10:10:26+08:00'
draft = false
title = 'std::simd：可移植向量化终于有了标准写法'
author = 'JekYUlll'
lastmod = '2026-09-14T10:10:26+08:00'
tags = ['simd', 'cpp26', 'vectorization']
categories = ['cpp']
+++

写 SIMD 一直是个两难：手写 intrinsics 性能可控，但代码绑死在 x86 的 `_mm512_*` 上，换平台就得重写；交给编译器自动向量化，代码干净，但同一个循环换个编译器、换个优化等级，向量化与否说变就变。C++26 给出了第三条路：`std::simd` 把一组数一起算写成普通表达式，具体映射到多宽的硬件指令，交给工具链。

## P1928 从 Parallelism TS 2 走进 C++26

数据并行类型不是新东西。2018 年发布的 Parallelism TS 2 里就有 `basic_simd<T>` 的完整定义（N4808 第 9 章），GCC 从 11 起把它作为 `<experimental/simd>` 随编译器发布。之后六年，Matthias Kretz 根据实现反馈反复修设计，2024 年 11 月的 Wrocław 会议上，P1928R15 被接收进 C++26，任务是把这一章并入工作草案。

命名兜了一圈。草案一度把类型放进 `std::datapar` 命名空间，2025 年 6 月的 Sofia 会议投票改回 `std::simd`，顺手把 `basic_simd` / `basic_simd_mask` 改成 `basic_vec` / `basic_mask`，别名 `vec` / `mask`（P3691R1）。2026 年 3 月 Croydon 会议还有 P4012 这类小修在收尾。最终形状在 [simd] 一节：`simd::vec<T, N>` 是 N 个元素的向量，N 缺省取目标平台的原生宽度，`simd::mask<T, N>` 是配套掩码。

TS 迁移过来的人要留意几处改名：`where()` 掩码赋值被删掉（改用 `select`），`hmin` / `hmax` 变成 `reduce_min` / `reduce_max`，载入载出的默认 flags 定为 `element_aligned`。这些改动在 P1928 的历次修订记录里都有出处。

## 接口：广播、掩码、归约

整套接口读起来就像标量代码：构造、运算、选择、归约全是普通函数和运算符：

```cpp
#include <simd>
namespace simd = std::simd;

constexpr simd::vec<int> a = 1;                            // 广播：所有通道都是 1
constexpr simd::vec<int> b([](int i) { return i - 2; });   // 生成器：按通道下标产出
constexpr auto c = a + b;                                  // 逐元素加
constexpr auto d = simd::select(c < 0, -c, c);             // 逐元素绝对值
constexpr int s = simd::reduce(d * d);                     // 归约成一个标量
```

比较产生掩码，`simd::select` 按掩码逐元素取值，TS 时代的 `where()` 掩码赋值已经删掉。载入载出用 `unchecked_load` / `partial_store` 这类自由函数，默认按元素对齐，要更严的对齐或载入时转换就加 flags。掩码上还挂着一组归约：`reduce_count` 数出多少个通道命中，`reduce_min_index` 找第一个命中的通道；`<cmath>` 里的 `sin`、`pow` 都有按元素重载，写数值代码基本不用离开这套类型。宽度也能写死，`simd::vec<double, 16>` 就是固定 16 个元素，比 TS 那套 ABI 标签直观得多。

后面长出来的还有 `compress` / `expand` 这类排列操作，那是自动向量化器根本不会替你生成的东西。

这段需要带 `<simd>` 的工具链才能编。截至 2026 年 9 月，GCC 16 起有实验性实现；libc++ 的状态表里 P1928 一栏还是空的，从 Wrocław 到 Croydon 的一串 LWG 修订也还挂着；MSVC 的一致性表格里搜不到它。跨编译器的现实选择还是 TS 版本。

## 今天就能跑的 `<experimental/simd>`

GCC 11 起就带 Parallelism TS 2 的实现，两种写法的对应关系很直接：`native_simd<T>` 对应 `simd::vec<T>`，`simd_mask` 对应 `simd::mask`，`where(cond, v) = x` 的掩码赋值换成 `simd::select`，指针加 `element_aligned` 的载入构造换成 `unchecked_load`。下面这段点积就是用它写的（本机 g++ 14 编译运行通过），也是我这次跑分用的内核：

```cpp
#include <experimental/simd>
#include <cstddef>
namespace stdx = std::experimental;

float dot(const float* a, const float* b, std::size_t n) {
    using V = stdx::native_simd<float>;          // C++26 写法：std::simd::vec<float>
    V acc(0.f);
    std::size_t i = 0;
    for (; i + V::size() <= n; i += V::size()) {
        V va(a + i, stdx::element_aligned);
        V vb(b + i, stdx::element_aligned);
        acc += va * vb;                          // 每个通道一个乘加
    }
    float s = stdx::reduce(acc);                 // 这里显式声明：求和允许重排
    for (; i < n; ++i) s += a[i] * b[i];         // 标量收尾
    return s;
}
```

`stdx::reduce` 那一行是题眼。它和标量循环里 `s += ...` 的语义差别，就是后面那一大截性能差距的来源。

## 实测：自动向量化干不了的那一半

测试机是 Ryzen 7 8845H（Zen 4，AVX-512BW），g++ 14。两个内核：u8 亮度提升 `y = min(255, x + k)`，256 KiB 数据；以及上面那段 f32 点积，128 KiB 数据。都小到能驻留 L2，每个配置重复几千次取平均，并用 asm 屏障防止优化器把循环不变的计算提出去。单位纳秒每元素，越小越好：

| 编译选项 | u8 标量 | u8 `std::simd` | 点积标量 | 点积 `std::simd` |
|---|---:|---:|---:|---:|
| `-O2` | 0.48 | 0.023 | 0.70 | 0.17 |
| `-O3` | 0.076 | 0.021 | 0.69 | 0.18 |
| `-O3 -march=native` | 0.019 | 0.019 | 0.69 | 0.062 |
| 加 `-ffast-math` | 0.020 | 0.019 | 0.062 | 0.065 |

先看 u8 那两组。`-O3 -march=native` 下自动向量化和显式写作打平，都是 0.019，约 100 GB/s，基本就是这个 L2 的带宽上限；GCC 14 把先加后饱和的模式扩宽到 32 位再压回来算，和显式版本殊途同归。规则的元素级循环，编译器真能干得不错。但在 `-O2` 和 `-O3` 下，显式版本快 3 到 20 倍：默认开关靠不住，离了 `-march` 就是另一回事。

点积完全是另一个故事。标量版稳稳停在 0.69，显式版 0.062，差 11 倍。根因是浮点加法的顺序语义：没有 `-ffast-math` 时编译器不允许重排你的求和，它只能把乘法向量化，然后把 16 个通道的积逐个抽出来，串行加回累加器，汇编里能看到一串互相依赖的 `vaddss`。而 `stdx::reduce` 是把允许重排写进了源码，编译器直接上 FMA，一次吞吐 16 个。

把 `-ffast-math` 打开，标量版也跑到 0.062，差距消失。这证明差的不是代码生成质量，而是一份**重排许可**。但 `-ffast-math` 是全程序开关，对你所有浮点代码生效；`std::simd` 把这份许可限制在你写的那几个循环里，别的代码不受影响。

换个角度看两列点积：显式版在四种配置下都落在 0.062 到 0.18 之间，标量版则从 0.70 一路被 `-ffast-math` 拽到 0.062，跨了一个数量级。收窄这种随编译选项漂移的不确定性，本身就是显式 SIMD 的一种收益。

顺带一个坑：GCC 14 的 `<experimental/simd>` 没实现 `add_saturated`，饱和加法得自己写。把 `y = min(255, x + k)` 改写成 `x + min(k, 255 - x)`，加法部分永远不超过 255，全程留在 u8 域，不需要扩宽。这个版本和自动向量化的扩宽路线速度一样，好处是写法直白，不依赖实现有没有饱和指令。

## 什么时候该上显式 SIMD

三个信号：循环里有语义上不允许的优化（浮点归约顺序、条件运算）；同一份代码要在多个编译器上保持一致的性能行为，不能赌每个版本向量化器的脾气；你要用的操作没有标量对应物（掩码载入、`compress`、置换）。反过来，规则的元素级运算先交给自动向量化，测完不满意再上显式版本。本文的两个内核刚好是两类：u8 那类规则循环，先写标量让编译器跑；点积这种带浮点归约的，直接上 `std::simd`，顺带把 `-ffast-math` 这类全程序开关收回去。

`std::simd` 也替代不了所有 intrinsics 的角落，但和它相比，区别在于把这份代码要并行地算 16 个数的意图从平台的汇编方言里拿出来，变成语言的一部分。等 `<simd>` 的工具链成熟，今天写的 `<experimental/simd>` 代码改改名字就能迁过去，比从 intrinsics 重写便宜得多。

## 参考

- P1928R15: std::simd, merge data-parallel types from the Parallelism TS 2，https://wg21.link/p1928r15
- P3691R1: Reconsider naming of the namespace for std::simd，https://wg21.link/p3691r1
- cppreference: Data-parallel types (SIMD)，https://en.cppreference.com/w/cpp/numeric/simd
- cppreference: SIMD library（TS），https://en.cppreference.com/w/cpp/experimental/simd
- GCC 16 Release Series Changes，https://gcc.gnu.org/gcc-16/changes.html
- libc++ C++26 Status，https://libcxx.llvm.org/Status/Cxx26.html
- std-simd README（TS 实现说明），https://github.com/VcDevel/std-simd
- eel.is working draft, [simd]，https://eel.is/c++draft/simd
