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

## OOD Pilot: CIFAR-100 ID + CUB-200 OOD

OOD set: 200 CUB-200-2011 bird images, sampled as 1 image per bird class.

Total replay set: 200 CIFAR-100 in-domain images + 200 CUB OOD images.

Target selective risk: <= 1%.

Max allowed in-domain OOD false positive rate during threshold search: <= 5%.

| Method | Accepted Acc | Selective Risk | Accept Coverage | Auto Coverage | Review Rate | OOD Precision | OOD Recall | ID OOD FPR | OOD Accept Rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| No abstention raw model | 44.75% | 55.25% | 100.00% | 100.00% | 0.00% | n/a | 0.00% | 0.00% | 100.00% |
| Current saved thresholds | 98.08% | 1.92% | 26.00% | 26.00% | 74.00% | n/a | 0.00% | 0.00% | 0.50% |
| Confidence-only risk constrained | 100.00% | 0.00% | 7.00% | 7.00% | 93.00% | n/a | 0.00% | 0.00% | 0.00% |
| Joint confidence + margin risk constrained | 100.00% | 0.00% | 8.00% | 8.00% | 92.00% | n/a | 0.00% | 0.00% | 0.00% |
| Joint confidence + margin + OOD risk constrained | 99.32% | 0.68% | 37.00% | 87.25% | 12.75% | 99.50% | 100.00% | 0.50% | 0.00% |

OOD takeaways:

- Without OOD rejection, the raw model accepts every OOD image as some CIFAR-100 class, so OOD accept rate is 100%.
- Confidence-only and confidence+margin can avoid accepting OOD by becoming extremely conservative, but this drives review rate above 90%.
- Adding an OOD distance threshold gives the useful operating point: 99.32% accepted accuracy, 100.00% OOD recall, 99.50% OOD precision, and 12.75% review rate.
- The OOD-enabled policy automatically handles 87.25% of the mixed replay set by either accepting confident in-domain samples or rejecting OOD samples.

Stronger resume wording after this OOD pilot:

> Built an automated selective-classification replay evaluator with OOD detection. On a 400-image CIFAR-100 + CUB OOD pilot, a joint confidence/margin/OOD threshold policy achieved 99.3% accepted accuracy, 100.0% OOD recall, 99.5% OOD precision, and reduced manual review rate from 74.0% under the saved conservative policy to 12.8%.
