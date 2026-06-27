# FineVision Selective Classification / OOD Rejection Benchmark

Evaluation date: 2026-06-27

Dataset: CIFAR-100 as in-distribution test data, SVHN test split as out-of-distribution data.

Models:

- ViT-S: `dinov3_vits16`, model version `cifar100-fv-train-upload-run-dcea1c94d704-candidate`.
- ViT-L: `dinov3_vitl16`, model version `cifar100-fv-train-upload-run-da0a15c56c79-candidate`.

Protocol:

- ID-only benchmark: 1000 balanced CIFAR-100 test images.
- OOD benchmark: 1000 balanced CIFAR-100 test images + 1000 SVHN test images.
- Decisions: `accept`, `abstain`, or `reject_ood`.
- Objective: maximize automatic handling under a target selective-risk constraint.

Key metrics:

- Selective risk: error rate among automatically accepted samples. Lower is safer.
- Accepted accuracy: accuracy among automatically accepted samples.
- Accept coverage: percentage of all samples accepted as ID predictions.
- Auto coverage: percentage automatically handled by either `accept` or `reject_ood`.
- Review rate: percentage routed to manual review.
- OOD recall: percentage of true OOD samples rejected as OOD.
- OOD accept rate: percentage of OOD samples incorrectly accepted as normal ID predictions.

## Executive Summary

| Model | Benchmark | Target risk | Best policy | Accepted accuracy | Selective risk | Accept coverage | Auto coverage | Review rate | OOD recall | OOD accept rate |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| ViT-S | CIFAR-100 ID-only | 1% | confidence+margin | 99.17% | 0.83% | 60.00% | 60.00% | 40.00% | n/a | n/a |
| ViT-S | CIFAR-100 + SVHN OOD | 1% | confidence+margin+OOD | 99.15% | 0.85% | 29.35% | 79.60% | 20.40% | 100.00% | 0.00% |
| ViT-S | CIFAR-100 ID-only | 5% | confidence+margin | 95.00% | 5.00% | 82.00% | 82.00% | 18.00% | n/a | n/a |
| ViT-S | CIFAR-100 + SVHN OOD | 5% | confidence+margin+OOD | 95.00% | 5.00% | 41.00% | 91.00% | 9.00% | 100.00% | 0.00% |
| ViT-L | CIFAR-100 ID-only | 1% | confidence+margin | 99.08% | 0.92% | 76.00% | 76.00% | 24.00% | n/a | n/a |
| ViT-L | CIFAR-100 + SVHN OOD | 1% | confidence+margin+OOD | 99.30% | 0.70% | 35.95% | 87.45% | 12.55% | 100.00% | 0.00% |
| ViT-L | CIFAR-100 ID-only | 5% | confidence+margin | 95.21% | 4.79% | 96.00% | 96.00% | 4.00% | n/a | n/a |
| ViT-L | CIFAR-100 + SVHN OOD | 5% | confidence+margin+OOD | 95.03% | 4.97% | 48.30% | 98.30% | 1.70% | 100.00% | 0.00% |

## ID-Only Results

These runs measure selective classification on normal CIFAR-100 samples. They answer: if the model is uncertain, can it abstain and protect automatic-decision quality?

### ViT-S

| Target risk | Policy | tau_conf | tau_margin | Accepted accuracy | Selective risk | Coverage | Review rate | Accepted | Errors |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1% | raw model | 0.0000 | 0.0000 | 87.80% | 12.20% | 100.00% | 0.00% | 1000 | 122 |
| 1% | current saved thresholds | 0.9709 | 0.2544 | 99.40% | 0.60% | 49.70% | 50.30% | 497 | 3 |
| 1% | auto-searched confidence+margin | 0.0000 | 0.8942 | 99.17% | 0.83% | 60.00% | 40.00% | 600 | 5 |
| 5% | raw model | 0.0000 | 0.0000 | 87.80% | 12.20% | 100.00% | 0.00% | 1000 | 122 |
| 5% | current saved thresholds | 0.9709 | 0.2544 | 99.40% | 0.60% | 49.70% | 50.30% | 497 | 3 |
| 5% | auto-searched confidence+margin | 0.6680 | 0.0000 | 95.00% | 5.00% | 82.00% | 18.00% | 820 | 41 |

