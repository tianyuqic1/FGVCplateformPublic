## ADDED Requirements

### Requirement: Model version registry
The system SHALL register each model version with dataset version, feature artifact, backbone, classifier head, threshold strategy, evaluation report, artifact location, and lifecycle status.

#### Scenario: Register candidate model
- **WHEN** a training run produces a model artifact and evaluation report
- **THEN** the system creates or updates a candidate model version in the registry

### Requirement: Lifecycle states
The system SHALL support model lifecycle states including experiment, staging, production, archived, and failed.

#### Scenario: Promote staging to production
- **WHEN** an authorized operator promotes a staging model that passes release gates
- **THEN** the system marks it production and archives or supersedes the previous production model according to rollback policy

### Requirement: Release gates
The system SHALL evaluate release gates for offline metrics, calibration, coverage/risk, OOD/stress performance, review pressure, and rollback availability before production promotion.

#### Scenario: Block production without review gate
- **WHEN** a candidate model has not completed required human spot checks
- **THEN** the system prevents production promotion and reports the missing gate

### Requirement: Rollback metadata
The system SHALL retain the previous production model and threshold strategy as rollback metadata when a new model is promoted.

#### Scenario: Roll back production model
- **WHEN** an operator requests rollback after a production regression
- **THEN** the system can restore the previous production model version and threshold strategy

### Requirement: Version comparison
The system SHALL compare model versions by metrics, coverage/risk, review cost estimate, OOD/stress results, dataset version, and changed configuration.

#### Scenario: Compare candidate with production
- **WHEN** an operator opens a staging model version
- **THEN** the system shows differences from the current production version
