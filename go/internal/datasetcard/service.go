package datasetcard

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

const PromptVersion = "dataset-card-v2"

var ErrNotFound = errors.New("dataset version not found")
var ErrConflict = errors.New("card revision changed or generation is already running")
var ErrInvalid = errors.New("invalid dataset card")
var ErrGeneration = errors.New("LLM generation failed")

type Card struct {
	Task            string   `json:"task"`
	Domain          string   `json:"domain"`
	Summary         string   `json:"summary"`
	KnownConfusions []string `json:"known_confusions"`
	OODPolicy       string   `json:"ood_policy"`
	ReviewGuidance  string   `json:"review_guidance"`
}
type Facts struct {
	VersionID   string                    `json:"dataset_version_id"`
	DatasetName string                    `json:"dataset_name"`
	Classes     []string                  `json:"classes"`
	ClassCount  int                       `json:"class_count"`
	SampleCount int                       `json:"sample_count"`
	SplitTotals map[string]int            `json:"split_totals"`
	SplitCounts map[string]map[string]int `json:"split_counts"`
	Readiness   map[string]any            `json:"readiness"`
}
type Input struct {
	Facts    Facts `json:"facts"`
	Existing Card  `json:"existing_card"`
	Revision int   `json:"revision"`
}
type Generated struct {
	Card          Card   `json:"dataset_card"`
	Provider      string `json:"provider"`
	Model         string `json:"model"`
	PromptVersion string `json:"prompt_version"`
	InputTokens   int    `json:"input_tokens"`
	OutputTokens  int    `json:"output_tokens"`
	LatencyMS     int64  `json:"latency_ms"`
	Basis         string `json:"basis"`
}
type Generation struct {
	ID              string     `json:"id"`
	BaseRevision    int        `json:"base_revision"`
	InputSHA256     string     `json:"input_sha256"`
	Status          string     `json:"status"`
	Result          *Generated `json:"result"`
	ErrorCode       string     `json:"error_code,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	AppliedRevision *int       `json:"applied_revision"`
}
type State struct {
	Facts       Facts        `json:"facts"`
	Card        Card         `json:"dataset_card"`
	Revision    int          `json:"revision"`
	Generations []Generation `json:"generations"`
}
type Repository interface {
	Get(context.Context, string) (State, error)
	Save(context.Context, string, int, Card, string) (State, error)
	Begin(context.Context, string, string, string, int) (Generation, bool, error)
	Finish(context.Context, string, *Generated, string) error
}
type Generator interface {
	Generate(context.Context, Input) (Generated, error)
}
type Service struct {
	Repository Repository
	Generator  Generator
}

func Validate(c Card) error {
	if c.Task != "image_classification" || len(c.KnownConfusions) > 30 {
		return ErrInvalid
	}
	for _, s := range append([]string{c.Domain, c.Summary, c.OODPolicy, c.ReviewGuidance}, c.KnownConfusions...) {
		if len(s) > 6000 || strings.ContainsRune(s, 0) {
			return ErrInvalid
		}
	}
	return nil
}
func (s *Service) Get(ctx context.Context, id string) (State, error) {
	return s.Repository.Get(ctx, id)
}
func (s *Service) Save(ctx context.Context, id string, revision int, c Card, draft string) (State, error) {
	if revision < 0 || Validate(c) != nil {
		return State{}, ErrInvalid
	}
	if c.KnownConfusions == nil {
		c.KnownConfusions = []string{}
	}
	return s.Repository.Save(ctx, id, revision, c, draft)
}
func (s *Service) Generate(ctx context.Context, id, requestID string) (Generation, error) {
	state, err := s.Repository.Get(ctx, id)
	if err != nil {
		return Generation{}, err
	}
	input := Input{Facts: state.Facts, Existing: state.Card, Revision: state.Revision}
	encoded, err := json.Marshal(input)
	if err != nil {
		return Generation{}, err
	}
	if len(encoded) > 64<<10 || len(state.Facts.Classes) == 0 {
		return Generation{}, ErrInvalid
	}
	hash := sha256.Sum256(append([]byte(PromptVersion), encoded...))
	generation, created, err := s.Repository.Begin(ctx, state.Facts.VersionID, requestID, hex.EncodeToString(hash[:]), state.Revision)
	if err != nil || !created {
		return generation, err
	}
	ctx, cancel := context.WithTimeout(ctx, 100*time.Second)
	defer cancel()
	output, err := s.Generator.Generate(ctx, input)
	code := ""
	if err != nil {
		code = "LLM_GENERATION_FAILED"
	}
	// Persist the outcome even if the browser disconnected or its deadline expired.
	persist, persistCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer persistCancel()
	var result *Generated
	if err == nil {
		result = &output
	}
	if finishErr := s.Repository.Finish(persist, requestID, result, code); finishErr != nil {
		return Generation{}, finishErr
	}
	if err != nil {
		return Generation{}, ErrGeneration
	}
	generation.Status = "succeeded"
	generation.Result = &output
	return generation, nil
}
