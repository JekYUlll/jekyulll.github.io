+++
date = '2026-06-28T10:21:51+08:00'
draft = false
title = 'pgvector HNSW：PostgreSQL 里的向量索引不是魔法'
author = 'JekYUlll'
lastmod = '2026-06-28T10:21:51+08:00'
tags = ['pgvector', 'hnsw', 'postgresql', 'vector-search']
categories = ['database']
+++

把向量塞进 PostgreSQL 很爽，也很容易误用。pgvector 让表、事务、权限、备份和向量检索待在同一个数据库里，这对小团队很香。但 HNSW 索引不是给 `ORDER BY embedding <=> query LIMIT 10` 加一个普通 B-tree。它是近似最近邻，拿一点召回率换延迟。

如果你把它当魔法按钮，后面大概率会遇到两个问题：索引构建吃内存，带 `WHERE` 的查询返回数量不稳。

## 背景

向量检索最朴素的写法是全表扫描：对每一行算一次距离，排序，取前 K 个。数据量小的时候没问题。到几十万、几百万条 embedding 后，CPU 会被距离计算和排序拖住。

pgvector 默认做精确最近邻。它的 README 说得很直白：加 approximate index 后，查询会用召回率换速度，结果可能和精确查询不同。这个前提要先接受。业务如果要求审计级正确性，先别碰近似索引。业务如果是 RAG 召回、相似推荐、候选集粗筛，HNSW 就很合适。

我判断能不能上 HNSW，先看两个数字：候选集规模和可接受的漏召回。几万行以内，全表扫描加合适的内存配置可能已经够用，省下一个索引反而更稳。几百万行还要 50ms 内返回 top 20，近似索引才有讨论价值。这个选择很朴素，也很工程。它不是玄学，是预算。预算错了，线上就会痛。别赌，先算账。

## 核心原理

HNSW 全名是 Hierarchical Navigable Small World。原论文的核心想法是建多层近邻图。上层图稀疏，负责快速接近目标区域；下层图密，负责在局部做更细的搜索。

查询时，它不会比较所有向量。它从图的入口点出发，在上层找更近的点，再往下层走，最后维护一个候选列表。候选列表越大，越可能找到真正的近邻，查询也越慢。

pgvector 里常调的三个参数是：

- `m`：每层最多连多少个邻居，默认 16。图更密通常召回更好，也更占内存。
- `ef_construction`：构建索引时的候选列表大小，默认 64。调高会拖慢建索引和写入。
- `hnsw.ef_search`：查询时的候选列表大小，默认 40。调高能改善召回，也会增加单次查询耗时。

还有一个容易被忽略的点：HNSW 不像 IVFFlat 那样需要训练。空表也能先建索引。代价是构建慢，占内存，插入时也要把新点放进图里。

距离函数也要对上。L2 距离通常配 `vector_l2_ops` 和 `<->`，余弦距离配 `vector_cosine_ops` 和 `<=>`，内积配 `vector_ip_ops` 和 `<#>`。PostgreSQL 不会猜你的语义。如果索引用余弦建，查询却按 L2 排序，优化器就没有理由走这个索引。

我更喜欢先把精确查询留在压测脚本里。比如同一个 query 先跑不带索引的结果，再跑 HNSW，把 top K 的交集算出来。这样调 `ef_search` 时心里有数。只盯延迟很危险，因为一个很快但召回乱掉的索引，在 RAG 里会把后面的重排和生成都带偏。

## 代码实战

下面这段我在本机用 `pgvector/pgvector:pg16` 跑过。它建一个 3 维小表，只是为了看清 SQL 形状。真实 embedding 一般是 384、768 或 1536 维，写法一样。

```bash
docker run --rm --name pgvector-hnsw-lab \
  -e POSTGRES_PASSWORD=postgres \
  -d pgvector/pgvector:pg16

until [ "$(docker logs pgvector-hnsw-lab 2>&1 | grep -c 'database system is ready to accept connections')" -ge 2 ]; do
  sleep 1
done

docker exec -i pgvector-hnsw-lab psql -U postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS docs;
CREATE TABLE docs (
  id bigserial PRIMARY KEY,
  title text NOT NULL,
  embedding vector(3) NOT NULL
);

INSERT INTO docs (title, embedding) VALUES
  ('postgres index', '[0.99,0.05,0.02]'),
  ('vector database', '[0.90,0.12,0.10]'),
  ('linux io', '[0.05,0.96,0.02]'),
  ('scheduler', '[0.10,0.85,0.05]'),
  ('cpp template', '[0.02,0.05,0.98]'),
  ('contracts', '[0.04,0.08,0.92]');

CREATE INDEX docs_embedding_hnsw
ON docs USING hnsw (embedding vector_cosine_ops)
WITH (m = 8, ef_construction = 32);

ANALYZE docs;
SET enable_seqscan = off;
SET hnsw.ef_search = 16;

EXPLAIN (COSTS OFF)
SELECT id, title, embedding <=> '[1,0,0]' AS distance
FROM docs
ORDER BY embedding <=> '[1,0,0]'
LIMIT 3;

SELECT id, title, round((embedding <=> '[1,0,0]')::numeric, 4) AS distance
FROM docs
ORDER BY embedding <=> '[1,0,0]'
LIMIT 3;
SQL

docker rm -f pgvector-hnsw-lab
```

