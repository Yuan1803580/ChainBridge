# 本地动态多链 V1

本地 V1 完成需求中的前七步，不连接公开测试网。`projects.zip` 只用于确认四个
`chainId`，其中的 DAG、节点定义、状态机和调度代码均未复用。

## 固定链边界

| 链 | chainId | RPC | 职责 |
| --- | ---: | --- | --- |
| A | 31337 | `http://127.0.0.1:8545` | WorkflowEngine、TaskEscrow、ChainRouteRegistry、协调与结算 |
| B | 31338 | `http://127.0.0.1:8546` | Agent-B1、RemoteWorkBoard、N1 成功 |
| C | 31339 | `http://127.0.0.1:8547` | Agent-C1、RemoteWorkBoard、N2 验收失败 |
| D | 31340 | `http://127.0.0.1:8548` | Agent-D1、RemoteWorkBoard、N2 新 attempt 重试成功 |

## 新实现的 DAG

```text
N1 research on B
  |  only NODE_SETTLED unlocks the dependency
  v
N2 verification on C -- review failed --> same nodeId, new attemptId on D
```

`SUCCEEDED` 不会解锁 N2。N1 必须先完成付款并进入 `SETTLED`。C 的失败尝试释放
预算，D 使用相同 `nodeId` 和新的 `attemptId`；C 的迟到结果会被拒绝。

## 一键运行

首次运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
pnpm local:v1
```

`pnpm local:v1` 会按顺序：

1. 执行 Alembic 数据库迁移并启动 FastAPI。
2. 启动 Next.js 可视化界面。
3. 启动 A/B/C/D 四个独立 Hardhat RPC。
4. 部署本地 V1 合约并运行异步 Mock CCIP Relay。
5. 执行 B 成功、C 失败、D 重试、迟到拒绝、付款、平台费和退款验收。
6. 通过真实 FastAPI 接口生成同一业务场景，供前端直接查看。

服务启动后保持运行：

- Web：`http://127.0.0.1:3000`
- API 文档：`http://127.0.0.1:8000/api/docs`
- RPC：`http://127.0.0.1:8545` 到 `http://127.0.0.1:8548`

停止由本地 V1 脚本启动的服务：

```powershell
pnpm local:v1:stop
```

## 分步命令

```powershell
pnpm local:v1:start      # 数据库、API、前端、四链
pnpm local:v1:accept     # API + Worker + 四链动态部署与资金闭环验收
pnpm local:v1:contracts-accept # 仅运行旧的独立合约场景
pnpm local:v1:api-demo   # 生成前端可查看的完整 FastAPI 闭环
pnpm local:v1:verify     # 后端、合约、Worker、前端和浏览器回归
```

## 验收证据

链上验收成功时输出：

```text
LOCAL_PROTOCOL_WORKER_ACCEPTED
workflow=<workflowId> B=8.4_BTK C=0_CTK D=27.2_DTK fee=1.1_USDC refund=7.9_USDC sourceGasRefund=0.01_ETH
```

详细的合约地址、消息 ID、交易哈希、Gas 责任、节点状态和账本位于：

```text
.runtime/local-v1/protocol-worker-acceptance.json
```

浏览器验收截图同样位于 `.runtime/local-v1/`。运行数据、依赖、私钥和日志不会
进入源码压缩包。

## 实现边界

本地 V1 的四链、合约、Relay、Worker、Coordinator、数据库和前端均可执行。
Mock CCIP 保留异步队列、幂等消息、`routeId + routeVersion` 路由和失败重试语义，
但不声称等同于 Chainlink 公共测试网。真实测试网部署、LINK/测试币、CCIP Router
和最终安全审计属于第八步。

四条 RPC 是启动时已存在的本地链。动态部分是 Coordinator 根据 Agent 的
`homeChainId` 选择 B/C/D，然后 Worker 在该归属链首次部署或复用对应 route 的
`RemoteWorkBoard`、`DynamicSettlementReceiver`、`MockPayoutToken` 和
`LocalPayoutSwap`。通过审核的 USDC 在目的链兑换为 BTK/CTK/DTK；系统不会在
匹配时临时创建新的区块链。

本地验收脚本会真实执行 `mint -> approve -> lockBudget -> depositSourceGas`。普通
前端目前只创建 `ONCHAIN` 资金意图并等待未来钱包适配器提交交易，因此不能把
前端显示的 `RESERVED` 当成真实用户锁款。
