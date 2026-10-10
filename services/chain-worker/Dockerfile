FROM node:22-bookworm-slim

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY services/chain-worker/package.json ./services/chain-worker/package.json
RUN pnpm install --frozen-lockfile --filter @agent-shop/chain-worker...
COPY services/chain-worker ./services/chain-worker
# Required by the protocol worker (testnet-ccip profile) to deploy the CCIP
# route contract stack (RemoteWorkBoard, identity registry, CCIP sender/receiver).
COPY hardhat-artifacts ./hardhat-artifacts

EXPOSE 9090
CMD ["node", "services/chain-worker/src/index.ts"]
