
(考察linux网络编程、系统编程、网络协议、网络传输协议等知识)

> 问：局域网内有A、B、C三台主机，A与B不知道相互之间的IP。A要向B传输一个1G的文件，怎么做？

---

大文件传输的优化：  
- 分块传输：将大文件分成多个小块（如4KB、8KB等），每次传输一块，避免占用过多内存。
- 校验和（Checksum）：在每一块传输后进行数据校验，确保数据的完整性。
- 带宽控制：通过控制每次发送的数据量来避免一次性传输过多数据，控制网络负载。

断点续传的实现：
- 记录传输进度：客户端和服务器都需要记录已经成功传输的数据块或字节的位置。
- 支持断点请求：客户端在恢复传输时，应该告知服务器从哪个位置开始传输。
- 校验和和确认机制：每次传输数据块后，都应该进行确认，确保数据正确传送。

**步骤**：

### 1. 使用局域网广播发现B的IP地址
由于A和B的IP地址不直接已知，A可以通过**局域网广播**来找到B的IP地址。A可以向网络中的所有主机发送一个*UDP广播消息*，所有主机都会接收到这个消息，B在接收到这个广播后，可以回复A，告知自己的IP地址。

- **UDP广播**：A可以通过发送一个UDP广播包到特定的端口，让局域网中的所有主机收到该消息。B可以通过监听这个端口，收到消息后回应自己的IP地址。

### 2. 使用TCP协议进行文件传输
一旦A得到了B的IP地址，就可以使用TCP协议进行文件传输。A通过TCP连接到B，建立数据通道，开始发送1GB的文件。

### 具体步骤和代码实现

#### 1. UDP广播发现B的IP地址

A使用UDP广播向局域网中的所有主机发送请求，B收到请求后会通过UDP回应自己的IP地址。

**A端：发送UDP广播请求**

```cpp
#include <iostream>
#include <cstring>
#include <arpa/inet.h>
#include <unistd.h>

#define BROADCAST_PORT 12345

void send_broadcast_message() {
    int sockfd;
    struct sockaddr_in broadcast_addr;
    int broadcast_enable = 1;
    const char* message = "Are you there, B? Please reply with your IP address.";

    // 创建UDP套接字
    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("Socket creation failed");
        return;
    }

    // 允许广播
    if (setsockopt(sockfd, SOL_SOCKET, SO_BROADCAST, &broadcast_enable, sizeof(broadcast_enable)) < 0) {
        perror("Setting broadcast option failed");
        close(sockfd);
        return;
    }

    memset(&broadcast_addr, 0, sizeof(broadcast_addr));
    broadcast_addr.sin_family = AF_INET;
    broadcast_addr.sin_port = htons(BROADCAST_PORT);
    broadcast_addr.sin_addr.s_addr = htonl(INADDR_BROADCAST);

    // 发送广播消息
    if (sendto(sockfd, message, strlen(message), 0, (struct sockaddr*)&broadcast_addr, sizeof(broadcast_addr)) < 0) {
        perror("Broadcast failed");
        close(sockfd);
        return;
    }

    std::cout << "Broadcast message sent!" << std::endl;

    close(sockfd);
}

int main() {
    send_broadcast_message();
    return 0;
}
```

**B端：接收UDP广播并回应自己的IP**

```cpp
#include <iostream>
#include <cstring>
#include <arpa/inet.h>
#include <unistd.h>

#define BROADCAST_PORT 12345
#define RESPONSE_PORT 12346

void listen_for_broadcasts() {
    int sockfd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_len = sizeof(client_addr);
    char buffer[1024];

    // 创建UDP套接字
    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("Socket creation failed");
        return;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(BROADCAST_PORT);
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    // 绑定UDP套接字
    if (bind(sockfd, (struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
        perror("Bind failed");
        close(sockfd);
        return;
    }

    // 接收广播消息
    while (true) {
        int recv_len = recvfrom(sockfd, buffer, sizeof(buffer), 0, (struct sockaddr*)&client_addr, &client_len);
        if (recv_len < 0) {
            perror("Failed to receive message");
            continue;
        }

        buffer[recv_len] = '\0';
        std::cout << "Received message: " << buffer << std::endl;

        // 如果消息包含特定请求，可以回复自己的IP地址
        std::string response = "IP Address of B: ";
        response += inet_ntoa(client_addr.sin_addr);
        sendto(sockfd, response.c_str(), response.length(), 0, (struct sockaddr*)&client_addr, client_len);
        std::cout << "Sent response with IP address: " << inet_ntoa(client_addr.sin_addr) << std::endl;
    }

    close(sockfd);
}

int main() {
    listen_for_broadcasts();
    return 0;
}
```

