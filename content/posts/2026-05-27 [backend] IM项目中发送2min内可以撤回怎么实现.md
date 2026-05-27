+++
date = '2026-05-27T12:55:10+08:00'
draft = false
title = 'IM项目中，"发送2min内可以撤回"怎么实现？'
author = 'JekYUlll'
lastmod = '2026-05-27T12:55:10+08:00'
tags = ['im', 'messaging']
categories = ['backend']
+++

## 背景

消息撤回是 IM 的标配功能。微信给你 2 分钟，钉钉也是 2 分钟，Telegram 不限时。

这个需求听起来简单：加一个倒计时，超时禁用按钮。但真落到后端，坑比想象中多。时间窗口怎么算？对方已读之后还能撤吗？离线用户上线后怎么同步？同一个消息被连点两次撤回怎么办？

我把实现思路拆开讲，从协议到数据库，再到边缘情况。

## 核心原理

### 时间窗口以服务端为准

很多人第一反应：客户端记录发送时间，2 分钟内允许点击撤回。这不行。客户端时间可以被篡改，手机切到时区不同的地区也会出问题。

**唯一可靠的做法：服务端在消息入库时写入 `sent_at`，撤回时检查 `now() - sent_at <= 2min`。**

```sql
CREATE TABLE messages (
    id          BIGINT PRIMARY KEY,
    sender_id   BIGINT NOT NULL,
    receiver_id BIGINT NOT NULL,
    content     TEXT,
    status      SMALLINT NOT NULL DEFAULT 1,  -- 1:正常 2:已撤回
    sent_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

撤回请求到达服务端后，直接查这条消息的 `sent_at`：

```sql
UPDATE messages
SET status = 2
WHERE id = ?
  AND sender_id = ?
  AND status = 1
  AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - sent_at)) <= 120;
```

`UPDATE` 返回受影响行数为 0 时，说明要么超时了，要么消息已经被撤回，要么不是发送者本人。直接拒绝。

### 消息状态机

一条消息的生命周期比看起来复杂：

```
[发送中] → [已发送] → [已送达] → [已读]
              ↓
           [已撤回]
```

撤回只能发生在「已发送」之后。如果消息还在「发送中」（比如客户端还没收到 ACK），实际上可以直接丢弃，不用走正式的撤回流程。不过大多数实现里为了统一，还是等 ACK 回来后才允许撤回。

已读之后能不能撤？微信的做法是：能撤，但对方已经看到了。所以你撤回后，对端显示的是「对方撤回了一条消息」。这是一个产品决策，不是技术限制。技术上，只要没超过 2 分钟窗口，状态改成 `recalled` 就行。

### 同步机制：推还是拉？

撤回操作必须通知到对端。两种方式：

**推模式（WebSocket / 长连接）**

服务端收到撤回请求，校验通过后，向接收方推送一条控制消息：

```json
{
  "type": "recall",
  "message_id": 12345,
  "timestamp": "2026-05-27T12:34:56Z"
}
```

接收方收到后，把本地消息内容替换为「对方撤回了一条消息」。这是主流做法，实时性好。

**拉模式（客户端轮询 / 增量同步）**

如果用户离线，推模式会失败。此时需要靠后续同步来补齐。每次客户端上线或拉取历史消息时，服务端把 `status = 2` 的消息一并返回，客户端根据状态渲染 UI。

实际系统里两种都用：在线时走推，离线补偿走拉。

### 幂等与并发

用户连点两次撤回按钮，或者网络重发导致两个撤回请求同时到达，怎么办？

`UPDATE ... WHERE status = 1` 本身就是原子操作，天然防并发。第一个请求把 `status` 改成 2，第二个请求的 `WHERE status = 1` 匹配不到，返回 0 行。服务端按失败处理，客户端无论收到哪个响应，结果都一样。

但这里有一个细节：推送也要做幂等。同一个 `message_id` 的撤回通知，不应该被推送两次。可以在服务端加一个内存级的 `recent_recall_set`（比如 Caffeine 缓存，TTL 5 分钟），已经处理过的 `message_id` 直接跳过推送。

## 代码实战

下面是一个简化版的撤回接口，用 Go + PostgreSQL：

```go
type RecallRequest struct {
    MessageID int64 `json:"message_id"`
}

type RecallResponse struct {
    Success bool   `json:"success"`
    Reason  string `json:"reason,omitempty"`
}

