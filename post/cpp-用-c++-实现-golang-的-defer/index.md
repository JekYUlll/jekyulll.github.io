
利用 RAII 和 C++ 的析构函数。

```cpp
#include <iostream>
#include <functional>
#include <vector>

class Defer {
public:
    explicit Defer(std::function<void()> func) : func_(std::move(func)), active_(true) {}

    // 禁止复制
    Defer(const Defer&) = delete;
    Defer& operator=(const Defer&) = delete;

    // 允许移动
    Defer(Defer&& other) noexcept
        : func_(std::move(other.func_)), active_(other.active_) {
        other.active_ = false;
    }

    // 析构函数中调用defer的函数
    ~Defer() {
        if (active_ && func_) {
            func_();
        }
    }

    void cancel() {
        active_ = false;
    }

private:
    std::function<void()> func_;
    bool active_;
};

#define CONCAT_IMPL(x, y) x##y
#define CONCAT(x, y) CONCAT_IMPL(x, y)
#define defer(func) Defer CONCAT(_defer_, __LINE__)(func)

int main() {
    std::cout << "Start of main function" << std::endl;

    defer([]() {
        std::cout << "Deferred action 1" << std::endl;
    });

    {
        defer([]() {
            std::cout << "Deferred action in scope" << std::endl;
        });

        std::cout << "Inside scope" << std::endl;
    }

    defer([]() {
        std::cout << "Deferred action 2" << std::endl;
    });

    std::cout << "End of main function" << std::endl;
    return 0;
}
```

这些宏的目的是为 `defer` 提供一种易用的语法，同时确保每次使用 `defer` 都会创建一个唯一的变量名，从而避免变量名冲突。

1. `CONCAT_IMPL(x, y)` 和 `CONCAT(x, y)`

```cpp
#define CONCAT_IMPL(x, y) x##y
#define CONCAT(x, y) CONCAT_IMPL(x, y)
```

- **`x##y`** 是 C++ 预处理器的标记连接运算符，将 `x` 和 `y` 拼接成一个标识符。
- `CONCAT_IMPL` 是底层的宏，用于直接连接标识符。
- `CONCAT` 是一个包装宏，它确保在预处理器展开过程中，所有参数被正确解析后再拼接。

例如：
```cpp
CONCAT(foo, bar) // 展开为 foobar
```

2. `defer(func)`

```cpp
#define defer(func) Defer CONCAT(_defer_, __LINE__)(func)
```

- `__LINE__` 是预处理器的内置宏，表示当前代码所在的行号。
- `CONCAT(_defer_, __LINE__)` 会将 `_defer_` 和当前行号拼接，生成一个唯一的变量名。

例如：
```cpp
defer([]() { std::cout << "Cleanup!" << std::endl; });
```
假设这段代码位于第 25 行，则展开后为：
```cpp
Defer _defer_25([]() { std::cout << "Cleanup!" << std::endl; });
```

这样每次调用 `defer` 时生成的变量名都唯一，避免了同一作用域中多个 `defer` 语句导致变量名冲突的问题。