### 2. 使用TCP协议传输文件
一旦A得到了B的IP地址，A就可以通过TCP连接与B进行文件传输。

#### A端：通过TCP向B发送文件

```cpp
#include <iostream>
#include <fstream>
#include <cstring>
#include <arpa/inet.h>
#include <unistd.h>

#define SERVER_PORT 8080
#define CHUNK_SIZE 4096 // 4KB per chunk

void send_file(const std::string &filename, const std::string &ip_address) {
    int sockfd;
    struct sockaddr_in server_addr;
    char buffer[CHUNK_SIZE];
    std::ifstream file(filename, std::ios::binary);
    off_t offset = 0;  // 记录已经发送的文件偏移量

    // 创建TCP套接字
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        perror("Socket creation failed");
        return;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(SERVER_PORT);
    server_addr.sin_addr.s_addr = inet_addr(ip_address.c_str());

    // 连接到B
    if (connect(sockfd, (struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
        perror("Connection failed");
        close(sockfd);
        return;
    }

    // 发送文件的分块数据
    while (file.read(buffer, CHUNK_SIZE)) {
        // 发送文件块数据
        ssize_t bytes_sent = send(sockfd, buffer, file.gcount(), 0);
        if (bytes_sent < 0) {
            perror("Send failed");
            break;
        }
        offset += bytes_sent;

        // 发送每个块后，需要确认接收进度
        // 可以通过协议的方式要求B确认
        std::cout << "Sent chunk, current offset: " << offset << std::endl;
    }

    // 发送文件剩余的数据（如果有）
    if (file.gcount() > 0) {
        send(sockfd, buffer, file.gcount(), 0);
        offset += file.gcount();
    }

    std::cout << "File sent successfully! Total bytes sent: " << offset << std::endl;
    close(sockfd);
}

int main() {
    send_file("large_file.bin", "192.168.1.2");
    return 0;
}
```

#### B端：接收文件并保存

```cpp
#include <iostream>
#include <fstream>
#include <cstring>
#include <arpa/inet.h>
#include <unistd.h>

#define SERVER_PORT 8080
#define CHUNK_SIZE 4096 // 4KB per chunk

void receive_file(const std::string &filename) {
    int sockfd, newsockfd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_len = sizeof(client_addr);
    char buffer[CHUNK_SIZE];
    std::ofstream file(filename, std::ios::binary | std::ios::app);  // 以追加方式打开文件
    off_t offset = file.tellp();  // 获取文件当前的偏移量（即已经接收的字节数）

    // 创建TCP套接字
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        perror("Socket creation failed");
        return;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(SERVER_PORT);
    server_addr.sin_addr.s_addr = INADDR_ANY;

    // 绑定套接字
    if (bind(sockfd, (struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
        perror("Bind failed");
        close(sockfd);
        return;
    }

    // 监听
    listen(sockfd, 1);
    std::cout << "Waiting for connection..." << std::endl;

    // 接受连接
    newsockfd = accept(sockfd, (struct sockaddr*)&client_addr, &client_len);
    if (newsockfd < 0) {
        perror("Accept failed");
        close(sockfd);
        return;
    }

    // 接收文件
    while (true) {
        int recv_len = recv(newsockfd, buffer, CHUNK_SIZE, 0);
        if (recv_len <= 0) break;

        // 写入文件（追加模式）
        file.write(buffer, recv_len);
        offset += recv_len;

        std::cout << "Received chunk, current offset: " << offset << std::endl;
    }

    std::cout << "File received successfully! Total bytes received: " << offset << std::endl;

    close(newsockfd);
    close(sockfd);
}

int main() {
    receive_file("received_large_file.bin");
    return 0;
}
```

断点续传实现说明：
- A端：每发送完一个块，A会更新文件的偏移量（即`offset`），并传递该偏移量的信息。如果传输过程中发生中断，A可以记录上次发送的偏移量，从该位置开始重新传输。
- B端：B端会在接收每个块时，记录接收到的字节数（即`offset`）。B端可以通过检查文件的大小来判断是否需要继续接收文件。如果B端关闭了连接，下次启动时会从文件尾部继续接收。

### 拓展：

**MD5**（Message Digest Algorithm 5）是一种广泛使用的加密哈希函数，它产生一个128位（16字节）的哈希值，通常用32个十六进制字符表示。MD5被设计用来接收任意长度的数据（通常是文件或消息）并生成一个固定长度的“摘要”或“指纹”，这个摘要用于验证数据的完整性。

