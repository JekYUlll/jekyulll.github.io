+++
date = '2025-04-23T12:05:47+08:00'
draft = false
title = '实现服务端断点续传：Go与Nginx'
author = 'JekYUlll'
lastmod = '2025-04-23T12:05:47+08:00'
tags = ['go', 'web', 'backend']
categories = ['web']
+++

### 一、HTTP协议基础

HTTP协议通过**Range请求**实现断点续传：

1. **客户端请求指定范围**  
   客户端在请求头中携带`Range`字段，例如：
   ```http
   GET /file.zip HTTP/1.1
   Range: bytes=500-1000
   ```

2. **服务端响应部分内容**  
   若支持范围请求，服务端返回状态码`206 Partial Content`及对应数据片段：
   ```http
   HTTP/1.1 206 Partial Content
   Content-Range: bytes 500-1000/5000
   Content-Length: 501
   ```

3. **完整性校验机制**  
   通过`ETag`或`Last-Modified`头确保文件未变更，避免续传数据不一致。

---

### 二、Nginx静态资源断点续传

Nginx默认支持静态文件的断点续传。需要有以下配置：

```nginx
server {
    location /static {
        root /data/files;           # 文件存储路径
        add_header Accept-Ranges bytes;  # 声明支持字节范围请求
    }
}
```

**验证方法**：  
使用`curl`检测响应头：
```bash
curl -I http://your-domain/static/large-file.iso
```
若输出包含`Accept-Ranges: bytes`与`Content-Length`，则表明支持续传。

---

### 三、Go实现

对于动态生成的文件（如需鉴权的资源），需手动处理`Range`请求。

```go
package main

import (
    "fmt"
    "net/http"
    "os"
    "strconv"
    "strings"
)

func handleDownload(w http.ResponseWriter, r *http.Request) {
    filePath := "/data/dynamic-file.bin"
    file, err := os.Open(filePath)
    if err != nil {
        http.Error(w, "File not found", http.StatusNotFound)
        return
    }
    defer file.Close()

    fileInfo, _ := file.Stat()
    fileSize := fileInfo.Size()
    w.Header().Set("Content-Length", strconv.FormatInt(fileSize, 10))
    w.Header().Set("ETag", fmt.Sprintf("\"%x\"", fileInfo.ModTime().UnixNano()))

    rangeHeader := r.Header.Get("Range")
    if rangeHeader == "" {
        http.ServeContent(w, r, fileInfo.Name(), fileInfo.ModTime(), file)
        return
    }

    ranges := strings.Split(rangeHeader, "=")[1]
    parts := strings.Split(ranges, "-")
    start, _ := strconv.ParseInt(parts[0], 10, 64)
    end := fileSize - 1
    if parts[1] != "" {
        end, _ = strconv.ParseInt(parts[1], 10, 64)
    }

    if start >= fileSize || end >= fileSize {
        http.Error(w, "Requested range not satisfiable", http.StatusRequestedRangeNotSatisfiable)
        return
    }

    w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, fileSize))
    w.Header().Set("Content-Length", strconv.FormatInt(end-start+1, 10))
    w.WriteHeader(http.StatusPartialContent)

    file.Seek(start, 0)
    http.ServeContent(w, r, fileInfo.Name(), fileInfo.ModTime(), file)
}

func main() {
    http.HandleFunc("/download", handleDownload)
    http.ListenAndServe(":8080", nil)
}
```

• 解析`Range`请求头并验证范围有效性
• 使用`Seek`定位文件指针，返回部分内容
• 通过`ETag`实现文件一致性校验

---

### 四、客户端如何检测服务端是否支持？

可通过以下步骤判断：

1. **发送HEAD请求**  
   获取响应头信息：
   ```bash
   curl -I http://your-domain/file.zip
   ```

2. **检查关键头字段**  
   • **`Accept-Ranges: bytes`**：表明支持字节范围请求
   • **`Content-Length`**：必须存在且为固定值（动态内容可能无法支持）
   • **`ETag`或`Last-Modified`**：用于文件变更校验

3. **实验性范围请求测试**  
   发送带`Range`头的GET请求：
   ```bash
   curl -H "Range: bytes=0-100" http://your-domain/file.zip
   ```
   若响应状态码为`206`且包含`Content-Range`头，则确认支持续传。

---

### 五、Nginx反向代理Go服务的注意事项

当Go服务部署于Nginx后，需确保配置正确处理Range请求：

```nginx
location /go-download {
    proxy_pass http://go-backend:8080/download;
    proxy_set_header Range $http_range;    # 传递原始Range头
    proxy_set_header If-Range $http_if_range;
    proxy_hide_header Accept-Ranges;      # 避免与后端冲突
    proxy_http_version 1.1;               # 支持HTTP/1.1特性
}
```

• 确认Nginx与Go服务对文件有读取权限
• 检查`Content-Length`是否被意外修改（如Gzip压缩）
• 使用`tcpdump`或Wireshark抓包验证请求头传递

---

### 六、边界问题与优化建议

1. **多范围请求处理**  
   支持形如`Range: bytes=0-100,200-300`的请求需分段响应，可通过Go的`multipart/byteranges`实现。

2. **速率限制与防滥用**  
   Nginx配置限速：
   ```nginx
   location /download {
       limit_rate 1m;  # 限制下载速度为1MB/s
   }
   ```

3. **日志监控**  
   监控`206`状态码频率，识别异常续传行为。