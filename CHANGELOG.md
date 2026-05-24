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

