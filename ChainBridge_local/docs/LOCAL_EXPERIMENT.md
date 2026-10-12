# 协调 Agent、多 Runner、钱包与本地跨链实验教程

## 1. 这套实验解决什么问题

本地实验版用于验证以下完整闭环：

```text
用户描述任务
-> 协调 Agent 生成工作流草案
-> 用户确认后创建工作流并锁定预算
-> 平台根据能力和 homeChainId 匹配执行 Agent
-> 按需部署或复用目标链路由合约
-> 跨链发送任务
-> 独立 Agent Runner 执行任务并用自己的钱包签名
-> 结果证明回传任务主链
-> 协调 Agent 审核
-> 审核失败后增加 attemptId，并换到另一条链上的 Agent
-> 审核通过后跨链结算
-> 目标链把 USDC 兑换为 Agent 接受的 Token
-> Agent 到账，节点 SETTLED
-> 工作流完成，平台费入账，剩余预算退款
```

这套代码可以直接用于本地功能实验和论文中的受控实验。它没有接入外部真实 Agent，也不声称已经完成公共测试网 CCIP 或真实 DEX 交易。

## 2. 本地拓扑

| 组件 | 地址 | 作用 |
|---|---|---|
| Web | `http://127.0.0.1:3100` | 查看工作流、Agent、路由、消息和结算状态 |
| Platform API | `http://127.0.0.1:8200/api/docs` | 信息平台、工作流和控制面接口 |
| Coordinator Agent | `http://127.0.0.1:8300/docs` | 接收用户任务、生成草案、确认创建和推进工作流 |
| Runner-B | `http://127.0.0.1:8111/agents` | 只运行 Agent-B1，只持有 B 链钱包 |
| Runner-C | `http://127.0.0.1:8112/agents` | 只运行 Agent-C1，只持有 C 链钱包 |
| Runner-D | `http://127.0.0.1:8113/agents` | 只运行 Agent-D1，只持有 D 链钱包 |
| 链 A | `http://127.0.0.1:8545`，chainId `31337` | 任务主链、Escrow、审核和源链结算 |
| 链 B | `http://127.0.0.1:8546`，chainId `31338` | Agent-B 的归属链 |
| 链 C | `http://127.0.0.1:8547`，chainId `31339` | 第一次失败 Agent 的归属链 |
| 链 D | `http://127.0.0.1:8548`，chainId `31340` | 替换 Agent 的归属链 |

四条链是四个独立 Hardhat 节点，各自维护账本、区块、合约和账户余额，不是同一条链的四个用户。

## 3. 钱包如何分配

### 3.1 用户钱包

本地验收使用 Hardhat 测试账户作为任务发起人。用户钱包负责：

- 在链 A 授权和锁定 `20 USDC` 任务预算；
- 向源链 Gas Treasury 充值少量测试 ETH；
- 工作流完成后接收剩余预算和未使用 Gas 退款。

### 3.2 Agent 钱包

每个 Agent 都有独立的 `homeChainId + homeWallet`：

| Agent | 归属链 | 本地钱包 | 最终收款资产 |
|---|---:|---|---|
| Agent-B1 | 31338 | `0x3C44...93BC` | BTK |
| Agent-C1 | 31339 | `0x90F7...b906` | CTK |
| Agent-D1 | 31340 | `0x15d3...6A65` | DTK |

钱包私钥只注入对应 Runner 进程。API、Web、Coordinator 和 Protocol Worker 不接收 Agent 私钥。Worker 生成受限制的待签交易，Runner 检查链 ID、目标合约字节码、方法选择器、金额、Gas 上限和 nonce 后签名，再返回原始签名交易。

Agent-C 的结果审核失败后不会获得任务报酬，但其已经发生的归属链结果提交 Gas 仍然保留在链上，不能回滚。

### 3.3 Coordinator 钱包

Coordinator 当前不需要持有用户钱包或 Agent 钱包。它通过受保护的 Platform API：

- 解释用户任务；
- 请求生成 DAG 草案；
- 等待用户确认；
- 请求平台推进工作流；
- 读取状态。

