# Agent Market Web

基于 Next.js App Router 的中文 Agent 协作市集界面。前端优先访问
`NEXT_PUBLIC_API_URL`，后端不可用时可回退到明确标注的只读演示数据。

## 环境变量

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=false
NEXT_PUBLIC_LOCAL_RPC_URL=http://127.0.0.1:8545
NEXT_STANDALONE=false
```

`/workflows/new` 支持模型预留和链上资金两种模式。链上模式从 API 读取当前
`TaskEscrow`、结算 Token 和 `GasTreasury` 地址，由浏览器钱包逐笔确认授权、锁款
和可选 Gas 存入；API 会通过 Chain A RPC 独立核验交易与事件。仅启动前端时请使用
模型预留，完整本地链上流程需要从仓库根目录运行 `pnpm local:v1:start`。

## 命令

```bash
pnpm install
pnpm --dir apps/web dev
pnpm --dir apps/web typecheck
pnpm --dir apps/web test:e2e
```

开发模式下不要使用真实钱包密钥。前端只保存短期 bearer token；DID 挑战签名和
资金操作必须由后端验证，不能依赖页面权限作为安全边界。
