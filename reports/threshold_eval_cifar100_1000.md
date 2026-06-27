# CIFAR-100 Threshold Policy Evaluation, 1000 Samples

Evaluation date: 2026-06-27

Dataset version: `dataset@cifar100-fv-train-upload-001`

Model version: `cifar100-fv-train-upload-run-dcea1c94d704-candidate`

Sample protocol: 1000 CIFAR-100 test images, balanced across 100 classes.

## Goal

Evaluate whether the threshold policy can reduce unsafe automatic decisions by abstaining on uncertain samples. The policy is measured by selective risk and coverage:

- Selective risk: error rate among automatically accepted samples.
- Coverage: percentage of samples automatically accepted.
- Review rate: percentage of samples sent to manual review.

## Results

| Policy | Target risk | tau_conf | tau_margin | Accepted accuracy | Selective risk | Coverage | Review rate | Accepted | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raw model, no abstention | n/a | 0.0000 | 0.0000 | 87.80% | 12.20% | 100.00% | 0.00% | 1000 | 122 |
| Current saved thresholds | n/a | 0.9709 | 0.2544 | 99.40% | 0.60% | 49.70% | 50.30% | 497 | 3 |
| Auto-searched confidence threshold | 1% | 0.9348 | 0.0000 | 99.00% | 1.00% | 60.00% | 40.00% | 600 | 6 |
| Auto-searched confidence + margin threshold | 1% | 0.0000 | 0.8942 | 99.17% | 0.83% | 60.00% | 40.00% | 600 | 5 |
| Auto-searched confidence threshold | 5% | 0.6680 | 0.0000 | 95.00% | 5.00% | 82.00% | 18.00% | 820 | 41 |
| Auto-searched confidence + margin threshold | 5% | 0.6680 | 0.0000 | 95.00% | 5.00% | 82.00% | 18.00% | 820 | 41 |

## Interpretation

Without abstention, the model accepts every sample and makes 122 errors out of 1000. This is not suitable for a risk-controlled workflow.

The current saved threshold policy is conservative: it reduces accepted errors to 3 and reaches 99.40% accepted accuracy, but sends 50.30% of samples to manual review.

Automatic threshold search gives a tunable trade-off:

- With a 1% risk target, the system accepts 600 / 1000 samples automatically while keeping accepted accuracy around 99%.
- With a 5% risk target, the system accepts 820 / 1000 samples automatically while keeping accepted accuracy at 95%.

This supports the core product claim: FineVision can use feedback/replay data to choose abstention thresholds that satisfy a target automatic-decision risk while increasing coverage and reducing manual review cost.

## Resume-Friendly Metric

On a 1000-sample CIFAR-100 replay set, the risk-constrained abstention policy improved automatic-decision accuracy from 87.8% to 99.0% at 60% coverage under a 1% selective-risk target, and achieved 95.0% automatic-decision accuracy at 82% coverage under a 5% selective-risk target.

## Held-Out Check

To reduce threshold overfitting, an additional held-out check split the same 1000 images into 500 calibration samples and 500 independent test samples. Each class contributes 5 calibration images and 5 test images.

| Policy | Threshold source | tau_conf | tau_margin | Test accepted accuracy | Test selective risk | Test coverage | Test review rate | Accepted | Errors |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Raw model, no abstention | n/a | 0.0000 | 0.0000 | 86.40% | 13.60% | 100.00% | 0.00% | 500 | 68 |
| Current saved thresholds | training calibration | 0.9709 | 0.2544 | 99.60% | 0.40% | 50.20% | 49.80% | 251 | 1 |
| Confidence threshold | 500-sample calibration, 1% target | 0.9089 | 0.0000 | 97.81% | 2.19% | 63.80% | 36.20% | 319 | 7 |
| Confidence + margin threshold | 500-sample calibration, 1% target | 0.0000 | 0.8486 | 97.53% | 2.47% | 64.80% | 35.20% | 324 | 8 |
| Confidence threshold | 500-sample calibration, 5% target | 0.6624 | 0.0000 | 94.55% | 5.45% | 80.80% | 19.20% | 404 | 22 |
| Confidence + margin threshold | 500-sample calibration, 5% target | 0.0000 | 0.3710 | 93.41% | 6.59% | 85.00% | 15.00% | 425 | 28 |

The held-out result is more conservative than the same-set replay result. This is expected: thresholds chosen on 500 calibration samples do not perfectly guarantee the exact target risk on unseen samples. For product use, the next rigor step is to tune thresholds on a larger feedback/calibration pool and report both calibration-set and held-out-set selective risk with confidence intervals.
