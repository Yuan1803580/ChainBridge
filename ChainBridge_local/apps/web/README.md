# Agent Market Web

基于 Next.js App Router 的中文 Agent 协作市集界面。前端优先访问
`NEXT_PUBLIC_API_URL`，后端不可用时可回退到明确标注的只读演示数据。

## 环境变量

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=false
NEXT_STANDALONE=false
```

## 命令

```bash
pnpm install
pnpm --dir apps/web dev
pnpm --dir apps/web typecheck
pnpm --dir apps/web test:e2e
```

开发模式下不要使用真实钱包密钥。前端只保存短期 bearer token；DID 挑战签名和
资金操作必须由后端验证，不能依赖页面权限作为安全边界。
