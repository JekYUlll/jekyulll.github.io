+++
date = '2026-08-30T10:10:16+08:00'
draft = false
title = 'std::expected：C++23 错误处理的新写法'
author = 'JekYUlll'
lastmod = '2026-08-30T10:10:16+08:00'
tags = ['cpp23', 'expected', 'error-handling', 'monadic']
categories = ['cpp']
+++

C++ 的错误处理长期在两个烂选项里选：异常和错误码。异常看不见，调用方不读文档根本不知道函数会抛什么；错误码可以被随手忽略，没人检查 errno 是常态。`std::expected` 想补上这个缺口：把"正常值或错误"直接写进返回类型，让失败成为类型的一部分，而不是靠自觉。

把失败写进类型，改变的不只是签名。代码评审时扫一眼函数声明，就知道哪些调用会失败、失败长什么样；想拿值就必须先过 `has_value()` 这道门。编译器不会强制你检查，但 API 形态在引导你这么做。

## 从提案到进标准：磨了八年

expected 的思路最早来自 Andrei Alexandrescu 在 2012 年 C++ and Beyond 上的演讲 Systematic Error Handling in C++。2014 年 N4015 第一次进入 WG21，之后 P0323 一路修订。它本来瞄着 C++20，结果 2021 年因为 `expected<void, E>` 的设计、和 variant 的交互、异常策略没谈拢，被移出草案。2022 年 2 月 P0323R12 终于合入 C++23，标准文本就是现在 ISO/IEC 14882:2024 的 [expected] 章节。

编译器跟进不算慢：GCC 12、Clang 16（配 libc++）就支持 `<expected>` 主体，monadic 操作（P2505R5）在 GCC 13、Clang 17 跟上。

## 异常、错误码、expected：三者的位置

P0323 里有一张对比表，把两种旧方案的毛病列得很清楚：

| | 异常 | 错误码 |
|---|---|---|
| 可见性 | 不读实现不知道会抛什么 | 看签名就知道，但可以无视 |
| 信息量 | 任意丰富（类型加调用栈） | 传统上就是一个 int |
| 正常流程 | 不被错误处理污染 | 每个调用点都要插 if |
| 通道占用 | 独立通道 | 独占返回值 |

expected 想站在两者中间：可见性来自返回类型，信息量来自任意类型的 E，正常流程靠 monadic 链保持干净，返回值通道既传值又传错。代价是显式，每一层都要表态。

## expected 是什么：要么值，要么错误

`std::expected<T, E>` 在类型层面表达"成功时是 T，失败时是 E"。它和 optional 的区别就在这里：optional 只能说"没有"，expected 能说"为什么没有"。错误值用 `std::unexpected` 构造：

```cpp
std::expected<int, std::string> ok{42};                  // 正常值
std::expected<int, std::string> err{std::unexpected("not found")};

err.has_value();    // false
err.error();        // "not found"
err.value_or(0);    // 0，失败时给默认值
// err.value();     // 抛 std::bad_expected_access
// *err;            // 未定义行为
```

`value()` 和 `operator*` 的行为不一样：前者失败时抛 `std::bad_expected_access`，后者失败时是未定义行为。拿到 expected 先想清楚走哪条路，我一般在"能接受异常"的代码里用 value()，在 noexcept 边界上用 has_value() 加分支。

expected 是纯值类型，不分配堆内存。实测（GCC 13，x86-64）：`expected<int,int>` 是 8 字节（int 4 字节加 1 字节判别位，对齐到 8），`expected<int,std::error_code>` 是 24 字节，`expected<std::string,int>` 是 40 字节。内存开销就是 T 和 E 中较大的那个加 1 字节判别位，再对齐取整，没有异常表，没有 RTTI，失败路径就是普通分支，编译器很容易优化。

## 一段最小代码：解析管线

真正好用的是 monadic 操作，也就是把"每步都可能失败"的操作串起来时，中间的成功失败自动传递。下面这个例子把字符串解析和范围校验串成一条链，任何一步失败，后面自动短路：

```cpp
enum class ParseErr { Empty, BadNumber, OutOfRange };

std::expected<int, ParseErr> parse_int(std::string_view s) {
    if (s.empty()) return std::unexpected(ParseErr::Empty);
    int v = 0;
    auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
    if (ec != std::errc{} || ptr != s.data() + s.size())
        return std::unexpected(ParseErr::BadNumber);
    return v;
}

std::expected<int, ParseErr> check_range(int v) {
    if (v < 0 || v > 100) return std::unexpected(ParseErr::OutOfRange);
    return v;
}

// 一步失败，整条链短路，错误类型一路保留
auto r = parse_int(s).and_then(check_range);
if (r.has_value()) { /* 用 *r */ }
```

