#!/usr/bin/env python3
"""Update tags and categories in blog post frontmatter - v2 handles page bundles."""

import re
import os
import glob

POSTS_DIR = '/home/horeb/_code/_space/blog/content/posts'

# Mapping: basename of the file or directory (relative to POSTS_DIR) -> (category, [tags])
# For page bundles, use the path as seen by find (dir/filename.md)
# For regular files, use the filename
MAPPING = {
    '2024-06-02 means.md': ('algorithm', ['learning-path', 'cpp', 'golang']),
    '2024-07-29 [opengl] 菜 · OpenGL 初学笔记 -- Cherno + LearnOpenGL.md': ('game-dev', ['opengl', 'graphics']),
    '2024-08-10 [cpp] C++ 17 编译期 if.md': ('cpp', ['cpp17', 'template']),
    '2024-08-26 [cpp] C++ 的三五法则是什么？.md': ('cpp', ['memory', 'raii']),
    '2024-09-01 [cpp] Morden C++ 模板类型推导.md': ('cpp', ['template', 'type-deduction']),
    '2024-09-19 [cpp] 对 C++ 左值、右值、智能指针的思考.md': ('cpp', ['memory', 'smart-pointer']),
    '2024-09-28 [cpp] C++ 中 tuple 是如何实现的？.md': ('cpp', ['template', 'stl']),
    '2024-09-28 [cpp] C++ 的四种类型转换.md': ('cpp', ['rtti', 'type-casting']),
    '2024-10-09 [system] CPU 的五级流水线.md': ('infra', ['cpu', 'architecture']),
    '2024-11-04 [database] 事务Transaction的基础特性.md': ('database', ['transaction', 'acid']),
    '2024-11-04 [linux] Linux 进程优先级.md': ('linux', ['process', 'scheduling']),
    '2024-11-04 [linux] 文件系统与虚拟文件系统.md': ('linux', ['file-system', 'vfs']),
    '2024-11-21 [database] 表格展示 MySQL 基础数据类型.md': ('database', ['mysql', 'data-type']),
    '2024-12-21 [cpp] 缓存的设计.md': ('algorithm', ['cache', 'design-pattern']),
    '2024-12-23 [cpp] C++ 编译器返回值优化.md': ('cpp', ['compiler', 'optimization']),
    '2025-01-01 [cpp] 分段锁技术详解及 C++ 实现.md': ('cpp', ['concurrency', 'lock-free']),
    '2025-01-01 [cpp] 如何解决哈希冲突？.md': ('algorithm', ['hash', 'data-structure']),
    '2025-01-07 [linux] Linux 大文件传输场景题.md': ('linux', ['io', 'network', 'zero-copy']),
    '2025-01-08 [redis] Redis 数据结构之超日志 HyperLogLog.md': ('database', ['redis', 'data-structure']),
    '2025-01-09 [cpp] 从场景解析 C++ shared_from_this.md': ('cpp', ['memory', 'smart-pointer']),
    '2025-01-09 [cpp] [转载] C++的POD以及如何判断是否POD.md': ('cpp', ['memory', 'stl']),
    '2025-01-15 [cpp] C++ std::function 之脱裤子放屁的优化.md': ('cpp', ['stl', 'lambda']),
    '2025-01-15 [cpp] 用 C++ 实现 Golang 的 defer.md': ('cpp', ['macro', 'raii']),
    '2025-01-21 [cpp] 写个相对现代的 C++ 二叉搜索树.md': ('algorithm', ['data-structure', 'template']),
    '2025-01-29 [cpp] 检查字符串是否是另一个的子串.md': ('algorithm', ['string', 'search']),
    '2025-02-08 [cpp] C++ 同一进程的线程之间共享哪些资源？.md': ('linux', ['process', 'memory', 'interview']),
    '2025-02-08 [cpp] 运行时是把整个动态库都加载到内存中吗？.md': ('linux', ['process', 'linker', 'interview']),
    '2025-02-15 [alg] 扫描线算法计算区间重叠.md': ('algorithm', ['scanline', 'geometry']),
    '2025-02-17 [web] web 访问认证机制.md': ('backend', ['auth', 'security']),
    '2025-02-21 [cpp] Linux 内核中 C 语言的面向对象.md': ('cpp', ['linux', 'design-pattern']),
    '2025-02-23 [cpp] 记录一个c_str的小坑.md': ('cpp', ['memory', 'stl']),
    '2025-02-25 [cpp] 线程池调度：动态优先级老化（Aging）+ 双队列混合轮询.md': ('algorithm', ['thread-pool', 'scheduling']),
    '2025-02-25 [cpp] 计时器 timer 的设计.md': ('algorithm', ['timer', 'data-structure']),
    '2025-03-06 [linux] 在运行的时候，修改并且覆盖该二进制文件会如何？.md': ('linux', ['process', 'file-system', 'interview']),
    '2025-03-07 [linux] mmap 和零拷贝.md': ('linux', ['memory', 'io', 'zero-copy']),
    '2025-03-07 [linux] 内核的用户态和内核态.md': ('linux', ['process', 'memory']),
    '2025-03-07 [linux] 进程、线程、协程的资源消耗简述.md': ('linux', ['process', 'concurrency']),
    '2025-03-10 [cpp] C++八股：main函数之前执行了什么？.md': ('cpp', ['compiler', 'linker', 'interview']),
    '2025-03-10 [cpp] 事务性内存.md': ('cpp', ['concurrency', 'memory']),
    '2025-03-10 [cpp] 使用普通的互斥锁实现读写锁.md': ('cpp', ['concurrency', 'lock']),
    '2025-03-10 [web] 半衰期算法在后端的应用.md': ('backend', ['algorithm', 'ranking']),
    '2025-03-16 [cpp] heap only 和 stack only 的 C++ 对象.md': ('cpp', ['memory', 'stl']),
    '2025-03-16 [web] 解析LRU与LFU算法及C++实现.md': ('algorithm', ['cache', 'data-structure']),
    '2025-03-18 [frontend] 【01】Flet 学习笔记 --Flutter原理.md': ('frontend', ['flutter', 'python']),
    '2025-03-21 [web] Nginx 的多进程模型.md': ('backend', ['nginx', 'process-model']),
    '2025-03-21 [web] 流计算中的反向压力模型与 Reactive Streams --C++实现.md': ('backend', ['reactive-streams', 'back-pressure']),
    '2025-03-26 [alg] 更新的二进制差异算法.md': ('algorithm', ['diff', 'binary']),
    '2025-03-26 [cpp] 文件锁（FileLock）的本质与价值.md': ('linux', ['file-system', 'concurrency']),
    '2025-03-27 [linux] Linux里fork出子进程的时候，哪些内容是共享的？.md': ('linux', ['process', 'memory']),
    '2025-03-29 [linux] fork出的子进程是否继承文件描述符表？.md': ('linux', ['process', 'file-descriptor']),
    '2025-03-30 [linux] Linux 的CPU保护环，三环和零环.md': ('linux', ['process', 'security']),
    '2025-04-17 [web] 304 Not Modified 是怎么检测的？.md': ('backend', ['http', 'cache']),
    '2025-04-21 [cpp] C++ 中的乐观锁和悲观锁.md': ('cpp', ['concurrency', 'lock']),
    '2025-04-23 [web] 判断http服务器断点续传.md': ('backend', ['go', 'http', 'nginx']),
    '2025-04-25 [game] 【1】Blender学习日记-入门.md': ('game-dev', ['blender', '3d-modeling']),
    '2025-04-29 [linux] tmux实现wezterm保存会话.md': ('linux', ['terminal', 'tmux']),
    '2025-04-29 [linux] TODO 守护进程，setsid，Linux三个id，权限.md': ('linux', ['process', 'daemon']),
    '2025-04-29 [linux] 守护进程.md': ('linux', ['process', 'daemon']),
    '2025-05-05 [redis] Redis Stream和MQ.md': ('database', ['redis', 'mq']),
    '2025-05-08 [cpp]C语言通过getaddrinfo函数获取域名的IP地址.md': ('cpp', ['network', 'socket']),
    '2025-06-08 [linux] 容器化技术之 Linux namespace.md': ('linux', ['namespace', 'container']),
    '2025-10-18 [normal] 实验室服务器使用教程.md': ('linux', ['tutorial', 'server']),
    '2026-05-23 [backend] IM应用开发的两个消息库：同步库与存储库.md': ('backend', ['im', 'messaging']),
    '2026-05-24 [backend] Go 生态新动向：Range-over-Func 迭代器与 Swiss Table Map 实战.md': ('backend', ['go', 'data-structure']),
    '2026-05-24 [cpp] C++26 编译期反射（Reflection）来了：P2996 全解析.md': ('cpp', ['cpp26', 'reflection']),
    '2026-05-24 [linux] io_uring 完全指南：从 ring buffer 到零系统调用 I/O.md': ('linux', ['io-uring', 'async-io']),
    '2026-05-24 [system] AI Agent 开发框架三强争霸：MCP、A2A 与 OpenAI Agents SDK 深度对比.md': ('infra', ['ai-agent', 'mcp', 'a2a']),
    '2026-05-25 [cpp] C++26 反射：从模板元编程到编译期内省.md': ('cpp', ['cpp26', 'reflection']),
    '2026-05-25 [linux] io_uring 完全指南：从共享环形缓冲区到零系统调用异步 I O.md': ('linux', ['io-uring', 'async-io']),
    '2026-05-25 [linux] io_uring 实战：Linux 异步 I/O 的终极答案.md': ('linux', ['io-uring', 'async-io']),
    '2026-05-25 [system] Speculative Decoding：大模型推理加速的新范式.md': ('infra', ['llm-inference', 'speculative-decoding']),
    '2026-05-26 [backend] Web Search 技术漫谈：ddgs _ SearXNG _ Firecrawl 与搜索架构.md': ('backend', ['search-engine', 'web-crawler']),
    '2026-05-26 [cpp] C++26 时代的 AI 应答：当 C++ 开始为机器学习铺路.md': ('cpp', ['cpp26', 'ai', 'machine-learning']),
    '2026-05-26 [linux] eBPF 可观测性：在内核里跑代码改变了什么.md': ('linux', ['ebpf', 'observability']),
    '2026-05-26 [linux] Linux memfd 秘闻：内存文件描述符的秘密区域.md': ('linux', ['memfd', 'memory']),
    '2026-05-27 [backend] IM项目中发送2min内可以撤回怎么实现.md': ('backend', ['im', 'messaging']),
    '2026-05-27 [system] vLLM PagedAttention：大模型推理的显存管理革命.md': ('infra', ['vllm', 'pagedattention', 'llm-inference']),
}


