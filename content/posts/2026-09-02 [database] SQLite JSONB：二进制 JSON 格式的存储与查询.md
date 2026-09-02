+++
date = '2026-09-02T10:07:59+08:00'
draft = false
title = 'SQLite JSONB：二进制 JSON 格式的存储与查询'
author = 'JekYUlll'
lastmod = '2026-09-02T10:07:59+08:00'
tags = ['sqlite', 'jsonb', 'json']
categories = ['database']
+++

SQLite 的 JSON 函数从 3.9 就存在，但直到 2024 年 1 月发布的 3.45.0，JSON 始终以文本形式存储在表里。每次 `json_extract()` 都要把整段 JSON 从头解析一遍，解析结果用完就丢。PostgreSQL 在 2014 年的 9.4 就有了二进制 jsonb 类型，SQLite 这步棋晚了十年。3.45.0 引入 JSONB：一种二进制编码，以 BLOB 存储。官方文档说它比文本 JSON 小 5-10%，处理所需的 CPU 周期不到一半。下面拆开它的字节格式，再用四组实测数据看它小多少、快多少、什么时候值得用。

## JSONB 的字节长什么样

JSONB 里每个元素由 header 加 payload 组成。header 第一个字节的高 4 位记录 payload 大小，低 4 位记录元素类型。payload 不超过 11 字节时 header 只有一个字节；更大时 header 扩展成 2、3、5 或 9 字节，长度用大端整数写在后续字节里。文本 JSON 里的引号、冒号、逗号、花括号这些分隔符全部消失，因为每个元素自带长度，解析时不需要向前扫描找结尾。

本地 SQLite 3.53 实测 `hex(jsonb('{"a":1}'))`，得到 `4C17611331`：

- `4C`：高 4 位是 4，表示 payload 共 4 字节；低 4 位是 0xC，OBJECT 类型
- `17 61`：字符串 `"a"`，长度 1，payload 是 UTF-8 原文
- `13 31`：INT 值 `1`，payload 是 ASCII 字符 '1'

元素类型一共 13 种：NULL、TRUE、FALSE、INT、INT5、FLOAT、FLOAT5、TEXT、TEXTJ、TEXT5、TEXTRAW、ARRAY、OBJECT。`true` 编码成单个字节 `01`，`null` 是 `00`，数组以 `6B` 开头（大小 6，类型 ARRAY）。header 不要求最短形式：同一个数字 `1`，规范里列出了 5 种合法编码，最短是 `13 31`，最长可以到 10 字节，只是构造数组时暂时不知道总大小，先占位再回填。实际生成时 SQLite 总是用最短形式，`hex(jsonb('1'))` 就是 `1331`。

数字不转二进制整数，字符串也不做转义展开，payload 基本是原文拷贝。这是刻意为之：JSONB 想直接充当 JSON 函数的内部解析树。JSONB 出现之前，`json_set()`、`json_patch()` 这类操作走三步：文本解析成内部格式、做修改、再渲染回文本。大部分 CPU 花在第一和第三步。JSONB 把解析结果序列化存下来，这三步只剩中间一步，格式转换全部推迟到真正需要时，文档里叫 lazy。

## 20 万行实测：小 23%，快 1.6 倍

第一组数据：短 key、短字符串的典型配置型 JSON。一张表两列，一列存文本，一列存 `jsonb()` 转换后的 BLOB，20 万行，每行是 `{"id":..,"name":"user-..","tags":["alpha","beta","gamma"],"score":..,"active":true}`。

```python
import sqlite3, time, json, os
DB = '/tmp/jsonb_bench.db'
if os.path.exists(DB): os.remove(DB)
con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, jt TEXT, jb BLOB)")
N = 200_000
cur.executemany("INSERT INTO t VALUES(?,?,?)", (
    (i, json.dumps({"id": i, "name": f"user-{i}",
                    "tags": ["alpha", "beta", "gamma"],
                    "score": i % 1000, "active": True},
                   separators=(',', ':')), None) for i in range(N)))
con.commit()
cur.execute("UPDATE t SET jb = jsonb(jt)"); con.commit()

def bench(sql, n=5):
    best = 1e9
    for _ in range(n):
        t0 = time.time(); cur.execute(sql).fetchall()
        best = min(best, time.time() - t0)
    return best * 1000

print(bench("SELECT sum(json_extract(jt,'$.score')) FROM t"))  # 文本
print(bench("SELECT sum(json_extract(jb,'$.score')) FROM t"))  # JSONB
```

| 指标 | 文本 JSON | JSONB |
|------|-----------|-------|
| 平均长度 | 90.8 字节 | 69.8 字节 |
| `json_extract` 全表求和 | 61 ms | 38 ms |
| `->>` 全表求和 | 64 ms | 41 ms |
| `score > 500` 过滤计数 | 62 ms | 38 ms |

体积小 23%，查询快约 1.6 倍。没有达到"CPU 周期减半"的 2 倍，因为全表扫描大部分时间花在读取 36 MB 的库和汇总结果上，纯解析部分省得更多。

第二组数据把字符串值换成长文本（每行 440 字节左右），同样 10 万行：文本平均 441.8 字节，JSONB 434.8 字节，只小了 1.6%。但 `json_extract` 求和仍然从 72 ms 降到 49 ms。结论很清楚：JSONB 的存储收益取决于 JSON 里标点占比，字符串越长越接近零；处理收益却稳定存在，因为省掉的是解析而不是存储。

