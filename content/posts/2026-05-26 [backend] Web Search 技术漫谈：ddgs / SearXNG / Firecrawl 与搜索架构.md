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

AI Agent 的「实时调研」能力已经成为标配。无论是写博客前查资料、回答问题前核实事实，还是分析最新市场动态，Agent 都需要在运行时通过搜索引擎获取实时信息。

但这里有一个关键区别：**Agent 需要的不是广告，不是 SEO 优化的摘要，不是几十个导航链接——它需要的是机器可读的结构化结果和完整文档内容。**

传统的搜索引擎（比如你在浏览器里用的 Google/Bing）是为人类设计的。它们返回的搜索结果页充满了广告、推荐卡片、图片轮播、知识面板。人类可以轻松地跳过干扰找到想要的信息，但对 Agent 来说，这些 HTML 结构的解析成本极高，而且搜索引擎返回的 snippet 通常只有 200 个字符左右，根本无法让 LLM 理解页面的完整内容。

这就引出了本文要讨论的核心问题：**如何为 AI Agent 构建一个高效、可靠的搜索管道？**

答案是将搜索能力拆分为两个独立的后端——**Search Backend** 和 **Extract Backend**。

---

## 搜索后端 vs 提取后端

先看一个简化的架构图：

```
Agent → web_search(query) → Search Backend → URL list
     → web_extract(urls)  → Extract Backend → Full content
```

**Search Backend（搜索后端）**：根据用户 query 返回 URL 列表 + 标题 + 简短摘要。它的职责就是「找到相关页面在哪」。

**Extract Backend（提取后端）**：根据 URL 获取页面的完整内容，并转换成 LLM 友好的格式（通常是 Markdown）。它的职责是「把页面内容读出来」。

为什么要把这两个功能分开？

1. **延迟要求不同**：搜索需要快速返回（通常在 1-3 秒内），而提取可能要 10-30 秒（尤其需要渲染 JavaScript 的页面）。
2. **成本模型不同**：搜索 API 和提取 API 往往是分开计费的。
3. **可用性需求不同**：搜索可以回退到另一个引擎（DDG 挂了换 Google），提取也可以回退到纯 HTML 解析。
4. **接口语义不同**：搜索返回的是 URL 列表，提取返回的是完整文档——让一个 API 同时做好两件事很难。

用「图书馆」类比：Search Backend 是目录检索系统，告诉你哪本书在哪个架子上；Extract Backend 是把那本书从架子上拿下来翻给你看。两个步骤缺一不可。

---

## 主流方案对比

### Search Backend 选项

| 后端 | 类型 | 是否需要 API Key | 优点 | 缺点 |
|------|------|-----------------|------|------|
| **ddgs** | Search | ❌ 免费 | 即装即用，搜索质量不错 | 有频率限制，search only |
| **SearXNG** | Search | ❌ 自托管 | 可聚合多引擎，完全可控 | 维护成本高，易被 CAPTCHA |
| **Tavily** | Search | ✅ 需注册 | AI 优化结果，响应速度快 | 免费额度 1000/月 |
| **Exa** | Search | ✅ 需注册 | 专为 AI 设计，语义搜索 | 付费 |

#### ddgs（DuckDuckGo 的 Python 客户端）

