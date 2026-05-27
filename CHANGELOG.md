# blog — Hermes Session Changelog

### 2026-05-24 01:26:45 UTC | session `20260524_092` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/[system] IM应用开发的两个消息库：同步库与存储库.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/\[system\] IM应用开发的两个消息库：同步库与存储库.md" && git commit -m "new post: IM应用开发的两个消息库：同步库与存储库" && git push origin master`

### 2026-05-24 01:33:06 UTC | session `20260524_092` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [backend] IM应用开发的两个消息库：同步库与存储库.md`
Commands:
  - `cd /home/horeb/_code/_space/blog/content/posts && mv "2026-05-24 [system] IM应用开发的两个消息库：同步库与存储库.md" "2026-05-24 [backend] IM应用开发的两个消息库：同步库与存储库.md"`
  - `cd /home/horeb/_code/_space/blog && git add -A && git commit -m "fix: reclassify IM message store article from system to backend

- Move file: [system] -> [backend]
- Update frontmatter tags/categorie…`

### 2026-05-24 01:36:44 UTC | session `20260524_092` | model `deepseek-v4-flash` | interrupted
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/hugo.toml`
  - `/home/horeb/_code/_space/blog/layouts/partials/head.html`
  - `/home/horeb/_code/_space/blog/layouts/posts/rss.xml`
Commands:
  - `curl -sI https://horeb.top/ | head -20`
  - `cd /home/horeb/_code/_space/blog && git log --oneline -10`
  - `cd /home/horeb/_code/_space/blog && git branch -a | grep -i gh-pages`
  - … and 108 more

### 2026-05-24 01:46:10 UTC | session `20260524_092` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/.github/workflows/deploy.yml`
Commands:
  - `cd /home/horeb/_code/_space/blog && git log --oneline -5`
  - `cd /home/horeb/_code/_space/blog && git revert 2203e70 --no-edit`
  - `cd /home/horeb/_code/_space/blog && git stash && git revert 2203e70 --no-edit && git stash pop`
  - … and 80 more

### 2026-05-24 02:07:42 UTC | session `20260524_092` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell
**Files:**
  - `/home/horeb/_code/_space/blog/hugo.toml`
Commands:
  - `cd /home/horeb/_code/_space/blog && git log --oneline -3 && echo "---gh-pages---" && git fetch origin gh-pages && git log origin/gh-pages --oneline -3`
  - `git show origin/gh-pages:posts/index.html | grep -c "post-item" && echo "---2026---" && git show origin/gh-pages:posts/index.html | grep -oP '2026-' | head -5 && echo "---backend tag---" && git ls-tre…`
  - `git -C /home/horeb/_code/_space/blog show origin/gh-pages:posts/index.html | grep -c "post-item" && echo "---2026---" && git -C /home/horeb/_code/_space/blog show origin/gh-pages:posts/index.html | gr…`
  - … and 39 more

### 2026-05-24 02:49:47 UTC | session `20260524_092` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [backend] IM应用开发的两个消息库：同步库与存储库.md`
Commands:
  - `cd /home/horeb/_code/_space/blog/content/posts && mv "2026-05-24 [backend] IM应用开发的两个消息库：同步库与存储库.md" "2026-05-23 [backend] IM应用开发的两个消息库：同步库与存储库.md"`
  - `cd /home/horeb/_code/_space/blog && git add -A && git commit -m "fix: change blog post date to yesterday (2026-05-23) to avoid future-date filtering" && git push origin master`
  - `sleep 45 && curl -s https://horeb.top/posts/ | grep "2026\|post-year" | head -5`

### 2026-05-24 03:19:04 UTC | session `20260524_092` | model `deepseek-v4-pro` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/.github/workflows/deploy.yml`
  - `/home/horeb/_code/_space/blog/hugo.toml`
Commands:
  - `cd /home/horeb/_code/_space/blog && git status --short`
  - `cd /home/horeb/_code/_space/blog && git stash`
  - `cd /home/horeb/_code/_space/blog && git submodule add https://github.com/CaiJimmy/hugo-theme-stack.git themes/stack 2>&1`
  - … and 57 more

