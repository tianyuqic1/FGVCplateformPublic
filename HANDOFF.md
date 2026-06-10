# FineVision Handoff

## Situation

The conversation and artifacts were initially created in the sibling `fine_agent` folder by mistake. They have been migrated into this `fineVision` folder.

## Product Direction

FineVision should become a reusable fine-grained image classification platform:

- Users can add datasets continuously.
- The system extracts frozen DINOv3-style visual features.
- Lightweight classifier heads are trained per dataset.
- Inference is scoped to a dataset/model version.
- High-confidence in-domain predictions can auto-accept.
- Low-confidence or OOD samples enter human review.
- LLM/VLM assistance is advisory only.
- Human final labels feed typed pools: training candidate, OOD/stress, bad-image, dispute, or ignore.
- Model versions require release gates beyond accuracy.

## Key Decisions

- Platform-general, dataset-scoped architecture.
- Frozen backbone plus lightweight linear/MLP heads for MVP.
- Calibrated abstention is a first-class decision.
- Feature database / nearest-neighbor evidence is core, not optional.
- LLM/VLM assists review but does not decide final labels.
- SAM3 subject masking is an extension point, not an MVP blocker.
- The frontend should be a workbench, not a marketing-style landing page.

## Created Artifacts

- Prototype: `frontend/prototypes/fine-grained-vision-platform.html`
- Prototype notes: `frontend/prototypes/NOTES.md`
- OpenSpec change: `openspec/changes/build-fine-grained-vision-platform-mvp/`
- Proposal: `openspec/changes/build-fine-grained-vision-platform-mvp/proposal.md`
- Design: `openspec/changes/build-fine-grained-vision-platform-mvp/design.md`
- Tasks: `openspec/changes/build-fine-grained-vision-platform-mvp/tasks.md`
- Specs:
  - `dataset-assets`
  - `training-evaluation`
  - `inference-abstention`
  - `human-review-feedback`
  - `model-versioning-release`
  - `operator-workbench`

## Recommended Tools To Consider

Near-term:

- FAISS for local feature nearest-neighbor lookup.
- Cleanlab for label issue and data quality detection.
- MLflow or a lightweight internal registry for experiment/model tracking.
- DVC once real image datasets and model artifacts become large.

Later:

- Label Studio for external human labeling/review workflows.
- FiftyOne for CV dataset exploration and model error analysis.
- Prefect for durable training/evaluation pipeline orchestration.
- Qdrant if vector search needs metadata filtering and service deployment.
- CVAT if SAM3/mask/bbox annotation workflows become central.
- BentoML if inference serving needs standalone packaging/deployment.

## Suggested Skills

- `openspec-apply-change` when starting implementation from the OpenSpec task list.
- `diagnose` for hard backend/ML/frontend bugs.
- `tdd` for implementing risky backend and ML behavior test-first.
- `prototype` for any further UI workflow exploration before production React work.
- `review` before merging substantial implementation changes.

## Next Suggested Implementation Order

1. Implement metadata schemas and backend service boundaries.
2. Generalize dataset assets and training/evaluation artifacts.
3. Implement dataset-scoped inference and abstention decision output.
4. Add review queue and typed feedback pools.
5. Add model registry and release gates.
6. Convert the HTML workbench prototype into React/Vite routes.

## Notes

The old `fine_agent` workspace had an unrelated modified file: `scripts/run_experiment.py`. It was not migrated or changed as part of this handoff.
