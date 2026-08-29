+++
date = '2026-08-29T10:12:28+08:00'
draft = false
title = 'DuckDB 列式执行引擎：分析查询为什么快'
author = 'JekYUlll'
lastmod = '2026-08-29T10:12:28+08:00'
tags = ['duckdb', 'columnar-storage', 'vectorized-execution']
categories = ['database']
+++

同一台机器，同一个查询：500 万行按 category 分组聚合。SQLite 跑了 1.8 秒，DuckDB 单线程 20 毫秒，16 线程 4 毫秒。不是 SQLite 写得差，它本来就不是干这个的。差距来自两个设计决定：数据怎么摆，数据怎么算。

DuckDB 从 2018 年起步，2024 年 6 月发布 1.0 稳定版，定位是"分析用的 SQLite"：进程内、零依赖、单文件。拿它跟一个经典行存引擎对比，正好能把分析查询提速的几层设计拆开看。

## 行存和列存：先决定读多少数据

OLTP 的典型操作是取一行：查一个用户，把整条记录拿回来。行存把一条记录的字段挨在一起放，一次 I/O 取整行，正合适。

OLAP 反过来。聚合查询扫几百万行，但只碰两三列。行存这时候很亏：读 100 列的数据才用得上 2 列，其余全是白读。列存把同一列的值连续存放，扫 2 列就真的只读 2 列，磁盘和内存带宽都省了。

列存思想出现得很早，真正普及是近十几年的事，原因和硬件变化有关：内存带宽和磁盘 I/O 的增速一直赶不上 CPU，少读数据比快算数据更值钱。分析型数据库就顺着这条路把列存、压缩、向量化一层层堆起来。

DuckDB 内部把表切成 row group，默认 122,880 行一组，每个 row group 里每列单独存成 column chunk。每个 chunk 还带 min/max 元数据（zone map），过滤条件命中不了就直接跳过整个 chunk。我的测试表里 user_id 的 zone map 是 `[Min: 0, Max: 99999]`，如果查 `user_id = 123456`，没有任何 chunk 能命中，整列不用读。

zone map 不是免费的，写入时要维护 min/max，换来的是查询时跳过整块数据的能力。分析负载以追加为主，这点维护成本很划算。

## 落盘时每列各选各的压缩

列存的第二个好处是压缩。同一列的值相似度高：category 只有 50 种，整数全在 0 到 99999 之间，压缩率远好过混合类型的一整行。

我在本机把 500 万行写进 DuckDB 文件，checkpoint 后用 `pragma_storage_info` 看每列实际选了什么压缩：

```sql
SELECT column_name, compression, count(*) AS segments, sum(count) AS rows
FROM pragma_storage_info('t')
WHERE segment_type != 'VALIDITY'
GROUP BY column_name, compression ORDER BY column_name;
```

DuckDB 1.5.5 的真实输出：

```text
user_id  INTEGER   -> BitPacking    值域只有 0~99999，17 位就够
category VARCHAR   -> Dictionary    50 个不同值，映射成整数 code
amount   DOUBLE    -> ALP           浮点自适应无损压缩
ts       TIMESTAMP -> BitPacking
```

引擎的压缩家族不止这几个：字符串还有 FSST（对短字符串做子串替换），浮点还有 Chimp、Patas，专门吃时间序列这类前后值接近的数据。每个 column chunk 写盘时按数据特征自动挑一个，不需要人工指定。

四列在内存里 36 字节一行（含 string_t 的 16 字节结构），500 万行 ≈ 180 MB，落盘后 15.8 MB，约 11 倍。压缩发生在数据写盘的时候：文件模式下 row group 一批批落盘就一批批压缩，纯内存模式干脆不压缩，写入路径保持简单。

## 火山模型：一行一行地把 CPU 烧掉

解释执行的数据库大多用火山模型：一棵 operator 树，父节点每次调用子节点要一行。PostgreSQL、MySQL、SQLite 都是这个路子。

500 万行聚合，树上每个 operator 的 next() 被调用 500 万次。每次调用有虚函数跳转、状态切换，还破坏缓存局部性。OLTP 查询只取几十行，这个开销无所谓；OLAP 扫全表就是灾难。

火山模型不是没有优点：实现简单，每个 operator 独立，加新算子容易。很多行存数据库几十年没换执行模型，不是不知道问题，是替换成本太高。向量化要求重写每个 operator，让它们一次处理一批值，等于把执行引擎翻新一遍。

## 向量化：一次处理 2048 行

DuckDB 的每个 operator 按固定大小的 vector 批量干活，这个尺寸叫 STANDARD_VECTOR_SIZE，默认 2048。同样是解释执行，循环次数从 500 万降到 2400 多次，每次循环对连续内存里的 2048 个值做同一操作，编译器能向量化成 SIMD。

压缩数据不一定要先解压再算。字典编码的列，聚合直接对整数 code 做，最后输出时才还原成字符串。`EXPLAIN ANALYZE` 里看得清清楚楚：

```text
PROJECTION (50 rows)         __internal_decompress_string(#0)  最后才还原
HASH_GROUP_BY (50 rows)      Groups: #0                       直接按 code 分组
PROJECTION (5,000,000 rows)  __internal_compress_string_ubigint(#0)
TABLE_SCAN (5,000,000 rows)  Sequential Scan
```

字符串在内存里的表示也为此优化过：`string_t` 是 16 字节，短字符串（不超过 12 字节）直接内联在结构体里，不碰堆；比较时先比内联的 4 字节前缀，前缀不同直接判不等，省掉指针追逐。`cat_00` 这类值全程享受这个优化。

向量化不是分析引擎提速的唯一路线，另一条是 JIT 编译，把查询直接编成机器码，HyPer、Umbra 这些研究系统走的就是这条路。DuckDB 选了解释加向量化：实现简单、跨平台，性能也够用。