[ddgs](https://github.com/joedicastro/ddgs) 是一个纯 Python 的 DuckDuckGo 搜索客户端，没有 API Key，直接对 DuckDuckGo 的 HTML 页面进行抓取解析。搜索结果质量出人意料地好——DuckDuckGo 本身聚合了 Bing 和其他引擎的结果，覆盖度和相关性都很可观。

```python
from ddgs import ddgs_web_search

for result in ddgs_web_search(query="AI Agent architecture 2025"):
    print(f"{result['title']}: {result['href']}")
```

优点：零配置、零成本、质量稳定。
缺点：有频率限制（过快请求会被 ban），只返回搜索结果的文本摘要，无法获取页面完整内容。

#### SearXNG

[SearXNG](https://docs.searxng.org/) 是一个自托管的元搜索引擎，可以同时向 Google、DuckDuckGo、Brave、Bing 等多个搜索引擎发起请求，聚合结果并去除重复。

Docker 部署非常方便：

```yaml
# docker-compose.yml
services:
  searxng:
    image: searxng/searxng:latest
    ports:
      - "8888:8080"
    environment:
      - SEARXNG_BASE_URL=https://your-domain/
    volumes:
      - ./searxng-data:/etc/searxng
```

优点：完全自控，可聚合多引擎，支持 JSON API。
缺点：维护成本较高，容易被搜索引擎识别为爬虫并触发 CAPTCHA。

#### Tavily / Exa

这两个是专为 AI Agent 设计的付费搜索 API。Tavily 由 LangChain 投资，Exa 则被很多 Agent 框架（包括 Hermes Agent）作为默认搜索后端。它们返回结构化的 JSON，包含摘要、来源、甚至相关图片。

优点：速度快、结果 AI 友好、有免费额度。
缺点：免费额度有限（Tavily 1000/月），深度使用时需要付费。

### Extract Backend 选项

| 后端 | 类型 | 是否需要 API Key | 优点 | 缺点 |
|------|------|-----------------|------|------|
| **Firecrawl** | Extract | ✅ 需注册 | 全文提取质量好 | 免费 1000 pages/月 |
| **浏览器自动化** | Extract | ❌ 本机 | 能对付复杂反爬 | 慢，资源消耗大 |
| **curl + HTML 解析** | Extract | ❌ 本机 | 轻量无依赖 | 对 JS 渲染页面无效 |

#### Firecrawl

[Firecrawl](https://firecrawl.com/) 是一个专门做网页内容提取的服务，能把任意 URL 转成干净的 Markdown。它内置了浏览器渲染引擎，可以处理 JavaScript 生成的页面内容。

```python
from firecrawl import FirecrawlApp

app = FirecrawlApp(api_key="your-key")
content = app.scrape_url("https://example.com")
print(content['markdown'])
```

免费额度 1000 pages/month，对于个人博客调研和日常使用完全足够。

#### 浏览器自动化

通过 Playwright/Selenium 打开浏览器、加载页面、截图或读取 DOM。可以应对绝大多数反爬虫策略，但速度慢（每个页面 5-30 秒），资源消耗大。

#### curl + HTML 解析

最轻量的方案，用 `curl` 获取 HTML，然后用 `BeautifulSoup` 或 `trafilatura` 提取正文。适合静态页面，对 SPA 和动态加载的内容无能为力。

---

## 实际部署踩坑

理论说完了，聊点真实的。

### SearXNG + Clash 代理的血泪史

我最早部署 SearXNG 是希望通过它聚合 Google、DDG、Brave 三个引擎的结果，让搜索质量达到最优。部署方案很标准：Docker 跑 SearXNG，出站经过 Clash 代理（因为某些引擎在国内不可用）。

**结果大翻车。**

代理 IP 被 Google、DDG、Brave 集体识别为数据中心流量，全部触发 CAPTCHA。SearXNG 的配置里虽然有「绕过 CAPTCHA」的选项，但对于高频且集中的 API 调用，CAPTCHA 几乎是不可避免的。最终所有引擎都跪了，只剩 Brave——而 Brave 在无法通过 CAPTCHA 后的 fallback 结果质量极差，大量返回词典网站、测速网站这些垃圾内容。

**我的修复方案：**

1. 禁用 Brave 引擎（返回垃圾比不返回更糟糕）
2. 启用了 [Mojeek](https://mojeek.com/)——一个拥有独立爬虫的搜索引擎，不依赖 Google/Bing 的结果。
3. 同时保留了部分有 API Key 的付费引擎作为保底。

但即便如此，维护 SearXNG 的成本依然很高：需要定期刷新代理 IP、更新引擎配置、处理 CAPTCHA 触发后的手动干预。

### 最终切回了 ddgs

经过几周的维护折磨，我最终选择了 **ddgs** 作为 Search Backend。讽刺的是，**一分钱不花、不需要自托管的 ddgs，搜索结果质量反而比精心配置的 SearXNG 更好。** DuckDuckGo 背后的聚合质量相当扎实，没有 CAPTCHA 问题（因为 DDG 本身不会对普通搜索流量设限），只需要注意请求频率不要过高即可。

### Firecrawl 替代了 broken 的 extract

之前我用 ddgs 也尝试提取页面全文，但 ddgs 的 extract 功能非常不稳定——很多时候返回空内容或乱码。后来换成了 **Firecrawl** 做 Extract Backend，提取效果非常稳定，Markdown 格式干净，对 LLM 非常友好。

---

## 代码实战：配置一个 Agent 搜索管道

在 Hermes Agent 中，配置搜索管道只需要两条命令：

```bash
hermes config set web.search_backend ddgs
hermes config set web.extract_backend firecrawl
```

配置后的完整工作流程如下：

```python
# 伪代码：Agent 搜索管道
async def research_topic(query: str):
    # Step 1: Search — 找到相关页面
    search_results = await web_search(query)  # ddgs
    # 返回: [{"title": "...", "url": "...", "snippet": "..."}, ...]
    
    # Step 2: Extract — 获取完整内容
    urls = [r["url"] for r in search_results[:3]]
    full_contents = await web_extract(urls)  # Firecrawl
    # 返回: [{"url": "...", "markdown": "..."}, ...]
    
    return full_contents
```

这个分离设计的优势在于：

- **可替换性**：Search Backend 可以随时切换（ddgs ↔ SearXNG ↔ Tavily），不影响 Extract 层。
- **失败隔离**：搜索失败不影响提取（可以手动提供 URL），提取失败不影响搜索。
- **成本控制**：搜索可以走免费方案（ddgs），提取走付费方案（Firecrawl），只对有价值的内容付费。

---

## 今日可执行动作

如果你也想在自己的 Agent 中实现这套搜索管道，今天的操作清单如下：

1. **安装 Firecrawl**：去 [firecrawl.com](https://firecrawl.com) 注册账号，在 Dashboard 获取 API Key
2. **配置 Search Backend**：
   ```bash
   hermes config set web.search_backend ddgs
   ```
3. **配置 Extract Backend**：
   ```bash
   hermes config set web.extract_backend firecrawl
   hermes config set firecrawl.api_key "sk-xxx"
   ```
4. 测试搜索管道：
   ```bash
   hermes search "最新 AI Agent 框架对比 2025"
   ```
5. 如果遇到 SearXNG 同类问题，尝试切回 ddgs 简化部署。

---

## 参考

- [ddgs - Python DuckDuckGo Search Client](https://github.com/joedicastro/ddgs)
- [SearXNG Documentation](https://docs.searxng.org/)
- [Firecrawl Documentation](https://docs.firecrawl.dev/)
- [Tavily API](https://tavily.com/)
- [Exa API](https://exa.ai/)
- [Mojeek Search Engine](https://mojeek.com/)
- [Hermes Agent 配置文档](https://hermes-agent.nousresearch.com/docs)
