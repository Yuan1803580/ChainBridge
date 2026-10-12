# VS Code 本地部署说明

## 一、系统软件

安装以下软件并在安装后重新打开 VS Code：

1. Visual Studio Code。
2. Node.js 24 LTS；最低支持 Node.js 22。
3. Python 3.12 或 3.13，并在安装器中勾选 `Add Python to PATH`。
4. pnpm 10 或更高版本。可以在 PowerShell 中执行：

```powershell
corepack enable
corepack prepare pnpm@10.13.1 --activate
```

如果 Corepack 因权限失败，可以使用：

```powershell
npm install -g pnpm@10.13.1
```

## 二、VS Code 插件

打开项目文件夹后，VS Code 会根据 `.vscode/extensions.json` 提示安装推荐插件。

- Python：Python 调试、虚拟环境和测试支持。
- Pylance：Python 类型分析和代码补全。
- ESLint：Next.js/TypeScript 检查。
- Prettier：前端代码格式化。
- Tailwind CSS IntelliSense：Tailwind 类名补全。
- Hardhat Solidity：Solidity 语法、跳转和诊断。
- Playwright Test：浏览器测试运行和调试。

这些插件只改善开发体验。项目运行真正依赖的是 Node.js、Python、pnpm 和项目依赖。

## 三、解压和打开

建议解压到较短的纯英文路径，例如：

```text
D:\agent-crosschain-platform
```

在 VS Code 中选择 `File -> Open Folder`，打开包含 `package.json` 的
`agent-crosschain-platform` 文件夹。不要只打开 `apps/web`。

## 四、安装依赖

打开 VS Code 的 PowerShell 终端，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-local.ps1
```

脚本会创建 `.venv`、安装 FastAPI 依赖、安装 Agent SDK，并执行 `pnpm install`。
首次安装需要能够访问 npm 和 Python 软件包源。

## 五、启动平台

可以在终端执行：

```powershell
pnpm local:v1:start
```

也可以按 `Ctrl+Shift+P`，选择 `Tasks: Run Task`，再选择
`Local V1: Start`。启动完成后访问：

```text
前端       http://127.0.0.1:3000
API 文档   http://127.0.0.1:8000/api/docs
RPC A-D    http://127.0.0.1:8545 到 http://127.0.0.1:8548
```

## 六、验收和停止

运行四链完整验收：

```powershell
pnpm local:v1:accept
```

预期看到 `LOCAL_PROTOCOL_WORKER_ACCEPTED`。停止全部本地服务：

```powershell
pnpm local:v1:stop
```

如果端口 `3000`、`8000` 或 `8545-8548` 已被其他程序使用，先关闭占用这些
端口的程序。运行日志位于 `.runtime/local-v1`。

## 七、当前产品边界

可视化创建页默认使用 `RESERVED` 模型资金。四链 ONCHAIN 锁款、审核、失败重试、
目的链兑换和退款已经通过本地验收脚本执行。真实浏览器钱包签名、真实 Chainlink
CCIP、真实 DEX 和生产 Agent API Key 仍需要后续接入。
