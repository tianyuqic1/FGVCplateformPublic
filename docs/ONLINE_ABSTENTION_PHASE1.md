# Online Abstention Phase 1: Risk-Constrained Threshold Strategy

Status: implemented MVP. FineVision now supports feedback-backed risk-constrained abstention
policy proposals in shadow mode.

## 一句话解释

在线弃权第一阶段不是让系统自动“越学越改生产阈值”，而是给模型加一组可解释的安全门：

```text
模型足够确定 -> 自动接受
模型像是数据集外样本 -> 进入 OOD 复核
其他不确定情况 -> 弃权，进入人工复核
```

然后用反馈池里的人工复核结果评估和更新候选阈值，但第一版只做候选策略和影子评估，不自动覆盖当前生产决策。

## 为什么应该先做这一阶段

FineVision 当前已经有这些基础：

- 数据集版本和类别范围。
- DINOv3 CLS 特征和分类头。
- 校准报告、阈值 sweep、阈值策略 artifact。
- 推理事件 `inference_events`。
- 自动复核队列 `review_items`。
- 人工结论反馈池 `feedback_items`。
- LLM 辅助，但 LLM 只做解释，不做最终标签或阈值更新。

这说明平台已经具备“从反馈里评估弃权策略”的闭环。但反馈样本还少，而且多数反馈来自模型已经不确定的样本，所以第一阶段不能直接自动调生产阈值。更稳的路线是：

```text
当前固定策略继续工作
新策略只在后台影子运行
对比新旧策略和人工反馈
生成策略报告
用户确认后再考虑启用策略版本
```

## 核心术语

### confidence / calibrated probability

模型第一名类别的可信度。

例如：

```text
cat 0.92
dog 0.05
bird 0.03
```

这里 `confidence = 0.92`。

`calibrated probability` 是校准后的置信度。它比 raw confidence 更适合做生产阈值，因为原始模型分数可能虚高或虚低。

### margin

第一名和第二名之间的差距。

```text
cat 0.92
dog 0.05
margin = 0.87
```

margin 大，说明模型区分清楚。margin 小，说明模型在两个类别之间摇摆。

### OOD score

OOD 是 `out-of-distribution`，意思是“数据集范围外”。

如果数据集是 CIFAR-10，但用户上传医学 CT、发票、花朵图片，这些都可能是 OOD。`ood_score` 越高，越像数据集外样本。

### entropy

模型概率分布的混乱程度。概率集中在一个类别时 entropy 低；概率分散到很多类别时 entropy 高。

第一阶段可先不强依赖 entropy，只作为后续增强信号。

### nearest-neighbor consistency

近邻一致性。看 query 图片在特征空间里最近的训练样本标签是否一致。

如果最近 5 个样本都是 `cat`，比最近样本混着 `cat/dog/deer` 更可靠。第一阶段可先作为报告字段，不一定直接进入决策规则。

## 第一阶段决策规则

第一阶段使用风险约束阈值策略：

```text
accept:
  confidence >= tau_conf
  margin >= tau_margin
  ood_score <= tau_ood

reject_ood:
  ood_score > tau_ood

otherwise:
  abstain
```

其中：

- `tau_conf` 是置信度门槛。
- `tau_margin` 是 top-1 和 top-2 差距门槛。
- `tau_ood` 是 OOD 分数门槛。

示例：

```text
tau_conf = 0.80
tau_margin = 0.20
tau_ood = 5.0
```

含义：

```text
置信度至少 0.80
第一名和第二名至少差 0.20
OOD 分数不能超过 5.0
```

三条都满足，模型才自动接受。否则进入人工路径。

## 目标风险是什么

`target_selective_risk` 可以理解成“自动放行部分允许的最高错误率”。

如果业务要求自动放行至少 95% 准确率：

```text
target_selective_risk = 5%
```

注意：这不是要求全部样本 95% 准确，而是要求模型自动接受的那部分样本至少 95% 准确。

例如 1000 张图片：

```text
模型自动接受 700 张
人工复核 300 张
自动接受的 700 张里错了 28 张
```

则：

```text
coverage = 700 / 1000 = 70%
selective_risk = 28 / 700 = 4%
review_cost = 300
```

如果目标风险是 5%，这个策略达标；如果目标风险是 1%，这个策略不达标。

优化目标是：

```text
在 selective_risk <= target_selective_risk 的前提下，
尽量提高 coverage，
尽量降低 review_cost。
```

翻译成业务语言：

```text
先保证自动放行足够安全，
再让模型尽可能多处理样本，
把人工复核留给真正不确定的样本。
```

## 如何用反馈池调阈值

反馈池提供人工结论。每个反馈样本都可以回放当时的推理指标：

- 模型 top-k。
- confidence。
- margin。
- ood_score。
- 当前 decision。
- 人工 final_outcome。
- 人工 final_label。

策略搜索流程：

