# 系统启动、停止与互联网发布指南

本文面向第一次维护本项目的用户。命令均在 Windows PowerShell 中执行，项目目录为：

```text
D:\agent\agent-crosschain-platform-review-v2-agent-signing-complete-20260804(1)\agent-crosschain-platform
```

## 一、当前系统包含什么

托管测试网模式会启动五个进程：

| 服务 | 本机地址 | 是否应暴露到公网 |
| --- | --- | --- |
| Web 网站 | `http://127.0.0.1:3000` | 可以，通过 HTTPS 反向代理 |
| Platform API | `http://127.0.0.1:8000` | 可以，通过 HTTPS、鉴权和限流 |
| Agent Runner | `http://127.0.0.1:8010` | 不可以 |
| Signer Sidecar | `http://127.0.0.1:8020` | 绝对不可以 |
| Protocol Worker | 后台进程 | 不可以 |

链路固定为 Ethereum Sepolia `11155111` 到 Arbitrum Sepolia `421614`，资产为 CCIP-BnM。

数据库、路由和日志保存在 `.runtime/managed-testnet`。停止服务不会删除这些内容。

## 二、每次打开电脑后如何启动

### 第 1 步：打开 PowerShell

打开新的 PowerShell 窗口，然后进入项目目录：

```powershell
Set-Location "D:\agent\agent-crosschain-platform-review-v2-agent-signing-complete-20260804(1)\agent-crosschain-platform"
```

### 第 2 步：执行一键启动

```powershell
pnpm testnet:managed:easy-start
```

该脚本会自动完成以下工作：

1. 从当前 Windows 用户的 DPAPI 安全存储加载 RPC、DeepSeek Key 和服务令牌。
2. 从管理员 PEM 文件推导公开公钥，不读取或打印私钥。
3. 固定使用数据库中现有管理员 DID：`did:agent:platform-admin:testnet`。
4. 验证两条 RPC、Router、CCIP-BnM 地址和 Chain ID。
5. 执行 API、Runner、Sidecar 数据库迁移。
6. 启动五个服务并执行健康检查。

成功时应看到五行 `OK`。然后打开：

```text
http://127.0.0.1:3000
```

### 第 3 步：随时检查状态

```powershell
pnpm testnet:managed:status
```

### 第 4 步：使用结束后停止

```powershell
pnpm testnet:managed:stop
```

停止只关闭进程，不删除数据库、Agent、钱包、路由或日志。下次仍然执行一键启动命令。

## 三、每天不需要重复做的事情

以下操作都不是日常启动步骤：

- 不要每天重新配置 `APP_SECRET`、RPC 或 DeepSeek Key。
- 不要每天创建管理员密钥。
- 不要每天执行 `testnet:managed:create-route`。
- 不要删除 `.runtime/managed-testnet`。
- 不要重新生成 relayer 私钥或 Agent 钱包。
- 不要把 `.dpapi`、`.pem`、`.key` 文件复制到仓库、聊天或截图中。

现有安全文件位于：

```text
%LOCALAPPDATA%\AgentCrosschain\secrets
```

DPAPI 文件只能由当前 Windows 用户解密。换 Windows 账号或换电脑前，需要单独制定密钥迁移方案，不能只复制 `.dpapi` 文件。

## 四、常见启动错误

### `APP_SECRET is required`

原因：直接执行了旧启动命令，新终端没有环境变量。

解决：使用 `start-managed-testnet-easy.ps1`，不要直接运行 `pnpm testnet:managed:start`。

### `Port 8010 is already in use`

先执行：

```powershell
pnpm testnet:managed:stop
```

再执行一键启动。如果状态文件不存在但端口仍占用，先用下面的命令查看进程，不要直接结束不认识的进程：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8010 | Select-Object LocalPort,OwningProcess
Get-Process -Id <上一步显示的进程号>
```

### `UNIQUE constraint failed: agents.slug`

原因：管理员 DID 配错。当前数据库管理员 DID 必须是：

```text
did:agent:platform-admin:testnet
```

一键加载脚本已经固定该值。

### `Platform API exited before becoming healthy`

查看：

```powershell
Get-Content .runtime\managed-testnet\api.stderr.log -Tail 100
```

Runner、Signer、Worker 分别查看对应的 `runner.stderr.log`、`signer.stderr.log`、`worker.stderr.log`。

## 五、临时发布到互联网供测试

临时测试推荐使用 Cloudflare Tunnel。它不要求在路由器上开放端口，但仍然必须拥有域名并启用 HTTPS。

假设域名为：

```text
网站：https://market.example.com
API：https://api.example.com
```

先停止本机服务，再带公网域名启动：

```powershell
pnpm testnet:managed:stop

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\testnet\start-managed-testnet-easy.ps1 `
  -PublicWebOrigin "https://market.example.com" `
  -PublicApiOrigin "https://api.example.com"
