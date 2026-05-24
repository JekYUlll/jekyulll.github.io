+++
date = '2025-04-29T21:05:47+08:00'
draft = false
title = '[linux] 在wezterm里使用tmux实现history restore 保存历史会话'
author = 'JekYUlll'
lastmod = '2025-04-29T21:05:47+08:00'
tags = ['linux']
categories = ['linux']
+++

---

知乎链接：https://zhuanlan.zhihu.com/p/1961547067106259944

wezterm在打开主进程、或者ctrl+alt+t新建tab的时候默认是空的shell会话，有时候不够方便。

tmux可以轻松解决痛点，但是如果设置wezterm的default_prog为tmux（即每次会话都启动）：在wezterm里新建tab，你会发现和之前共享同一个tmux，相当于wezterm自带的tab功能没用了。

	config.default_prog = { "/usr/bin/tmux", "new-session", "-A", "-s", "main" }


于是：能否只让wezterm的第一个tab始终开启一个tmux来保存会话，其他tab则使用默认的新建shell功能？

具体实现：

	-- 第一个tab打开tmux，之后的为空的shell
	local tmux_started = false
	wezterm.on("gui-startup", function(cmd)
		-- 启动 wezterm 时自动打开一个 window
		local tab, pane, window = wezterm.mux.spawn_window(cmd or {})
		if not tmux_started then
			tmux_started = true
			-- 启动 tmux
			pane:send_text("tmux -u new-session -A -s main\n")
		end
	end)
加在wezterm配置合适的位置即可。