第三组数据看修改：10 万行把 `$.score` 全部加 1。`json_set` 更新文本列花 308 ms，`jsonb_set` 更新 BLOB 列花 274 ms，只快 12%。写路径被 B-tree 页写入和提交开销淹没，解析省下的时间占比很小。所以 JSONB 的收益主要落在读路径上，UPDATE 密集的场景别指望它。

第四组数据做个对照：20 万行存完全相同的 JSON，想隔离纯解析成本。结果出乎意料，文本 32 ms，JSONB 34 ms，打平。翻源码才明白，JSON 函数带一个最多 4 条目的解析缓存（`src/json.c` 里的 JsonCache，挂在语句的 auxdata 上），内容相同的文本 JSON 在一条语句里只解析一次，后面全命中缓存。JSONB 不走这个缓存，因为 BLOB 本来就不用解析。这个缓存也解释了为什么官方"CPU 周期不到一半"的说法在真实数据上测不出来：真实表里每行 JSON 内容不同，缓存命中率低，第一组数据才是常态。

## 怎么存、怎么查

`jsonb()` 把文本 JSON 转成 BLOB，直接存 BLOB 列：

```sql
CREATE TABLE t(data BLOB);
INSERT INTO t VALUES(jsonb('{"country":"Luxembourg","capital":"Luxembourg City","languages":["French","German","Luxembourgish"]}'));
```

查询不需要改写法。`json_extract()`、`->`、`->>` 都接受 JSONB 作为输入，返回结果和文本 JSON 完全一致：

```sql
SELECT data ->> 'capital' FROM t;             -- Luxembourg City
SELECT json_extract(data, '$.languages') FROM t;  -- ["French","German","Luxembourgish"]
SELECT json(data) FROM t;                     -- 转回文本
```

`->` 返回 JSON 文本形式，`->>` 返回 SQL 原生值，整数就是整数，不是带引号的字符串。3.47 起 `->>` 支持负索引，`jsonb('[10,20,30]') ->> -1` 得到 30，从右往左数。修改操作用 `jsonb_set()`、`jsonb_insert()`、`jsonb_remove()`，和 `json_set()` 系列一一对应，返回值同样是 JSONB，可以链式调用。分解用 `jsonb_each()`、`jsonb_tree()` 表值函数，聚合有 `jsonb_group_array()`、`jsonb_group_object()`。整个函数族和 `json_` 前缀版本平行存在：

```sql
SELECT key, value FROM jsonb_each(jsonb('{"a":1,"b":2}'));  -- a|1, b|2
```

JSONB 列一样能建表达式索引，这是文档查询最常见的加速手段：

```sql
CREATE INDEX idx_score ON t(jsonb_extract(jb, '$.score'));
-- EXPLAIN QUERY PLAN 输出:
-- SEARCH t USING COVERING INDEX idx_score (<expr>=?)
```

`jsonb_extract()` 是确定性函数，SQLite 允许拿它建索引，查询时自动走覆盖索引，不用全表扫。

存量数据迁移就是一条 UPDATE，我第一组测试的 20 万行就是这么转的，耗时 0.4 秒：

```sql
UPDATE t SET jb = jsonb(jt);
```

SQLite 是灵活类型，列声明成 TEXT 还是 BLOB 不影响函数行为，函数只看值的实际存储类。所以新列用 BLOB 声明更直观，不换列类型也照样能用。

一个容易踩的坑：`json_valid()` 默认只认文本 JSON，对合法 JSONB BLOB 返回 0。要验证 BLOB 是不是 JSONB，用 `json_valid(x, 4)`（粗略检查）或 `json_valid(x, 8)`（严格按内部格式校验）。

## 取舍与坑

JSONB 是 SQLite 内部格式，官方明确说不要拿它做跨系统数据交换。它和 PostgreSQL 的 jsonb 只是同名，字节格式完全不同，二进制不兼容，带出去给外部工具解析会得到乱码。

值得用的场景：JSON 被反复查询、反复更新，改存 JSONB 收益稳定，查询代码一行不用动。只存不查，或者 JSON 本身就很小，收益有限。另一个细节：`json_object()` 这类生成函数返回的仍是文本，只有 `jsonb()` 系列才产出 BLOB，别指望混用自动变成二进制。

不适合的场景也明确：数据库文件要在 3.45 之前的老版本 SQLite 之间移动时，别用 JSONB，旧版本不认识 `jsonb()` 函数；JSON 需要交给外部系统解析时，存文本更省事，JSONB 的 BLOB 对别的程序就是一堆乱码。这两个限制都和"JSONB 是内部格式"这一条绑定。

格式本身有向后兼容承诺，升级 SQLite 不需要导出重导。代价是它不可读、不可调试，好在 `json()` 随时能转回文本。JSON1 从 3.38.0 起默认内置，JSON5 语法从 3.42.0 起支持，加上 3.45 的 JSONB，SQLite 的 JSON 能力这几年一直在往"文档数据库"的方向走。

## 参考

- [The SQLite JSONB Format](https://www.sqlite.org/jsonb.html)
- [SQLite Release 3.45.0](https://www.sqlite.org/releaselog/3_45_0.html)
- [SQLite JSON1 函数文档](https://www.sqlite.org/json1.html)
- [JSON and JSONB support in SQLite, Fedora Magazine](https://fedoramagazine.org/json-and-jsonb-support-in-sqlite-3-45-0/)
