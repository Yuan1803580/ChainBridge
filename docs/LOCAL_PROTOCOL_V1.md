# 本地动态多链协议 V1 实现说明

## 当前可运行结果

本地协议使用四个独立 RPC：A `31337`、B `31338`、C `31339`、D `31340`。
它已经跑通真实本地合约交易、动态归属链路由、B 成功、C 审核失败、D 候补重试、
目的链收款、平台费和剩余预算退款。验收入口是：

```powershell
pnpm local:v1:accept
```

最近一次报告写入 `.runtime/local-v1/protocol-worker-acceptance.json`，其中保存基础
合约、route 合约、messageId、attemptId、交易哈希、节点状态和最终余额。

## 从用户发起到结束的完整过程

1. 用户创建 `fundingMode=ONCHAIN` 的工作流。API 先生成稳定的 `nodeId`、验收
   条件哈希和 Chain A 的 `onchainWorkflowId`，状态保持 `AWAITING_FUNDING`。
2. 本地验收账户在 A 链执行 `mint`、`approve`、`TaskEscrow.lockBudget` 和
   `GasTreasuryA.depositSourceGas`。API 使用 RPC 验证交易成功和付款地址后才允许
   Coordinator 派发。
3. Coordinator 根据能力、在线状态、容量、Gas readiness、价格、信誉、成功率
   和延迟生成 Top-K 快照。它先选择 Agent，再读取该 Agent 的 `homeChainId` 和
   `homeWallet`。
4. 如果该 `routeId + routeVersion` 还没有部署，Coordinator 创建
   `DEPLOY_ROUTE_STACK`。Worker 只根据选中 Agent 的 `homeChainId` 选择 B/C/D RPC，
   在该链部署 `MockUSDC`、`MockPayoutToken`、`LocalPayoutSwap`、
   `RemoteWorkBoard`、`DynamicSettlementReceiver`，为兑换池注入本地 Token 流动性，再配置
   A 链的 Registry、ResultProofGateway、SettlementSender 和 FeePolicy。
5. Coordinator 创建新的 `attemptId`，冻结 Agent、`homeChainId`、`homeWallet`、
   `routeId` 和 `routeVersion`，并只为第一名候选保留奖励预算。Worker 在 A 链登记
   assignment，再把 assignment 写入归属链的 `RemoteWorkBoard`。
6. Agent 在归属链调用 `startWork` 和 `submitResult`。API 只把结果置为
   `RESULT_PROOF_PENDING`，不会直接进入审核。
7. Worker 检查目的链交易回执中的 `ResultSubmitted` 事件，再在 A 链调用
   `ResultProofGateway.receiveResultProof`。只有 messageId、routeVersion、attemptId、
   resultHash 和 proofHash 全部一致，API 才把节点送入 Coordinator 审核。
8. Coordinator 按不可变规则审核。B 通过后进入 `SETTLEMENT_PENDING`；C 的
   `verified=false` 失败，释放 reservation、记录 `AGENT_RESULT_REJECTED`，并从原
   Top-K 候选中重新校验 D。D 使用相同 nodeId、全新的 attemptId。
9. 审核通过后，A 链 `TaskEscrow` 把对应 USDC 锁入
   `DynamicSettlementSender`。Worker 在 B 或 D 的 Receiver 验证同一 messageId 和
   attemptId；Receiver mint 目的链 MockUSDC 给自己并调用白名单 `LocalPayoutSwap`，
   兑换后向冻结的 `homeWallet` 支付 BTK/DTK。兑换报价、最低到账量和 deadline 任一
   不一致都会使目的链交易整体回滚。A 链收到 source confirmation 后节点才是
   `SETTLED`。失败 attempt 永远不会创建付款。
10. 所有节点 `SETTLED` 后，Worker 执行 `payPlatformFee`、`refundRemainder` 和
    `refundSourceGas`。API 收到最终确认后才将工作流改为 `COMPLETED / FINALIZED`。

## 动态部署的准确含义

