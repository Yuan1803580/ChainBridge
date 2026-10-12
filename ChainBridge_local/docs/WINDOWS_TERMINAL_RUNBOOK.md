# Agent Cross-chain Platform Windows 终端运行手册

本文档用于在 Windows 和 VS Code PowerShell 终端中运行本项目，适用于项目目录：

```text
D:\agent-crosschain-platform
```

本地 V1 会启动四条独立的 Hardhat 测试链：

| 链 | chainId | RPC 端口 | 作用 |
|---|---:|---:|---|
| 链 A | 31337 | 8545 | 任务主链、工作流和 Escrow |
| 链 B | 31338 | 8546 | 第一个成功执行 Agent 的归属链 |
| 链 C | 31339 | 8547 | 第一次执行失败的 Agent 归属链 |
| 链 D | 31340 | 8548 | 替换 Agent 后重新执行的归属链 |

本地验收使用模拟跨链协议。它会实际运行多条本地 EVM 链、部署合约、发送交易和核算 Gas，但不代表已经连接公开测试网的真实 Chainlink CCIP Lane。

---

## 1. 打开正确的项目目录

在 VS Code 中打开：

```text
D:\agent-crosschain-platform
```

打开 VS Code 终端后，提示符应为：

```powershell
PS D:\agent-crosschain-platform>
```

如果不是这个目录，运行：

```powershell
Set-Location D:\agent-crosschain-platform
```

`apps`、`contracts`、`scripts`、`services` 和 `package.json` 应直接位于当前目录下。

---

## 2. 首次运行前检查工具

执行：

```powershell
py -3.13 --version
node --version
pnpm --version
```

本机已经验证过的版本为：

```text
Python 3.13.14
Node.js v24.18.0
pnpm 可正常运行
```

本地 V1 当前不依赖 Java 和 Docker，因此它们没有进入 PATH 不会阻止本次四链验收。

---

## 3. 首次创建 Python 虚拟环境

### 3.1 判断虚拟环境是否正确

Windows 项目应使用：

```text
.venv\Scripts\python.exe
```

如果看到 `.venv\bin\python.exe`，说明它由 MSYS Python 创建，不适合当前 PowerShell 脚本。

### 3.2 删除错误环境并重新创建

如果终端前面有 `(.venv)`，先执行：

```powershell
deactivate
```

然后执行：

```powershell
Remove-Item -Recurse -Force .venv
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe --version
```

最后一条命令应显示：

```text
Python 3.13.14
```

如果 `.venv\Scripts\python.exe` 已经存在且版本正确，不需要重复创建。

---

## 4. 安装项目依赖

