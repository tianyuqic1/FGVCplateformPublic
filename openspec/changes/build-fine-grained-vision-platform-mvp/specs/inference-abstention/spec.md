## ADDED Requirements

### Requirement: Dataset-scoped inference
The system SHALL require inference to be scoped to a dataset version and model version so predictions use the correct taxonomy, thresholds, feature index, and OOD strategy.

#### Scenario: Run scoped inference
- **WHEN** a user submits an image for inference against a selected dataset and model version
- **THEN** the system returns predictions only from that dataset taxonomy

### Requirement: Top-k candidate output
The system SHALL return top-k candidates with labels, calibrated probabilities or scores, and the effective candidate count capped by the number of dataset classes.

#### Scenario: Two-class dataset candidate count
- **WHEN** inference runs on a two-class dataset
- **THEN** the system returns no more than two class candidates

### Requirement: Abstention decision
The system SHALL return a decision of `accept`, `abstain`, or `reject_ood` based on calibrated confidence, top1/top2 margin, dataset thresholds, and domain/OOD checks.

#### Scenario: Low margin abstains
- **WHEN** top-1 confidence exceeds the minimum threshold but the top1/top2 margin is below the configured margin threshold
- **THEN** the system returns `abstain` and includes the margin reason

#### Scenario: OOD rejection
- **WHEN** the sample is outside the dataset feature distribution according to the configured OOD strategy
- **THEN** the system returns `reject_ood` and does not produce an automatic final class

### Requirement: Nearest-neighbor evidence
The system SHALL include nearest-neighbor evidence when available, including neighbor sample identifiers, labels, split/source, and distances.

#### Scenario: Review evidence generated
- **WHEN** an inference result is sent to review
- **THEN** the review item includes nearest-neighbor evidence from the dataset feature index

### Requirement: Automatic review item creation
The system SHALL create a review item for inference results that abstain or reject as OOD when review routing is enabled.

#### Scenario: Low-confidence sample enters review
- **WHEN** inference returns `abstain`
- **THEN** the system creates a pending review item with prediction context and abstention reason