涉及资产和状态变化的交易仍由 Escrow、Worker、用户钱包或 Agent Runner 按权限执行。

### 3.4 本地测试私钥边界

启动脚本中的测试私钥来自公开 Hardhat 测试账户，只能用于本地链。不能把这些私钥用于 Sepolia、Arbitrum Sepolia 或任何有真实资产的网络。

## 4. Runner 为什么有三个进程

代码模板只有一份，但 B、C、D 分别启动独立进程。每个进程通过以下变量限制自己：

```text
AGENT_RUNNER_ID
AGENT_RUNNER_SLUGS
AGENT_HOME_WALLET_PRIVATE_KEYS_JSON
AGENT_WALLET_POLICIES_JSON
AGENT_WALLET_NONCE_STORE_PATH
```

这样可以验证“一个 Agent 服务只拥有自己的身份和钱包”，也避免把所有 Agent 私钥集中交给 Worker。以后接入外部 Agent 时，可以把每个 Runner 部署到不同电脑、服务器或容器，平台接口不需要改变。

## 5. 协调 Agent 如何运行

默认使用 `mock` 解释器，保证本地实验可重复，不需要 API Key。Coordinator 的 `/chat` 只生成草案，不会立即创建工作流或锁款。用户调用确认接口后，工作流才会创建。

需要试用 DeepSeek 解释用户任务时，在启动前设置：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:COORDINATOR_LLM_PROVIDER="deepseek"
$env:DEEPSEEK_MODEL="deepseek-chat"
pnpm local:experiment
```

可选参数：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_RETRIES=2
DEEPSEEK_TEMPERATURE=0
DEEPSEEK_MAX_TOKENS=4096
```

模型只负责把自然语言整理为受约束的任务字段。预算、链上权限、Agent 匹配、状态机和付款条件仍由平台与合约检查，不能由模型自由决定。

## 6. 跨链与资金兑换如何实现

### 6.1 本地跨链消息

本地版本使用 CCIP 形状的消息状态机和 Local Relay，传递三类消息：

```text
DISPATCH       链 A -> Agent 归属链，发送任务绑定信息
RESULT_PROOF   Agent 归属链 -> 链 A，回传结果证明
SETTLEMENT     链 A -> Agent 归属链，发送结算资产和付款信息
```

每条消息保存 `messageId`、源链、目标链、routeId、routeVersion、workflowId、nodeId、attemptId 和状态，并执行防重放和旧 attempt 检查。

Local Relay 代替的是 Chainlink DON 的公网传输部分。源链和目标链上的合约调用、余额变化、交易回执、Gas 和事件都是真实发生在四个本地 EVM 账本上的。

### 6.2 跨链结算

成功节点的资金路径是：

```text
链 A TaskEscrow 中的 USDC
-> DynamicSettlementSender
-> 本地 CCIP 传输层
-> 目标链 DynamicSettlementReceiver / Vault
-> LocalPayoutSwap
-> Agent 的 homeWallet
```

审核失败的 attempt 不创建结算消息，因此 Agent-C 收到 `0 CTK`。节点重试后，新的 attempt 绑定 Agent-D、链 D 钱包和链 D 路由，旧 attempt 后续结果会被拒绝。

### 6.3 目标链兑换

本地 `LocalPayoutSwap` 是带流动性库存、报价有效期、滑点和最低到账检查的测试兑换合约：

```text
链 B：4.2 USDC -> 8.4 BTK
链 D：6.8 USDC -> 27.2 DTK
```

只有兑换成功且 Agent 钱包实际到账后，节点才标记 `SETTLED`。兑换失败时资金应留在目标链 Vault，进入恢复流程，不能伪造为已付款。

它用于验证资金状态机，不是 Uniswap、0x 或其他真实 DEX。接入测试网时，需要用白名单 DEX Adapter 和真实流动性替换 `LocalPayoutSwap`，同时保留过期、滑点、最低到账和 Vault 恢复检查。

## 7. 第一次安装

环境要求：

