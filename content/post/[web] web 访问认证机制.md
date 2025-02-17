+++
date = '2025-02-17T21:05:47+08:00'
draft = false
title = 'web 访问认证机制'
author = 'JekYUlll'
lastmod = '2025-02-17T21:05:47+08:00'
tags = ['web', 'backend']
categories = ['web']
+++


[25 | 认证机制：应用程序如何进行访问认证？](https://time.geekbang.org/column/article/398410)讲得非常好，图文结合。

**IAM**：身份识别与访问管理（Identity and Access Management）。

- **认证**（Authentication，英文缩写 **authn**）：用来验证某个用户是否具有访问系统的权限。如果认证通过，该用户就可以访问系统，从而创建、修改、删除、查询平台支持的资源。  
- **授权**（Authorization，英文缩写 **authz**）：用来验证某个用户是否具有访问某个资源的权限，如果授权通过，该用户就能对资源做增删改查等操作。

> 认证证明了你是谁，授权决定了你能做什么。

<u>四种基本的认证方式：*Basic*、*Digest*、*OAuth*、*Bearer*</u>。

1. **Basic** 基础认证  
Basic 认证（基础认证），是最简单的认证方式。它简单地将用户名:密码进行 `base64` 编码后，放到 HTTP Authorization Header 中。HTTP 请求到达后端服务后，后端服务会解析出 Authorization Header 中的 `base64` 字符串，解码获取用户名和密码，并将用户名和密码跟数据库中记录的值进行比较，如果匹配则认证通过。  

2. **Digest** 摘要认证  
Digest 认证（摘要认证）与基本认证兼容，但修复了基本认证的严重缺陷。  
Digest 具有如下特点：  
	- 绝不会用明文方式在网络上发送密码。
	- 可以有效防止恶意用户进行重放攻击。
	- 可以有选择地防止对报文内容的篡改。
四步：  
	1. 客户端请求服务端的资源。
	2. 在客户端能够证明它知道密码从而确认其身份之前，服务端认证失败，返回`401 Unauthorized`，并返回`WWW-Authenticate`头，里面包含认证需要的信息。
	3. 客户端根据`WWW-Authenticate`头中的信息，选择加密算法，并使用密码随机数 `nonce`(防止*重放攻击*)，计算出密码摘要 `response`，并再次请求服务端。
	4. 服务器将客户端提供的密码摘要与服务器内部计算出的摘要进行对比。如果匹配，就说明客户端知道密码，认证通过，并返回一些与授权会话相关的附加信息，放在 `Authorization-Info` 中。

3. **OAuth** 开放授权  
OAuth（开放授权）是一个开放的授权标准，允许用户让第三方应用访问该用户在某一 Web 服务上存储的私密资源（例如照片、视频、音频等），而无需将用户名和密码提供给第三方应用。  
> OAuth2.0 一共分为四种授权方式，分别为*密码式*、*隐藏式*、*凭借式*和*授权码*模式。

4. **Bearer** 令牌认证  
Bearer 认证是一种 HTTP 身份验证方法。Bearer 认证的核心是 `bearer token`。`bearer token` 是一个加密字符串，通常由服务端根据密钥生成。客户端在请求服务端时，必须在请求头中包含`Authorization: Bearer` 。服务端收到请求后，解析出`<token>`，并校验`<token>`的合法性，如果校验通过，则认证通过。  
> 跟基本认证一样，Bearer 认证需要配合 HTTPS 一起使用，来保证认证安全性。

###### JWT

JSON Web Token（JWT）是 Bearer Token 的一个具体实现，由 JSON 数据格式组成，通过 HASH 散列算法生成一个字符串。该字符串可以用来进行授权和信息交换。

1. 客户端使用用户名和密码请求登录。
2. 服务端收到请求后，会去验证用户名和密码。如果用户名和密码跟数据库记录不一致，则验证失败；如果一致则验证通过，服务端会签发一个 Token 返回给客户端。
3. 客户端收到请求后会将 Token 缓存起来，比如放在浏览器 Cookie 中或者 LocalStorage 中，之后每次请求都会携带该 Token。
4. 服务端收到请求后，会验证请求中的 Token，验证通过则进行业务逻辑处理，处理完后返回处理后的结果。

JWT 由三部分组成，分别是 `Header`、`Payload` 和 `Signature`。  
- `Header`：包含了 Token 的类型、Token 使用的加密算法。在某些场景下，你还可以添加 kid 字段，用来标识一个密钥 ID。
- `Payload`：Payload 中携带 Token 的具体内容，由 JWT 标准中注册的声明、公共的声明和私有的声明三部分组成。
- `Signature`：Signature 是 Token 的签名部分，程序通过验证 Signature 是否合法，来决定认证是否通过。

*问*：JWT的Token存储在哪里比较好？  
cookie中比较好，可由服务端保存，localstorage在纯前端，中很容易泄露。  
服务器可以将 cookie 设置为 HTTP - Only，无法被 JavaScript 脚本访问。  
localStorage 完全处于前端控制之下，可以被同源的 JavaScript 代码访问和修改。

