# Agent 候选池、备用选择与失败恢复

## 当前行为

Coordinator 第一次派发节点时，从 Agent 信息平台生成最多三个候选的排名快照：

```text
能力、在线状态、容量、Gas readiness、报价、信誉、成功率、延迟
-> 先选择 Agent
-> 再读取 Agent homeChainId
-> 再保存 routeId + routeVersion
```

只有第一名会创建业务 `attemptId` 和奖励 reservation。第二、第三名保持
`AVAILABLE`，不会占用工作流资金，也不会被当成已经接单。

## 失败恢复

如果当前 Agent 的结果违反不可变验收条件：

```text
Attempt -> REVIEW_FAILED
Candidate -> FAILED
FailureIncident -> AGENT_RESULT_REJECTED
Reservation -> RELEASED
Node -> RETRYING
```

Coordinator 会按排名重新校验备用候选：

```text
Agent 仍为 active
能力仍然匹配
availability = ONLINE
capacityAvailable = true
gasReady = true
报价未过期且不超过节点预算
原 routeId + routeVersion 仍为 ACTIVE
```

校验通过才创建新的 attempt。旧 attempt 的迟到结果继续由
`currentAttemptId` 检查拒绝。

## 候选耗尽

默认数据只有 Agent-B1 具备 `research` 能力。B 失败后如果没有新候选：

```text
Node -> WAITING_FOR_AGENT
Workflow 保持 RUNNING
Coordinator 返回 waiting_for_agent
```

管理员可以把 Agent 信息平台中的现有 provider 登记为新的工作流 Agent：

```http
POST /api/v1/workflow-agents
Authorization: Bearer <admin-token>
Idempotency-Key: <uuid>
Content-Type: application/json

{
  "agentId": "<provider-agent-id>",
  "agentKey": "Agent-D2",
  "chainId": 31340,
  "capabilities": ["research", "extraction"],
  "priority": 15,
  "priceFloor": "4.20",
  "successRateBps": 9300,
  "trustScore": 89,
  "endpointLatencyMs": 220,
  "availability": "ONLINE",
  "capacityAvailable": true,
  "gasReady": true
}
```

下一次调用 `/workflows/{workflowId}/coordinate` 时，Coordinator 会创建新的
匹配轮次、选中 Agent-D2，并为相同 `nodeId` 创建新的 `attemptId`。

## 审计数据

工作流详情为每个节点返回：

```text
matchRounds[]
matchRounds[].policyVersion
matchRounds[].candidates[]
candidates[].rank
candidates[].score
candidates[].scoreBreakdown
candidates[].quoteAmount
candidates[].routeId
candidates[].routeVersion
candidates[].status
candidates[].exclusionReason
failureIncidents[]
```

前端工作流详情页会展示最新一轮候选排名、选中状态、失败状态和淘汰原因。
