
```cpp
#include <cstring>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <arpa/inet.h>

// 2025/05/08
// C语言通过getaddrinfo函数获取域名的IP地址

int main() {
    struct addrinfo hints, *res, *p;
    int status;
    char ipstr[INET6_ADDRSTRLEN];

    // 初始化hints结构
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC; // IPv4 或者 IPv6
    hints.ai_socktype = SOCK_STREAM; // TCP 套接字

    // 获取地址信息
    if ((status = getaddrinfo("www.baidu.com", NULL, &hints, &res)) != 0) {
        fprintf(stderr, "getaddrinfo error: %s\n", gai_strerror(status));
        return 1;
    }

    // 遍历地址列表
    for(p = res; p != NULL; p = p->ai_next) {
        void *addr;
        char *ipver;

        // 获取 IP 地址
        if (p->ai_family == AF_INET) { // IPv4
            struct sockaddr_in *ipv4 = (struct sockaddr_in *)p->ai_addr;
            addr = &(ipv4->sin_addr);
            ipver = "IPv4";
        } else { // IPv6
            struct sockaddr_in6 *ipv6 = (struct sockaddr_in6 *)p->ai_addr;
            addr = &(ipv6->sin6_addr);
            ipver = "IPv6";
        }

        // 将二进制 IP 地址转换为文本格式
        inet_ntop(p->ai_family, addr, ipstr, sizeof ipstr);
        printf("%s: %s\n", ipver, ipstr);
    }

    // 释放地址信息
    freeaddrinfo(res);

    return 0;
}
```