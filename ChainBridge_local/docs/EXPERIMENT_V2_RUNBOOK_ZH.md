# 受控多链实验 V2 运行手册

## 1. 这一版实际实现了什么

V2 与旧版固定汇率、本地直接铸币模拟并行存在，不会改变旧版 `local:experiment` 的含义。V2 新增了以下实验能力：

1. 链 A 保存 Workflow、DAG、Coordinator 审核结果、用户 Escrow、源链 AMM 和 SourceVault。
2. 链 B、C、D 分别保存 Agent 身份、RemoteWorkBoard、目标链 AMM 和预充值 DestinationVault。
3. A/B/C 先进入部署清单，D 只启动 RPC，不部署协议合约；C 失败后才在 D 部署合约、配置双向路线并更新清单。
4. Relay 只能扫描 `ExperimentMessageSent` 链上事件，等待确认区块后向目标链提交交易，不能通过修改数据库推进工作流。
5. Relay 使用 SQLite 保存扫描游标、未完成消息、重试次数和已处理事件，进程重启后继续处理。
6. 源链使用恒定乘积 AMM 将 TokenA 兑换为 BridgeAsset；目标链使用独立 AMM 将 BridgeAsset 兑换为 Agent 接受的 Token。
7. SourceVault 锁定源链 BridgeAsset，DestinationVault 只能从实验前预充值的 BridgeAsset 中释放资金，不在付款时任意铸币。
8. 目标链兑换失败时，BridgeAsset 保留在 DestinationVault；Controller 可以更新最低到账额和期限后重试。
9. 用户锁款时同时固定 `platformFeeCap`，Coordinator 不能把超出上限的剩余预算收作平台费。
10. Workflow 配置 SourceVault 为结算验证器后，未收到目标链付款确认的 attempt 不能进入 `SETTLED`。

## 2. 四条链的职责

| 链 | chainId | 职责 |
|---|---:|---|
| A | 31337 | Workflow、审核、Escrow、源链 AMM、SourceVault、结果接收 |
| B | 31338 | N1 成功 Agent、RemoteWorkBoard、目标 AMM、DestinationVault |
| C | 31339 | N2 第一次失败 Agent，验证超时、释放预留和旧结果拒绝 |
| D | 31340 | C 失败后运行时接入，承接 N2 的 attempt 2 并完成付款 |

Coordinator Agent 是链下服务，但它的调度、审核和结算操作受链 A 合约状态约束。它不需要单独占用一条链。

## 3. 最快的功能验收

前提：已经完成项目依赖安装。

在项目根目录执行：

```powershell
pnpm experiment:v2:hardhat:run
```

这个命令会自动完成：

```text
停止上一次 V2 Hardhat 进程
-> 启动独立链 A/B/C
-> 部署源链和目标链协议
-> 启动空白链 D，但不登记 D 路线
-> 创建两节点 DAG 和 30 TokenA 预算
-> 第一次 Relay 入队后模拟崩溃
-> Relay 使用原 SQLite 队列恢复并交付
-> N1 在 B 成功、审核、付款和 SETTLED
-> N2 在 C 超时失败并释放 reservation
-> 在 D 部署协议并热更新 Relay 清单
-> N2 attempt 2 在 D 成功并付款
-> C 的迟到结果被 StaleAttempt 拒绝
-> 收取 1 TokenA 平台费并退回 14 TokenA
-> 核对所有测试 Token 的总量和 Vault 跨链资金对应关系
```

停止功能链：

```powershell
pnpm experiment:v2:hardhat:stop
```

Hardhat 模式用于功能正确性和故障状态机验收。它按交易即时出块，不能把延迟和吞吐量作为现实区块链性能结论。

## 4. Besu 固定出块实验

前提：安装并启动 Docker Desktop。

一键执行：

```powershell
pnpm experiment:v2:besu:run
```

Besu 配置为四条独立 Clique 私链，每条链具有独立 RPC、chainId、数据卷、账户余额和合约，固定约 2 秒出块。A/B/C 先启动，D 使用 Compose profile 后启动并在业务流程中注册。

也可以分步执行：

```powershell
pnpm experiment:v2:besu:start
pnpm experiment:v2:besu:deploy
pnpm experiment:v2:besu:add-chain-d
pnpm experiment:v2:besu:accept
```

