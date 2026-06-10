## ADDED Requirements

### Requirement: Dataset version asset
The system SHALL represent each classification dataset as a versioned asset with a stable dataset identifier, version identifier, class taxonomy, split metadata, sample counts, and readiness status.

#### Scenario: Create dataset version from class folders
- **WHEN** a user imports an ImageFolder-style dataset without explicit train/val/test splits
- **THEN** the system creates a dataset version with stratified split metadata and per-class sample counts

#### Scenario: Preserve provided splits
- **WHEN** a user imports a dataset with train/val/test directories
- **THEN** the system preserves the provided splits and records them in the dataset version metadata

### Requirement: Class taxonomy governance
The system SHALL store class names, aliases, disabled classes, parent/child relationships, confusing-class notes, and reviewer-facing class definitions for each dataset version.

#### Scenario: Add confusing-class guidance
- **WHEN** an algorithm engineer adds a note that class A is frequently confused with class B
- **THEN** the dataset detail view exposes that guidance for training diagnostics, LLM assistance, and human review

### Requirement: Sample quality states
The system SHALL track sample-level quality states including accepted, suspected mislabeled, bad image, OOD candidate, duplicate, and disputed class.

#### Scenario: Bad image excluded from training
- **WHEN** a reviewer marks a sample as bad image
- **THEN** the system stores the outcome in a bad-image pool and does not include it in the default training candidate pool

### Requirement: Feature index asset
The system SHALL bind a feature index to a dataset version, backbone identifier, feature dimension, extraction configuration, and index artifact location.

#### Scenario: Feature index lookup
- **WHEN** inference or review requests nearest-neighbor evidence for a sample
- **THEN** the system uses the feature index associated with the selected dataset version and backbone

### Requirement: OOD and stress assets
The system SHALL allow each dataset to reference OOD or stress evaluation assets used for threshold selection and release checks.

#### Scenario: Pressure set configured
- **WHEN** a dataset has a configured pressure set
- **THEN** evaluation reports include OOD/stress metrics for that pressure set
