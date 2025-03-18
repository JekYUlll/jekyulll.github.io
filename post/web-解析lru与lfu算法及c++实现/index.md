
在计算机系统中，缓存是提升性能的核心技术之一。当内存资源有限时，如何高效淘汰无用数据、保留热点数据？**LRU（最近最少使用）**和**LFU（最不频繁使用）**算法为此提供了经典解决方案。本文将从原理到实践，详解这两种算法，并附完整C++实现代码。

- ​LRU（Least Recently Used）​
    - 基于时间维度，淘汰最久未被访问的数据。例如，若缓存容量为3，依次访问A→B→C→A，则再次插入新数据时，最久未访问的B会被淘汰。其核心假设是：最近被访问的数据未来更可能被使用。
- ​LFU（Least Frequently Used）​
    - 基于频率维度，淘汰访问次数最少的数据。例如，若数据A被访问5次，B被访问3次，则优先淘汰B。LFU通过计数器记录访问频次，并可能结合时间衰减机制避免旧高频数据长期占用缓存。

---

### **一、LRU算法：时间维度淘汰策略**
#### **核心原理**  
LRU基于“时间局部性”假设：**最近被访问的数据更可能被再次使用**。其淘汰策略简单直接——移除最久未访问的数据。例如，若缓存容量为3，访问顺序为A→B→C→A，则新数据插入时淘汰最旧的B。

#### **C++实现**  
LRU需高效支持两种操作：  
1. **快速查询**（哈希表，O(1)）  
2. **顺序维护**（双向链表，O(1)调整顺序）  

**数据结构设计**：  
```cpp
struct Node {
    int key, value;
    Node *prev, *next;
    Node(int k, int v) : key(k), value(v), prev(nullptr), next(nullptr) {}
};

class LRUCache {
private:
    int capacity;
    unordered_map<int, Node*> cache;  // 哈希表：键到节点映射
    Node *head, *tail;                // 双向链表头尾哨兵节点

    void moveToHead(Node* node) {     // 将节点移至头部
        removeNode(node);
        addToHead(node);
    }
    
    void removeNode(Node* node) {     // 移除节点
        node->prev->next = node->next;
        node->next->prev = node->prev;
    }

    void addToHead(Node* node) {     // 头部插入节点
        node->next = head->next;
        node->prev = head;
        head->next->prev = node;
        head->next = node;
    }

public:
    LRUCache(int cap) : capacity(cap) {
        head = new Node(-1, -1);      // 初始化哨兵节点
        tail = new Node(-1, -1);
        head->next = tail;
        tail->prev = head;
    }

    int get(int key) {               // 查询操作
        auto it = cache.find(key);
        if (it == cache.end()) return -1;
        moveToHead(it->second);       // 更新为最近访问
        return it->second->value;
    }

    void put(int key, int value) {   // 插入/更新操作
        if (cache.find(key) != cache.end()) {
            cache[key]->value = value;
            moveToHead(cache[key]);
            return;
        }
        Node* newNode = new Node(key, value);
        cache[key] = newNode;
        addToHead(newNode);
        if (cache.size() > capacity) {  // 触发淘汰
            Node* toDelete = tail->prev;
            cache.erase(toDelete->key);
            removeNode(toDelete);
            delete toDelete;
        }
    }
};
```
**关键点**：  
• 使用**哈希表+双向链表**实现O(1)操作复杂度  
• 头节点存放最新访问数据，尾节点为待淘汰数据  

---

### **二、LFU算法：频率维度淘汰策略**
#### **核心原理**  
LFU基于“频率优先”原则：**淘汰访问次数最少的数据**。例如，数据A访问5次、B访问3次，则优先淘汰B。LFU需记录每个键的访问频率，并维护最小频率值。

#### **C++实现**  
LFU需维护三个核心结构：  
1. **键到值和频率的映射**  
2. **频率到键集合的映射**  
3. **当前最小频率值**  

**数据结构设计**：  
```cpp
class LFUCache {
private:
    int capacity, minFreq;
    unordered_map<int, pair<int, int>> keyMap;       // key→{value, freq}
    unordered_map<int, list<int>> freqMap;          // freq→keys list
    unordered_map<int, list<int>::iterator> keyIter;// key在freqMap中的迭代器

    void increaseFreq(int key) {     // 增加键的频率
        int oldFreq = keyMap[key].second;
        keyMap[key].second++;
        freqMap[oldFreq].erase(keyIter[key]);        // 从旧频率列表移除
        if (freqMap[oldFreq].empty()) {              // 更新最小频率
            freqMap.erase(oldFreq);
            if (oldFreq == minFreq) minFreq++;
        }
        freqMap[oldFreq + 1].push_front(key);        // 加入新频率列表
        keyIter[key] = freqMap[req + 1].begin();
    }

public:
    LFUCache(int cap) : capacity(cap), minFreq(0) {}

    int get(int key) {
        if (keyMap.find(key) == keyMap.end()) return -1;
        increaseFreq(key);           // 更新频率
        return keyMap[key].first;
    }

    void put(int key, int value) {
        if (capacity == 0) return;
        if (keyMap.find(key) != keyMap.end()) {  // 已存在则更新值
            keyMap[key].first = value;
            increaseFreq(key);
            return;
        }
        if (keyMap.size() >= capacity) {         // 触发淘汰
            int evictKey = freqMap[minFreq].back();
            freqMap[minFreq].pop_back();
            if (freqMap[minFreq].empty()) 
                freqMap.erase(minFreq);
            keyMap.erase(evictKey);
            keyIter.erase(evictKey);
        }
        keyMap[key] = {value, 1};                // 插入新键
        freqMap[1].push_front(key);
        keyIter[key] = freqMap[1].begin();
        minFreq = 1;                             // 最小频率重置为1
    }
};
```
**关键点**：  
• 通过三层映射实现频率统计与快速淘汰  
• 维护`minFreq`避免遍历所有频率值  

---

### **三、LRU与LFU对比与应用场景**
| **维度**       | **LRU**                            | **LFU**                            |
|----------------|-----------------------------------|-----------------------------------|
| **淘汰依据**   | 访问时间（最久未用）              | 访问频率（最少使用）              |
| **优点**       | 实现简单，适应突发流量            | 精准捕捉长期热点数据              |
| **缺点**       | 周期性访问易误淘汰（如扫描操作）  | 新数据易被淘汰（冷启动问题）      |
| **适用场景**   | 实时榜单、用户会话管理            | 热门视频缓存、搜索引擎热词        |

**实际案例**：  
• **数据库缓存**：MySQL的Buffer Pool使用改进版LRU（冷热数据分离）  
• **高并发系统**：Redis采用近似LFU，平衡性能与内存开销  

---

### **四、总结与选型建议**
• **选择LRU**：若业务存在明显的时间局部性（如新闻热点），或需快速响应访问顺序变化。  
• **选择LFU**：若数据访问频次差异大（如电商热门商品），且需长期保留高频数据。  

**性能优化方向**：  
• 分段锁减少并发竞争（如将缓存分16段）  
• 添加频率衰减机制（避免旧高频数据长期占用）  

建议根据场景调整参数（如缓存容量、锁粒度等）以获得最佳效果。