停止但保留链数据：

```powershell
pnpm experiment:v2:besu:stop
```

删除 Besu 数据卷需要执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\experiment-v2\stop-besu.ps1 -DeleteChainData
```

## 5. 实验结果在哪里

最新验收报告：

```text
.runtime/experiment-v2/results/latest.json
```

压缩包中同时保留最后一次通过验收的完整证据：

```text
docs/evidence/experiment-v2-hardhat-latest.json
```

部署和动态路线清单：

```text
.runtime/experiment-v2/deployment.json
```

Relay 持久化数据库：

```text
data/experiment-v2-relay-acceptance.db
```

结果 JSON 包含：

- `runId`、完成时间和四链 RPC。
- N1、N2 的 attempt、链和最终状态。
- 预算、源链支出、平台费、退款和 SourceVault backing。
- 崩溃恢复、重复付款防护、旧 attempt 拒绝和付款后 SETTLED 证据。
- Coordinator、Agent 和 Relay 交易的交易哈希、区块号、Gas、耗时及按链 Gas 汇总。

当前基线资金结果为：

```text
30 TokenA = 15 TokenA 节点源链支出 + 1 TokenA 平台费 + 14 TokenA 退款
```

C 链失败 Agent 的目标 Token 到账为 0。B、D 的实际到账额由各自 AMM 当时的储备决定，不使用固定汇率。

## 6. Relay 故障参数

独立运行 Relay：

```powershell
pnpm experiment:v2:relay
```

可在启动前设置以下变量：

| 参数 | 用途 |
|---|---|
| `EXPERIMENT_V2_RELAY_DELAY_MS=30000` | 每条消息延迟 30 秒后才允许交付 |
| `EXPERIMENT_V2_CRASH_AFTER_ENQUEUE=1` | 消息写入 SQLite 后立即模拟崩溃 |
| `EXPERIMENT_V2_FAIL_TARGET_CHAIN=31338` | 模拟目标链 B 的 RPC 不可用 |
| `EXPERIMENT_V2_REVERSE_PENDING=1` | 按 attemptNumber 倒序处理待交付消息 |
| `EXPERIMENT_V2_CONFIRMATIONS=2` | Relay 只扫描达到 2 个确认的事件 |

例如：

```powershell
$env:EXPERIMENT_V2_RELAY_DELAY_MS="30000"
pnpm experiment:v2:relay
```

停止后重新执行同一命令，Relay 会使用原数据库继续处理。已经由目标合约处理的 `messageId` 不会再次付款。

Besu RPC 中断实验应使用 `docker compose stop` 暂停容器，再使用 `docker compose start` 恢复同一数据卷。不要删除数据卷，否则那是重建链，不是 RPC 临时中断。

## 7. 自动验证的安全性质

合约测试和四链验收共同检查：

1. 同一 `messageId` 不能处理两次。
2. 同一 `settlementMessageId` 最多付款一次。
3. attempt 2 生效后，迟到的 attempt 1 结果不能覆盖当前状态。
4. Coordinator 审核失败或 attempt 超时后不创建付款消息。
5. DEX 滑点失败不会损失 Vault 中的 BridgeAsset。
6. 目标付款确认前，Workflow 不能进入 `SETTLED`。
7. Relay 在入队后崩溃并重启不会丢消息或重复付款。
8. 平台费累计不能超过用户锁款时设置的上限。
9. 每种测试 Token 的已知账户余额之和等于 `totalSupply`。
10. SourceVault 锁定 backing 等于 B 和 D 的 DestinationVault 实际释放总额。

## 8. 论文中可以和不可以声称的内容

Hardhat/Besu 实验可以证明协议状态机、链上余额变化、DEX 行为、故障恢复和资金守恒在受控环境中可复现。

它不能证明：

- 已完成公网 CCIP 交付。
- 本地 Relay 的安全性等同于 Chainlink CCIP。
- 私链延迟代表 Ethereum、Arbitrum 等公共网络延迟。
- 测试 Token 具有真实经济价值。
- 当前动态链能无条件接入非 EVM 或未知安全模型的链。

公开测试网阶段仍需把 Relay 传输层替换为 CCIP，并保留相同的 `workflowId/nodeId/attemptId/messageId`、Vault、幂等和付款确认接口。
