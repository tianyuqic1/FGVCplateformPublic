# CIFAR-100 Threshold Policy Pilot Evaluation

Date: 2026-06-27

Dataset: `dataset@cifar100-fv-train-upload-001`

Model: `cifar100-fv-train-upload-run-dcea1c94d704-candidate`

Sample: 200 CIFAR-100 test images, balanced as 2 images per class across 100 classes.

Scope: in-domain CIFAR-100 replay only. This run does not estimate OOD precision or recall.

## Result: Target Selective Risk <= 5%

Raw top-1 accuracy without abstention: 89.50%.

| Method | tau_conf | tau_margin | Accepted Acc | Selective Risk | Coverage | Review Rate | Accepted | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No abstention raw model | 0.0000 | 0.0000 | 89.50% | 10.50% | 100.00% | 0.00% | 200 | 21 |
| Current saved thresholds | 0.9709 | 0.2544 | 99.03% | 0.97% | 51.50% | 48.50% | 103 | 1 |
| Confidence-only risk constrained | 0.5165 | 0.0000 | 95.05% | 4.95% | 91.00% | 9.00% | 182 | 9 |
| Joint confidence + margin risk constrained | 0.4029 | 0.1843 | 95.05% | 4.95% | 91.00% | 9.00% | 182 | 9 |

## Result: Target Selective Risk <= 1%

| Method | tau_conf | tau_margin | Accepted Acc | Selective Risk | Coverage | Review Rate | Accepted | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No abstention raw model | 0.0000 | 0.0000 | 89.50% | 10.50% | 100.00% | 0.00% | 200 | 21 |
| Current saved thresholds | 0.9709 | 0.2544 | 99.03% | 0.97% | 51.50% | 48.50% | 103 | 1 |
| Confidence-only risk constrained | 0.8177 | 0.0000 | 99.32% | 0.68% | 74.00% | 26.00% | 148 | 1 |
| Joint confidence + margin risk constrained | 0.0000 | 0.7077 | 99.32% | 0.68% | 74.00% | 26.00% | 148 | 1 |

## Takeaways

- The raw classifier is not production-safe on this replay: accepting every sample gives 89.50% accuracy and 10.50% selective risk.
- The current saved threshold policy is conservative: 99.03% accepted accuracy, but 48.50% of samples still require review.
- Under a 5% selective-risk target, a replay-tuned threshold can reduce review rate from 48.50% to 9.00% on this 200-image sample.
- Under a stricter 1% selective-risk target, a replay-tuned threshold can reduce review rate from 48.50% to 26.00% while keeping accepted accuracy at 99.32%.
- On this small in-domain sample, confidence-only and joint confidence+margin reach the same accepted set size. To prove the value of joint thresholds over confidence-only, the next evaluation should add OOD samples and a larger/harder replay set.

## Resume-Friendly Metric Candidate

Pilot wording:

> Built a feedback-replay threshold evaluation pipeline for visual selective classification. On a balanced 200-image CIFAR-100 replay, the optimized risk-constrained policy reduced manual review rate from 48.5% to 26.0% under a <=1% selective-risk target, while maintaining 99.3% accepted accuracy.

For a stronger final resume metric, rerun on a larger test set with an explicit OOD subset and report OOD precision/recall.