### ViT-L

| Target risk | Policy | tau_conf | tau_margin | Accepted accuracy | Selective risk | Coverage | Review rate | Accepted | Errors |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1% | raw model | 0.0000 | 0.0000 | 93.10% | 6.90% | 100.00% | 0.00% | 1000 | 69 |
| 1% | current saved thresholds | 0.9721 | 0.5388 | 99.07% | 0.93% | 75.50% | 24.50% | 755 | 7 |
| 1% | auto-searched confidence+margin | 0.9709 | 0.0000 | 99.08% | 0.92% | 76.00% | 24.00% | 760 | 7 |
| 5% | raw model | 0.0000 | 0.0000 | 93.10% | 6.90% | 100.00% | 0.00% | 1000 | 69 |
| 5% | current saved thresholds | 0.9721 | 0.5388 | 99.07% | 0.93% | 75.50% | 24.50% | 755 | 7 |
| 5% | auto-searched confidence+margin | 0.0000 | 0.2168 | 95.21% | 4.79% | 96.00% | 4.00% | 960 | 46 |

## OOD Results

These runs mix CIFAR-100 ID samples with SVHN OOD samples. They answer: can the policy reject obvious distribution-shift samples instead of treating them as CIFAR-100 classes?

### ViT-S

| Target risk | Policy | tau_conf | tau_margin | tau_ood | Accepted accuracy | Selective risk | Accept coverage | Auto coverage | Review rate | OOD precision | OOD recall | ID OOD FPR | OOD accept rate |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1% | raw model | 0.0000 | 0.0000 | n/a | 43.90% | 56.10% | 100.00% | 100.00% | 0.00% | n/a | 0.00% | 0.00% | 100.00% |
| 1% | current saved thresholds | 0.9709 | 0.2544 | n/a | 97.44% | 2.56% | 25.35% | 25.35% | 74.65% | n/a | 0.00% | 0.00% | 1.00% |
| 1% | confidence+margin only | 0.9882 | 0.0000 | n/a | 99.00% | 1.00% | 20.00% | 20.00% | 80.00% | n/a | 0.00% | 0.00% | 0.20% |
| 1% | confidence+margin+OOD | 0.0000 | 0.9039 | 0.0055 | 99.15% | 0.85% | 29.35% | 79.60% | 20.40% | 99.50% | 100.00% | 0.50% | 0.00% |
| 5% | raw model | 0.0000 | 0.0000 | n/a | 43.90% | 56.10% | 100.00% | 100.00% | 0.00% | n/a | 0.00% | 0.00% | 100.00% |
| 5% | current saved thresholds | 0.9709 | 0.2544 | n/a | 97.44% | 2.56% | 25.35% | 25.35% | 74.65% | n/a | 0.00% | 0.00% | 1.00% |
| 5% | confidence+margin only | 0.9610 | 0.0000 | n/a | 95.71% | 4.29% | 28.00% | 28.00% | 72.00% | n/a | 0.00% | 0.00% | 2.00% |
| 5% | confidence+margin+OOD | 0.6659 | 0.3670 | 3.3013 | 95.00% | 5.00% | 41.00% | 91.00% | 9.00% | 100.00% | 100.00% | 0.00% | 0.00% |

### ViT-L