我用 g++ -std=c++23 编译跑过："" 得到 Empty，"abc" 得到 BadNumber，"42" 通过，"200" 被 OutOfRange 拦下，"3.14" 因为 `from_chars` 只解析数字前缀被判定 BadNumber。每一层都不用写 if 检查上一步的成败，错误类型还不丢。手写等价逻辑得三层 if 嵌套，链式写法一行一个操作。

## and_then 之外还有三个

- `and_then(f)`：成功时把值交给 f（f 返回 expected），失败时原样传递。适合串联每步都可能失败的操作。
- `transform(f)`：成功时把值映射成新值（f 返回普通值），失败时原样传递。
- `transform_error(f)`：失败时改写错误，适合把底层的 errno 翻译成业务错误码。
- `or_else(f)`：失败时用 f 补救，可以返回一个兜底值。`parse_int("abc").or_else(返回 0)` 得到 0。

区分 `and_then` 和 `transform` 有个简单办法：回调还会失败（返回 expected）就用 and_then，只是变换值就用 transform。用错了编译器会直接报错，类型系统兜底，不会出现运行时才发现的问题。C++23 里 optional 也加了同一套接口（P0798R8），两个类型手感一致。

## 错误类型怎么选

E 可以是任意对象类型，但不能是引用。常见的三种选择：

- 枚举：错误种类少、比较方便，上面 demo 就是这种。够用就别升级。
- `std::error_code`：要跨模块、要兼容 errno 风格时用它，还能挂 category。
- 结构体：需要带上下文时用，比如 `struct { ParseErr code; std::string_view field; }`，调试时能直接看出是哪个字段出错，比裸枚举好查。

我见过最糟糕的用法是 E 直接上 `std::string`：错误信息倒是随便写，但每次传播都要复制字符串，比较还要逐字符，性能和维护都亏。错误类型的设计原则和返回值一样，够表达意图就行。

## 生产里谁在用

WebKit 的 URL 解析器是 P0323 论文自己引的案例：`parseIPv4Piece` 返回 `Expected<uint32_t, IPv4PieceParsingError>`，错误是一个枚举，直接塞进返回值。Chromium 在 C++17 时代就把 expected 移植进 base/ 了，因为他们整个构建不开异常，`bad_expected_access` 在那里直接变成进程终止，访问错误值宁可死掉也不留未定义行为。Chromium 的移植还专门加了 `base::ok`，补 `std::unexpected` 之外的成功侧构造。

LLVM 更早，自己造了 `llvm::Expected<T>`：包一个 T 或 Error，调用方用 `takeError()` 取错误，`if (!v) return v.takeError();` 是官方文档里的惯用形态。无异常构建的大项目基本都走了同一条路，std::expected 只是把这个模式标准化了。

## 什么时候别用 expected

它也有代价。每层调用都要显式传播，中间层不想处理也得写一行 return，链条长了确实啰嗦。T 的构造函数如果会抛异常，异常照样飞出去，expected 不接。性能上，成功路径多一个分支和一次判别位读写，但失败时没有异常展开的开销，特征稳定可预测，这本身对实时系统就是卖点。

迁移的起点也很明确：out 参数模式是 expected 最直接的替代对象。Chromium 的文档里给了现成例子，`bool ParseInt32(input, int32_t* output, ParseIntError* error)` 直接改成 `base::expected<int32_t, ParseIntError> ParseInt32(input)`，调用方的 if 结构几乎不用动。

我的判断：对外 API 边界（解析、I/O、协议）用 expected，把"为什么失败"留在类型里；真正不可恢复的路径（内存耗尽、逻辑不可能）留给异常或直接终止。Rust 的 Result 证明这套模式在无异常语言里能活得很舒服，C++ 现在总算有个标准版本。

## 参考

- P0323R12 std::expected（2022 年 1 月，合入 C++23 的版本）：https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2022/p0323r12.html
- cppreference std::expected：https://en.cppreference.com/w/cpp/utility/expected
- cppreference C++23 编译器支持表：https://en.cppreference.com/w/cpp/compiler_support/23
- P0798R8 Monadic operations for std::optional：https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2021/p0798r8.html
- LLVM Programmer's Manual（Error handling 一节）：https://llvm.org/docs/ProgrammersManual.html
- Chromium base::expected（C++17 移植版）：https://chromium.googlesource.com/chromium/src/+/main/base/types/expected.h