`EXPLAIN` 里应该能看到 `Index Scan using docs_embedding_hnsw`。如果看不到，先别急着调参数。先确认 `ORDER BY` 的距离表达式和索引 operator class 一致，比如余弦距离用 `vector_cosine_ops` 和 `<=>`。

## 工程取舍

HNSW 最大的坑不在语法，在资源账本。

建索引时，pgvector 希望图能放进 `maintenance_work_mem`。官方文档里有一个提示：如果出现 `hnsw graph no longer fits into maintenance_work_mem`，后续构建会慢很多。这个参数不能无脑调大，因为它吃的是数据库服务器内存。我的做法是先用接近生产的数据量试建一次，再决定 `maintenance_work_mem` 和并行 worker。

这里还有一个运营细节：初始导入时，先 COPY 数据，再建 HNSW，通常比边写边维护图便宜。线上持续写入就要看写放大。每插入一条向量，索引都要找邻居、补边、更新图结构。写多读少的表，我宁愿晚一点建索引，或者把热数据和冷数据拆开。

过滤条件也要单独看。pgvector 的 approximate index 先扫索引，再应用过滤。如果 `WHERE category_id = 123` 只命中 10% 的行，默认 `hnsw.ef_search = 40` 时，平均只会留下大约 4 个匹配行。pgvector 0.8.0 为这个场景加了 iterative index scans，可以用 `SET hnsw.iterative_scan = strict_order;` 让索引在结果不够时继续扫，直到满足条件或碰到阈值。

这不是说所有过滤都交给 HNSW。低基数过滤可以考虑 partial index：

```sql
CREATE INDEX docs_embedding_cat_123_hnsw
ON docs USING hnsw (embedding vector_cosine_ops)
WHERE category_id = 123;
```

高基数或租户隔离场景，分区表有时更干净。向量索引解决的是距离搜索，不负责替你设计数据模型。

我也不建议一上来就把 pgvector 当独立向量数据库的替代品。PostgreSQL 的优势是事务、SQL、备份、权限和现有业务表能直接复用。缺点也同样清楚：索引和普通 OLTP 负载抢 CPU、内存和 I/O。数据规模继续涨时，要么把向量表放到单独实例，要么把召回服务拆出去。先把边界想清楚，后面少救火。

## 今日可执行动作

1. 在测试库跑一遍 `EXPLAIN (ANALYZE, BUFFERS)`，确认查询真的走 HNSW，而不是全表扫描。
2. 用同一批 query 对比精确查询和 HNSW 查询，记录 recall、P50、P95。不要只看一条样例。
3. 如果查询带 `WHERE`，试一下 `hnsw.iterative_scan`、partial index 和分区。三者不是同一种东西，别混着调。

我的粗规则是：先用默认 `m = 16, ef_construction = 64`，只调 `hnsw.ef_search`。只有召回不够，再动建索引参数。建索引参数一动，重建成本就来了。

最后别忘了把向量检索放回业务链路里看。RAG 常见流程是召回、重排、拼 prompt、生成。HNSW 只负责第一步。第一步漏掉了正确文档，后面模型再强也补不回来。第一步召回太多，重排和上下文窗口又会被挤爆。调索引时把这条链路一起压测，结果会比单看数据库耗时靠谱。

## 参考

- pgvector README / PGXN：<https://pgxn.org/dist/vector/README.html>
- pgvector GitHub：<https://github.com/pgvector/pgvector>
- PostgreSQL 新闻：pgvector 0.8.0 Released：<https://www.postgresql.org/about/news/pgvector-080-released-2952/>
- Crunchy Data：HNSW Indexes with Postgres and pgvector：<https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector>
- Neon：Understanding vector search and HNSW index with pgvector：<https://neon.com/blog/understanding-vector-search-and-hnsw-index-with-pgvector>
- Malkov, Yashunin：Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs：<https://arxiv.org/abs/1603.09320>
