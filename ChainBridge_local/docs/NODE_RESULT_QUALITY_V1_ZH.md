# 节点结果质量评分策略 V1

策略版本：`NODE_RESULT_QUALITY_V1`

## 公式

```text
节点结果质量 Q = 0.50 * 完成度 + 0.30 * 审核结果 + 0.20 * 匹配时 Agent 信任分
```

所有分项均归一化到 0-100，最终结果保留两位小数。实验开始后，同一组实验必须固定使用该策略版本和权重。

## 分项定义

- 完成度：Coordinator Core 对 `acceptanceCriteria.requiredFields` 和 `mustEqual` 逐项检查后的通过比例。支持 `artifacts.pdf` 形式的嵌套路径。若没有结构化验收项，则以是否提交非空结果作为一个检查项。
- 审核结果：最终审核通过为 100，审核失败、执行超时或 Agent 执行失败为 0。
- 匹配时信任分：创建 attempt 时冻结 Agent 的信任分。后续信誉变化不会改写该 attempt 的历史评分。

`acceptanceCriteria.minScore` 和 `requiredFields` 中的 `score` 仅作为旧工作流数据保留，不参与 Core 审核或质量公式。Agent 返回的 `result.score` 保存为 `reportedScore`，明确标记为不可信。Agent 无法通过提高自评分改变审核结果、结算结果或 Core 质量分。

若结果中只有 Agent 自报 `score` 而没有业务输出，Core 将其视为未提交有效结果。用户界面的任务结果摘要不展示自评分，只在评分审计区域将其作为“不参与计算”的原始记录显示。

## 生命周期

- 结果审核完成：生成 `PROVISIONAL` 质量分。
- 审核失败或 Agent 执行失败：直接生成 `FINAL` 质量分。
- 节点付款并进入 `SETTLED`：将通过审核的质量分定稿为 `FINAL`。
- Agent 被替换：每个 `attemptId` 独立保存评分，旧 attempt 不覆盖；节点最终结果取当前成功 attempt。

## 审计字段

- `reportedScore`
- `matchingTrustScore`
- `matchingTrustSource`
- `qualityScore`
- `qualityStatus`
- `qualityBreakdown`
- `qualityPolicyVersion`
- `qualityEvidenceHash`
- `qualityCalculatedAt`

`qualityEvidenceHash` 绑定策略版本、结果哈希、验收条件哈希、最终分数和分项明细。相同输入必须得到相同分数和证据哈希。

## 与实验指标的关系

节点质量分用于衡量单个 attempt 的结果质量。实验层面的平均质量可计算为：

```text
平均节点质量 = 所有纳入统计的 FINAL attempt qualityScore 之和 / attempt 数量
```

若实验需要工作流级质量，建议仅汇总每个节点的最终成功 attempt，失败恢复研究则同时报告所有失败 attempt 的分数分布。不要将 Agent 匹配分、节点风险分或长期信誉分直接当作节点质量分。
