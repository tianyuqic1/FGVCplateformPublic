package modelregistry

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type Status string

const (
	StatusCandidate  Status = "candidate"
	StatusStaging    Status = "staging"
	StatusProduction Status = "production"
	StatusArchived   Status = "archived"
	StatusFailed     Status = "failed"
)

var (
	ErrNotFound = errors.New("model version not found")
	ErrConflict = errors.New("model version conflict")
	ErrInvalid  = errors.New("invalid model registry command")
)

type Filter struct {
	DatasetID        string
	DatasetVersionID string
	Architecture     string
	Pretraining      string
	Status           Status
}

type Version struct {
	ReleaseVersion       string                `json:"release_version"`
	ReleaseSequence      int64                 `json:"release_sequence"`
	ReleaseReason        string                `json:"release_reason"`
	ReleaseSignature     string                `json:"-"`
	ReleasePrecision     string                `json:"-"`
	NextReleaseVersion   string                `json:"next_release_version,omitempty"`
	NextReleaseReason    string                `json:"next_release_reason,omitempty"`
	DatasetVersionNumber int                   `json:"dataset_version_number"`
	TrainingConfig       map[string]any        `json:"training_config"`
	ID                   string                `json:"model_version_id"`
	ModelKey             string                `json:"model_key"`
	Name                 string                `json:"name"`
	Description          string                `json:"description"`
	DatasetID            string                `json:"dataset_id"`
	DatasetName          string                `json:"dataset_name"`
	DatasetVersionID     string                `json:"dataset_version_id"`
	DatasetVersionKey    string                `json:"dataset_version_key"`
	TrainingRunID        string                `json:"training_run_id"`
	Status               Status                `json:"status"`
	Aliases              []string              `json:"aliases"`
	BackboneKey          string                `json:"backbone_key"`
	Architecture         string                `json:"architecture"`
	PretrainingMethod    string                `json:"pretraining_method"`
	PretrainingDataset   string                `json:"pretraining_dataset"`
	InputSize            *int                  `json:"input_size"`
	FeatureDim           *int                  `json:"feature_dim"`
	ParameterCount       *int64                `json:"parameter_count"`
	Pooling              string                `json:"pooling"`
	HeadType             string                `json:"head_type"`
	Metrics              map[string]any        `json:"metrics"`
	EvaluationContext    map[string]any        `json:"evaluation_context"`
	Artifacts            []artifact.Descriptor `json:"artifacts"`
	Events               []Event               `json:"events"`
	CreatedAt            time.Time             `json:"created_at"`
	UpdatedAt            time.Time             `json:"updated_at"`
}

type Warning struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type Event struct {
	ID         string         `json:"event_id"`
	EventType  string         `json:"event_type"`
	FromStatus string         `json:"from_status,omitempty"`
	ToStatus   string         `json:"to_status,omitempty"`
	Alias      string         `json:"alias,omitempty"`
	Actor      string         `json:"actor"`
	Reason     string         `json:"reason,omitempty"`
	Payload    map[string]any `json:"payload"`
	CreatedAt  time.Time      `json:"created_at"`
}

type Comparison struct {
	Versions            []Version `json:"model_versions"`
	Comparable          bool      `json:"comparable"`
	Warnings            []Warning `json:"warnings"`
	DatasetVersionID    string    `json:"dataset_version_id,omitempty"`
	ProtocolFingerprint string    `json:"protocol_fingerprint,omitempty"`
}

type AliasResult struct {
	DatasetID    string    `json:"dataset_id"`
	Alias        string    `json:"alias"`
	ModelVersion Version   `json:"model_version"`
	UpdatedBy    string    `json:"updated_by"`
	Reason       string    `json:"reason"`
	UpdatedAt    time.Time `json:"updated_at"`
}

type Repository interface {
	List(context.Context, Filter) ([]Version, error)
	Get(context.Context, string) (Version, error)
	Transition(context.Context, string, Status, Status, string, string) (Version, error)
	SetAlias(context.Context, string, string, string, string, string) (AliasResult, error)
}

type Service struct {
	repository Repository
	exporter   HeadExporter
	verifier   ArtifactVerifier
}

func NewService(repository Repository) *Service { return &Service{repository: repository} }

func (service *Service) List(ctx context.Context, filter Filter) ([]Version, error) {
	return service.repository.List(ctx, filter)
}

func (service *Service) Get(ctx context.Context, id string) (Version, error) {
	v, err := service.repository.Get(ctx, id)
	if err != nil {
		return v, err
	}
	versions, err := service.repository.List(ctx, Filter{DatasetID: v.DatasetID})
	if err != nil {
		return v, err
	}
	v.NextReleaseVersion, v.NextReleaseReason, _ = NextRelease(v, LatestRelease(versions, v.DatasetID))
	return v, nil
}

