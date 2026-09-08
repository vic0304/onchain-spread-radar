# Telegram 告警

这个项目会用 Telegram **Bot** 给指定聊天发消息；它不是交易机器人，不能下单、不能签名，也不会读取钱包私钥。

## 一次性设置

1. 在 Telegram 搜索 `@BotFather`，发送 `/newbot`，按提示完成名称和用户名设置；它会给你一个 Bot token。
2. 打开你刚创建的 Bot，发送 `/start`。然后用一个可信的本地 Bot 管理工具或 Telegram API 获取该聊天的 `chat_id`；私聊通常是数字 ID，群组通常是负数 ID。
3. 在项目根目录的 [`.env`](.env) 追加以下内容（不要发给任何人，也不要提交 Git）：

   ```text
   TELEGRAM_BOT_TOKEN=从BotFather获得的token
   TELEGRAM_CHAT_ID=你接收消息的chat_id
   TELEGRAM_EXPECTED_BOT_USERNAME=你刚创建的Bot用户名（不带@）
   TELEGRAM_POLL_SECONDS=90
   TELEGRAM_ALERT_COOLDOWN_SECONDS=900
   TELEGRAM_ALERT_MAX_PER_SCAN=3
   ```

`TELEGRAM_POLL_SECONDS` 不小于 30；`TELEGRAM_ALERT_COOLDOWN_SECONDS` 是同一合约地址与同一 CEX 市场再次通知前的等待时间。默认每轮最多合并 3 条最高净价差，避免刷屏。

`TELEGRAM_EXPECTED_BOT_USERNAME` 是强制的防串 Bot 校验：启动和测试时会先调用 `getMe`，只有 Token 所属公开用户名与该值完全一致才允许发送。不要使用其他项目已经在用的 Bot；请在 `@BotFather` 新建一个专给本雷达的 Bot。

如果本机不能直连 Telegram，但你有本地 HTTP 代理，在 `.env` 加：

```text
TELEGRAM_PROXY_URL=http://127.0.0.1:7890
```

端口按你的代理软件实际 HTTP 端口填写；该设置只用于连接 Telegram，不会把行情或任何密钥发给代理之外的其他服务。

## 启动监控

在项目目录打开 PowerShell：

```powershell
.\.venv\Scripts\spread-radar-alerts.exe --source okx --network 56 --exchanges gate --market-types spot,swap --candidate-limit 20 --all-in-cost-bps 150 --min-net-bps 50
```

若使用免费候选源，则换成：

```powershell
.\.venv\Scripts\spread-radar-alerts.exe --source geckoterminal --network bsc --exchanges gate
```

命中时会按“链上参考买入价 → CEX 最优买价”、毛价差、成本缓冲、净价差、链上 24 小时交易量和流动性推送。无新机会、同一机会仍在冷却期，或数据源短暂失败时，它会继续下一轮，不会交易。
