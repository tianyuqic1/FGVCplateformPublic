## ADDED Requirements

### Requirement: Review queue
The system SHALL maintain a review queue for low-confidence, OOD, bad-image, and disputed-class samples with priority, status, reason, model context, and assigned dataset.

#### Scenario: Queue lists pending high-risk items
- **WHEN** a reviewer opens the review queue
- **THEN** the system shows pending items ordered by risk priority and includes why each item needs review

### Requirement: LLM/VLM assistance metadata
The system SHALL store LLM/VLM assistance as advisory metadata including candidate likelihoods, visual inspection notes, class-difference reasoning, and prompt/model metadata.

#### Scenario: LLM suggestion visible but non-final
- **WHEN** a reviewer opens a review item with LLM assistance
- **THEN** the system displays the suggestion as assistance and clearly separates it from the human final label

### Requirement: Human final label by default
The system SHALL require a human final outcome before a review item can be completed unless an
explicitly enabled VLM auto-review task passes the persisted deterministic safety gate. Auto review
SHALL remain disabled by default.

#### Scenario: Complete corrected label review
- **WHEN** a reviewer chooses a corrected class and submits the review
- **THEN** the system stores the human final label, reviewer note, completion timestamp, and outcome type

#### Scenario: Disabled VLM auto review cannot complete an item
- **WHEN** auto review has not passed target-dataset shadow enablement
- **THEN** the system stores VLM output as advisory metadata and leaves the review item pending

#### Scenario: Gated VLM auto review is source-labelled
- **WHEN** an explicitly enabled auto task processes an abstain item and every deterministic gate passes
- **THEN** the system may complete the item and stores `vlm_auto` source, run, result, model, prompt, and gate metadata

#### Scenario: OOD never auto-completes
- **WHEN** the original decision is `reject_ood`
- **THEN** the system requires a human final outcome regardless of VLM output

### Requirement: Typed feedback pools
The system SHALL route completed review outcomes into typed pools: training candidate, OOD/stress, bad image, dispute, or ignore.

#### Scenario: OOD outcome enters pressure pool
- **WHEN** a reviewer marks an item as OOD
- **THEN** the system routes the item to the OOD/stress pool and excludes it from default class training data

### Requirement: Review audit trail
The system SHALL retain model prediction context, LLM/VLM assistance, final decision source, and
feedback routing history for each review item.

#### Scenario: Audit completed review
- **WHEN** an operator inspects a completed review item
- **THEN** the system shows the original model result, assistance metadata, human final outcome, and feedback pool destination
