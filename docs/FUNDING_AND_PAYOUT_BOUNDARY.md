# 本地 V1 资金与本链 Token 边界

## 资金分层

本地 V1 把三类资产分开处理：

1. A 链业务预算使用 6 decimals 的 MockUSDC。用户锁定 20 USDC，最终拆分为
   Agent 服务费 11、平台费 1.1、退款 7.9。
2. B/C/D 使用各自的本链支付 Token：BTK 18 decimals、CTK 8 decimals、DTK
   6 decimals。它们只用于目的链 Agent 到账，不参与 A 链 USDC 守恒求和。
3. A 链 Gas 保证金使用本地 ETH。0.01 ETH 与业务 Token 分账；本地 Router 费用为
   0，因此当前验收全部退还。

## 成功结算

```text
A TaskEscrow 锁定 USDC
  -> DynamicSettlementSender 记录 messageId 并持有通过审核的 USDC
  -> Worker 在 Agent homeChain 调用 DynamicSettlementReceiver
  -> Receiver mint 同额目的链 MockUSDC
  -> LocalPayoutSwap 收到 USDC，按 attempt 快照兑换
  -> Agent homeWallet 收到 BTK/CTK/DTK
  -> A 链确认 destinationTxHash
  -> TaskEscrow reservation: SettlementPending -> Paid
```

当前验收结果：

| 链 | 审核结果 | 兑换池 USDC 输入 | Agent 到账 |
| --- | --- | ---: | ---: |
| B | 通过 | 4.2 USDC | 8.4 BTK |
| C | 失败 | 0 USDC | 0 CTK |
| D | 候补重试通过 | 6.8 USDC | 27.2 DTK |

## 失败边界

- Agent 业务审核失败时不会创建 settlement message，不支付服务费，也不执行兑换。
- 最低到账量不足、报价过期、Token 不匹配、Receiver 未授权或池流动性不足时，
  `receiveSettlementAndSwap` 整笔回滚，messageId 不会被标为已处理。
- 目的链已支付但 Worker 在源链确认前重启时，Worker 从目的链事件恢复 txHash，
  并只在 reservation 仍为 `SettlementPending` 时再次确认，避免重复扣款。
- 路由没有 payout Token/Swap 地址时使用兼容模式，Agent 直接收到目的链 MockUSDC。

## 不代表的能力

本地 V1 没有运行 Chainlink 公共 CCIP 网络、真实 Token Pool、真实 USDC 或真实 DEX。
固定汇率和测试流动性只用于证明动态 route、不同 decimals、滑点、deadline、回滚、
重试和账本边界。接入测试网时必须替换为受支持的 CCIP lane、Token Pool、价格来源、
DEX 路由和真实 native/LINK 费用策略。
