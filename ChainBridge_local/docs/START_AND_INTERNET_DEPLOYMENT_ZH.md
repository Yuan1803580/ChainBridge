# 日常启动、故障排查与互联网发布

本文件是中文入口，覆盖三件事：每天怎么启动、出问题怎么排查、怎么发布到 GitHub。

---

## 一、环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（脚本均为 PowerShell） |
| Python | **原生 Windows Python 3**，从 python.org 安装并启用 `py` launcher。不支持 WSL / Anaconda |
| 包管理器 | `pnpm`（`corepack enable pnpm` 或独立安装） |
| Node | 由 pnpm 管理，本机实测 v22 |
| 模型 | 本地模型（Ollama / vLLM / LM Studio 等），非必须——缺省会退化为桩 |

首次克隆后先装依赖：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
```

---

## 二、日常启动

### 完整本地实验（推荐入口）

```powershell
Remove-Item -Recurse -Force .runtime\local-experiment
pnpm local:experiment:start
pnpm local:agents:connect
pnpm local:stack:check
```

启动约 2–3 分钟（要拉起四条链）。

| 命令 | 作用 |
|---|---|
| `local:experiment:start` | 拉起四条链、API、Web、三个 Runner、Coordinator、Requester 钱包服务 |
| `local:agents:connect` | 注册 Agent：挑战、验签、激活、心跳。**不跑这步 Agent 无法被匹配** |
| `local:stack:check` | 只读体检，就绪时输出 `RESULT: all local Agents are connected and matchable.` |

打开 `http://127.0.0.1:3100`。

### 只跑 V1 控制台

```powershell
pnpm local:v1
```

打开 `http://127.0.0.1:3000`。

### 停止

```powershell
pnpm local:experiment:stop
```

**每次重来都要先 stop 再 start**，否则会留下占端口的孤儿进程。

### 开机 / 重启电脑后

服务全停、环境变量也丢了，所以：

```powershell
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:11434/v1"
$env:DEEPSEEK_API_KEY  = "ollama"
$env:DEEPSEEK_MODEL    = "qwen2.5:7b"
Remove-Item -Recurse -Force .runtime\local-experiment
pnpm local:experiment:start
pnpm local:agents:connect
pnpm local:stack:check
```

不想每次手输，存进用户环境变量（重启终端后生效）：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_BASE_URL", "http://127.0.0.1:11434/v1", "User")
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "ollama", "User")
[Environment]::SetEnvironmentVariable("DEEPSEEK_MODEL", "qwen2.5:7b", "User")
```

---

## 三、本地模型配置

本项目用**本地模型**跑，不依赖任何托管 API。Coordinator 和三个 Runner 从同一组
变量解析 provider，所以一次配置全栈生效：

```powershell
$env:LLM_PROVIDER      = "deepseek"
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:11434/v1"
$env:DEEPSEEK_API_KEY  = "ollama"
$env:DEEPSEEK_MODEL    = "qwen2.5:7b"
```

Runner 用的是标准 OpenAI `/chat/completions` 协议，所以 Ollama、vLLM、LM Studio、
自建端点都行，只需改 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。

**对本地服务端点的两个要求：**

1. 必须暴露 OpenAI 兼容的 `/v1/chat/completions`
2. 最好支持 `response_format: {"type":"json_object"}`。结构化节点调用依赖它；
   Ollama 较新版本支持。若不支持，节点输出校验会直接失败而不是降级。

不设这些变量时，栈退化为**确定性桩**：身份、心跳、匹配、结算仍然真实，但节点
产出是硬编码模板。`local:stack:check` 会明确报告当前是哪种：

| `llmMode` | 含义 | 可支持的结论 |
|---|---|---|
| `mock` | 节点产出是硬编码模板 | **仅协议层** |
| `real` | 节点产出来自本地模型 | 协议层 + 推理行为 |
| `mixed` | 部分是桩、部分是模型 | 逐组件看 `llm-mode.json` |

---

## 四、故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `port 8200 is already owned by PID …` | 上一轮遗留的孤儿进程 | `pnpm local:experiment:stop`；仍不行就手动结束该 PID |
| `409 A funding transaction hash has already been used` | 链重置了但账本没清 | 删除 `.runtime\local-experiment` 后重启 |
| Agent 显示「匹配不到」 | 没跑 `local:agents:connect`，或心跳过期（TTL 60 秒） | 重跑 `local:agents:connect` |
| Coordinator 一直是 `mock` | 环境变量没传进服务进程 | 必须在**同一终端**先设变量再 start；或显式设 `COORDINATOR_LLM_PROVIDER=deepseek` |
| 改了代码没生效 | 服务跑的是旧代码 | **任何代码改动都必须重启栈** |
| Requester 报余额不足 | 链重置后余额归零 | `pnpm requester -- faucet --token 100 --native 1` |
| `service did not become ready` | 系统代理吞掉了 127.0.0.1 探测 | 脚本已内置绕过；若仍失败检查系统代理设置 |
| `ELIFECYCLE Command failed with exit code 1` | 不是崩溃 | 只是检查脚本发现问题后返回退出码 1，看它上面几行 |
| 节点产出全是同一句话 | 处于 `mock` 模式 | 配置本地模型后重启，确认 `llmMode = real` |

### 判断是不是孤儿进程

每个服务的 `/health` 都返回 `runtimeId`。把它跟 `.runtime\local-experiment\platform.json`
里的 `runtimeId` 对比，**不一致就是上一轮遗留**。

---

## 五、发布到 GitHub

### 项目还不是 git 仓库，首次上传

```powershell
git init
git add .
git commit -m "Initial commit: Agent ChainBridge"
git remote add origin <你的仓库地址>
git push -u origin main
```

`git add .` 后文件数应在 **570 左右**。差太多说明有意外文件混入。

### 会被上传的内容

约 570 个文件，含 `experiments/`（实验证据，建议保留，这是工作量证明）、
`apps/`、`services/`、`contracts/`、`docs/`、`scripts/`。

### 已被 .gitignore 排除

`.runtime/`（账本、日志、注册信息）、`.venv/`、`node_modules/`、`.env` 及变体、
`*keystore*.json`、`*.db`、`data/`、`hardhat-artifacts/`、`hardhat-cache/`、`*.bak`、
`*.zip`、`.workbuddy/`（本地工作笔记）。

### 推送前自检

```powershell
git status --porcelain
git grep -nI "sk-[A-Za-z0-9]\{16,\}"     # 必须无输出：不能有真实模型密钥
```

### 关于脚本里的私钥

`scripts\local\start-local-experiment.ps1` 等脚本含 Agent 归属链钱包私钥
（`0x5de4…`、`0x7c85…`、`0x47e1…`）。**这些是 Hardhat 公开文档的默认测试账户**，
在本地 `31337`–`31340` 链之外没有任何价值，刻意提交是为了让项目从干净克隆即可
复现。切勿在公网复用，也不要替换成持有真实资金的密钥。自动化密钥扫描器会报警，
这是预期的。

---

## 六、实验证据的诚实性

`pnpm experiment:real-agents` 导出的每份证据都带 `llmMode` 字段，能自己声明节点
产出来自真模型还是桩。

- `mock` 运行只能声称**协议层结论**（身份、心跳、匹配、跨链消息、Escrow、结算）
- 不能声称"Agent 具备推理能力"或任何关于输出质量的结论

故障重试是**自然触发**的（超时、输出被拒、签名错误），不是脚本编排的。项目里
没有任何"强制出结果"的伪造脚本。