func (s *Server) HandleRecall(ctx context.Context, req RecallRequest, userID int64) (RecallResponse, error) {
    // 1. 校验并更新消息状态，120 秒窗口
    res, err := s.db.ExecContext(ctx, `
        UPDATE messages
        SET status = 2
        WHERE id = $1
          AND sender_id = $2
          AND status = 1
          AND EXTRACT(EPOCH FROM (NOW() - sent_at)) <= 120
    `, req.MessageID, userID)
    if err != nil {
        return RecallResponse{}, err
    }

    n, _ := res.RowsAffected()
    if n == 0 {
        return RecallResponse{Success: false, Reason: "expired or not owner"}, nil
    }

    // 2. 查询接收方，准备推送
    var receiverID int64
    err = s.db.QueryRowContext(ctx,
        "SELECT receiver_id FROM messages WHERE id = $1", req.MessageID,
    ).Scan(&receiverID)
    if err != nil {
        return RecallResponse{}, err
    }

    // 3. 推送撤回通知
    s.pushService.Send(ctx, receiverID, PushMessage{
        Type:      "recall",
        MessageID: req.MessageID,
    })

    return RecallResponse{Success: true}, nil
}
```

这段代码假设几件事：
- 数据库事务已经保证了 `UPDATE` 的原子性。
- 推送失败不阻塞接口返回。推送是「尽力而为」，失败的话靠客户端后续拉取同步兜底。
- `EXTRACT(EPOCH FROM (NOW() - sent_at)) <= 120` 是 PostgreSQL 语法，MySQL 可以写成 `TIMESTAMPDIFF(SECOND, sent_at, NOW()) <= 120`。

## 边缘情况

**对方正在输入时撤回**

客户端收到撤回通知时，如果用户已经点进了输入框准备回复，应该把输入框关掉或者给出提示。这属于客户端交互细节，但协议层要支持：撤回消息里可以带一个 `action_hint` 字段，告诉客户端「这条消息被撤回了，请取消相关 UI 状态」。

**群聊场景**

群聊的撤回复杂一个数量级。一条群消息有 N 个接收者，撤回时要推给 N 个人。不能一个 UPDATE 完就逐个推送，太慢。正确的做法是：
1. 更新消息状态（和单聊一样）。
2. 把撤回事件写进群消息的扩散队列，由专门的投递服务批量推送。
3. 离线用户靠拉取历史消息时同步。

**撤回后重新编辑**

微信支持「撤回后重新编辑」，这其实是前端技巧：撤回成功后，客户端把原消息内容填回输入框，用户修改后再发一条新消息。后端不需要额外支持，它就是撤回 + 发送两个独立操作。

## 生态现状

| 产品 | 撤回时限 | 已读后能否撤回 | 备注 |
|------|---------|---------------|------|
| 微信 | 2 分钟 | 能 | 显示「对方撤回了一条消息」 |
| 钉钉 | 2 分钟 | 能 | 群聊支持 |
| Telegram | 无限 | 能 | 可不留痕迹删除双方消息 |
| Slack | 无原生支持 | - | 需第三方插件 |
| WhatsApp | ~1 小时 | 能 | 对端也可能已读 |

Telegram 的做法更激进：不限时，而且可以从双方设备上彻底删除。这背后是更强的消息所有权设计，发送者对自己的消息有完全控制权。国内产品选择 2 分钟窗口，更多是产品策略的取舍，不是技术瓶颈。

## 今日可执行动作

1. **检查你的消息表**：确认有没有 `status` 和 `sent_at` 字段。如果没有，加一个迁移脚本。撤回功能 90% 的坑都在表结构没设计好。

2. **给撤回接口加上慢查询日志**：`EXTRACT(EPOCH FROM ...)` 这种写法在 `sent_at` 上没有索引时会全表扫描。确认 `messages` 表有 `(sender_id, sent_at)` 或 `(id, sender_id)` 的索引。

3. **模拟离线场景测试**：杀掉客户端网络，发送消息后撤回，再恢复网络。验证客户端能否正确拉取到撤回状态并更新 UI。

## 参考

- WeChat 消息撤回产品逻辑，基于公开功能观察
- Telegram FAQ: "Delete Messages" — https://telegram.org/faq#q-can-i-delete-my-messages
- PostgreSQL 日期函数文档 — https://www.postgresql.org/docs/current/functions-datetime.html
