# Agent 部署接入与 DeepSeek 参数

## 1. 当前版本的角色

本地验收由四个逻辑角色组成：

| 角色 | 当前实现 | 作用 |
|---|---|---|
| 协调端 | FastAPI 中的确定性协调服务 | 创建节点、查询信息平台、选择 Agent、审核结果、失败重试和发起结算 |
| Agent-B | 独立 Agent Runner | 在链 B 完成 `research` 节点 |
| Agent-C | 独立 Agent Runner | 在链 C 返回不合格结果，用于验证失败重试 |
| Agent-D | 独立 Agent Runner | 在链 D 接替失败尝试并完成节点 |

协调端第一版使用规则审核，保证本地验收结果可重复。它目前不是独立的 DeepSeek 协调 Agent。后续可以增加 DeepSeek 规划与审核，但链上状态转换、预算条件和旧尝试拒绝仍必须由确定性代码和合约约束。

## 2. 部署与接入不是同一件事

部署 Agent 是把 Runner 运行在本机、服务器或容器中。接入平台是在 Runner 已经在线后，完成以下流程：

```text
Runner 启动
-> 平台读取 /health 和 /manifest
-> 登记 DID、能力、endpoint、homeChainId 和 homeWallet
-> Agent 身份私钥签名所有权 challenge
-> homeWallet 用 EIP-191 签名同一 challenge
-> 平台执行健康、清单、测试调用、路线和 Gas 检查
-> 管理员激活
-> Runner 每 30 秒发送一次签名心跳
-> Coordinator 才能匹配该 Agent
```

网页 `/onboard` 已实现上述接入流程。页面不会创建云服务器，也不会替 Agent 生成生产私钥。

## 3. DeepSeek Runner 必填参数

在 Runner 所在机器通过环境变量或 Secret Manager 配置：

```env
APP_ENV=production
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_RETRIES=2
DEEPSEEK_TEMPERATURE=0
DEEPSEEK_MAX_TOKENS=4096

AGENT_RUNNER_API_TOKEN=长度足够的随机访问令牌
AGENT_RUNNER_ALLOW_INSECURE_LOCAL=false
AGENT_RUNNER_CORS_ORIGINS=https://你的平台域名
AGENT_PLATFORM_TIMEOUT_SECONDS=10
```

参数说明：

| 参数 | 是否必须 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 使用 DeepSeek 时必须 | 只保存在 Runner，不上传平台，不上链 |
| `DEEPSEEK_MODEL` | 是 | 第一版使用 `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | 是 | DeepSeek 的 OpenAI 兼容接口根地址 |
| `DEEPSEEK_TIMEOUT_SECONDS` | 是 | 单次模型请求超时 |
| `DEEPSEEK_MAX_RETRIES` | 是 | 模型网络错误重试，不等于工作流 `attemptId` 重试 |
| `DEEPSEEK_TEMPERATURE` | 是 | 结构化任务建议为 `0` |
| `DEEPSEEK_MAX_TOKENS` | 是 | 限制单次响应长度和成本 |
| `AGENT_RUNNER_API_TOKEN` | 必须 | Worker、接入页面调用 Runner 时使用 |

## 4. 两套私钥必须分开

Agent 身份私钥用于签任务接受、结果和心跳；homeWallet 私钥用于归属链交易和 Gas。二者不能共用。

Runner 配置 Agent 身份私钥：

```env
AGENT_RUNTIME_PRIVATE_KEYS_JSON={"workflow-research-b":"0x...","workflow-verification-c":"0x...","workflow-verification-d":"0x..."}
```

链上 Worker 配置归属链钱包私钥：

```env
AGENT_HOME_WALLET_PRIVATE_KEYS_JSON={"workflow-research-b":"0x...","workflow-verification-c":"0x...","workflow-verification-d":"0x..."}
```

要求：

1. 身份私钥和钱包私钥均为 32 字节 secp256k1 私钥。
2. `homeWallet` 必须由对应钱包私钥导出，并在 `homeChainId` 上持有足够原生币。
3. 生产环境不能使用 `DEV_IDENTITY_SEED`。
4. 私钥和 API Key 不能写进 `.env.example`、Git、前端、数据库或链上。

## 5. Agent 业务参数

每个 Agent 定义至少包含：

```text
slug / agentKey
DID
名称和版本
capabilityId
输入 JSON Schema
输出 JSON Schema
示例测试输入
最低报价和结算币种
homeChainId
homeWallet
服务 endpoint
```

当前模板定义在 `services/agent-runner/app/agents.py`。增加新能力时，必须同时增加输入、输出 Schema 和处理函数，平台激活时会使用示例输入执行一次真实测试调用并验证结果签名。

## 6. 心跳配置

接入页面激活 Agent 后，会调用 Runner 的：

```text
POST /agents/{slug}/platform-registration
```

Runner 随后自动执行：

```text
平台签发一次性 heartbeat challenge
-> Runner 使用 Agent 身份私钥签名
-> 平台验证签名、nonce 和有效期
-> 更新 ONLINE / BUSY / OFFLINE
-> 30 秒后重复
```

生产重启后要自动恢复心跳，可设置：

```env
AGENT_PLATFORM_REGISTRATIONS_JSON={"workflow-research-b":{"platformUrl":"https://platform.example.com","profileId":"...","heartbeatIntervalSeconds":30}}
```

平台默认 60 秒未收到有效心跳就停止匹配该 Agent，因此心跳间隔限制为 10 至 50 秒。

## 7. Gas 责任

```text
链 A 派发与结果证明入账 Gas：用户预存的 GasTreasuryA 承担
Agent 在 homeChain 提交 resultHash：Agent 的 homeWallet 承担
结果审核失败：Agent 不获得节点报酬，已发生的 homeChain Gas 不退
审核通过后的源链结算与 CCIP 费用：用户源链 Gas 预算承担
```

Agent 不再需要在链 A 为每个 attempt 额外充值。它只需要在自己的 `homeChainId` 持有原生币。

## 8. CCIP 测试网切换条件

本地动态工作流当前使用 Mock transport 完成任务派发和结果证明回传；结算层保留 `CHAINLINK_CCIP` 路径。上测试网前还需要：

1. 选择 Chainlink CCIP 官方支持的源链和目标链。
2. 在两端部署并验证 Sender、Receiver、WorkBoard 和 Vault。
3. 配置 chain selector、Router、远端合约白名单和 CCIP 支持资产。
4. 给源链 Sender 准备 LINK 或原生币支付 CCIP fee。
5. 给每个 Agent 的 homeWallet 准备该归属链原生币。
6. 用 CCIP Explorer 的 `messageId` 完成发送、交付和失败追踪。

第一轮测试网只使用同一种 CCIP 支持资产，不同时接入 DEX。资产跨链稳定后，再增加源链和目标链换币。
