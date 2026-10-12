# 审核 V2 与 Agent 独立签名

## 1. 本次实现范围

本版本完成两项本地可运行的安全加固：

1. 审核 V2：EIP-712 审核签名、结果提交后的随机抽样、链上挑战时间锁和阈值多签仲裁。
2. Agent 独立签名：Agent Runner 管理自己的归属链钱包并签署结果交易，Protocol Worker 只负责广播。

这两项能力已接入动态工作流、`workflowId/nodeId/attemptId`、失败重试和结算门禁，不是独立演示合约。

## 2. 审核 V2 完整流程

```text
Agent Runner 完成任务
-> Agent Runner 签署并提交归属链 submitResult 交易
-> Worker 监听结果并回传任务主链证明
-> API 固定 resultHash、proofHash 和 reviewPolicyHash
-> 中风险节点在结果固定后请求随机数
-> 被抽中则等待独立审核；未抽中则按规则自动审核
-> 审核人对 EIP-712 ReviewApproval 签名
-> Worker 把签名提交 ReviewManagerV2
-> 高风险节点达到审核阈值后开启链上挑战期
-> 无挑战：链上时间到期后 finalizeUnchallenged
-> 有挑战：进入 DISPUTED，等待仲裁人 EIP-712 多签投票
-> ReviewManagerV2 最终为 Approved 才允许创建跨链结算
```

### 2.1 EIP-712 审核签名

签名绑定以下关键字段：

```text
workflowId
nodeId
attemptId
resultHash
reviewPolicyHash
decision
evidenceHash
notesHash
nonce
deadline
chainId
ReviewManagerV2 地址
```

合约恢复签名钱包并检查 Reviewer 白名单、签名期限、同一审核人是否重复提交以及 nonce 是否重放。旧 attempt、其他链或其他合约上的签名不能直接复用。

### 2.2 结果提交后的随机抽样

随机请求只能在 `resultHash` 和 `reviewPolicyHash` 已固定后创建，避免协调方先看到抽样结果再替换任务结果。抽样规则为：

```text
randomWord % 10000 < samplingRateBps
```

本地模式使用 `REVIEW_RANDOMNESS_MODE=MOCK`，用于重复运行测试。生产模式强制要求 `REVIEW_RANDOMNESS_MODE=VRF`，由独立监听服务通过 HMAC 认证接口提交 VRF 请求编号、随机数和提供方证明哈希。

当前边界必须明确：本项目已经完成 VRF 请求、等待、回填、幂等和抽样状态机，但尚未留下公共测试网上 Chainlink VRF 订阅、请求交易和 fulfillment 交易回执。上测试网时必须把监听服务连接到真实 VRF Consumer 事件，并把交易回执加入验收材料，不能把本地 MOCK 称为真实 VRF。

### 2.3 链上挑战时间锁

高风险审核达到阈值后，`ReviewManagerV2` 使用 `block.timestamp` 计算 `challengeDeadline`。API 时间不能提前关闭挑战窗口。挑战期内：

```text
isSettlementAllowed(attemptId) = false
```

只有挑战期结束且无人挑战，任何人才能调用 `finalizeUnchallenged`。若收到有效挑战，状态变为 `Disputed`，不能直接结算。

### 2.4 阈值多签仲裁

仲裁票同样使用 EIP-712，绑定 `challengeId`、`attemptId`、`resultHash`、resolution、reasonHash、nonce 和 deadline。单个管理员不能调用旧接口直接结束争议。达到 `arbitrationThreshold` 的同向有效票后，合约才把审核最终设为 `Approved` 或 `Rejected`。

### 2.5 两层结算门禁

付款前同时检查：

1. API 中最终 `SYNC_REVIEW_V2` 任务已经完成。
2. Worker 和 `TaskEscrow` 从链上读取 `ReviewManagerV2.isSettlementAllowed(attemptId)`。

任一检查不通过都不会创建跨链付款。审核失败时释放节点预算并增加 `attemptId`，重新匹配 Agent；旧 attempt 后续提交的结果和签名会被拒绝。

## 3. Agent 独立签名完整流程

```text
Worker 构造未签名 RemoteWorkBoard.submitResult 交易
-> Worker 向对应 Agent Runner 请求签名
-> Runner 检查 Agent、homeChainId、homeWallet 和 RPC
-> Runner 查询目标合约 runtime bytecode 并匹配白名单 artifact
-> Runner 检查 method selector 只能为 submitResult
-> Runner 检查 calldata 中的 attemptId
-> Runner 检查 value、Gas 和 GasPrice 上限
-> Runner 检查 requestNonce 未使用
-> Runner 使用 Agent homeWallet 私钥签名
-> Runner 持久化 requestNonce 并返回 rawTransaction
-> Worker 只广播 rawTransaction 并等待交易回执
```

Worker 在任何环境发现 `AGENT_HOME_WALLET_PRIVATE_KEYS_JSON` 都会拒绝启动。启动脚本只在创建 Runner 子进程时注入 Agent 测试私钥，随后立即从父进程环境删除，因此 API 和 Worker 不会继承这些私钥。

生产环境应把 Agent 钱包私钥放入独立 Secret Manager、HSM 或受限签名服务。本地 JSON 环境变量只用于测试，不能提交到 Git 或发送给平台管理员。

## 4. 已完成验证

```text
Solidity / Hardhat        65 passing
FastAPI                   50 passing
Agent Runner              19 passing
Chain Worker              18 passing
Python SDK                11 passing
Worker TypeScript         通过
Next.js production build  通过
四链动态工作流验收         通过
```

四链验收覆盖 Agent-B 成功、Agent-C 审核失败、释放旧预留、拒绝旧 attempt、改选 Agent-D、跨链模拟付款、最终结算和剩余资金退款。

机器可读验收记录保存在 `docs/review-v2-agent-signing-local-acceptance.json`，其中包含工作流、attempt、路由、交易哈希、结算状态和最终余额快照，不包含私钥。

## 5. 尚未被本版本声称完成的事项

1. 尚未提供公共测试网 Chainlink VRF 请求与 fulfillment 回执。
2. 尚未提供公共测试网动态工作流 CCIP 端到端回执。
3. 合约尚未经过面向真实资产的第三方安全审计。
4. Reviewer/Arbitrator 白名单控制权在生产环境仍需改为多签和最小权限治理。
5. 本地测试私钥不能用于测试网或生产环境。

因此，本版本的准确定位是：审核 V2 与 Agent 独立签名的本地完整闭环已经完成，可以作为进入 CCIP/VRF 公共测试网验证前的代码基线。
