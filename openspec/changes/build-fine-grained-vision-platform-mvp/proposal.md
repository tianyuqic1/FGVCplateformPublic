## Why

FineVision needs to move from a cats-vs-dogs proof of concept into a reusable fine-grained image classification platform that can accept new datasets, train lightweight heads on top of a strong vision backbone, run inference, abstain on uncertain or out-of-domain samples, and route them through human review.

The main product value is not just higher accuracy; it is a reliable human-in-the-loop workflow where model confidence, OOD detection, LLM/VLM assistance, and human feedback form a controlled production loop.

## What Changes

- Introduce a multi-dataset asset model that tracks dataset versions, class taxonomy, sample quality, feature indexes, OOD/stress sets, and production readiness.
- Add a training workflow for frozen DINOv3-style feature extraction plus lightweight classifier heads, with evaluation, calibration, threshold scanning, and generated reports.
- Add inference behavior that returns top-k candidates, calibrated confidence, abstention decision, OOD/domain signal, and nearest-neighbor evidence.
- Add a human review workflow for low-confidence, OOD, bad-image, and disputed-class samples, with optional LLM/VLM assistance and explicit human final labels.
- Add feedback and review outcomes as first-class data that can flow into training candidate pools, OOD/stress sets, bad-image pools, or dispute pools.
- Add model version and release-gate tracking so a production model is always tied to a dataset version, feature version, classifier head, threshold strategy, metrics, and rollback candidate.
- Add a workbench-style frontend prototype and implementation target with operational dashboard, dataset details, training runs, inference lab, review queue, model registry, and pipeline run views.
- Keep SAM3-based subject masking and more advanced online abstention algorithms as extension points, not MVP blockers.

## Capabilities

### New Capabilities

- `dataset-assets`: Manage reusable classification datasets, versions, class taxonomy, sample quality status, split metadata, feature indexes, and OOD/stress assets.
- `training-evaluation`: Extract frozen vision features, train lightweight classifier heads, evaluate model candidates, calibrate confidence, and produce reports.
- `inference-abstention`: Run dataset-scoped inference with top-k candidates, calibrated confidence, OOD/domain checks, nearest-neighbor evidence, and abstention decisions.
- `human-review-feedback`: Route uncertain samples to review, show LLM/VLM assistance, capture human final labels, and store feedback outcomes for future data/version updates.
- `model-versioning-release`: Track model versions, threshold strategies, release gates, production/staging/experiment states, and rollback metadata.
- `operator-workbench`: Provide the frontend workbench for daily operation across datasets, training, inference, review, models, and pipelines.

### Modified Capabilities

- None.

## Impact

- Frontend: replace the current shallow prototype shape with a workbench-driven product surface and later map the prototype views into React/Vite routes/components.
- Backend API: expand dataset, training run, inference, review, model registry, and system readiness endpoints.
- ML modules: formalize feature extraction, classifier-head training, evaluation metrics, threshold calibration, OOD/stress evaluation, and inference decision outputs.
- Storage: add durable metadata for datasets, class taxonomy, samples, feature indexes, review items, feedback outcomes, model versions, reports, and release states.
- Runtime artifacts: continue storing images, features, reports, model weights, and indexes under runtime artifact locations with stable IDs.
- External services: integrate LLM/VLM review assistance behind an optional adapter; keep SAM3 segmentation behind a future optional adapter.
