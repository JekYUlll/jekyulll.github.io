+++
date = '2026-05-24T15:27:55+08:00'
draft = false
title = '[cpp] C++26 编译期反射（Reflection）来了：P2996 全解析'
author = 'JekYUlll'
lastmod = '2026-05-24T15:27:55+08:00'
tags = ['cpp']
categories = ['cpp']
+++

## 背景

C++ 的 RTTI（运行时类型信息）被诟病了二十年——慢、不透明、只支持多态类型。模板元编程虽然能做到编译期类型推断，但它的表达能力受限于模板语法：你无法在编译期遍历一个 struct 的成员，无法将一个 enum 自动转为字符串，无法动态查询一个类的布局。

这就是 Reflection（反射）要解决的问题。经过十年的提案迭代，C++26 即将引入基于 P2996 的**编译期反射**，核心贡献者包括 Wyatt Childers、Peter Dimov、Barry Revzin 和 Daveed Vandevoorde（C++ 模板元编程的奠基人之一）。

这不是运行时反射（像 Java/Python 那种），而是**纯编译期**的——所有反射操作在编译时求值，零运行时开销。编译器生成的反射信息通过新的 `<meta>` 头文件暴露给 constexpr 代码。

## 核心原理

### 两个新语法元素

C++26 反射引入两个核心操作：

1. **`^^` 反射运算符**（reflection operator）：获取类型的反射值
   ```cpp
   constexpr auto r = ^^int;   // r 的类型是 std::meta::info
   ```

2. **`[:  :]` 拼接运算符**（splicer）：将反射值拼回源代码
   ```cpp
   typename[:^^int:] x = 42;   // 等价于 int x = 42;
   ```

### 新类型和中转

```
             ^^                          [:]
源代码实体 ───────→ std::meta::info ───────────→ 代码实体
(type/func/enum)    (不透明 handle)       (代换回源码中)
```

`std::meta::info` 是一个不透明的 handle 类型。你不能直接"看"它的内部，只能通过 `<meta>` 头文件中的元函数来查询：

| 元函数 | 作用 |
|--------|------|
| `std::meta::members_of(r)` | 获取类型的所有成员（函数、变量、类型） |
| `std::meta::nonstatic_data_members_of(r)` | 仅获取非静态数据成员 |
| `std::meta::enumerators_of(r)` | 获取 enum 的所有枚举项 |
| `std::meta::type_of(r)` | 获取实体的类型 |
| `std::meta::identifier_of(r)` | 获取实体的名字（字符串） |
| `std::meta::size_of(r)` | 获取类型的大小 |
| `std::meta::dealias(r)` | 去掉类型别名 |
| `std::meta::reflect_constant(r)` | 获取枚举的值 |

### 枚举转字符串（最实用的例子）

```cpp
#include <meta>
#include <string>
#include <string_view>

enum class Color { Red, Green, Blue };

consteval std::string color_to_string(Color c) {
    std::string result;
    // 反射：获取 Color 的所有枚举项
    for (auto e : std::meta::enumerators_of(^^Color)) {
        auto val = std::meta::reflect_constant(e);    // e.g. 0, 1, 2
        auto name = std::meta::identifier_of(e);       // e.g. "Red"
        if (val == (int)c) {
            result = name;
            break;
        }
    }
    return result;
}

static_assert(color_to_string(Color::Red) == "Red");
static_assert(color_to_string(Color::Green) == "Green");
```

这里没有宏，没有运行时字符串对比，没有反射库——纯标准 C++26，全部编译期确定。`static_assert` 证明了这一点。

### Struct 成员反射（序列化的基础）

```cpp
struct Point {
    int x;
    int y;
    int z;
};

consteval auto member_names() {
    std::vector<std::string_view> names;
    for (auto m : nonstatic_data_members_of(^^Point)) {
        names.push_back(identifier_of(m));
    }
    return names;
}

static_assert(member_names().size() == 3);
// member_names() == {"x", "y", "z"}
```