### 2026-05-24 06:51:03 UTC | session `20260524_144` | model `deepseek-v4-flash` | interrupted
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [linux] 从文件描述符到 epoll：理解 Linux I/O 多路复用.md`
Commands:
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-24 [linux] 从文件描述符到 epoll：理解 Linux I/O 多路复用.md" && git commit -m "new post: [linux] 从文件描述符到 epoll：理解 Linux I/O 多路复用" && git push origi…`
  - `sleep 60 && cd /home/horeb/_code/_space/blog && git fetch origin gh-pages && git log origin/gh-pages --oneline -3`

### 2026-05-24 07:12:42 UTC | session `20260524_144` | model `deepseek-v4-flash` | interrupted
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && git rm "content/posts/2026-05-24 [linux] 从文件描述符到 epoll：理解 Linux I/O 多路复用.md" && git commit -m "remove shallow epoll post, replacing with io_uring deep dive" && git …`
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md" && git commit -m "new post: [linux] io_uring 完全指南" && git push origin master`
  - … and 4 more

### 2026-05-24 07:21:05 UTC | session `20260524_151` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [backend] Go 生态新动向：Range-over-Func 迭代器与 Swiss Table Map 实战.md`
Commands:
  - `curl -sL "https://raw.githubusercontent.com/golang/go/master/doc/next/release-notes.md" 2>/dev/null | head -500 || echo "FAILED"`
  - `curl -sL "https://go.dev/doc/go1.23" 2>/dev/null | grep -oP '(?<=<article class="Article">)[\s\S]*?(?=</article>)' | head -200 || echo "FAILED"`
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - … and 2 more

### 2026-05-24 07:23:11 UTC | session `20260524_151` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [system] AI Agent 开发框架三强争霸：MCP、A2A 与 OpenAI Agents SDK 深度对比.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-24 [system] AI Agent 开发框架三强争霸：MCP、A2A 与 OpenAI Agents SDK 深度对比.md"`
  - `cd /home/horeb/_code/_space/blog && git commit -m "new post: AI Agent 开发框架三强争霸：MCP、A2A 与 OpenAI Agents SDK 深度对比"`
  - `cd /home/horeb/_code/_space/blog && git push origin master`

### 2026-05-24 07:31:11 UTC | session `20260524_144` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [cpp] C++26 编译期反射（Reflection）来了：P2996 全解析.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && git log --oneline -5 && echo "---gh-pages---" && git fetch origin gh-pages && git log origin/gh-pages --oneline -5`
  - `sleep 120 && cd /home/horeb/_code/_space/blog && git fetch origin gh-pages && git log origin/gh-pages --oneline -5`
  - `sleep 30 && cd /home/horeb/_code/_space/blog && git fetch origin gh-pages && git log origin/gh-pages --oneline -5`
  - … and 6 more

### 2026-05-24 20:33:37 UTC | session `20260525_031` | model `deepseek-v4-pro` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-25 [cpp] C++26 反射：从模板元编程到编译期内省.md`
Commands:
  - `curl -sL 'https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2025/p2996r13.html' | sed -n '/<h2>.*Introduction/,/<h2>/p' | lynx -stdin -dump -width=120 2>/dev/null | head -80`
  - `curl -sL 'https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2025/p2996r13.html' | html2text 2>/dev/null | head -120 || python3 -c "
from html.parser import HTMLParser
import sys, urllib.request

cla…`
  - `python3 -c "
from html.parser import HTMLParser
import urllib.request

class Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False
…`
  - … and 10 more

### 2026-05-24 21:01:01 UTC | session `20260525_031` | model `deepseek-v4-pro` | completed
**Tools:** edit, shell
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md`
Commands:
  - `grep "^title = " "/home/horeb/_code/_space/blog/content/posts/2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md"`
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md" && git commit -m "fix: remove [linux] prefix from io_uring post title" && git p…`

