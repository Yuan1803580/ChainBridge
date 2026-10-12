FROM node:22-alpine AS build

WORKDIR /workspace
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json
RUN pnpm install --filter @agent-market/web... --frozen-lockfile

COPY apps/web ./apps/web

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=false
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=$NEXT_PUBLIC_ENABLE_MOCK_FALLBACK \
    NEXT_STANDALONE=true

RUN pnpm --dir apps/web build

FROM node:22-alpine AS runner

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

COPY --from=build /workspace/apps/web/.next/standalone ./
COPY --from=build /workspace/apps/web/.next/static ./apps/web/.next/static

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
