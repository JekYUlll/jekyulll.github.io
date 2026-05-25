+++
date = '2026-05-26T01:11:41+08:00'
draft = false
title = 'Web Search 技术漫谈：ddgs / SearXNG / Firecrawl 与搜索架构'
author = 'JekYUlll'
lastmod = '2026-05-26T01:11:41+08:00'
tags = ['backend']
categories = ['backend']
+++

## 背景

AI Agent 的「实时调研」能力已经成为标配。写博客前查资料、回答问题前核实事实、分析最新市场动态——Agent 都需要实时搜索。

但这里有一个关键区别：**Agent 不需要广告，不需要 SEO 摘要，不需要几十个导航链接。它需要的是完整文档内容。**

传统搜索引擎的返回格式（标题 + 200 字 snippet）对人类够用，对 AI 来说太短了。一个 LLM 要从搜索结果中理解技术概念，需要读完整原文。这就引出了搜索架构的一个核心分层：**Search Backend** 和 **Extract Backend**。

## 搜索后端 vs 提取后端

这两个概念是 AI Agent 搜索管道的两个阶段：

```
Agent → web_search(query) → Search Backend → [URL 列表]
     → web_extract(urls)  → Extract Backend → [完整内容]
```

**Search Backend（搜索后端）** 负责根据 query 找到相关的 URL。它的输出是一组链接，每条附带标题和一段简介。它解决的是「去哪儿找」的问题。

**Extract Backend（提取后端）** 负责根据 URL 获取页面的完整文本内容。它的输出是整篇文章的 markdown 或纯文本。它解决的是「怎么读」的问题。

为什么需要拆成两个？三个原因：

| 维度 | Search Backend | Extract Backend |
|------|---------------|-----------------|
| 任务 | 找到页面 | 读取页面 |
| 输出 | URL + snippet | 全文 markdown |
| 延迟要求 | 低（几百 ms） | 中（1-5s） |
| 频率 | 每次搜索 1-2 次 | 每 URL 一次 |
| 反爬难度 | 高（搜索引擎都很敏感） | 中（普通网站 ok） |
| API Key | 通常需要 | 通常需要 |

两个阶段对稳定性、成本和延迟的要求不同，分开实现可以独立调优。

## 主流 Search Backend 方案

### 1. ddgs（DuckDuckGo 的 Python 客户端）

```bash
pip install ddgs
```

```python
from ddgs import DDGS

with DDGS() as client:
    results = client.text('linux memfd secret', max_results=5)
    for r in results:
        print(f"[{r['title']}]({r['href']})")
        print(r['body'][:200])
```

纯免费，无需 API Key，即装即用。底层是模拟浏览器向 DuckDuckGo 的 HTML 接口发起搜索。搜索质量对英语技术关键词来说相当不错——我实测 `speculative decoding` 返回了 Google Research 博客、NVIDIA Developer Blog、arXiv 论文。

**缺点：** 有服务端频率限制，不能高并发。且是 search only，不能做 extract。

### 2. SearXNG（自托管元搜索引擎）

```yaml
# docker-compose.yml
services:
  searxng:
    image: searxng/searxng:latest
    ports:
      - "18765:8080"
    environment:
      - SEARXNG_BASE_URL=http://localhost:18765
```

SearXNG 是一个 Docker 部署的元搜索引擎，聚合了 245 个引擎（Google、DuckDuckGo、Brave、Bing、Wikipedia……），用户可自由启用/禁用。

**优点：** 完全可控，可以精细选配引擎，单节点部署。

**坑：** 出站流量通常走代理。如果代理 IP 被搜索引擎 CAPTCHA 封锁，所有 scraping 引擎同时失效。我遇到的真实情况：Google + DuckDuckGo + Brave + Startpage 全部被 CAPTCHA 封锁，只剩 Brave 在工作，而 Brave 返回词典和测速站等垃圾。

**修复方式：** 禁用 Brave，启用 Mojeek（有独立爬虫库的搜索引擎）和 API 类引擎（HackerNews、GitHub、StackOverflow）。或者配置 Google Custom Search API Key 绕过 CAPTCHA。

### 3. Tavily / Exa（AI 原生搜索 API）

```bash
export TAVILY_API_KEY=tvly-xxx
```

```python
from tavily import TavilyClient

client = TavilyClient()
results = client.search(query="memfd_create Linux API")
```

这些是专为 AI Agent 设计的搜索 API。Tavily 免费 1000 次/月，Exa 同样有免费额度。它们返回的内容更结构化、更干净，但需要注册账号。

### 对比总表

| 后端 | 类型 | API Key | 免费额度 | 搜索质量 | 维护成本 |
|------|------|---------|---------|---------|---------|
| ddgs | Search | ❌ 无 | 无限（有频率限制） | ⭐⭐⭐⭐ | 极低 |
| SearXNG | Search | ❌ 自托管 | 无限 | ⭐⭐⭐（取决引擎）| 中（Docker） |
| Tavily | Search | ✅ | 1000/月 | ⭐⭐⭐⭐⭐ | 低 |
| Exa | Search | ✅ | 1000/月 | ⭐⭐⭐⭐⭐ | 低 |

## 主流 Extract Backend 方案

### 1. Firecrawl

```bash
curl -X POST https://api.firecrawl.dev/v1/scrape \
  -H "Authorization: Bearer fc-xxx" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown"]}'
```

Firecrawl 是目前推荐的 extract backend。免费 1000 pages/月，无需信用卡。它会自动将网页转为干净的 markdown，过滤掉导航、广告等噪音。对于技术文档类页面，提取质量很高。