### 2026-05-25 02:04:38 UTC | session `cron_6cc8018` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-25 [linux] io_uring 实战：Linux 异步 I/O 的终极答案.md`
Commands:
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-25 [linux] io_uring 实战：Linux 异步 I/O 的终极答案.md"`
  - `cd /home/horeb/_code/_space/blog && git commit -m "new post: [linux] io_uring 实战：Linux 异步 I/O 的终极答案"`
  - … and 8 more

### 2026-05-25 15:48:10 UTC | session `20260525_234` | model `deepseek-v4-flash` | interrupted
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/.blog-topic-registry.json`
  - `/tmp/build_registry.py`
Commands:
  - `cd /home/horeb/_code/_space/blog && ls -la content/posts/ | head -60`
  - `cd /home/horeb/_code/_space/blog && ls -la content/posts/ | tail -n +60`
  - `cd /home/horeb/_code/_space/blog && ls content/posts/`
  - … and 55 more

### 2026-05-25 15:53:13 UTC | session `cron_6cc8018` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-25 [linux] io_uring 完全指南：从共享环形缓冲区到零系统调用异步 I O.md`
Commands:
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `curl -sL "https://man7.org/linux/man-pages/man7/io_uring.7.html" | head -500`
  - `curl -sL "https://unixism.net/loti/" | head -500`
  - … and 23 more

### 2026-05-25 15:56:41 UTC | session `20260525_234` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-25 [system] Speculative Decoding：大模型推理加速的新范式.md`
Commands:
  - `python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --check-topic linux 'BPF 可观测性'`
  - `python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --check-topic system 'Speculative Decoding：大模型推理加速的新范式'`
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - … and 7 more

### 2026-05-25 17:10:47 UTC | session `20260526_010` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [linux] Linux memfd 秘闻：内存文件描述符的秘密区域.md`
Commands:
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `curl -sL "https://man7.org/linux/man-pages/man2/memfd_create.2.html" | head -1000`
  - `curl -sL "https://man7.org/linux/man-pages/man2/memfd_secret.2.html" | head -1000`
  - … and 9 more

### 2026-05-25 17:13:58 UTC | session `20260526_011` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [backend] Web Search 技术漫谈：ddgs / SearXNG / Firecrawl 与搜索架构.md`
Commands:
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && git add "content/posts/2026-05-26 [backend] Web Search 技术漫谈：ddgs / SearXNG / Firecrawl 与搜索架构.md" && git commit -m "new post: Web Search 技术漫谈" && git push origin mas…`
  - `sleep 90 && cd /home/horeb/_code/_space/blog && gh run list --workflow="Deploy Hugo Site" --limit=1 --json=databaseId,conclusion,status`
  - … and 2 more

### 2026-05-25 17:19:23 UTC | session `20260525_234` | model `deepseek-v4-flash` | completed
**Tools:** shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [backend] Web Search 技术漫谈：ddgs _ SearXNG _ Firecrawl 与搜索架构.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && gh run view 26411805067 --log 2>&1 | grep -E "deploy-pages|Created deployment for" && echo "---" && hugo list all 2>/dev/null | grep "Web Search"`
  - `python3 -c "
import urllib.request
# Check HTTP status - use URL encoding for Chinese characters
req = urllib.request.Request(
    'https://horeb.top/posts/2026-05-26-backend-web-search-%E6%8A%80%E6%9…`
  - `python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --list-categories`
  - … and 16 more

### 2026-05-25 17:42:44 UTC | session `20260525_234` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-25 [system] Speculative Decoding：大模型推理加速的新范式.md`
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [backend] Web Search 技术漫谈：ddgs _ SearXNG _ Firecrawl 与搜索架构.md`
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [linux] Linux memfd 秘闻：内存文件描述符的秘密区域.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && ls -t content/posts/*.md content/posts/*/ 2>/dev/null | head -10`
  - `cd /home/horeb/_code/_space/blog && ls content/posts/2024-*.md content/posts/2025-01-*.md 2>/dev/null | head -5`
  - `wc -l "/home/horeb/_code/_space/blog/content/posts/2026-05-25 [system] Speculative Decoding：大模型推理加速的新范式.md" "/home/horeb/_code/_space/blog/content/posts/2026-05-26 [linux] Linux memfd 秘闻：内存文件描述符的秘密区域.…`
  - … and 9 more

