# ML Toolkit Validation

This document records the first real-data validation of the Iteration 0.5 ML/Data Toolkit.

## Validation Run: CIFAR-10 Mini + DINOv3 ViT-L

Date: 2026-06-11

Purpose:

- Verify that the toolkit can run on a real ImageFolder dataset, not only the generated toy dataset.
- Verify that the locally cached DINOv3 ViT-L timm model can extract features.
- Verify the artifact flow:

```text
DatasetManifest -> FeatureArtifact -> ModelArtifact -> EvaluationReport -> CalibrationReport -> ThresholdStrategy -> InferenceResult
```

## Inputs

Dataset:

```text
data/test/cifar10-mini-imagefolder
```

The dataset was exported from CIFAR-10 into ImageFolder format:

```text
train: 300 images
val: 80 images
test: 80 images
classes: airplane, automobile, bird, cat, deer, dog, frog, horse, ship, truck
```

DINOv3 model:

```text
vit_large_patch16_dinov3.lvd1689m
```

Local Hugging Face cache:

```text
/home/YuQi/.cache/huggingface/hub/models--timm--vit_large_patch16_dinov3.lvd1689m
```

Cache size:

```text
1.2 GB
```

## Command

```bash
uv run --extra dinov3 --group dev python -m finevision.ml_toolkit.smoke \
  --dataset-dir data/test/cifar10-mini-imagefolder \
  --dataset-id cifar10-mini \
  --dataset-version-id dataset@cifar10-mini-001 \
  --work-dir .finevision-cifar10-dinov3 \
  --extractor dinov3_vitl \
  --device cuda \
  --batch-size 4
```

Hardware:

```text
NVIDIA GeForce RTX 4060 Laptop GPU
```

## Output Artifacts

Artifact root:

```text
.finevision-cifar10-dinov3/artifacts
```

Key artifacts:

```text
dataset_manifest.json
features/dataset@cifar10-mini-001-dinov3_vitl16/feature_artifact.json
features/dataset@cifar10-mini-001-dinov3_vitl16/features.npz
models/dataset@cifar10-mini-001-linear-head/model_artifact.json
models/dataset@cifar10-mini-001-linear-head/linear_head.npz
models/dataset@cifar10-mini-001-linear-head/training_report.json
models/dataset@cifar10-mini-001-linear-head/calibration_report.json
models/dataset@cifar10-mini-001-linear-head/threshold_sweep.json
models/dataset@cifar10-mini-001-linear-head/threshold_strategy.json
inference_result.json
smoke_summary.json
```

Feature shape:

```text
460 samples x 1024 dimensions
```

## Metrics

Linear head evaluation:

```text
accuracy: 0.9875
macro_f1: 0.987450980392157
```

The score is expected to be strong because CIFAR-10 mini is visually simple and DINOv3 ViT-L features separate the classes well.

## Calibrated Validation Run

After the first validation exposed low raw softmax confidence, the toolkit added:

- temperature scaling on the validation split
- calibration metrics: ECE, NLL, and Brier score
- confidence-threshold candidates generated from validation confidence quantiles
- a persisted `ThresholdStrategy`
- inference that consumes the persisted strategy instead of hard-coded confidence and margin defaults

Command:

```bash
uv run --extra dinov3 --group dev python -m finevision.ml_toolkit.smoke \
  --dataset-dir data/test/cifar10-mini-imagefolder \
  --dataset-id cifar10-mini \
  --dataset-version-id dataset@cifar10-mini-001 \
  --work-dir .finevision-cifar10-dinov3 \
  --extractor dinov3_vitl \
  --device cuda \
  --batch-size 4
```

Result:

```text
accuracy: 0.9875
macro_f1: 0.987450980392157
calibration_temperature: 0.055900120116560266
calibration_ece_before: 0.7681884661316871
calibration_ece_after: 0.0053471561521291735
threshold_points: 11
accept_threshold: 0.998412
margin_threshold: 0.9981164336204529
expected_coverage: 0.95
expected_selective_risk: 0.0
inference_decision: accept
inference_top1: airplane, 0.9993278980255127
```