func (service *Service) Compare(ctx context.Context, ids []string) (Comparison, error) {
	if len(ids) < 2 || len(ids) > 5 {
		return Comparison{}, fmt.Errorf("%w: comparison requires 2 to 5 model versions", ErrInvalid)
	}
	seen := map[string]struct{}{}
	versions := make([]Version, 0, len(ids))
	for _, id := range ids {
		if _, exists := seen[id]; exists {
			return Comparison{}, fmt.Errorf("%w: comparison model versions must be unique", ErrInvalid)
		}
		seen[id] = struct{}{}
		version, err := service.repository.Get(ctx, id)
		if err != nil {
			return Comparison{}, err
		}
		versions = append(versions, version)
	}
	comparison := Comparison{Versions: versions, Comparable: true, Warnings: []Warning{}}
	if versions[0].DatasetID == "" || versions[0].DatasetVersionID == "" {
		return Comparison{}, fmt.Errorf("%w: 模型缺少数据集或数据版本信息，无法比较", ErrInvalid)
	}
	for _, version := range versions[1:] {
		if version.DatasetID != versions[0].DatasetID || version.DatasetVersionID != versions[0].DatasetVersionID {
			return Comparison{}, fmt.Errorf("%w: 只能比较同一数据集、同一数据版本下的模型", ErrInvalid)
		}
	}
	comparison.DatasetVersionID = versions[0].DatasetVersionID
	comparison.ProtocolFingerprint = contextString(versions[0].EvaluationContext, "protocol_fingerprint")
	if comparison.ProtocolFingerprint == "" {
		comparison.Comparable = false
		appendWarning(&comparison, "EVALUATION_PROTOCOL_MISSING", "Evaluation protocol fingerprint is required for direct comparison.")
	}
	for _, version := range versions[1:] {
		fingerprint := contextString(version.EvaluationContext, "protocol_fingerprint")
		if fingerprint == "" {
			comparison.Comparable = false
			appendWarning(&comparison, "EVALUATION_PROTOCOL_MISSING", "Evaluation protocol fingerprint is required for direct comparison.")
		} else if fingerprint != comparison.ProtocolFingerprint {
			comparison.Comparable = false
			appendWarning(&comparison, "EVALUATION_PROTOCOL_MISMATCH", "Evaluation protocol fingerprints differ or are missing.")
		}
	}
	return comparison, nil
}

func (service *Service) Promote(ctx context.Context, id string, target Status, actor, reason string) (Version, error) {
	if target == StatusProduction {
		return service.Publish(ctx, id, actor, reason)
	}
	if strings.TrimSpace(actor) == "" || strings.TrimSpace(reason) == "" {
		return Version{}, fmt.Errorf("%w: actor and reason are required", ErrInvalid)
	}
	current, err := service.repository.Get(ctx, id)
	if err != nil {
		return Version{}, err
	}
	allowed := (current.Status == StatusCandidate && target == StatusStaging) ||
		(current.Status == StatusStaging && target == StatusProduction)
	if !allowed {
		return Version{}, fmt.Errorf("%w: cannot promote %s to %s", ErrConflict, current.Status, target)
	}
	return service.repository.Transition(ctx, id, current.Status, target, actor, reason)
}

func (service *Service) Archive(ctx context.Context, id, actor, reason string) (Version, error) {
	if strings.TrimSpace(actor) == "" || strings.TrimSpace(reason) == "" {
		return Version{}, fmt.Errorf("%w: actor and reason are required", ErrInvalid)
	}
	current, err := service.repository.Get(ctx, id)
	if err != nil {
		return Version{}, err
	}
	if current.Status == StatusArchived {
		return Version{}, fmt.Errorf("%w: model version is already archived", ErrConflict)
	}
	return service.repository.Transition(ctx, id, current.Status, StatusArchived, actor, reason)
}

func (service *Service) SetAlias(ctx context.Context, datasetID, alias, versionID, actor, reason string) (AliasResult, error) {
	if alias != "champion" && alias != "challenger" {
		return AliasResult{}, fmt.Errorf("%w: alias must be champion or challenger", ErrInvalid)
	}
	if strings.TrimSpace(datasetID) == "" || strings.TrimSpace(actor) == "" || strings.TrimSpace(reason) == "" {
		return AliasResult{}, fmt.Errorf("%w: dataset, actor, and reason are required", ErrInvalid)
	}
	version, err := service.repository.Get(ctx, versionID)
	if err != nil {
		return AliasResult{}, err
	}
	if alias == "champion" && version.Status != StatusProduction {
		return AliasResult{}, fmt.Errorf("%w: champion must reference a production Model Version", ErrConflict)
	}
	if alias == "challenger" && version.Status != StatusCandidate && version.Status != StatusStaging {
		return AliasResult{}, fmt.Errorf("%w: challenger must reference a candidate or staging Model Version", ErrConflict)
	}
	return service.repository.SetAlias(ctx, datasetID, alias, versionID, actor, reason)
}