执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
```

该脚本会自动完成：

1. 安装 FastAPI 的 Python 依赖。
2. 安装 Agent Runner 的 Python 依赖。
3. 安装项目 Python SDK。
4. 使用 pnpm 安装前端、Worker 和 Hardhat 依赖。

出现以下提示表示安装成功：

```text
Local dependencies are ready. Run pnpm local:v1:start next.
```

pnpm 可能显示：

```text
Ignored build scripts: @arbitrum/nitro-contracts, esbuild
```

这在当前本地验收中是安全提醒，不代表安装失败。

如果切换网络时出现 `WinError 10054`，等待命令结束后重新运行同一条安装命令即可。不要删除 `.venv`；已经安装或缓存的依赖会被复用。

---

## 5. 设置隔离运行参数

本机的 `3000` 和 `8000` 端口可能被其他 Node.js 或 Codex 服务占用，因此本项目固定使用：

```text
前端：3100
API：8200
```

每次打开新的 PowerShell 终端，都要重新设置以下环境变量：

```powershell
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
$env:LOCAL_DATABASE_PATH=".runtime\acceptance\acceptance.db"
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
$env:LOCAL_SKIP_WEB="true"
```

这些环境变量只对当前终端有效，关闭终端后不会永久修改系统配置。

在验收阶段暂时设置 `LOCAL_SKIP_WEB=true`，可以先验证后端、四条链、合约和 Worker，避免前端影响验收结果。

---

## 6. 运行四链完整验收

确认已经完成第 5 节的环境变量设置，然后运行：

```powershell
pnpm local:v1:accept
```

该命令会自动执行：

1. 创建独立 SQLite 验收数据库。
2. 启动链 A、B、C、D。
3. 在链 A 部署工作流、Escrow、Gas 和路由合约。
4. 根据 Agent 的 `homeChainId` 动态部署目标链合约栈。
5. 创建包含两个节点的工作流。
6. 在链 B 完成第一个成功节点。
7. 在链 C 模拟第二个节点审核失败，并且不付款。
8. 增加 `attemptId`，更换链 D 上的 Agent。
9. 在链 D 重试成功并完成付款。
10. 收取平台费、退回未使用预算和剩余 Gas。

运行期间不要关闭终端。最终出现以下提示表示验收成功：

```text
LOCAL_PROTOCOL_WORKER_ACCEPTED
```

本机已经验证过的结果示例：

```text
B=8.4_BTK
C=0_CTK
D=27.2_DTK
fee=1.1_USDC
refund=7.9_USDC
```

含义是：

- 链 B 的 Agent 成功，收到 `8.4 BTK`。
- 链 C 的 Agent 审核失败，收到 `0 CTK`。
- 系统更换到链 D 的 Agent，重试成功并收到 `27.2 DTK`。
- 平台收取 `1.1 USDC`。
- 用户收到 `7.9 USDC` 未使用预算退款。

验收报告保存在：

```text
.runtime\acceptance\protocol-worker-acceptance.json
```

---

## 7. 启动前端控制台

四链验收成功后，四条链、API 和 Worker 仍在运行。不要关闭当前终端。

先允许启动前端：

```powershell
Remove-Item Env:LOCAL_SKIP_WEB -ErrorAction SilentlyContinue
```

然后运行：

```powershell
pnpm local:v1:start
```

启动成功后打开：

```text
前端：http://127.0.0.1:3100
API 文档：http://127.0.0.1:8200/api/docs
```

前端主要页面包括：

- `运行总览`：查看平台整体运行状态。
- `工作流`：查看 workflowId、节点、attempt 和结算状态。
- `创建任务`：创建新的工作流。
- `Agent 信息平台`：查看能力、DID、归属链和钱包。
- `动态路由`：查看链 A 到不同 Agent 归属链的路由。
- `Worker 运维`：查看链上任务和跨链处理作业。
- `API 控制台`：查看接口状态。
- `平台管理`：查看平台配置和审计数据。

四链验收生成的工作流应显示为：

```text
WF-1001
Protocol Worker A/B/C/D acceptance
状态：COMPLETED
```

---

## 8. 正确关闭平台

建议在启动平台的同一个终端中运行：

```powershell
pnpm local:v1:stop
```

它会关闭：

- API
- 前端
- Protocol Worker
- 链 A、B、C、D

如果已经打开了新的终端，先恢复运行目录配置：

```powershell
Set-Location D:\agent-crosschain-platform
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
pnpm local:v1:stop
```

Hardhat 是本地临时链。链进程关闭后，链上合约和余额状态会消失，但 Solidity、TypeScript 和 Python 源代码不会消失，也不需要重新编写。

---

## 9. 下次从头重新运行

由于本地链关闭后链上状态会消失，下一次演示建议重新执行完整验收，而不是直接复用旧数据库中的合约地址。

### 9.1 设置运行参数

```powershell
Set-Location D:\agent-crosschain-platform
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
$env:LOCAL_DATABASE_PATH=".runtime\acceptance\acceptance.db"
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
$env:LOCAL_SKIP_WEB="true"
```

### 9.2 清理旧的临时验收数据

确保平台已经停止后再运行：

```powershell
if (Test-Path .runtime\acceptance) {
    Remove-Item -Recurse -Force .runtime\acceptance
}
```

这里只删除可重新生成的本地运行数据，不会删除源代码、`.venv` 或 `node_modules`。

### 9.3 重新验收并启动界面

```powershell
pnpm local:v1:accept
Remove-Item Env:LOCAL_SKIP_WEB -ErrorAction SilentlyContinue
pnpm local:v1:start
```

然后访问：

```text
http://127.0.0.1:3100
```

---

## 10. 常见问题

### 10.1 `.venv\Scripts\python.exe` 找不到

原因：当前 `python` 可能指向 MSYS Python，创建了 `.venv\bin`。

处理：

```powershell
deactivate
Remove-Item -Recurse -Force .venv
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe --version
```

### 10.2 端口 8545 已存在 chainId 31337

错误示例：

```text
Port 8545 already serves chainId 31337 but is not owned by this project
```

含义：旧 Hardhat 链仍占用 `8545-8548`，当前项目拒绝不安全地复用它。

先尝试使用当前运行目录关闭本项目进程：

```powershell
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
pnpm local:v1:stop
```

如果仍然占用，检查端口：

```powershell
netstat -ano | Select-String ':8545|:8546|:8547|:8548'
```

确认对应进程是旧的本地 Hardhat `node.exe` 后，关闭原来的链终端，或者按显示的 PID 执行：

```powershell
Stop-Process -Id <PID1>,<PID2>,<PID3>,<PID4>
```

不要在不了解进程用途时随意结束其他 PID，也不建议使用 `-ReuseExisting`，否则可能复用旧合约和旧路由。

### 10.3 验收只显示 `Integrated local protocol V1 acceptance failed`

这是外层汇总错误，真正原因通常位于前面的终端输出或：

```text
.runtime\acceptance
```

优先检查：

```text
api.stderr.log
web.stderr.log
protocol-worker.stderr.log
chain-A.stdout.log
```

### 10.4 页面显示 `Failed to fetch`

含义：前端已经启动，但浏览器不能访问 API。确认当前终端中的端口配置为：

```powershell
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
```

当前启动脚本会记录 API 启动时允许的前端端口。如果 `LOCAL_WEB_PORT` 发生变化，再次运行启动命令时，脚本会先确认 API 属于当前 runtime，然后自动重启 API，使新的 CORS 配置生效：

```powershell
pnpm local:v1:start
```

然后在浏览器按 `Ctrl+R`。

### 10.5 API 或前端端口不属于当前 runtime

错误示例：

```text
FastAPI port 8000 is already used by PID(s) ... but is not owned by this runtime
```

这表示端口上的服务没有同时通过以下检查：

```text
platform.json schema v2
项目根目录和 runtime 目录
PID 和进程启动时间
实际端口监听 PID
服务名称
runtimeId
```

脚本会在迁移数据库、启动四条链和部署合约之前停止，不能再把其他项目的 API 或前端误当成本项目服务。不要直接结束不认识的 PID；优先设置隔离端口：

```powershell
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
pnpm local:v1:start
```

旧版本的 `platform.json` 只记录 PID。升级后首次停止时，如果这些 PID 都已结束，脚本会自动删除旧状态；如果仍有旧 PID 在运行，脚本会拒绝自动结束它们，避免 PID 被 Windows 复用后误杀其他程序。

### 10.6 API 有工作流，但页面显示空列表

先在浏览器打开：

```text
http://127.0.0.1:8200/api/v1/workflows
```

如果 API 的 `total` 大于 `0`，但前端仍为空，检查：

```text
apps\web\app\workflows\page.tsx
```

工作流筛选的依赖数组应为：

```tsx
}), [workflows, query, status]);
```

当前项目已经修复这一问题。

### 10.7 为什么终端关闭后合约消失

当前使用的是 Hardhat 本地临时链。关闭链进程后，内存中的区块、合约和余额会消失。合约源代码仍保存在 `contracts` 目录中；下次运行验收脚本会自动重新部署，不需要重新写 Solidity。

---

## 11. 最短可执行流程

首次依赖已经安装后，每次进行一次干净的完整演示，可按以下顺序执行：

```powershell
Set-Location D:\agent-crosschain-platform

$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
$env:LOCAL_DATABASE_PATH=".runtime\acceptance\acceptance.db"
$env:LOCAL_API_PORT="8200"
$env:LOCAL_WEB_PORT="3100"
$env:LOCAL_SKIP_WEB="true"

pnpm local:v1:accept

Remove-Item Env:LOCAL_SKIP_WEB -ErrorAction SilentlyContinue
pnpm local:v1:start
```

打开：

```text
http://127.0.0.1:3100
```

结束时运行：

```powershell
pnpm local:v1:stop
```
