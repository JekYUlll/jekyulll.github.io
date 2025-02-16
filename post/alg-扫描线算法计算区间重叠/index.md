
题目来源：[Marscode](https://www.marscode.cn/practice/jn1075jrp7j47d?problem_id=7424418560930611244)。

> 小C和小U有一个从`0`开始的数组`nums`，以及一个非负整数`k`。每次操作中，小C可以选择一个尚未选择的下标`i`（范围在 `[0, nums.length - 1]`），然后将`nums[i]`替换为`[nums[i] - k, nums[i] + k]`之间的任意整数（包含边界）。  
> 在应用任意次数的操作后，返回数组`nums`可能达到的最大分数。数组的分数被定义为数组中最多重复的元素个数。注意，每个下标只能被操作一次。

---

### 暴力解（超时） `O(n²k)`

```cpp
int solution(vector<int>& nums, int k) {
    int n = nums.size();
    int maxCount = 1;  // 至少有一个数
    
    // 遍历每个数作为可能的目标值
    for (int i = 0; i < n; i++) {
        // 以nums[i]为中心，考虑范围[nums[i]-k, nums[i]+k]内的所有可能值
        for (int target = nums[i]-k; target <= nums[i]+k; target++) {
            int count = 0;
            // 检查每个位置的数是否能变成target
            for (int j = 0; j < n; j++) {
                if (abs(nums[j] - target) <= k) {
                    count++;
                }
            }
            maxCount = max(maxCount, count);
        }
    }
    
    return maxCount;
}
```

### 优化暴力解 `O(n²)`

```cpp
int solution(vector<int>& nums, int k) {
    int n = nums.size();
    int maxCount = 1;
    
    // 只需要考虑将某些数变成数组中已有的数
    for (int i = 0; i < n; i++) {
        int target = nums[i];  // 以当前数作为目标值
        int count = 0;
        for (int j = 0; j < n; j++) {
            if (abs(nums[j] - target) <= k) {
                count++;
            }
        }
        maxCount = max(maxCount, count);
    }
    
    return maxCount;
}
```

### 扫描线算法 `O(nlogn)`

像是在数某个时刻有多少个区间重叠。一条水平线从左向右扫过，每个起点让重叠数+1，每个终点让重叠数-1，过程中的最大重叠数就是答案。

```cpp
int solution(vector<int>& nums, int k) {
    int n = nums.size();
    vector<pair<int, int>> ranges;  // 存储每个数可以变化的范围
    
    // 计算每个数可以变化的范围
    for (int i = 0; i < n; i++) {
        ranges.push_back({nums[i] - k, 1});  // 范围起点
        ranges.push_back({nums[i] + k + 1, -1});  // 范围终点
    }
    
    // 按照位置排序
    sort(ranges.begin(), ranges.end());
    
    int maxCount = 1;
    int count = 0;
    
    // 扫描线算法
    for (const auto& range : ranges) {
        count += range.second;
        maxCount = max(maxCount, count);
    }
    
    return maxCount;
}
```
*注*：  
`std::sort` 对 `std::pair` 的默认排序规则是：首先比较 `first` 成员，如果 `first` 相等，则比较 `second` 成员。

---

含模板：
```cpp
#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;

int solution(vector<int>& nums, int k) {
    int n = nums.size();
    vector<pair<int, int>> ranges;  // 存储每个数可以变化的范围
    
    // 计算每个数可以变化的范围
    for (int i = 0; i < n; i++) {
        ranges.push_back({nums[i] - k, 1});  // 范围起点
        ranges.push_back({nums[i] + k + 1, -1});  // 范围终点
    }
    
    // 按照位置排序
    sort(ranges.begin(), ranges.end());
    
    int maxCount = 1;
    int count = 0;
    
    // 扫描线算法
    for (const auto& range : ranges) {
        count += range.second;
        maxCount = max(maxCount, count);
    }
    
    return maxCount;
}

int main() {
    vector<int> nums1 = {4, 6, 1, 2};
    cout << (solution(nums1, 2) == 3) << endl;

    vector<int> nums2 = {1, 3, 5, 7};
    cout << (solution(nums2, 1) == 2) << endl;

    vector<int> nums3 = {1, 3, 5, 7};
    cout << (solution(nums3, 3) == 4) << endl;

    return 0;
}
```