Interpretation:

- The linear head was under-confident before calibration.
- Temperature scaling sharpened the probability distribution for this validation split.
- ECE dropped from roughly `0.768` to `0.0053`, so the calibrated confidence is much more usable for automated accept/abstain decisions.
- The selected threshold strategy expects `95%` coverage at `0%` selective risk on the validation split used by this mini dataset.
- The inference path now loads `ThresholdStrategy`, so thresholds are tied to the model artifact instead of scattered through the caller.

## Threshold Sweep

The first uncalibrated sweep exposed an important calibration issue: thresholds starting at `0.5` produced zero coverage because the ridge linear head logits were not calibrated. This historical sweep is kept here to show why calibration became part of the toolkit contract.

Final calibrated validation sweep:

| Threshold | Coverage | Selective Risk | Abstention Rate | Review Cost |
| --- | ---: | ---: | ---: | ---: |
| 0.335617 | 1.0000 | 0.0125 | 0.0000 | 0 |
| 0.998412 | 0.9500 | 0.0000 | 0.0500 | 4 |
| 0.999323 | 0.9000 | 0.0000 | 0.1000 | 8 |
| 0.999882 | 0.8500 | 0.0000 | 0.1500 | 12 |
| 0.999962 | 0.8000 | 0.0000 | 0.2000 | 16 |
| 0.999983 | 0.7500 | 0.0000 | 0.2500 | 20 |
| 0.999989 | 0.6875 | 0.0000 | 0.3125 | 25 |
| 0.999994 | 0.6375 | 0.0000 | 0.3625 | 29 |
| 0.999996 | 0.5875 | 0.0000 | 0.4125 | 33 |
| 0.999999 | 0.5250 | 0.0000 | 0.4750 | 38 |
| 1.000000 | 0.1875 | 0.0000 | 0.8125 | 65 |

Interpretation:

- DINOv3 features plus a simple linear head rank the classes well.
- The first threshold has full validation coverage but keeps the one validation error, so it misses the `1%` selective-risk target.
- The selected strategy chooses threshold `0.998412`, which is the maximum-coverage point under the target selective risk.
- Production threshold decisions should use the persisted calibrated `ThresholdStrategy`, not raw softmax confidence or a caller-provided constant.

## Inference Result

The smoke inference picked one test sample.

Calibrated top-k:

```text
airplane: 0.9993278980255127
dog: 0.0002942189166788012
truck: 0.00010253614891553298
```

Decision:

```text
decision: accept
reason: meets_acceptance_thresholds
threshold_strategy_id: dataset@cifar10-mini-001-linear-head-selective-v1
confidence threshold: 0.998412
margin threshold: 0.9981164336204529
margin: 0.9990336791088339
confidence: 0.9993278980255127
```

This decision is backed by the persisted `ThresholdStrategy`. The older uncalibrated run correctly abstained under fixed thresholds, but that was a symptom of an unusable confidence scale rather than a ranking failure.

## Findings

- The DINOv3 ViT-L timm extractor works with the current toolkit.
- The ImageFolder scanner correctly preserves explicit `train` / `val` / `test` splits.
- Feature artifacts, model artifacts, evaluation reports, calibration reports, threshold sweeps, threshold strategies, and inference results are written with stable ids.
- The toolkit can run on a real dataset with GPU acceleration.
- Calibration is now implemented as part of the toolkit decision contract.

## Follow-Up

- Keep the lightweight `color_stats` smoke test as the default test path.
- Use the DINOv3 CIFAR-10 mini run as a manual validation path because it requires heavy optional dependencies and model weights.
- Carry `CalibrationReport` and `ThresholdStrategy` into the future model-version API and registry metadata.
- Add richer calibration selection later if needed, such as per-class thresholds, pressure-set OOD thresholds, or class-pair-specific review rules.
- Consider a smaller `data/test/*` fixture generator or downloader script if future agents need to reproduce the CIFAR-10 mini export from scratch.