func appendWarning(comparison *Comparison, code, message string) {
	for _, warning := range comparison.Warnings {
		if warning.Code == code {
			return
		}
	}
	comparison.Warnings = append(comparison.Warnings, Warning{Code: code, Message: message})
}

func contextString(context map[string]any, key string) string {
	value, _ := context[key].(string)
	return value
}

type MemoryRepository struct {
	mu       sync.Mutex
	versions map[string]Version
	aliases  map[string]string
	clock    func() time.Time
}

func NewMemoryRepository(versions ...Version) *MemoryRepository {
	items := make(map[string]Version, len(versions))
	for _, version := range versions {
		items[version.ID] = cloneVersion(version)
	}
	return &MemoryRepository{versions: items, aliases: map[string]string{}, clock: time.Now}
}

func (repository *MemoryRepository) List(_ context.Context, filter Filter) ([]Version, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	result := make([]Version, 0)
	for _, version := range repository.versions {
		if filter.DatasetID != "" && version.DatasetID != filter.DatasetID ||
			filter.DatasetVersionID != "" && version.DatasetVersionID != filter.DatasetVersionID ||
			filter.Architecture != "" && version.Architecture != filter.Architecture ||
			filter.Pretraining != "" && version.PretrainingMethod != filter.Pretraining ||
			filter.Status != "" && version.Status != filter.Status {
			continue
		}
		result = append(result, cloneVersion(version))
	}
	sort.Slice(result, func(i, j int) bool { return result[i].CreatedAt.After(result[j].CreatedAt) })
	return result, nil
}

func (repository *MemoryRepository) Get(_ context.Context, id string) (Version, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	version, exists := repository.versions[id]
	if !exists {
		return Version{}, ErrNotFound
	}
	return cloneVersion(version), nil
}

func (repository *MemoryRepository) Transition(_ context.Context, id string, from, target Status, _, _ string) (Version, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	version, exists := repository.versions[id]
	if !exists {
		return Version{}, ErrNotFound
	}
	if version.Status != from {
		return Version{}, ErrConflict
	}
	version.Status = target
	version.UpdatedAt = repository.clock().UTC()
	repository.versions[id] = version
	return cloneVersion(version), nil
}

func (repository *MemoryRepository) SetAlias(_ context.Context, datasetID, alias, versionID, actor, reason string) (AliasResult, error) {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	version, exists := repository.versions[versionID]
	if !exists {
		return AliasResult{}, ErrNotFound
	}
	if version.DatasetID != datasetID {
		return AliasResult{}, ErrConflict
	}
	key := datasetID + ":" + alias
	if previousID := repository.aliases[key]; previousID != "" && previousID != versionID {
		previous := repository.versions[previousID]
		previous.Aliases = removeValue(previous.Aliases, alias)
		repository.versions[previousID] = previous
	}
	repository.aliases[key] = versionID
	version.Aliases = appendUnique(version.Aliases, alias)
	now := repository.clock().UTC()
	version.Events = append(version.Events, Event{
		ID: "memory-" + alias, EventType: "alias_set", Alias: alias, Actor: actor,
		Reason: reason, Payload: map[string]any{"dataset_id": datasetID}, CreatedAt: now,
	})
	repository.versions[versionID] = version
	return AliasResult{DatasetID: datasetID, Alias: alias, ModelVersion: cloneVersion(version), UpdatedBy: actor, Reason: reason, UpdatedAt: now}, nil
}

func cloneVersion(source Version) Version {
	result := source
	result.Aliases = append([]string(nil), source.Aliases...)
	result.Metrics = cloneMap(source.Metrics)
	result.EvaluationContext = cloneMap(source.EvaluationContext)
	result.Artifacts = append([]artifact.Descriptor(nil), source.Artifacts...)
	result.Events = make([]Event, len(source.Events))
	for index, event := range source.Events {
		result.Events[index] = event
		result.Events[index].Payload = cloneMap(event.Payload)
	}
	return result
}

func cloneMap(source map[string]any) map[string]any {
	if source == nil {
		return map[string]any{}
	}
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func appendUnique(values []string, value string) []string {
	for _, current := range values {
		if current == value {
			return values
		}
	}
	return append(values, value)
}

func removeValue(values []string, value string) []string {
	result := values[:0]
	for _, current := range values {
		if current != value {
			result = append(result, current)
		}
	}
	return result
}