## 实测：同一份数据，两个引擎

下面这个脚本跑一遍就能复现，只需要 `pip install duckdb`：

```python
import sqlite3, duckdb, time, os

N = 5_000_000
Q = "SELECT category, count(*), sum(amount) FROM t GROUP BY category"
DB = "demo.duckdb"
if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE t(user_id INT, category TEXT, amount REAL, ts DATETIME)")
t0 = time.perf_counter()
con.execute("""
WITH RECURSIVE cnt(i) AS (
    SELECT 1 UNION ALL SELECT i+1 FROM cnt WHERE i < ?
)
INSERT INTO t
SELECT abs(random()) % 100000,
       'cat_' || printf('%02d', abs(random()) % 50),
       (abs(random()) % 100000) / 100.0,
       datetime('2024-01-01', '+' || (i % 86400) || ' seconds')
FROM cnt
""", (N,))
print(f"[sqlite] 插入 {N} 行: {time.perf_counter()-t0:.2f}s")
con.execute(Q).fetchall()
t0 = time.perf_counter()
con.execute(Q).fetchall()
print(f"[sqlite] 聚合查询: {(time.perf_counter()-t0)*1000:.0f} ms")

d = duckdb.connect(DB)
d.execute("CREATE TABLE t(user_id INT, category VARCHAR, amount DOUBLE, ts TIMESTAMP)")
t0 = time.perf_counter()
d.execute("""
INSERT INTO t
SELECT CAST(i % CAST(100000 AS BIGINT) AS INTEGER),
       'cat_' || lpad(CAST(i % CAST(50 AS BIGINT) AS VARCHAR), 2, '0'),
       CAST(CAST(i AS BIGINT) * 7919 % CAST(100000 AS BIGINT) AS DOUBLE) / 100.0,
       TIMESTAMP '2024-01-01' + INTERVAL (CAST(i % CAST(86400 AS BIGINT) AS INTEGER)) SECOND
FROM range(5000000) AS t(i)
""")
print(f"[duckdb] 插入 {N} 行: {time.perf_counter()-t0:.2f}s")
for threads in (1, os.cpu_count() or 4):
    d.execute(f"SET threads={threads}")
    d.execute(Q).fetchall()
    t0 = time.perf_counter()
    d.execute(Q).fetchall()
    print(f"[duckdb] 聚合查询 (threads={threads}): {(time.perf_counter()-t0)*1000:.0f} ms")

d.execute("CHECKPOINT")
print("\n[duckdb] 每列压缩方式:")
for row in d.execute("""
SELECT column_name, compression, count(*) AS segments, sum(count) AS rows
FROM pragma_storage_info('t')
WHERE segment_type != 'VALIDITY'
GROUP BY column_name, compression ORDER BY column_name
""").fetchall():
    print(" ", row)
print(f"[duckdb] 文件大小: {os.path.getsize(DB)/1024/1024:.1f} MB")
```

我这台 16 核机器上的输出：

```text
[sqlite] 插入 5000000 行: 3.09s
[sqlite] 聚合查询: 1806 ms
[duckdb] 插入 5000000 行: 1.54s
[duckdb] 聚合查询 (threads=1): 20 ms
[duckdb] 聚合查询 (threads=16): 4 ms
[duckdb] 文件大小: 15.8 MB
```

脚本里 SQLite 用递归 CTE 生成数据，DuckDB 用 range() 表函数，两边数据分布不完全一样，但都是 50 个分组、每组约 10 万行，聚合结果等价。计时取预热后的第二次运行，排除冷缓存和编译开销。

这不是公平 benchmark。SQLite 是单线程 OLTP 引擎，数据全在页缓存里，这个对比只用来展示执行模型差异的量级，别拿去做性能测试。压缩后反而比纯内存快，是因为 15.8 MB 的数据比 180 MB 未压缩数据占的内存带宽小得多。

同一查询的 `EXPLAIN ANALYZE`（单线程）总耗时 0.03 秒：TABLE_SCAN 扫完 500 万行只占 0.02 秒，HASH_GROUP_BY 0.01 秒。瓶颈不在某个算子，而在扫描本身，这正是列存加压缩把数据量压下来的效果。

## 并行：morsel 驱动

DuckDB 的并行不是给 operator 树加锁，而是把表按 row group 切成小块（morsel），多个线程各扫各的、各自做部分聚合，最后合并。morsel 这个词来自论文：把关系切成固定大小的片，工作线程抢着领活，天然负载均衡。聚合算子各自维护自己的哈希表，跑完再合并，避免锁竞争。

单线程 20 ms 到 16 线程 4 ms，没有线性扩展，哈希聚合的合并和内存带宽有开销，但量级对了。这个思路来自 Leis 等人的 Morsel-Driven Parallelism 论文，DuckDB 官网把它列在致谢里，与向量化执行（MonetDB/X100 一脉）并列。

下次遇到"数据库跑得慢"，先问数据怎么存、一次算几行，别急着调参数。同一个查询在两个引擎里的差距，不是优化器谁更聪明，是这两层设计差了两个数量级。

## 参考

- DuckDB: Why DuckDB: https://duckdb.org/why_duckdb
- DuckDB 文档：Execution Format: https://duckdb.org/docs/stable/internals/vector.html
- MotherDuck 术语表：Columnar storage: https://motherduck.com/glossary/columnar-storage/
- Announcing DuckDB 1.0.0: https://duckdb.org/2024/06/03/announcing-duckdb-100.html
- MonetDB/X100: Hyper-Pipelining Query Execution: https://cidrdb.org/cidr2005/papers/P19.pdf
- Morsel-Driven Parallelism: https://db.in.tum.de/~leis/papers/morsels.pdf