动态部署不是运行时新建区块链。A/B/C/D 四条本地链先启动；Agent 信息平台保存
Agent 的 `homeChainId`。Coordinator 匹配出 Agent 后，Worker 才选择对应 RPC，并在
那条现有链上首次部署或复用 route 合约。后续 route 升级创建新版本，旧 attempt
继续绑定旧版本，不能被新地址悄悄替换。

## 资金与 Gas 口径

本地 Token 模型是 lock-and-mint-and-swap：20 USDC 在 A 链锁入 TaskEscrow；通过
审核的 11 USDC 转入 A 链 SettlementSender 保持锁定。目的链 Receiver 分别 mint
4.2 和 6.8 MockUSDC 到 B/D 兑换池，按不可变 attempt 快照兑换为 8.4 BTK 和
27.2 DTK 后支付 Agent。C 审核失败，CTK 到账和 C 兑换池 USDC 都是 0。平台收到
1.1 USDC，用户收到 7.9 USDC，TaskEscrow 余额为 0。这个模型证明业务时序、兑换
边界和金额守恒，不等同于 Chainlink Token Pool 或真实 DEX 流动性。

默认 route 汇率为 B `1 USDC = 2 BTK`（18 decimals）、C `1 USDC = 3 CTK`
（8 decimals）、D `1 USDC = 4 DTK`（6 decimals）。滑点保护为 100 bps，报价有效期
10 分钟。只有 route 同时存在 `payoutTokenAddress` 和 `payoutSwapAddress` 才启用
兑换；旧 route 继续把 USDC 直接支付给 Agent。

源链 Gas 预存 0.01 ETH 已由 `GasTreasuryA` 合约接收并在完成后退还。本地 Router
费用为 0，Worker 的开发账户支付本地交易 Gas，因此 0.01 全退。结果回传 Gas 会
写入 FeeLedger；V1 账本保留 8 位小数，精确 wei 数仍可由保存的交易哈希和回执
复核。`ResultRelayGasVault` 和 Agent GasDeposit 数据模型已经存在，但在没有真实
Agent 保证金时不会伪造充值或宣称已经从 Agent 资产扣款。

## 已预留但尚未接入

| 项目 | V1 当前实现 | 后续真实接入 |
| --- | --- | --- |
| 用户资金 | ONCHAIN 模型、RPC 确认接口、验收脚本真实本地锁款 | 浏览器钱包签名和真实测试 Token |
| Agent API key | `credentialStatus`、`credentialRef`、指纹 | Secret Manager/KMS 和实际 Agent endpoint |
| Agent 钱包 | profile 和 attempt 保存不可变 `homeWallet` 快照 | Agent 自主管理签名账户 |
| 跨链 Token | 本地 lock-and-mint、目的链固定汇率兑换与 source confirmation | Chainlink Router、Token Pool、真实 DEX、LINK/native fee |
| DAG Planner | `RULE_PLANNER_V1` 动态计划接口 | LLM Planner、结构化校验和人工审批策略 |
| Coordinator | 独立决策记录、匹配、审核、重试和 job 编排 | 独立常驻 DID Agent 与受限 signer |

任何界面和文档都不得把 `RESERVED` 写成链上锁款，也不得把 Mock CCIP、开发账户
或 credentialRef 写成真实资金、生产密钥或公共 CCIP 安全证明。

## 验收结果

当前回归结果：FastAPI `31 passed`、Worker `15 passed`、Solidity `60 passing`，
Worker 和前端 TypeScript 检查通过。集成验收要求：

```text
B home chain payout = 8.4 BTK
C failed payout     = 0 CTK
D retry payout      = 27.2 DTK
B swap pool input   = 4.2 USDC
C swap pool input   = 0 USDC
D swap pool input   = 6.8 USDC
platform fee        = 1.1 USDC
requester refund    = 7.9 USDC
source Gas refund   = 0.01 ETH
TaskEscrow balance  = 0 USDC
workflow status     = COMPLETED / FINALIZED
```
