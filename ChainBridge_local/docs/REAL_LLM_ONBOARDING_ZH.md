# 接入真实模型推理层（操作手册）

本手册说明如何把本地实验从**确定性推理桩**切换到**真实 LLM**，并产出可自证的
证据文件。OpenAI 官方 API 在当前网络环境下不可达，因此只列出国内直连的模型。

## 0. 先理解：桩不等于造假，但不标注就是问题

| 模式 | 含义 | 证据可以支持的结论 |
|---|---|---|
| `mock` | 节点产出是硬编码模板，无任何外网调用 | **只有协议层**：身份、心跳、匹配、跨链消息、Escrow、结算 |
| `real` | 节点产出由真实模型生成 | 协议层 + 推理层行为 |
| `mixed` | 部分组件用桩、部分用真模型 | 逐组件看 `llm-mode.json` 的 `components` |

**不要删除 mock provider。** 没有密钥时它是唯一能让链路跑起来的降级路径。
正确的处理是**标注**——每份证据自带 `llmMode` 字段，自己证明自己是哪一类。

## 1. 注册并拿 key（二选一，都零代码改造）

- **DeepSeek**：官方开放平台注册后创建 API key（`sk-` 开头）
- **Moonshot / Kimi**：官方开放平台注册后创建 API key

两者都已内置在本仓库中（`DeepSeekStructuredTaskProvider` / `KimiProvider`），
**不需要改任何代码**。以官网实际页面为准。

推荐先选 **DeepSeek**：只需一个环境变量就能让 Coordinator 和三个 Runner 同时生效，
配置量最小。

## 2. 配置（不要把 key 贴进聊天或提交到仓库）

### 最简方式：只设一个环境变量

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的key"
pnpm local:experiment:start
```

**这一行同时让 Coordinator 和三个 Runner 全部切到 deepseek**，不需要改任何代码，
也不需要写配置文件。原因是 `start-local-experiment.ps1` 里有自动选择逻辑：

- Runner：`LLM_PROVIDER` 未显式设置时，看到 `DEEPSEEK_API_KEY` 就选 `deepseek`
- Coordinator：`COORDINATOR_LLM_PROVIDER` 未设置且 `COORDINATOR_OPENAI_API_KEY`
  为空时，看到 `DEEPSEEK_API_KEY` 就选 `deepseek`

代价是**每次新开终端都要重设一次**。

### 注意：`secrets/coordinator-openai.env` 不能用来配 deepseek

`start-local-experiment.ps1` 读取该文件时的变量映射是：

| 文件里的键 | 映射到的环境变量 |
|---|---|
| `LLM_PROVIDER` | `COORDINATOR_LLM_PROVIDER` |
| `LLM_API_KEY` | `COORDINATOR_OPENAI_API_KEY` |
| `LLM_BASE_URL` | `COORDINATOR_OPENAI_BASE_URL` |
| `LLM_MODEL` | `COORDINATOR_OPENAI_MODEL` |

而 `DeepSeekTaskInterpreter` 读的是 **`DEEPSEEK_API_KEY`**，不是
`COORDINATOR_OPENAI_API_KEY`。所以在文件里写 `LLM_PROVIDER=deepseek` +
`LLM_API_KEY=...` 会导致 Coordinator 启动失败：

```
ValueError: DEEPSEEK_API_KEY is required when COORDINATOR_LLM_PROVIDER=deepseek
```

那个 env 文件只适用于 **OpenAI 兼容**模式（`LLM_PROVIDER=openai_compatible`），
且只影响 Coordinator，不影响 Runner。

### 各模型对应的配置

| 模型 | 配置 |
|---|---|
| DeepSeek | `$env:DEEPSEEK_API_KEY = "sk-..."`（两者都生效） |
| Moonshot / Kimi | `$env:LLM_PROVIDER = "kimi"` + `$env:KIMI_API_KEY = "..."`（注意启动脚本会清掉 `KIMI_*`，需在脚本外单独处理外部 Agent） |
| 其他 OpenAI 兼容端点 | Runner 侧：`$env:LLM_PROVIDER = "deepseek"` + `$env:DEEPSEEK_BASE_URL = "<端点>"` + `$env:DEEPSEEK_API_KEY` + `$env:DEEPSEEK_MODEL`（`DeepSeekStructuredTaskProvider` 用的就是标准 OpenAI `/chat/completions` 协议，`base_url` 可覆盖） |

`LLM_PROVIDER` 只接受 `mock` / `deepseek` / `kimi` 三个值，写错会直接报错退出。

**外部独立 Agent（L3，`D:\kimi-external-agent`）**：
`start.ps1` 目前强制校验 `KIMI_API_KEY`。若改用 DeepSeek，需要放开该校验
（改动很小，可单独提出）。

## 3. 启动并确认真的生效

```powershell
# Coordinator —— 关键看 provider 字段
curl http://127.0.0.1:8300/health
# 期望： "provider":"deepseek"  "model":"deepseek-chat"
# 若仍是 "provider":"mock"，说明 DEEPSEEK_API_KEY 没传进服务进程

# 三个 Runner
curl http://127.0.0.1:8111/health
curl http://127.0.0.1:8112/health
curl http://127.0.0.1:8113/health
# 期望： "llmProvider":"deepseek"

# 一条命令看全部（推荐）
pnpm local:stack:check
# 期望： llmMode = real
```

只要任何一个是 `mock`，最终结果就是 `mixed`，不是 `real`。

## 4. 跑实验并产出证据

```powershell
pnpm experiment:real-agents
```

输出目录 `experiments/results/real-agents/<suite>/` 下会多出两个关键文件：

- `llm-mode.json` —— 本次运行的推理模式详情（逐组件 provider）
- `summary.json` 里的 `llmMode` 字段 —— 同上，随证据一起归档
- `runs.csv` 多一列 `llmMode` —— 每一行都能自证

**判定**：`llm-mode.json` 里 `"mode": "real"` 才算真实推理的证据。

## 5. 论文 / 报告中必须写清的三件事

1. **推理层模式**：明确写出是 `real` 还是 `mock`（引用 `llm-mode.json`）。
2. **claim 范围**：`mock` 版本只能声称协议层结论，不得声称"Agent 具备推理能力"
   或任何关于输出质量的结论。
3. **受控故障**：REAL-F3 与 NFRDA 场景的失败是**受控注入**
   （REAL-F3 为平台故障注入，NFRDA 为手动停止 Runner C），不是自然发生的故障。
   `experiments/real_agents/README.md` 已注明，正文里需再次声明。

## 6. 常见问题

**Q：`/chat` 报 `requiredFields ... not shared by ... providers`**
A：真模型可能造出不在 `commonOutputFields` 里的字段名。该校验已改为
契约未知时降级放行；契约已知时仍会拒绝，并把可用字段写进错误信息。
把任务特定的措辞放进 `acceptanceCriteria.rules` 而不是新建字段名。

**Q：端点不支持 `response_format: {"type":"json_object"}`**
A：所有结构化调用都依赖这一项。自建端点（Ollama / vLLM）可能不支持，
需要额外做一层"从纯文本提取 JSON"的降级。官方 DeepSeek / Moonshot 均支持。

**Q：想完全离线**
A：走本地模型（Ollama 等），但需要上述 JSON 降级适配，且本机要跑得动模型。
