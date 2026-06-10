## ADDED Requirements

### Requirement: Operational dashboard
The frontend SHALL provide a workbench dashboard showing priority tasks, review backlog, training status, production coverage, OOD alerts, dataset status, and release gates.

#### Scenario: Operator opens dashboard
- **WHEN** an operator opens the application
- **THEN** the first screen shows actionable operational status rather than a marketing-style hero page

### Requirement: Dataset detail views
The frontend SHALL provide dataset detail views with tabs or equivalent sections for overview, class governance, samples, feature index, and OOD/abstention strategy.

#### Scenario: Inspect dataset taxonomy
- **WHEN** a user opens the class governance section
- **THEN** the UI shows class health, confusing classes, long-tail classes, and reviewer-facing class definitions

### Requirement: Training and pipeline views
The frontend SHALL provide training queue, training detail, pipeline template, and pipeline run views with progress, reports, artifacts, logs, and failure recovery affordances.

#### Scenario: Open running training task
- **WHEN** a user clicks a running training task
- **THEN** the UI opens a detail view with progress, run configuration, metrics, and log/report placeholders

### Requirement: Inference lab
The frontend SHALL provide an inference lab for single-image testing with selected dataset/model, top-k output, abstention decision, OOD reason, nearest-neighbor evidence, and SAM3 extension controls.

#### Scenario: Low-confidence inference displayed
- **WHEN** inference returns an abstention result
- **THEN** the UI shows the abstention reason, review routing state, top-k candidates, and nearest-neighbor evidence

### Requirement: Review detail workflow
The frontend SHALL provide a review detail workflow with image comparison, model candidates, LLM/VLM assistance, human final label, feedback destination, and submit action.

#### Scenario: Submit review outcome
- **WHEN** a reviewer selects a final label and feedback destination
- **THEN** the UI submits the review outcome and confirms that it entered the selected feedback pool

### Requirement: Model registry views
The frontend SHALL provide model registry and model detail views with lifecycle status, version metadata, release gates, metrics, and rollback or promotion actions.

#### Scenario: Inspect release gate failure
- **WHEN** a model candidate fails a release gate
- **THEN** the UI shows which gate failed and what action is needed

### Requirement: Prototype interaction depth
The frontend prototype SHALL include clickable subpages, hover states, page transitions, progress motion, pipeline motion, and toast feedback sufficient to validate product flow before implementation.

#### Scenario: Navigate prototype subpage
- **WHEN** a user clicks a dataset row, training run, review card, model card, or pipeline node in the prototype
- **THEN** the prototype navigates to a corresponding detail page with realistic layout and state