```text
1. 收集某个 dataset_version + model_version 下的反馈样本。
2. 对候选阈值组合进行回放。
3. 计算每组阈值的 coverage、selective_risk、review_cost。
4. 保留 selective_risk <= target_selective_risk 的候选。
5. 从候选中选择 coverage 最高、review_cost 最低的一组。
6. 生成 abstention_policy_version 和评估报告。
```

第一阶段不应该把这组阈值直接写回生产模型，而是生成候选策略。

## 影子模式

影子模式是第一阶段最重要的安全边界。

每次推理仍然返回当前真实策略的 decision：

```text
decision = accept | abstain | reject_ood
```

同时后台额外计算候选策略的 shadow decision：

```text
shadow_decision = accept | abstain | reject_ood
```

但 shadow decision 不改变用户路径，只用于统计：

- 新策略比旧策略多接受了哪些样本。
- 新策略比旧策略多拦截了哪些样本。
- 如果人工反馈存在，新策略是否更接近人工结论。
- 新策略预计会增加还是减少复核量。

## 为什么不用 LLM 做弃权算法

LLM 可以解释模型证据，可以辅助复核，但不应该直接决定弃权阈值。

原因：

- LLM 决策不可稳定回放。
- LLM 输出难以做严格风险证明。
- LLM 容易受提示词和上下文变化影响。
- 阈值策略需要可审计、可复现、可版本化。

所以第一阶段坚持使用可解释的数值策略。LLM 只能解释策略报告，不能改阈值，不能自动发布策略。

## MVP 数据模型建议

第一阶段建议新增两个核心概念。

### abstention_policy_versions

保存候选弃权策略版本。MVP 已由 migration `20260623_0005` 落地。

建议字段：

```text
id uuid primary key
policy_key text unique not null
dataset_id uuid not null
dataset_version_id uuid not null
model_version_id uuid not null
status text not null
target_selective_risk double precision not null
tau_conf double precision not null
tau_margin double precision not null
tau_ood double precision
metrics jsonb not null
source_feedback_count integer not null
selection_config jsonb not null
created_by text
created_at timestamptz not null
updated_at timestamptz not null
```

`status` 建议：

```text
shadow
candidate
archived
```

第一阶段只实现 `shadow` / `candidate` / `archived`。`active` 没有实现，避免误导为生产启用能力。

### abstention_shadow_decisions

保存候选策略对推理事件的影子判断。

建议字段：

```text
id uuid primary key
policy_version_id uuid not null
inference_event_id uuid not null
current_decision text not null
shadow_decision text not null
score_snapshot jsonb not null
decision_diff text not null
created_at timestamptz not null
```

`decision_diff` 示例：

```text
same
new_accepts_old_abstains
new_abstains_old_accepts
new_rejects_ood
other_change
```

## API 设计建议

第一阶段已实现这些 API：

```text
POST /api/abstention-policies/propose
GET  /api/abstention-policies
GET  /api/abstention-policies/{policy_key}
GET  /api/abstention-policies/{policy_key}/shadow-decisions
```

暂不建议做自动启用接口。若要启用，也必须是后续阶段的手动操作：

```text
POST /api/abstention-policies/{policy_key}/activate
```

这个接口第一阶段可以不做。

## 前端页面建议

第一阶段主入口已放在 `/feedback` 反馈池页面的“弃权策略评估”面板。数据集、模型和推理页面不提供启用入口。

页面展示：

- 当前目标风险。
- 候选阈值。
- 反馈样本数。
- 估计 coverage。
- 估计 selective risk。
- 估计人工复核量。
- 新旧策略差异样本。
- 策略状态：shadow / candidate。

页面文案必须明确：

```text
当前策略仅为影子评估，不改变真实推理结果。
```

## 非目标

第一阶段不做：

- 不自动修改生产阈值。
- 不自动把策略设为 active。
- 不让 LLM 决定是否 accept/abstain/reject_ood。
- 不把反馈直接写回训练集。
- 不做强化学习或 bandit。
- 不承诺 conformal 的统计保证。

## 验收标准

第一阶段完成后应满足：

- 可以从反馈池生成一个候选弃权策略版本。
- 候选策略包含 `tau_conf`、`tau_margin`、`tau_ood` 和目标风险。
- 候选策略报告包含 coverage、selective risk、review cost。
- 推理事件可以记录 shadow decision，且不改变真实 decision。
- UI 明确展示 shadow-only 状态。
- LLM 只能解释报告，不能改策略。
- 测试覆盖策略生成、指标计算、shadow decision 记录和不改变真实推理路径。

## 推荐实施顺序

```text
1. 文档和术语落地。
2. 策略评估函数：输入历史样本和阈值，输出 metrics。
3. 候选阈值搜索函数：在目标风险约束下找最大 coverage。
4. 数据表和 API。
5. 影子决策写入。
6. 前端报告页。
7. 回归测试和验收。
```
