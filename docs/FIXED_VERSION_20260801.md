# Agent Cross-chain Platform 修复版说明

## 1. 交付结论

本修复版在独立副本中完成，原始压缩包没有被修改。

当前已经通过四条独立本地 EVM 链的完整验收：

```text
链 A：任务主链、Workflow、Escrow、源链 Gas
链 B：N1 成功执行并收到 8.4 BTK
链 C：N2 第一次执行审核失败，不付款
链 D：N2 attemptId 增加后更换 Agent 和目标链，成功收到 27.2 DTK
平台费：1.1 USDC
用户剩余预算退款：7.9 USDC
工作流终态：COMPLETED / FINALIZED
```

验收入口是 `pnpm local:v1:accept`，成功标志是：

```text
LOCAL_PROTOCOL_WORKER_ACCEPTED
```

## 2. 问题与修复对应表

| 原问题 | 修复结果 |
| --- | --- |
| 锁款证明可被其他成功交易伪造 | 校验交易状态、发送人、TaskEscrow 地址、`lockBudget` selector、workflowId、termsHash、Token、预算、平台费及精确事件参数；同一交易哈希不能重复资助不同工作流。 |
| 用户可在任务运行时提前退款 | `TaskEscrow` 绑定 Workflow 和审核状态；正常退款要求全部节点 `SETTLED`，失败退款要求工作流进入失败退款终态。 |
| Gas 押金可提前退款 | `GasTreasuryA` 只允许工作流终态退款；`ResultRelayGasVault` 只允许 attempt 终态退款。 |
| Gas 只是数据库布尔值 | Worker 读取真实交易 receipt；结果证明费从 attempt 押金扣，源链协议费从 Source Gas Treasury 扣。 |
| 18 位原生币 Gas 被 API 拒绝 | Gas schema 与数据库押金字段升级为 18 位精度；业务预算仍保持原金额精度。 |
| Mock 结算存在双花窗口 | 源链先进入不可取消的 `Committed`，目标链再执行，最后源链确认；中断后只能重试。 |
| 旧 attempt 消息覆盖新 attempt | `RemoteWorkBoard` 保存单调递增的 `attemptNumber`，延迟到达的旧 attempt 会被拒绝。 |
| Agent 身份只在中心数据库 | 增加 `AgentIdentityRegistry`，目标链校验 DID hash、钱包、homeChainId 和 active 状态。 |
| 动态链只写死 B/C/D | 增加管理员链路注册 API；校验 RPC chainId；Worker 从路由读取 RPC，并可为新 EVM 链部署模板。 |
| DAG 顺序可能错误 | API 和 Worker 都执行拓扑排序并拒绝循环依赖。 |
| 部署脚本可能复用错误合约 | 部署清单保存并验证 runtime codeHash 和 manifestHash；默认拒绝复用来源不明的同 chainId RPC。 |
| 动态工作流未连接 CCIP | 增加动态 CCIP Sender/Receiver，Escrow 可通过 Chainlink Router 接口取得 transport `messageId`，目标端按工作流快照付款。 |
| 一键运行不稳定 | 启动/停止脚本支持独立 runtime、数据库、端口和无前端验收模式；只停止本次脚本拥有的进程。 |

## 3. 资金和状态的安全顺序

成功节点按以下顺序运行：

```text
用户调用 TaskEscrow.lockBudget
-> API 完整验证锁款交易和事件
-> Coordinator 匹配 Agent
-> TaskEscrow.reserve 为 attempt 预留节点预算
-> 目标链 Registry 登记 DID、homeWallet 和 homeChainId
-> RemoteWorkBoard 接收带 attemptNumber 的任务
-> Agent 提交结果和 resultHash
-> ResultProofGateway 回传结果证明
-> 协调 Agent 审核 PASS
-> 源链付款进入 Committed
-> 目标链接收资产并支付冻结的 homeWallet
-> 源链确认付款
-> 节点变为 SETTLED
-> 所有节点 SETTLED 后支付平台费，并退回剩余预算和 Gas
```

失败节点按以下顺序运行：

```text
协调 Agent 审核 FAIL
-> 不创建跨链付款
-> 释放该 attempt 的预算 reservation
-> attempt 进入失败终态并关闭 Gas 押金
-> nodeId 保持不变
-> attemptNumber 和 attemptId 增加
-> 重新查询 Agent 平台
-> 可以选择另一条链上的新 Agent
```

旧 Agent 后续提交旧 attempt 的结果，不会覆盖新 attempt，也不能获得付款。

## 4. 动态链的准确边界

动态链不是运行时新建区块链，而是把已有链注册进系统：

```text
POST /api/v1/chain-routes
-> RPC chainId 校验
-> 保存 routeId + routeVersion
-> 匹配 Agent 后读取 homeChainId
-> 首次使用时部署该链的 EVM 合约模板
-> 保存合约地址、交易哈希、codeHash 和 manifestHash
-> 后续 attempt 复用同一版本路由
```

当前可动态接入的是兼容 EVM、RPC 可访问、允许部署所需 Solidity 字节码的链。Solana、Bitcoin 等非 EVM 链不能直接复用这些合约，必须单独实现链适配层并重新完成安全审计。

## 5. CCIP 的准确状态

