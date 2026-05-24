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

