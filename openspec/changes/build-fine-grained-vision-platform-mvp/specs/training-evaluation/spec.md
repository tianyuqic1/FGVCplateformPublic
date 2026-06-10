## ADDED Requirements

### Requirement: Frozen feature extraction
The system SHALL extract frozen vision-backbone features for a dataset version and persist them as a reusable artifact before classifier-head training.

#### Scenario: Reuse feature artifact
- **WHEN** a user trains a new classifier head on an unchanged dataset version and backbone
- **THEN** the system reuses the existing feature artifact instead of recomputing all features

### Requirement: Lightweight classifier-head training
The system SHALL train lightweight classifier heads, including at least a linear head, using dataset-version features and configured train/val/test splits.

#### Scenario: Train linear head
- **WHEN** a user starts a linear-head training run for a ready dataset version
- **THEN** the system creates a training run, trains the head, and stores the model artifact and run metadata

### Requirement: Evaluation report
The system SHALL generate an evaluation report for each completed training run with overall accuracy, macro F1, top-k/candidate recall, per-class metrics, confusion information, and run configuration.

#### Scenario: Completed run report
- **WHEN** a training run completes successfully
- **THEN** the system stores an evaluation report linked to the training run and candidate model version

### Requirement: Confidence calibration and threshold sweep
The system SHALL support calibration and threshold scanning that reports coverage, selective accuracy/risk, abstention rate, and estimated review cost.

#### Scenario: Select threshold strategy
- **WHEN** an algorithm engineer reviews a threshold sweep
- **THEN** the system shows candidate threshold strategies with their coverage/risk trade-offs

### Requirement: Training readiness diagnostics
The system SHALL return diagnostics instead of silently training when dataset quality, class sample count, split coverage, or feature extraction readiness is insufficient.

#### Scenario: Class has too few samples
- **WHEN** a dataset version contains classes with too few samples for train/val/test splitting
- **THEN** the system marks the dataset as not ready and reports the affected classes
