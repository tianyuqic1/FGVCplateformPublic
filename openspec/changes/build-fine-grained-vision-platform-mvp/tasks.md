## 1. Data Model And Metadata

- [ ] 1.1 Define dataset version metadata schema for dataset id, version id, taxonomy, splits, sample counts, readiness, and artifact references
- [ ] 1.2 Define class taxonomy schema for aliases, disabled classes, parent/child relationships, confusing-class notes, and reviewer-facing definitions
- [ ] 1.3 Define sample quality and feedback outcome enums for accepted, suspected mislabeled, bad image, OOD candidate, duplicate, disputed class, and ignored
- [ ] 1.4 Define feature index metadata schema for backbone, feature dimension, extraction config, index artifact, and dataset version binding
- [ ] 1.5 Define model version metadata schema for dataset version, feature artifact, backbone, classifier head, threshold strategy, report, lifecycle state, and rollback metadata

## 2. Dataset Asset APIs

- [ ] 2.1 Add dataset list API returning version, readiness, counts, model binding, and quality summary
- [ ] 2.2 Add dataset detail API returning overview, taxonomy, split metadata, sample quality summary, feature index metadata, and OOD/stress configuration
- [ ] 2.3 Add dataset import/readiness endpoint or service function for ImageFolder-style datasets with stratified split diagnostics
- [ ] 2.4 Add class taxonomy update endpoint for aliases, confusing-class notes, and reviewer-facing class definitions
- [ ] 2.5 Add tests for dataset version creation, split preservation, low-sample diagnostics, and taxonomy updates

## 3. Training And Evaluation

- [ ] 3.1 Generalize feature extraction to write reusable dataset-version feature artifacts with metadata
- [ ] 3.2 Generalize classifier-head training to accept dataset version and feature artifact inputs
- [ ] 3.3 Extend evaluation reports with macro F1, candidate recall@k, per-class metrics, confusion data, and run configuration
- [ ] 3.4 Add calibration and threshold sweep output for coverage, selective risk, abstention rate, and estimated review cost
- [ ] 3.5 Add training run APIs for queue list, run detail, progress, report links, and candidate model version creation
- [ ] 3.6 Add tests for feature reuse, training run metadata, evaluation report content, and threshold sweep outputs

## 4. Inference And Abstention

- [ ] 4.1 Update inference service contract to require dataset version and model version scope
- [ ] 4.2 Return top-k candidates capped by dataset class count with calibrated scores
- [ ] 4.3 Add abstention decision object with decision, reasons, thresholds, margin, confidence, and OOD/domain signal
- [ ] 4.4 Add nearest-neighbor evidence lookup from the dataset feature index metadata
- [ ] 4.5 Create review items automatically for `abstain` and `reject_ood` decisions when routing is enabled
- [ ] 4.6 Add tests for accept, abstain, low-margin abstain, OOD reject, and review item creation

## 5. Human Review And Feedback

- [ ] 5.1 Extend review queue storage with dataset id, sample id, priority, reason, model context, nearest neighbors, and assistance metadata
- [ ] 5.2 Add optional LLM/VLM assistance adapter interface and store advisory output separately from final labels
- [ ] 5.3 Add review detail API returning image context, candidates, nearest-neighbor evidence, LLM/VLM assistance, and audit history
- [ ] 5.4 Add review completion API requiring human final outcome, feedback destination, reviewer note, and completion metadata
- [ ] 5.5 Route completed review outcomes into training candidate, OOD/stress, bad-image, dispute, or ignore pools
- [ ] 5.6 Add tests for queue ordering, review completion, typed feedback routing, and audit trail retention

## 6. Model Registry And Release Gates

- [ ] 6.1 Add model registry API for production, staging, experiment, archived, and failed model versions
- [ ] 6.2 Add model detail API with metadata, metrics, threshold strategy, artifacts, and comparison against production
- [ ] 6.3 Implement release gate evaluation for metrics, calibration, OOD/stress performance, review pressure, and rollback availability
- [ ] 6.4 Add promotion and rollback service functions with lifecycle state transitions
- [ ] 6.5 Add tests for model registration, gate failure, production promotion, and rollback metadata

## 7. Operator Workbench Frontend

- [ ] 7.1 Convert the HTML prototype direction into React/Vite route structure for dashboard, datasets, training, inference, review, models, and pipelines
- [ ] 7.2 Implement dashboard with priority tasks, review backlog, training status, production coverage, OOD alerts, dataset status, and release gates
- [ ] 7.3 Implement dataset list and dataset detail pages with overview, class governance, samples, feature index, and OOD/abstention sections
- [ ] 7.4 Implement training queue and training detail pages with progress, configuration, metrics, reports, and logs
- [ ] 7.5 Implement inference lab with dataset/model selection, top-k candidates, abstention decision, OOD reason, and nearest-neighbor evidence
- [ ] 7.6 Implement review queue and review detail workflow with image comparison, LLM/VLM assistance, final label, feedback destination, and submit action
- [ ] 7.7 Implement model registry and model detail pages with lifecycle state, release gates, metrics, promotion, and rollback actions
- [ ] 7.8 Implement pipeline template and run detail pages with progress motion, logs, artifacts, and retry/pause affordances

## 8. Validation And Documentation

- [ ] 8.1 Update README with the new workbench routes, backend endpoints, and MVP workflow
- [ ] 8.2 Add or update architecture documentation to reflect dataset-scoped inference, review feedback pools, and release gates
- [ ] 8.3 Add backend API tests covering the main dataset → training → inference → review → feedback loop
- [ ] 8.4 Add frontend build verification and smoke interaction checks for key workbench routes
- [ ] 8.5 Run `uv run pytest` and `cd frontend && npm run build`