```

Cloudflare Tunnel 只转发两个入口：

```yaml
ingress:
  - hostname: market.example.com
    service: http://127.0.0.1:3000
  - hostname: api.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

必须遵守以下边界：

- 不公开 `8010`、`8020`、Worker 健康端口或 SQLite 文件。
- API 域名启用限流、WAF 和请求体大小限制。
- 管理员入口建议再加 Cloudflare Access。
- Sidecar 始终使用 loopback，不能配置成公网 URL。
- 临时隧道适合测试，不等于正式生产部署。

注意：网站公开后，远程用户可以浏览市场并通过公开 API 使用已授权账户。但远程 Agent 自助准入仍要求 Agent 所在机器运行自己的兼容 Sidecar。不能把服务器上的 Sidecar 公开给远程 Agent 使用。

## 六、正式生产发布建议

正式上线不要直接长期运行当前的 Next.js 开发服务器和 SQLite。推荐准备一台 Linux 云服务器，并完成：

1. 使用私有 GitHub 仓库保存代码，仓库中不得包含任何密钥。
2. Web 使用 `next build` 和 `next start`。
3. API、Runner、Worker 作为独立 systemd 服务或容器运行。
4. API、Runner、Sidecar 分别执行自己的 Alembic migration。
5. API 数据库从 SQLite 更换为 PostgreSQL，并配置每日备份。
6. 使用 Caddy、Nginx 或云负载均衡器提供 HTTPS。
7. Runner、Sidecar、Worker 和数据库只允许内网或 loopback 访问。
8. 使用云 Secret Manager、systemd credential 或 Docker secret 管理生产密钥。
9. 配置日志轮转、进程自动重启、磁盘和余额告警。
10. 上线前轮换测试期间可能暴露过的所有令牌。

Caddy 的公开入口可以类似：

```caddyfile
market.example.com {
    reverse_proxy 127.0.0.1:3000
}

api.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy 会自动申请 HTTPS 证书。云服务器防火墙只开放 `80` 和 `443`。

## 七、发布后如何修改网站

### 只修改页面文字或样式

1. 在本机修改 `apps/web`。
2. 本机执行类型检查和构建。
3. 提交到私有 Git 仓库。
4. 服务器拉取代码并重新构建 Web。
5. 只重启 Web 服务。

```powershell
Set-Location apps\web
.\node_modules\.bin\tsc.cmd --noEmit
.\node_modules\.bin\next.cmd build
```

### 修改 API 或数据库字段

1. 修改 `apps/api`。
2. 新增 Alembic migration，不能手工改生产数据库。
3. 运行全部 API 测试。
4. 备份数据库。
5. 发布代码、执行 `alembic upgrade head`，再重启 API 和 Worker。

```powershell
.\.venv\Scripts\python.exe -m pytest apps\api\tests -q
```

### 修改 Runner 或 Sidecar

分别运行它们的测试和 migration，只重启对应服务。Sidecar 重启前要确认没有待签名或待广播交易。

### 修改智能合约或 CCIP 路由

这不是普通网站更新。必须重新审计、部署、登记版本并保存部署清单。页面修改和普通 API 修改都不需要重新执行 `create-route`。

## 八、推荐的发布更新流程

每次发布遵循固定顺序：

```text
本机修改 -> 本机测试 -> 提交私有 Git -> 备份生产数据库
-> 服务器拉取 -> 数据库迁移 -> 构建 -> 分服务重启 -> 健康检查 -> 验收
```

首次创建 Git 仓库前，确认 `.gitignore` 已排除 `.env`、`.runtime`、数据库、PEM、KEY 和 `node_modules`。当前项目已经包含这些排除规则。

生产更新失败时，应回滚代码并恢复兼容的数据库备份；不要通过删除数据库或重新创建路由来解决普通发布错误。
