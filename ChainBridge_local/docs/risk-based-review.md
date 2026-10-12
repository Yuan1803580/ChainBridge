# 节点风险分级审核方案

## 1. 目标

本版本不再要求每个节点都调用固定的验证 Agent，而是按照节点风险选择审核方式：

```text
LOW
-> 协调 Agent 执行不可变规则检查
-> 通过后进入 SUCCEEDED

MEDIUM
-> 协调 Agent 执行不可变规则检查
-> 使用可复现哈希进行抽样
-> 未抽中：自动通过
-> 抽中：等待 1 个独立审核身份确认

HIGH
-> 协调 Agent 执行不可变规则检查
-> 等待至少 2 个不同审核身份确认
-> 开启挑战期
-> 无挑战：挑战期结束后通过
-> 有挑战：进入 DISPUTED，等待仲裁
```

所有等级都遵守同一条资金规则：

```text
审核通过只得到 SUCCEEDED
-> 尚未解锁后续节点
-> 尚未视为付款完成
-> 跨链结算确认后得到 SETTLED
-> 后续节点才可以进入 READY
```

## 2. 节点审核快照

协调 Agent 先根据节点难度生成可审计评分。第一版评分使用以下确定性因素：

```text
任务是否涉及安全、合约、结算、支付、金融或法律
是否属于验证、审计、分析或复核能力
前置 SETTLED 依赖数量
requiredFields、mustEqual 和 minScore 等验收约束
节点报酬暴露
```

评分结果映射为：

```text
0-29   -> LOW
30-69  -> MEDIUM
70-100 -> HIGH
```

系统同时保存 `riskAssignmentMode`、`riskScore` 和 `riskReasons`。如果工作流请求明确提供 `riskLevel`，则使用 `MANUAL` 模式；否则由协调 Agent 使用上述规则生成 `AUTO` 模式。风险判定发生在 Agent 匹配和派发之前。

本地和测试环境默认把自动中风险抽样率设为 0，保证演示流程不会随机停在人工复核；生产环境默认使用 1000 bps，也可以通过 `AUTO_MEDIUM_SAMPLING_BPS` 修改。自动高风险挑战期通过 `AUTO_HIGH_CHALLENGE_SECONDS` 配置，默认 3600 秒。

每个节点在工作流创建时固定以下字段：

```text
riskLevel
riskAssignmentMode
riskScore
riskReasons
reviewPolicyVersion
reviewPolicyHash
requiredApprovals
challengePeriodSeconds
samplingRateBps
reviewBudget
```

这些字段包含在工作流条款中，因此受到 `termsHash` 约束。每次创建 `attempt` 时再次复制 `reviewPolicyHash`，任务邀请也会把 `riskLevel` 和 `reviewPolicyHash` 发给执行 Agent。

最终 `coordinatorReviewHash` 同时包含：

```text
criteriaHash
resultHash
proofHash
riskLevel
reviewPolicyHash
抽样结论
独立审批记录
挑战与仲裁结果
最终 PASS / FAIL
```

链上 `CoordinatorReview` 仍记录最终聚合审核哈希，因此没有破坏现有 Worker 和结算合约接口。

## 3. 中风险抽样

抽样不是由协调 Agent 临时随机决定，而是使用以下内容计算可复现哈希：

```text
workflowId
nodeId
attemptId
reviewPolicyHash
resultHash
proofHash
```

把哈希映射到 `0-9999`，再与 `samplingRateBps` 比较。`resultHash` 和 `proofHash` 在抽样前已经作为当前 attempt 的结果证明提交，任何人使用同一输入都能复核抽样结论，协调 Agent 不能在审核阶段临时改变抽样结果。

## 4. 审批和挑战接口

提交独立审批：

```text
POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/review-approvals
```

约束：

- 执行该 attempt 的 Agent 不能审核自己。
- 工作流请求方不能作为独立审核人。
- 审核人必须具有 `reviewer` 或 `admin` 角色。
- 同一审核人对同一 attempt 只能提交一次决定。
- 任一有效 `FAIL` 会阻止结算，并按原有重试规则释放预留、增加 `attemptId`、重新选择 Agent。

发起高风险挑战：

```text
POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/review-challenges
```

解决挑战：

```text
POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/review-challenges/{challengeId}/resolve
```

挑战期间节点为 `DISPUTED`，不会创建结算，也不会解锁后续节点。

## 5. 失败与资金处理

```text
规则检查失败、独立审核 FAIL 或仲裁 REJECT_RESULT
-> attempt = REVIEW_FAILED
-> 目标链付款不执行
-> 节点预算 reservation 释放
-> 记录 AGENT_RESULT_REJECTED
-> 尚有重试次数：node = RETRYING
-> attemptId + 1，重新匹配 Agent，可切换到另一条链
-> 重试耗尽：workflow = FAILED
-> 按现有退款流程退回未支付预算
```

执行 Agent 已经发生的本链结果提交 Gas 仍由该 Agent 承担，不能回滚。结果证明回传和成功结算的协议 Gas 继续沿用现有 Gas 账本规则。

## 6. 当前边界

本地版本中的多主体审批使用平台认证身份和数据库唯一约束，已经能证明不同登录身份分别作出决定，但还不是链上 EIP-712 多签。进入公开测试网前应继续完成：

1. 审核决定使用 EIP-712 签名，并把签名集合或 Merkle 根写入最终 `reviewHash`。
2. 高风险仲裁权限改为多签或治理合约，避免单个管理员独立裁决。
3. 为挑战期增加链上时间锁，避免只依赖 API 服务器时间。
4. 明确 `reviewBudget` 的实际支付对象、收费条件和退款规则；当前字段只作为预算上限，不会自动支付给审核人。

## 7. 验收覆盖

新增自动化测试覆盖：

- 中风险 100% 抽样后必须等待独立审核。
- 执行 Agent 不能审核自己的结果。
- 中风险审核通过后才能进入结算。
- 高风险必须获得两个不同身份的审批。
- 高风险挑战期间不能付款。
- 仲裁拒绝后释放预算，失败 Agent 不获得报酬。
- 原有成功、失败、更换 Agent、跨链更换归属链、旧 attempt 拒绝和账本平衡流程保持通过。