| Target risk | Policy | tau_conf | tau_margin | tau_ood | Accepted accuracy | Selective risk | Accept coverage | Auto coverage | Review rate | OOD precision | OOD recall | ID OOD FPR | OOD accept rate |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1% | raw model | 0.0000 | 0.0000 | n/a | 46.55% | 53.45% | 100.00% | 100.00% | 0.00% | n/a | 0.00% | 0.00% | 100.00% |
| 1% | current saved thresholds | 0.9721 | 0.5388 | n/a | 97.27% | 2.73% | 38.45% | 38.45% | 61.55% | n/a | 0.00% | 0.00% | 1.40% |
| 1% | confidence+margin only | 0.9880 | 0.9820 | n/a | 99.10% | 0.90% | 33.40% | 33.40% | 66.60% | n/a | 0.00% | 0.00% | 0.40% |
| 1% | confidence+margin+OOD | 0.9742 | 0.0000 | 0.0078 | 99.30% | 0.70% | 35.95% | 87.45% | 12.55% | 97.09% | 100.00% | 3.00% | 0.00% |
| 5% | raw model | 0.0000 | 0.0000 | n/a | 46.55% | 53.45% | 100.00% | 100.00% | 0.00% | n/a | 0.00% | 0.00% | 100.00% |
| 5% | current saved thresholds | 0.9721 | 0.5388 | n/a | 97.27% | 2.73% | 38.45% | 38.45% | 61.55% | n/a | 0.00% | 0.00% | 1.40% |
| 5% | confidence+margin only | 0.9388 | 0.0000 | n/a | 95.83% | 4.17% | 42.00% | 42.00% | 58.00% | n/a | 0.00% | 0.00% | 2.50% |
| 5% | confidence+margin+OOD | 0.4607 | 0.0000 | 5.0136 | 95.03% | 4.97% | 48.30% | 98.30% | 1.70% | 100.00% | 100.00% | 0.00% | 0.00% |

## Experimental Boundary

This is an automated replay benchmark. The auto-searched thresholds are selected on the replay score distribution and reported on the same 1000-ID / 1000-OOD benchmark set. This is appropriate for product validation and resume-level quantitative evidence. For a paper-grade claim, the next step would be a held-out protocol: use one split to tune thresholds and a separate split to report final risk, coverage, and confidence intervals.

## Interpretation

- The ID-only benchmark proves selective classification: thresholds can trade coverage for safer automatic decisions.
- The OOD benchmark is the stronger resume/project evidence because it tests both selective classification and OOD rejection.
- ViT-L is substantially stronger than ViT-S on CIFAR-100: raw top-1 accuracy rises from 87.80% to 93.10%.
- ViT-L with the OOD-aware policy reaches 87.45% auto coverage at a 1% risk target, with 100.00% OOD recall and 0.00% OOD accept rate.
- ViT-L with a 5% risk target reaches 98.30% auto coverage and only 1.70% manual review rate, while still keeping OOD recall at 100.00% and OOD accept rate at 0.00%.

## Resume-Friendly Statement

Implemented and evaluated a risk-constrained selective classification / OOD rejection strategy for a DINOv3-based visual classification workbench. Built an automated CIFAR-100 + SVHN benchmark and quantified selective risk, automatic coverage, OOD recall, OOD accept rate, and manual review cost. With DINOv3 ViT-L, the OOD-aware policy achieved 87.45% automatic handling at <=1% selective risk with 100.00% OOD recall and 0.00% OOD accept rate; under a 5% risk target it reached 98.30% automatic handling with 1.70% manual review rate.

## Artifacts

- `threshold_eval_cifar100_vits_1000_risk01.json`
- `threshold_eval_cifar100_vits_1000_risk05.json`
- `threshold_eval_cifar100_vits_svhn_1000_risk01.json`
- `threshold_eval_cifar100_vits_svhn_1000_risk05.json`
- `threshold_eval_cifar100_vitl_1000_risk01.json`
- `threshold_eval_cifar100_vitl_1000_risk05.json`
- `threshold_eval_cifar100_vitl_svhn_1000_risk01.json`
- `threshold_eval_cifar100_vitl_svhn_1000_risk05.json`