### 2026-05-25 22:55:48 UTC | session `20260526_042` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [cpp] C++26 时代的 AI 应答：当 C++ 开始为机器学习铺路.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --list-categories`
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --check-topic 'cpp' '在AI编程快速进化的情况下，C++的发展前景：现代C++应对AI的新技术和新提案'`
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - … and 2 more

### 2026-05-26 02:06:43 UTC | session `cron_6cc8018` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-26 [linux] eBPF 可观测性：在内核里跑代码改变了什么.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py 2>&1`
  - `date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --list-categories 2>&1`
  - … and 10 more

### 2026-05-27 02:08:20 UTC | session `cron_6cc8018` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-27 [system] vLLM PagedAttention：大模型推理的显存管理革命.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --list-categories`
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --list-all`
  - `ls -la /home/horeb/_code/_space/blog/content/posts/ | tail -40`
  - … and 9 more

### 2026-05-27 04:59:52 UTC | session `20260527_120` | model `kimi-k2.6` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/content/posts/2026-05-27 [backend] IM项目中发送2min内可以撤回怎么实现.md`
Commands:
  - `cd /home/horeb/_code/_space/blog && python3 ~/.hermes/skills/blog/hugo-blog-new-post/scripts/build-registry.py --check-topic 'backend' 'IM项目中发送2min内可以撤回怎么实现'`
  - `cd /home/horeb/_code/_space/blog && date '+%Y-%m-%dT%H:%M:%S+08:00'`
  - `cd /home/horeb/_code/_space/blog && ls content/posts/ | tail -5`
  - … and 9 more

### 2026-05-27 05:58:29 UTC | session `20260527_135` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/.gitmodules`
  - `/home/horeb/_code/_space/blog/assets/jsconfig.json`
  - `/home/horeb/_code/_space/blog/config/_default/hugo.toml`
  - `/home/horeb/_code/_space/blog/config/_default/languages.zh-cn.toml`
  - `/home/horeb/_code/_space/blog/config/_default/markup.toml`
  - `/home/horeb/_code/_space/blog/config/_default/menus.zh-cn.toml`
  - `/home/horeb/_code/_space/blog/config/_default/params.toml`
  - `/home/horeb/_code/_space/blog/content/page/archives/index.md`
  - `/home/horeb/_code/_space/blog/content/page/search/index.md`
  - `/home/horeb/_code/_space/blog/hugo.toml`
Commands:
  - `cd /home/horeb/_code/_space/blog && ls -la`
  - `cd /home/horeb/_code/_space/blog && ls -la config/ 2>/dev/null || echo "config/ not found"`
  - `cd /home/horeb/_code/_space/blog && ls -la themes/`
  - … and 307 more

### 2026-05-27 06:37:43 UTC | session `20260527_143` | model `deepseek-v4-flash` | completed
**Tools:** edit, shell, write
**Files:**
  - `/home/horeb/_code/_space/blog/config/_default/languages.zh-cn.toml`
  - `/home/horeb/_code/_space/blog/config/_default/params.toml`
  - `/home/horeb/_code/_space/blog/update_taxonomy.py`
  - `/home/horeb/_code/_space/blog/update_taxonomy_v2.py`
Commands:
  - `cd /home/horeb/_code/_space/blog && echo "=== 当前所有 tags ===" && hugo list tags 2>/dev/null && echo "=== 当前所有 categories ===" && hugo list categories 2>/dev/null && echo "=== 文章 tag/category 统计 ===" &&…`
  - `ls -la ~/Pictures/WallPaper/ 2>/dev/null && echo "---" && file ~/Pictures/WallPaper/* 2>/dev/null | head -30`
  - `cd /home/horeb/_code/_space/blog && for f in $(find content/posts -name "*.md" -o -name "index.md" | sort); do echo "=== $f ==="; head -20 "$f"; done`
  - … and 175 more

