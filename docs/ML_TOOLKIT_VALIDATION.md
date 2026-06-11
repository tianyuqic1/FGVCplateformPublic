# ML Toolkit Validation

This document records the first real-data validation of the Iteration 0.5 ML/Data Toolkit.

## Validation Run: CIFAR-10 Mini + DINOv3 ViT-L

Date: 2026-06-11

Purpose:

- Verify that the toolkit can run on a real ImageFolder dataset, not only the generated toy dataset.
- Verify that the locally cached DINOv3 ViT-L timm model can extract features.
- Verify the artifact flow:

```text
DatasetManifest -> FeatureArtifact -> ModelArtifact -> EvaluationReport -> ThresholdSweep -> InferenceResult
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
models/dataset@cifar10-mini-001-linear-head/threshold_sweep.json
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

## Threshold Sweep

The first sweep exposed an important calibration issue: thresholds starting at `0.5` produced zero coverage because the ridge linear head logits are not calibrated. The default sweep was widened to include lower confidence thresholds.

Final sweep:

| Threshold | Coverage | Selective Risk | Abstention Rate | Review Cost |
| --- | ---: | ---: | ---: | ---: |
| 0.10 | 1.0000 | 0.0043 | 0.0000 | 0 |
| 0.15 | 0.9870 | 0.0000 | 0.0130 | 6 |
| 0.20 | 0.9000 | 0.0000 | 0.1000 | 46 |
| 0.30 | 0.0130 | 0.0000 | 0.9870 | 454 |
| 0.50 | 0.0000 | 0.0000 | 1.0000 | 460 |
| 0.70 | 0.0000 | 0.0000 | 1.0000 | 460 |
| 0.90 | 0.0000 | 0.0000 | 1.0000 | 460 |

Interpretation:

- DINOv3 features plus a simple linear head rank the classes well.
- Raw softmax confidence from the current ridge head is not calibrated.
- Iteration 2 should add explicit calibration, such as temperature scaling or validation-set calibration, before production threshold decisions.

## Inference Result

The smoke inference picked one test sample.

Top-k:

```text
airplane: 0.1636459231376648
dog: 0.10387708991765976
truck: 0.09793300181627274
```

Decision:

```text
decision: abstain
reason: confidence_below_threshold
confidence threshold: 0.55
margin threshold: 0.05
margin: 0.059768833220005035
confidence: 0.1636459231376648
```

The abstention is correct under the current uncalibrated threshold settings. It does not mean the model ranked the wrong class; it means the confidence scale is not yet calibrated.

## Findings

- The DINOv3 ViT-L timm extractor works with the current toolkit.
- The ImageFolder scanner correctly preserves explicit `train` / `val` / `test` splits.
- Feature artifacts, model artifacts, evaluation reports, threshold sweeps, and inference results are written with stable ids.
- The toolkit can run on a real dataset with GPU acceleration.
- Calibration is now a real requirement, not just a future nice-to-have.

## Follow-Up

- Keep the lightweight `color_stats` smoke test as the default test path.
- Use the DINOv3 CIFAR-10 mini run as a manual validation path because it requires heavy optional dependencies and model weights.
- Add calibration in the training/evaluation service phase before treating confidence thresholds as production-ready.
- Consider a smaller `data/test/*` fixture generator or downloader script if future agents need to reproduce the CIFAR-10 mini export from scratch.
