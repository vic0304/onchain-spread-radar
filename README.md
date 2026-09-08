# 链上 / CEX 价差雷达

一个只读的价差监控器，按 `@dan326714` 置顶帖的公开思路实现：

1. 取某条链上高成交量代币作为候选池；
2. 与 Binance、OKX、Bybit、Gate.io 的现货和线性永续盘口对比；
3. 按“链上买入 / CEX 卖出”的净价差排序。

它**不会下单、不会保存交易所密钥、不会签名交易、不会支付 API 费用**。这是刻意的安全边界：推文中的价差是线索，不等于可实现利润。

## 快速开始

需要 Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

# BSC：最多 100 个高成交量候选，比较四所现货和永续
spread-radar --network bsc --json-output artifacts\latest-scan.json

# 浏览器界面：http://127.0.0.1:8000
spread-radar-web
```

默认使用 GeckoTerminal 的公开池子数据作为候选池，因此开箱可跑。它会在前 100 个活跃池中按 24 小时成交量排序；这是 OKX 热门代币数据的安全免费替代，而不是同一数据源。

## 与置顶帖的对应关系

推文提到的是“OKX 指定链交易量前 100，再与四大交易所对比价格”。OKX 的 Hot Token API 是 Basic 接口：有 API Key 的账户每月有免费额度。要使用原始候选源，先在项目目录创建本机专用的 `.env`（该文件已被 Git 忽略）：

```powershell
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填入一把只读、无交易和提现权限的 OKX API Key：

```text
OKX_ACCESS_KEY=你的 OKX API Key
OKX_SECRET_KEY=你的 OKX Secret Key
OKX_PASSPHRASE=你的 API Passphrase
```

重启前端或运行：

```powershell
spread-radar --source okx --network 56
```

API 凭证只在本机进程内用于签名请求；不要发到聊天、提交到 Git，或给它交易/提现权限。免费额度耗尽后，OKX 才会要求 x402 支付；`X-PAYMENT` 是单次、短时效支付证明，不能作为长期自动化凭证。工具从不接收钱包私钥或助记词。若不想配置 API Key，请继续用默认数据源。

在前端选择 OKX 但未配置任何 OKX 凭证时，工具会自动改用免费 GeckoTerminal 数据完成本次扫描，并显示降级提示；它不会付款，也不会使用钱包。

## 常用参数

```powershell
# 只比较现货；提高保守成本缓冲；只显示净价差 >= 2%
spread-radar --network bsc --market-types spot --all-in-cost-bps 250 --min-net-bps 200

# 每 45 秒刷新一次（Ctrl+C 停止）
spread-radar --network bsc --interval-seconds 45

# Base 链
spread-radar --network base
```

`--all-in-cost-bps` 是**两腿费用、Gas、滑点、资金费、失败重试和持有期间风险**的保守总缓冲，默认 150 bps（1.5%）。它不是事实成本，必须依自己实际规模、链和账户等级调整。

## 前端与 API 限速

前端是本机仪表盘，不会暴露到互联网。启动 `spread-radar-web` 后在浏览器打开 `http://127.0.0.1:8000`。前端默认使用 Gate 的 20 个候选快速扫描，且可按需勾选其他交易所；不可达交易所会在 5 秒后显示为告警而不阻塞整个页面。同一组筛选条件会缓存 60 秒，避免重复消耗数据源额度。

- **GeckoTerminal**：公开 API 的文档写明额度约为 **10 次/分钟**，且数据会缓存 1 分钟。工具抓取 100 个候选时需读 5 页，因此会串行等待约 6 秒/页（首次约 25 秒），不会并发突发请求。
- **CEX**：CCXT 的内建限速保持开启；本工具又把每家交易所的请求间隔设为不少于 **250 ms**，即最多约 **4 请求/秒/交易所**。交易所对不同端点、IP 和市场类型有不同权重，遇到 429/超时应增加间隔而不是提高并发。
- **OKX Hot Token**：每页最多返回 100 条。Basic 接口有月度免费额度；超额后会触发 x402。请以 OKX 账户实际额度和返回的支付要求为准。

## 如何阅读结果

结果只表示：当前链上参考价低于某个 CEX 的最佳买价，扣除你设定的成本缓冲后仍有正数。它没有验证：

- 合约地址和 CEX 上币是否同一资产；
- 目标下单规模下的链上价格冲击和盘口深度；
- 交易所充提状态、跨链额度、桥和合约安全；
- 永续资金费、指数/标记价格、强平风险；
- 能否两腿近似同时完成。

因此，请将它当成“发现行情”的雷达，不能当成自动交易或投资建议。`BULLA`/`TAC` 一类事件正说明，表面价差可能只是无法退出的风险溢价。

## 测试

```powershell
python -m pytest
```