这意味着自动序列化/反序列化可以用十几行通用代码实现，不再需要手写 `to_json`/`from_json` 或者依赖第三方宏库。

## 代码实战：一个通用的 struct-to-tuple 转换器

```cpp
#include <meta>
#include <tuple>
#include <type_traits>

template <typename T>
    requires std::is_aggregate_v<T>
constexpr auto struct_to_tuple(T&& obj) {
    constexpr auto members = nonstatic_data_members_of(^^std::remove_cvref_t<T>);
    // 注意：完整的实现需要展开成员（这里展示思路）
    // 每个成员的类型通过 type_of() 获取
    // 值通过拼接访问
    return [&]<std::size_t... I>(std::index_sequence<I...>) {
        return std::make_tuple(
            obj.[:members[I]:]...   // 拼接语法访问每个成员
        );
    }(std::make_index_sequence<members.size()>{});
}

// 使用
struct Config {
    int port;
    std::string host;
    bool enable_ssl;
};

auto cfg = Config{8080, "localhost", true};
auto tup = struct_to_tuple(cfg);
// tup 的类型是 std::tuple<int, std::string, bool>
// 可以结构化绑定: auto [port, host, ssl] = tup;
```

这段代码在 C++20 里需要几十行 SFINAE+ 递归元组生成，在 C++26 中只需要一个 constexpr 循环和拼接语法。

### 编译器支持状态

| 编译器 | 反射支持状态 |
|--------|-------------|
| EDG (Compiler Explorer) | ✅ 完整原型，可在线试用 |
| Clang (Bloomberg fork) | ✅ 开源版（github.com/bloomberg/clang-p2996） |
| GCC 16 | ⚠️ 部分支持进行中 |
| MSVC | ❌ 尚未开始 |

## 生态现状

C++26 反射的影响面很广：

| 领域 | 影响 |
|------|------|
| 序列化 | struct→JSON/Protobuf 自动生成，不再需要宏/代码生成器 |
| 日志/调试 | 自动打印任意 struct 字段名+值 |
| ORM | 从 struct 定义自动生成 SQL schema 映射 |
| 配置解析 | 命令行参数自动绑定到 struct 成员 |
| 测试框架 | 自动注册测试函数（不再需要手动枚举） |
| 枚举工具 | enum→string、string→enum、enum 值范围检查 |

Bloomberg 的 reflection 实现已经用在内部金融系统中——struct A 数据布局变化后，序列化代码**自动适配**，不再需要手写同步。

## 今日可执行动作

1. **在 Compiler Explorer 上试用**：打开 godbolt.org，选择 EDG 编译器（experimental），`-std=c++26` 标志，粘贴上面的 `color_to_string` 示例。亲眼确认 `static_assert` 通过——这是体验"纯编译期反射"最直接的方式。

2. **对比 C++20 反射方案**：写一个相同的 `color_to_string` 用 C++20 实现（宏版或魔术枚举库版），对比代码量和可维护性。C++20 最接近的方案也只能用宏 + `X_MACRO` 模式。

3. **Bloomberg 的 Clang fork**：`git clone https://github.com/bloomberg/clang-p2996`，按 README 构建。运行 `test/P2996/` 目录下的测试用例，理解 `substitute()` 和 `define_aggregate()` 等高级 API（可以用反射创建新的类型）。

## 参考

- [P2996R13: Reflection for C++26](https://wg21.link/p2996) — WG21 标准提案
- [Bloomberg clang-p2996](https://github.com/bloomberg/clang-p2996) — 开源 Clang 参考实现
- [Compiler Explorer EDG](https://godbolt.org/) — 选 EDG + -std=c++26 在线测试
- [Daveed Vandevoorde: C++ Reflection](https://www.youtube.com/watch?v=we8UbgHrJqs) — CppCon 2024 演讲
- [P3096R12: Function Reflection](https://wg21.link/p3096) — 函数反射扩展
- [P3293R3: constexpr reflected pointers](https://wg21.link/p3293) — constexpr 反射指针