### 2. 浏览器自动化

```python
browser_navigate(url="https://lwn.net/Articles/838160/")
browser_snapshot(full=True)
```

当网站有反爬限制时，Firecrawl 可能失败。此时 fallback 是用浏览器（Playwright/Selenium）加载页面并获取 DOM。Hermes 的 browser_navigate + browser_snapshot 就是做这个的。慢（需要加载 JS、渲染），但能应对大多数反爬网站。

### 3. curl + HTML 解析

```bash
curl -s "https://man7.org/linux/man-pages/man2/memfd_create.2.html" | \
  python3 -c "
import sys, re
html = sys.stdin.read()
text = re.sub(r'<[^>]+>', ' ', html)
text = re.sub(r'\s+', ' ', text).strip()
print(text[:2000])
"
```

纯文本 fallback。没有 CSS/JS，最快，但丢失了文档结构和格式。适用于 man pages、C++ 标准论文等纯内容页面。

### 对比总表

| 后端 | 类型 | 质量 | 速度 | 反爬能力 |
|------|------|------|------|---------|
| Firecrawl | API | ⭐⭐⭐⭐⭐ clean markdown | 中（2-5s） | 中 |
| 浏览器 | 自动化 | ⭐⭐⭐⭐ 完整 DOM | 慢（5-15s） | 强 |
| curl + sed | shell | ⭐⭐ 纯文本 | 快（<1s） | 弱 |

## 实际部署踩坑实录

在今年 5 月的实际配置中，我遇到了一个典型的搜索管道问题：

1. SearXNG Docker 部署在本地 127.0.0.1:18765
2. 它通过 Clash 代理（127.0.0.1:7890）访问外网
3. 代理节点的 IP 被 Google、DuckDuckGo、Brave 等搜索引擎集体拉黑
4. 所有 scraping 引擎返回 `CAPTCHA` 或 `too many requests`
5. 唯一还能响应的 Brave 返回了大量词典、测速站、游戏广告等垃圾
6. 搜索 "speculative decoding" 得到 0 条相关结果

修复过程：
- 禁用 Brave → 0 条结果（所有引擎都跪了）
- 启用 Mojeek（独立爬虫）+ HackerNews / GitHub / StackOverflow / Semantic Scholar（API 类引擎）
- 搜索得到 18 条相关结果，质量恢复正常

之后干脆把 search backend 切成了 ddgs——API Key 都不要，质量反而更好。

教训：代理 IP 被污染对搜索引擎的影响比想象中大。掌握多种后端方案并在它们之间切换，是 AI Agent 工程的必备技能。

## 代码实战：配置一个 Agent 搜索管道

以下是一个最小化的 Agent 搜索工具实现：

```python
import json
from urllib.request import urlopen, Request

def web_search(query: str, backend: str = "ddgs") -> list:
    """统一的搜索接口"""
    if backend == "ddgs":
        from ddgs import DDGS
        with DDGS() as client:
            return [
                {"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                for r in client.text(query, max_results=5)
            ]
    elif backend == "searxng":
        url = f"http://127.0.0.1:18765/search?q={query}&format=json"
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        return [
            {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in data.get("results", [])[:5]
        ]
    else:
        raise ValueError(f"Unknown backend: {backend}")

def web_extract(url: str, backend: str = "firecrawl") -> str:
    """统一的页面提取接口"""
    if backend == "firecrawl":
        import os
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        req = Request(
            "https://api.firecrawl.dev/v1/scrape",
            data=json.dumps({"url": url, "formats": ["markdown"]}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("data", {}).get("markdown", "")
    raise ValueError(f"Unknown extract backend: {backend}")

# 使用示例
if __name__ == "__main__":
    results = web_search("eBPF kernel tracing")
    print(f"Found {len(results)} results")
    for r in results[:2]:
        content = web_extract(r["url"])
        print(f"\n## {r['title']}\n{content[:300]}...")
```

这个实现封装了 `web_search` 和 `web_extract` 两个函数，支持切换到不同的后端，是 Agent 搜索管道的最小工程模板。

## 今日可执行动作

1. **检查当前搜索配置**：运行以下命令查看 Agent 当前的 search 和 extract 后端：
   ```bash
   grep -A1 "search_backend\|extract_backend" ~/.hermes/config.yaml
   ```
   尝试切换后端并观察搜索结果质量的变化：
   ```bash
   hermes config set web.search_backend ddgs   # 切换为 DuckDuckGo
   hermes config set web.search_backend searxng # 切换回 SearXNG
   ```

2. **注册 Firecrawl 并配置 extract**：Firecrawl 提供每月 1000 次免费提取。注册后设置：
   ```bash
   hermes config set web.extract_backend firecrawl
   # 将 API Key 写入 ~/.hermes/.env: FIRECRAWL_API_KEY=fc-xxx
   ```

3. **部署本地 SearXNG**：如果你有多引擎聚合需求，5 分钟就能部署：
   ```bash
   docker run -d --name searxng -p 18765:8080 searxng/searxng:latest
   ```
   然后配置 `~/.hermes/config.yaml` 中的 `search_backend: searxng`。

## 参考

- ddgs Python package: https://pypi.org/project/ddgs/
- SearXNG 文档: https://docs.searxng.org/
- Firecrawl: https://www.firecrawl.dev/
- Tavily: https://tavily.com/
- Hugging Face Blog: "Assisted Generation" (关于 Agent 工具使用)
