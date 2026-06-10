## Context

FineVision already has an MVP skeleton: ML scripts for DINOv3/timm feature extraction, lightweight classifier-head training, metrics, fixed-threshold abstention, a FastAPI backend, a React/Vite frontend, and exploratory documentation for a human-in-the-loop vision classification platform.

The next step is to turn the proof of concept into a reusable platform for fine-grained image classification. The system must support multiple datasets and model versions, keep model decisions tied to dataset scope, route uncertain samples into review, and capture human feedback for future dataset versions and retraining.

Primary stakeholders:

- Algorithm engineers who create datasets, train heads, inspect metrics, and publish model versions.
- Reviewers who resolve low-confidence, OOD, bad-image, and disputed-class samples.
- Product/operator users who need a daily workbench showing risks, queues, training status, and release readiness.

## Goals / Non-Goals

**Goals:**

- Support a full MVP loop: dataset asset → feature extraction → classifier-head training → evaluation/calibration → inference → abstention → human review → feedback storage.
- Treat each dataset as a scoped classification task with its own taxonomy, versions, feature index, model versions, thresholds, and OOD/stress assets.
- Make abstention a first-class decision, not an error path.
- Use LLM/VLM assistance only as a review aid; human final labels remain the ground truth for feedback.
- Provide a workbench-style frontend that is useful for daily operation, not a marketing-style landing page.
- Preserve extension points for SAM3 subject masking and more advanced online abstention without making them MVP blockers.

**Non-Goals:**

- Full fine-tuning of the vision backbone.
- Real-time online model updates.
- Replacing human review with automatic LLM decisions.
- Multi-tenant permissioning and enterprise RBAC.
- Distributed training infrastructure.
- Production SAM3 segmentation integration in the first MVP.

## Decisions

### 1. Platform-General, Dataset-Scoped

The platform will be generic, but every inference request will be scoped to a dataset and model version. Dataset-specific taxonomy, thresholds, feature statistics, and OOD gates must be used together.

Alternatives considered:

- One global classifier for every dataset: simpler but unreliable for unrelated datasets and OOD handling.
- Separate bespoke apps per dataset: safer initially but prevents reusable workflows and model/version governance.

### 2. Frozen Backbone + Lightweight Heads

The MVP will use frozen DINOv3-style features and train lightweight linear/MLP classifier heads. Feature extraction output becomes a reusable artifact tied to a dataset version.

Alternatives considered:

- Full backbone fine-tuning: higher ceiling but more expensive, harder to version, and slower for new datasets.
- Zero-shot VLM-only classification: faster to demo but less reliable for fine-grained, dataset-specific class boundaries.

### 3. Calibrated Abstention Before Review

Inference will produce top-k candidates, calibrated confidence, top1/top2 margin, embedding/domain distance, and a decision: `accept`, `abstain`, or `reject_ood`.

Alternatives considered:

- Raw softmax threshold only: easy but often overconfident and poorly calibrated.
- Review every sample: reliable but too expensive and defeats automation.

### 4. LLM/VLM as Review Assistant

LLM/VLM output will be stored as assistance metadata with candidate reasoning and suggested inspection points. It must not write final labels directly.

Alternatives considered:

- Let LLM decide final class for low-confidence samples: cheaper than human review but risky and hard to audit.
- No LLM assistance: simpler but loses value in review efficiency and class-difference explanation.

### 5. Feedback Outcomes Are Typed

Review output will not be a single label-only field. Outcomes must distinguish confirmed class, corrected class, OOD, bad image, and class dispute. Each outcome maps to a different feedback pool.

Alternatives considered:

- Put every reviewed item into the training set: risks polluting training data with bad images, OOD samples, or unresolved disputes.
- Keep review data separate forever: safe but blocks continuous improvement.

### 6. Release Gate Over Accuracy-Only Publishing

Model versions must pass release gates including offline metrics, calibration, OOD/stress results, review pressure, and rollback availability. Accuracy alone is not enough.

Alternatives considered:

- Publish the best accuracy model automatically: fast but unsafe when coverage, OOD, or review cost regress.
- Manual file-based model selection: flexible but hard to audit and reproduce.

### 7. Workbench Frontend

The frontend target is a compact operational workbench: left navigation, sticky context bar, dashboard cards, clickable detail pages, review forms, and pipeline status. The HTML prototype under `frontend/prototypes/fine-grained-vision-platform.html` is the visual/product direction for implementation.

Alternatives considered:

- Website-style hero page: visually appealing but inefficient for daily operation.
- Extremely dense admin table UI: efficient but poor for onboarding and cross-functional review.

## Risks / Trade-offs

- Dataset taxonomy quality can dominate model quality → Add category governance, disputed-class pools, and class-level diagnostics early.
- Raw confidence can be misleading → Require calibration and threshold sweep reports before production release.
- OOD detection is approximate → Combine calibrated confidence, embedding distance, pressure sets, and human feedback instead of relying on one signal.
- LLM/VLM review assistance can hallucinate → Store it as advisory metadata only and require human final labels.
- SAM3 masking can improve or harm fine-grained accuracy → Keep original image inference as baseline and evaluate crop/mask variants before enabling per dataset.
- Feedback loops can accumulate noisy labels → Type review outcomes and keep bad images/OOD/disputes out of training pools by default.
- A deep workbench can over-scope MVP → Implement the workbench incrementally, starting with dashboard, dataset detail, inference lab, and review queue.

## Migration Plan

1. Keep the existing cats-vs-dogs flow as a smoke dataset while adding generalized dataset/model/review metadata.
2. Introduce dataset-version and model-version identifiers across API responses before adding new UI screens.
3. Add backend endpoints for dataset details, training runs, inference decisions, review items, feedback outcomes, and model registry data.
4. Implement workbench React routes based on the HTML prototype, initially backed by mock/fallback data where backend endpoints are not ready.
5. Wire the routes to backend APIs progressively and keep the existing demo pipeline working.
6. Add release gates and typed feedback pools before allowing a model to be marked production.

Rollback strategy:

- Keep existing scripts and current frontend route available until the workbench routes are functionally equivalent.
- Treat new metadata tables/JSON artifacts as additive.
- Do not delete existing artifacts or model reports during migration.

## Open Questions

- Which database should be used for the first durable MVP: SQLite with migration discipline, or PostgreSQL from the start?
- Which LLM/VLM provider and prompt contract should be used for review assistance?
- Should feature indexes use FAISS/HNSW immediately, or start with numpy/sklearn nearest neighbors until dataset sizes require an index service?
- What production acceptance thresholds should be dataset-configurable versus globally defaulted?
- What is the first non-cats-dogs real fine-grained dataset to validate the platform workflow?
