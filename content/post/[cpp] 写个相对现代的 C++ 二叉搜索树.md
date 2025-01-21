+++
date = '2025-01-21T12:05:47+08:00'
draft = false
title = '写个相对现代的 C++ 二叉搜索树'
author = 'JekYUlll'
lastmod = '2025-01-21T12:05:47+08:00'
tags = ['cpp', 'morden cpp', 'template', 'data structure']
categories = ['cpp']
+++

```cpp
#include <functional>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

template <typename T>
concept Comparable = requires(T a, T b) {
    { a < b } -> std::convertible_to<bool>;
    { a > b } -> std::convertible_to<bool>;
    { a == b } -> std::convertible_to<bool>;
};

template <typename T>
concept Streamable = requires(T a, std::ostream& os) {
    { os << a } -> std::same_as<std::ostream&>;
};

template <Comparable K, Streamable V>
class PairBSTree {
  private:
    using Pair = std::pair<K, V>;

    struct TreeNode {
        Pair _pair;
        TreeNode* _left;
        TreeNode* _right;

        TreeNode() = default;
        TreeNode(Pair pair) : _pair(pair), _left(nullptr), _right(nullptr) {}
        ~TreeNode() = default;
    };

    TreeNode* _root;

    void build_(const std::vector<Pair>& nodes) {
        for (const auto& pair : nodes) {
            Insert(pair);
        }
    }

    void destroy_(TreeNode* node) {
        if (node) {
            destroy_(node->_left);
            destroy_(node->_right);
            delete node;
            node = nullptr;
        }
    }

    TreeNode*& search_(TreeNode*& node, K key) const {
        if (!node || key == node->_pair.first) {
            return node;
        }
        if (key < node->_pair.first) {
            return search_(node->_left, key);
        }
        return search_(node->_right, key);
    }

    void insert_(TreeNode*& node, Pair pair) {
        if (!node) {
            node = new TreeNode(pair);
            return;
        }
        auto key = pair.first;
        if (key == node->_pair.first) {
            node->_pair = pair;
        } else if (key < node->_pair.first) {
            insert_(node->_left, pair);
        } else {
            insert_(node->_right, pair);
        }
    }

    TreeNode*& go_to_max_(TreeNode*& node) {
        while (node->_right) {
            node = node->_right;
        }
        return node;
    }

    TreeNode*& go_to_min_(TreeNode*& node) {
        while (node->_left) {
            node = node->_left;
        }
        return node;
    }

    void delete_(TreeNode*& node, K key) {
        auto& target = search_(node, key);
        if (!target) {
            return;
        }
        if (!target->_left && !target->_right) {
            delete target;
            target = nullptr;
            return;
        }
        if (!target->_left) {
            TreeNode* temp = target->_right;
            delete target;
            target = temp;
            return;
        }
        if (!target->_right) {
            TreeNode* temp = target->_left;
            delete target;
            target = temp;
            return;
        }
        auto& max_in_left = go_to_max_(target->_left);
        target->_pair = max_in_left->_pair;
        // 1. 常规的递归，把整个左子树当做新的树
        // delete_(target->_left, max_in_left->_pair.first);
        // 2. 直接传入 max_in_left 即可
        // delete_(max_in_left, max_in_left->_pair.first);
        // 3. 实际上不需要递归，因为 max_in_left 是左边最大的值，一定没有右子树
        TreeNode* temp = max_in_left->_left;
        delete max_in_left;
        max_in_left = temp;
        // 我开始时候的代码（有误）：
        // auto& max_in_left = go_to_max_(node->_left);  // 应该是
        // current->_left current->_pair = max_in_left->_pair; delete
        // (max_in_left); max_in_left = nullptr;
        // 第三种和我开始时候的逻辑类似
        // 但我当时忘了保留 max_in_left 的左子树（如果存在）
    }

    static void normal_print_func_(Pair pair) {
        std::cout << pair.second << " | ";
    }

    void in_order_(TreeNode* node, std::function<void(Pair)> func) {
        if (!node) {
            return;
        }
        in_order_(node->_left, func);
        func(node->_pair);
        in_order_(node->_right, func);
    }

  public:
    PairBSTree() : _root(nullptr) {}

    PairBSTree(const std::vector<Pair>& pairs) : _root(nullptr) {
        build_(pairs);
    }

    ~PairBSTree() { destroy_(_root); }

    std::optional<V> Search(Pair::first_type key) {
        auto node = search_(_root, key);
        if (!node) {
            return std::nullopt;
        }
        return node->_pair.second;
    }

    void Insert(Pair pair) { insert_(_root, pair); }

    void Delete(K key) { delete_(_root, key); }

    void InOrder(std::function<void(Pair)> func = normal_print_func_) {
        in_order_(_root, func);
    }

    [[nodiscard]] size_t Size() {
        size_t size = 0;
        InOrder([&size](std::pair<K, V>) { ++size; });
        return size;
    }

    [[nodiscard]] V Max() {
        auto temp = _root;
        go_to_max_(temp);
        return temp->_pair.second;
    }

    [[nodiscard]] V Min() {
        auto temp = _root;
        go_to_min_(temp);
        return temp->_pair.second;
    }
};

int main(void) {

    std::vector<std::pair<int, std::string>> pairs = {
        {2, "Bob"},    {9, "Jack"},    {4, "Lucy"},   {23, "Evan"},
        {3, "Gorge"},  {12, "Lily"},   {15, "Mono"},  {90, "Rick"},
        {14, "Lance"}, {76, "Molly"},  {24, "Stan"},  {11, "Scot"},
        {54, "Mint"},  {37, "Biance"}, {35, "Cower"}, {1, "Brick"},
    };

    PairBSTree tree(pairs);

    std::cout << "Name of 9: " << tree.Search(9).value_or("nothing") << '\n';

    std::cout << "Size: " << tree.Size() << '\n';
    // std::cout << "Min: " << tree.Min() << '\n';
    // std::cout << "Max: " << tree.Max() << '\n';

    tree.InOrder();
    std::cout << '\n';

    tree.Delete(15);
    std::cout << "Size: " << tree.Size() << '\n';

    tree.InOrder();
    std::cout << '\n';

    std::vector<std::string> names_in_order;
    tree.InOrder([&names_in_order](std::pair<int, std::string> pair) {
        std::cout << pair.second << " -- ";
        names_in_order.push_back(pair.second);
    });
    std::cout << std::endl;

    return 0;
}
```