+++
date = '2025-04-17T04:05:47+08:00'
draft = false
title = '304 Not Modified 是怎么检测的？'
author = 'JekYUlll'
lastmod = '2025-04-17T04:05:47+08:00'
tags = ['web']
categories = ['web']
+++

**最终判断逻辑由服务端完成**。

• **浏览器行为**（客户端）：
  • 浏览器会缓存资源（如 HTML、图片、CSS 等），并根据服务端之前返回的响应头（如 `Cache-Control`、`Expires`、`ETag`、`Last-Modified`）决定是否发起**条件请求**。
  • 当缓存过期或页面刷新（非强制刷新）时，浏览器会向服务端发送一个带有**验证头**的请求，例如：
    ◦ `If-None-Match`（对应服务端之前返回的 `ETag`）
    ◦ `If-Modified-Since`（对应服务端之前返回的 `Last-Modified`）

• **服务端行为**：
  • 服务端收到请求后，根据客户端的验证头（`If-None-Match` 或 `If-Modified-Since`）检查资源是否已修改。
  • **如果资源未修改**，返回 **304 Not Modified**，且不返回资源内容，仅返回响应头。
  • **如果资源已修改**，返回 **200 OK** 并附带新内容。

• **浏览器**：负责发起条件请求（携带验证头），并根据响应状态码决定是否使用缓存。
• **服务端**：负责验证资源是否修改，并决定返回 304 或 200。

1. 用户首次访问网页，服务端返回资源，响应头包含：
   ```http
   HTTP/1.1 200 OK
   ETag: "abc123"
   Last-Modified: Wed, 01 Jan 2024 00:00:00 GMT
   Cache-Control: max-age=3600
   ```
2. 用户再次访问时，浏览器缓存未过期（`max-age=3600` 内）：
   • 直接使用缓存，无需请求服务端。
3. 缓存过期后，浏览器发起条件请求：
   ```http
   GET /example.html HTTP/1.1
   If-None-Match: "abc123"
   If-Modified-Since: Wed, 01 Jan 2024 00:00:00 GMT
   ```
4. 服务端验证资源未修改，返回：
   ```http
   HTTP/1.1 304 Not Modified
   ETag: "abc123"
   Last-Modified: Wed, 01 Jan 2024 00:00:00 GMT
   ```
5. 浏览器收到 304 后，继续使用本地缓存。

