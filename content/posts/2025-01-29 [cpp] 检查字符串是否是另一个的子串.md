+++
date = '2025-01-29T12:05:47+08:00'
draft = false
title = '检查字符串是否是另一个的子串'
author = 'JekYUlll'
lastmod = '2025-01-29T12:05:47+08:00'
tags = ['cpp','algorithm']
categories = ['cpp']
+++

## 常见 C/C++ API

1. **`std::string`的 `string::find` 成员函数**
   ```cpp
   #include <string>
   using namespace std;

   bool isSubstring(const string& mainStr, const string& subStr) {
       return mainStr.find(subStr) != string::npos;
   }
   ```
   > 大多数标准库的 `strstr`（如Glibc）和 `string::find`（如MSVC、libc++）已针对子串搜索优化。  
   > 实现中可能直接调用 `memmem` 或 `strstr`，性能与 `strstr` 相当。


2. **C标准库的 `strstr` 函数**
   ```cpp
   #include <cstring>
   bool isSubstring(const string& mainStr, const string& subStr) {
       return strstr(mainStr.c_str(), subStr.c_str()) != nullptr;
   }
   ```
   需要将 `std::string` 转换为C风格字符串，可能引入额外开销。
   > Glibc的 `strstr` 使用Two-Way算法，适合长文本和模式。时间复杂度接近`O(n)`。


3. **STL `std::search`**
   ```cpp
   #include <algorithm>
   #include <string>
   bool isSubstring(const string& mainStr, const string& subStr) {
       return std::search(
           mainStr.begin(), mainStr.end(),
           subStr.begin(), subStr.end()
       ) != mainStr.end();
   }
   ```

---

## 算法

### **1. 暴力法（Brute Force）**
```cpp
#include <string>

bool isSubstringBruteForce(const std::string& mainStr, const std::string& subStr) {
    if (subStr.empty()) return true; // 空子串是任何字符串的子串
    int m = mainStr.length(), n = subStr.length();
    if (m < n) return false;

    for (int i = 0; i <= m - n; ++i) {
        int j;
        for (j = 0; j < n; ++j) {
            if (mainStr[i + j] != subStr[j]) break;
        }
        if (j == n) return true; // 完全匹配
    }
    return false;
}
```
- **时间复杂度**：最坏情况为 \(O(m \times n)\)（如主串为`AAAAAAB`，子串为`AAAB`）。
- **空间复杂度**：\(O(1)\)。

---

### **2. KMP算法（Knuth-Morris-Pratt）**

通过预处理子串生成部分匹配表（Longest Prefix Suffix, LPS），利用已匹配的信息跳过不必要的比较。

1. **构建部分匹配表（LPS）**：
   - 计算子串每个位置的最长相等前缀和后缀的长度。
2. **双指针匹配**：
   - 主串指针`i`和子串指针`j`同时移动，匹配失败时根据LPS表回退`j`。

```cpp
#include <string>
#include <vector>

// 预处理LPS表
std::vector<int> computeLPS(const std::string& subStr) {
    int n = subStr.length();
    std::vector<int> lps(n, 0);
    int len = 0; // 当前最长前缀后缀长度
    for (int i = 1; i < n;) {
        if (subStr[i] == subStr[len]) {
            lps[i++] = ++len;
        } else {
            if (len != 0) len = lps[len - 1];
            else lps[i++] = 0;
        }
    }
    return lps;
}

// KMP匹配
bool isSubstringKMP(const std::string& mainStr, const std::string& subStr) {
    if (subStr.empty()) return true;
    int m = mainStr.length(), n = subStr.length();
    if (m < n) return false;

    std::vector<int> lps = computeLPS(subStr);
    int i = 0, j = 0; // i:主串指针, j:子串指针

    while (i < m) {
        if (mainStr[i] == subStr[j]) {
            i++;
            j++;
            if (j == n) return true; // 完全匹配
        } else {
            if (j != 0) j = lps[j - 1]; // 回退j
            else i++; // 无法回退，移动i
        }
    }
    return false;
}
```

- **时间复杂度**：\(O(m + n)\)，预处理LPS表 \(O(n)\)，匹配过程 \(O(m)\)。
- **空间复杂度**：\(O(n)\)（存储LPS表）。

适合处理长文本或频繁匹配同一子串。

---

### **3. Sunday算法**

利用坏字符规则，根据主字符串中当前匹配窗口后的第一个字符决定跳跃步长。

1. **预处理偏移表**：
   - 记录子串中每个字符最后出现的位置距末尾的距离。
2. **匹配与跳跃**：
   - 匹配失败时，根据主字符串中下一个字符的位置跳跃。

```cpp
#include <string>
#include <unordered_map>

bool isSubstringSunday(const std::string& mainStr, const std::string& subStr) {
    if (subStr.empty()) return true;
    int m = mainStr.length(), n = subStr.length();
    if (m < n) return false;

    // 预处理偏移表：字符到跳跃步长的映射
    std::unordered_map<char, int> shift;
    for (int i = 0; i < n; ++i) {
        shift[subStr[i]] = n - i; // 字符最后出现的位置距末尾的距离
    }

    int i = 0;
    while (i <= m - n) {
        bool match = true;
        for (int j = 0; j < n; ++j) {
            if (mainStr[i + j] != subStr[j]) {
                match = false;
                break;
            }
        }
        if (match) return true;

        // 计算跳跃步长
        char nextChar = (i + n < m) ? mainStr[i + n] : 0;
        int step = (shift.find(nextChar) != shift.end()) ? shift[nextChar] : n + 1;
        i += step;
    }
    return false;
}
```

- **时间复杂度**：平均 \(O(m)\)，最坏 \(O(m \times n)\)。
- **空间复杂度**：\(O(k)\)（k为字符集大小）。

适合字符分布不均匀的场景（如英文文本）。

---

| 算法   | 时间复杂度        | 空间复杂度 | 适用场景                     |
| ------ | ----------------- | ---------- | ---------------------------- |
| 暴力法 | \(O(m \times n)\) | \(O(1)\)   | 短文本、简单场景             |
| KMP    | \(O(m + n)\)      | \(O(n)\)   | 长文本、需频繁匹配同一子串   |
| Sunday | 平均 \(O(m)\)     | \(O(k)\)   | 字符分布不均匀（如自然语言） |