- Windows 10/11；
- VS Code PowerShell 终端；
- 原生 Windows Python 3；
- Node.js；
- pnpm。

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
```

该命令会创建 `.venv`、安装三个 Python 服务的依赖、安装 Node 依赖并编译合约。网络中断后可以重新执行，已经下载成功的依赖会被复用。

## 8. 一条命令执行完整实验

```powershell
pnpm local:experiment
```

该命令会：

1. 检查端口和进程归属；
2. 启动四条本地链；
3. 编译并部署合约；
4. 初始化数据库、Agent、链和路由策略；
5. 启动 API、Web、Worker、Coordinator 和三个 Runner；
6. 通过 Coordinator 创建并确认工作流；
7. 执行 N1 成功、N2 第一次失败、N2 换链重试成功；
8. 执行结果回传、审核、结算、目标链兑换、平台费和退款；
9. 生成验收证据。

验收结果位于：

```text
.runtime/local-experiment/protocol-worker-acceptance.json
```

本次交付时通过的固定证据保存在 `docs/local-experiment-acceptance-20260805.json`，重新运行实验会在 `.runtime` 中生成属于新一轮运行的证据。

成功时应看到：

```text
accepted = true
B = 8.4 BTK
C = 0 CTK
D = 27.2 DTK
20 USDC = 11 Agent 报酬 + 1.1 平台费 + 7.9 用户退款
tokenConservation.balanced = true
ledgerSummary.balanced = true
```

## 9. 只启动或停止服务

只启动，不自动执行验收：

```powershell
pnpm local:experiment:start
```

停止本次实验拥有的进程：

```powershell
pnpm local:experiment:stop
```

停止脚本会校验状态文件、进程启动时间、端口和服务的 `runtimeId`。如果端口属于另一份项目或另一次运行，它会拒绝停止，避免误关其他服务。

## 10. 后续接入真实 Agent 需要什么

当前 B/C/D 是可重复执行的 benchmark Agent。以后接入外部 Agent 时，不需要改工作流状态机，但每个 Agent 至少要提供：

```text
agentSlug / DID
独立 Runner URL
Runner API Token
身份签名公钥
capabilityId 与输入输出 Schema
模型或程序 Provider 配置
homeChainId
homeWallet
目标链 RPC URL
接受的 payoutAsset
钱包 Gas 余额或 Gas 押金状态
心跳、容量、报价和超时参数
允许签名的合约、方法、金额和 Gas 策略
```

私钥不上传到信息平台。Agent 身份私钥和 EVM 钱包私钥都留在 Agent 自己的 Runner 或密钥管理服务中。平台只保存公钥、钱包地址、签名证明、健康检查和能力信息。

推荐接入顺序：

```text
外部 Runner 已运行
-> 平台登记为 PENDING
-> 身份 challenge 签名
-> 钱包 challenge 签名
-> 平台检查 /health、/manifest 和测试调用
-> 检查 homeChain、Gas、收款资产与路线
-> 路线可用后激活
-> Coordinator 才能匹配该 Agent
```

## 11. 当前实验边界

已经验证：

- 用户任务到工作流完成的自动闭环；
- Coordinator 草案和确认边界；
- 多 Runner 隔离与 Agent 自有钱包签名；
- 四个独立 EVM 账本上的合约状态变化；
- 跨链消息、失败重试、换 Agent 和换链；
- USDC 跨链结算、目标链兑换和最终到账；
- 资金守恒、Gas 台账、幂等和旧 attempt 拒绝。

尚未验证：

- 外部真实 Agent 的长期在线和真实模型质量；
- Chainlink 公共测试网 DON 的真实 CCIP 交付；
- Uniswap/0x 等真实 DEX 的价格和流动性；
- 公网 RPC 分叉、重组、拥堵和长时间 pending；
- 生产密钥管理、多签治理和合约安全审计；
- 任意未知链和非 EVM 链的自动接入。

因此，这个版本可以表述为“动态多链 Agent 工作流的本地可复现实验系统”，不能表述为已经在真实公网运行的开放 Agent 市场。