本项目区分两种 transport。

### `LOCAL_MOCK`

已经通过四链端到端验收。它验证业务状态机、失败重试、换链、预算、Gas 和付款顺序，不代表真实公网资产跨链。

### `CHAINLINK_CCIP`

已经完成：

- `DynamicCCIPWorkflowSender` 和 `DynamicCCIPWorkflowReceiver`。
- Escrow 触发动态 CCIP 付款。
- 分别保存业务 `messageId` 和 Chainlink `transportMessageId`。
- Receiver 校验来源链、来源 Sender、workflowId、nodeId、attemptId、payee、Token、amount 和重放。
- Worker 等待目标事件，之后才在源链确认并将 reservation 标记为 Paid。
- CCIP 网络费由 Source Gas Treasury 给 Sender 充值并记账。

尚未声称完成：

- 没有真实测试网 RPC、受限私钥、LINK/native fee、测试 Token 和受支持 Lane，因此没有公网 CCIP Explorer 回执。
- 当前真实 CCIP V1 让 Agent 直接收到路由登记的结算 Token，不执行未经登记的目标链 DEX 兑换。
- assignment 和 result proof 仍使用现有 Worker/Gateway 路径；本轮真实 CCIP 接线重点是动态资产结算。

## 6. Agent 身份和 API 边界

中心化 Agent 信息平台负责发现、能力、在线状态、报价、负载和 `homeChainId`。链上 `AgentIdentityRegistry` 负责确认此次付款绑定的 DID hash、钱包、归属链和启用状态。

Agent Runner 已提供带 ES256K 签名的 API 结果，并通过 14 个测试。当前四链验收脚本仍显式提交测试 Agent 的结果，以保证成功和失败场景可重复；生产版要实现完全自主调用，还需把真实 Agent endpoint、凭据引用和超时策略接入 Worker 的调度任务。不能把中心数据库记录描述成链上身份，也不能把本地 dev-login 当作生产认证。

## 7. 独立验收运行方式

```powershell
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
$env:LOCAL_DATABASE_PATH=".runtime\acceptance\acceptance.db"
$env:LOCAL_SKIP_WEB="true"
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
$env:LOCAL_CHAIN_A_RPC_URL="http://127.0.0.1:10645"
$env:LOCAL_CHAIN_B_RPC_URL="http://127.0.0.1:10646"
$env:LOCAL_CHAIN_C_RPC_URL="http://127.0.0.1:10647"
$env:LOCAL_CHAIN_D_RPC_URL="http://127.0.0.1:10648"
pnpm local:v1:accept
```

验收成功后，使用同一个 `LOCAL_RUNTIME_DIR` 运行 `pnpm local:v1:stop`，即可停止 API、Worker 和四条本地链。验收报告写入：

```text
<LOCAL_RUNTIME_DIR>/protocol-worker-acceptance.json
```

## 8. 本轮验证结果

```text
Solidity / Hardhat       62 passing
Chain Worker             15 passing
Worker TypeScript        passed
FastAPI                  40 passed
Agent Runner             14 passed
Python SDK               11 passed
Next.js typecheck        passed
Next.js production build passed, 15 pages
Four-chain acceptance    LOCAL_PROTOCOL_WORKER_ACCEPTED
```

这些结果证明本地动态多链业务闭环可运行，但不等于生产安全审计，也不等于真实公网 CCIP 已验收。

## 9. 服务进程归属加固（2026-08-02）

旧启动脚本只要能访问 API `/health` 或前端首页，就会直接复用端口上的服务。若 `8000` 或 `3000` 被另一份项目、Codex runtime 或旧进程占用，后续流程可能连接错误数据库和错误 ABI。

当前版本已经改为：

- `platform.json` 使用 schema v2，记录 `projectRoot`、`runtimeDir`、`runtimeId`、PID、端口和进程启动时间。
- API `/api/v1/health` 返回 `agent-crosschain-platform-api` 和本次 `runtimeId`。
- 前端 `/api/runtime-health` 返回 `agent-crosschain-platform-web` 和同一个 `runtimeId`。
- 端口已占用时，必须同时匹配运行目录状态、PID、启动时间、实际监听 PID、服务名和 `runtimeId` 才允许复用。
- 陌生 API 或前端会在数据库迁移、启动本地链和部署合约之前被拒绝。
- 数据库路径、前端端口或 API URL 改变时，只重启已经确认属于当前 runtime 的服务。
- 服务迁移到新端口时，先安全关闭旧端口上的自有进程，避免后台残留。
- 停止脚本在 PID 启动时间不匹配时拒绝结束进程，避免陈旧 PID 被 Windows 复用后误杀其他程序。
- 第一次升级时，若旧版 `platform.json` 中记录的进程均已结束，停止脚本会自动移除旧状态；若旧 PID 仍在运行，则拒绝在缺少启动时间证明时结束它们。

新增验证结果：

```text
Service ownership helper             passed
Unowned API rejection before chains  passed
Owned API/Worker repeated reuse       passed
Web runtime identity and reuse        passed
Web port migration + API CORS restart passed
Safe stop and cleanup                 passed
Four-chain business acceptance        LOCAL_PROTOCOL_WORKER_ACCEPTED
```