def format_tags(tags_list):
    return "tags = ['" + "', '".join(tags_list) + "']"

def format_categories(cat):
    return "categories = ['" + cat + "']"


def update_file(filepath, category, tags):
    """Update tags and categories lines in a file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    # Find the +++ delimiters
    frontmatter_start = -1
    frontmatter_end = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '+++':
            if frontmatter_start == -1:
                frontmatter_start = i
            elif frontmatter_end == -1:
                frontmatter_end = i
                break
    
    if frontmatter_start == -1 or frontmatter_end == -1:
        # Try HTML-commented frontmatter: <!-- +++ ... +++ -->
        for i, line in enumerate(lines):
            stripped = line.strip()
            if '+++' in stripped:
                if frontmatter_start == -1:
                    frontmatter_start = i
                elif frontmatter_end == -1:
                    frontmatter_end = i
                    break
        if frontmatter_start == -1 or frontmatter_end == -1:
            print(f"  WARNING: No frontmatter found in {filepath}")
            return False
    
    # Find and update tags and categories lines within frontmatter
    tags_line_idx = -1
    cat_line_idx = -1
    
    for i in range(frontmatter_start + 1, frontmatter_end):
        line = lines[i].strip()
        if line.startswith('tags ') or line.startswith('tags='):
            tags_line_idx = i
        elif line.startswith('categories ') or line.startswith('categories='):
            cat_line_idx = i
    
    if tags_line_idx == -1 or cat_line_idx == -1:
        print(f"  WARNING: Could not find tags/categories lines in {filepath}")
        return False
    
    # Replace the lines
    lines[tags_line_idx] = format_tags(tags)
    lines[cat_line_idx] = format_categories(category)
    
    # Preserve original indentation
    # Find indentation of the tags line
    orig_indent_tags = ''
    for c in lines[tags_line_idx]:
        if c in ' \t':
            orig_indent_tags += c
        else:
            break
    orig_indent_cat = ''
    for c in lines[cat_line_idx]:
        if c in ' \t':
            orig_indent_cat += c
        else:
            break
    
    if orig_indent_tags:
        lines[tags_line_idx] = orig_indent_tags + format_tags(tags)
    if orig_indent_cat:
        lines[cat_line_idx] = orig_indent_cat + format_categories(category)
    
    new_content = '\n'.join(lines)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return True


def main():
    files_processed = 0
    files_skipped = 0
    errors = []
    
    # Use os.walk for recursive traversal (handles page bundles)
    for root, dirs, files in os.walk(POSTS_DIR):
        for filename in files:
            if not filename.endswith('.md'):
                continue
            filepath = os.path.join(root, filename)
            
            # Get relative path from POSTS_DIR
            rel_path = os.path.relpath(filepath, POSTS_DIR)
            
            if rel_path == '_index.md':
                print(f"SKIP: _index.md")
                files_skipped += 1
                continue
            
            if rel_path in MAPPING:
                category, tags = MAPPING[rel_path]
                success = update_file(filepath, category, tags)
                if success:
                    print(f"OK:   {rel_path} -> category: {category}, tags: {tags}")
                    files_processed += 1
                else:
                    errors.append(rel_path)
            else:
                print(f"MISS: {rel_path} (NOT IN MAPPING)")
                files_skipped += 1
    
    print(f"\n{'='*60}")
    print(f"Done. Processed: {files_processed}, Skipped: {files_skipped}, Errors: {len(errors)}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == '__main__':
    main()
