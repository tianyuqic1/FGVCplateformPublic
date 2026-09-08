package postgres_test

import (
	"context"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"os"
	"testing"
)

type cardGenerator struct {
	calls int
	fail  bool
}

func (g *cardGenerator) Generate(context.Context, datasetcard.Input) (datasetcard.Generated, error) {
	g.calls++
	if g.fail {
		return datasetcard.Generated{}, errors.New("secret provider error")
	}
	return datasetcard.Generated{Card: datasetcard.Card{Task: "image_classification", Summary: "AI draft", KnownConfusions: []string{}}}, nil
}
func TestDatasetCardRevisionDraftIdempotencyAndRecovery(t *testing.T) {
	url := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if url == "" {
		t.Skip("requires migrated PostgreSQL")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	d, v := uuid.NewString(), uuid.NewString()
	_, err = pool.Exec(ctx, `INSERT INTO datasets(id,dataset_key,name,status,created_at,updated_at) VALUES($1::uuid,$1::text,'card-test','ready',now(),now())`, d)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Exec(ctx, `DELETE FROM datasets WHERE id=$1`, d)
	_, err = pool.Exec(ctx, `INSERT INTO dataset_versions(id,dataset_id,version_key,root_uri,sample_count,class_count,split_summary,readiness_status,readiness_report,created_at) VALUES($1::uuid,$2,$1::text,'s3://test/fixture',2,2,'{"train":{"circle":1,"square":1}}','ready','{}',now())`, v, d)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Exec(ctx, `DELETE FROM dataset_versions WHERE id=$1`, v)
	defer pool.Exec(ctx, `DELETE FROM dataset_card_revisions WHERE dataset_version_id=$1`, v)
	defer pool.Exec(ctx, `DELETE FROM dataset_card_generations WHERE dataset_version_id=$1`, v)
	repo := &postgresadapter.DatasetCardRepository{Pool: pool}
	generator := &cardGenerator{}
	s := &datasetcard.Service{Repository: repo, Generator: generator}
	state, err := s.Get(ctx, v)
	if err != nil || state.Revision != 0 || len(state.Facts.Classes) != 2 {
		t.Fatalf("initial: %+v %v", state, err)
	}
	state.Card.Summary = "human approved"
	state, err = s.Save(ctx, v, 0, state.Card, "")
	if err != nil {
		t.Fatal(err)
	}
	request := uuid.NewString()
	g, err := s.Generate(ctx, v, request)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Generate(ctx, v, request)
	if err != nil || generator.calls != 1 {
		t.Fatalf("idempotency: %v %d", err, generator.calls)
	}
	reopened := &postgresadapter.DatasetCardRepository{Pool: pool}
	state, err = reopened.Get(ctx, v)
	if err != nil || state.Card.Summary != "human approved" || len(state.Generations) != 1 {
		t.Fatal("generation overwrote card or lost history", err)
	}
	_, err = s.Save(ctx, v, 0, g.Result.Card, g.ID)
	if !errors.Is(err, datasetcard.ErrConflict) {
		t.Fatal("stale save accepted", err)
	}
	state, err = s.Save(ctx, v, 1, g.Result.Card, g.ID)
	if err != nil || state.Revision != 2 || state.Generations[0].AppliedRevision == nil {
		t.Fatal("apply draft", err)
	}
	_, err = s.Save(ctx, v, 2, g.Result.Card, g.ID)
	if !errors.Is(err, datasetcard.ErrConflict) {
		t.Fatal("draft reused", err)
	}
	active := uuid.NewString()
	_, _, err = repo.Begin(ctx, v, active, "hash", 2)
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = repo.Begin(ctx, v, uuid.NewString(), "hash", 2)
	if !errors.Is(err, datasetcard.ErrConflict) {
		t.Fatal("concurrent generation accepted", err)
	}
	_, err = pool.Exec(ctx, `UPDATE dataset_card_generations SET expires_at=now()-interval '1 second' WHERE id=$1`, active)
	if err != nil {
		t.Fatal(err)
	}
	if err = repo.Finish(ctx, active, g.Result, ""); !errors.Is(err, datasetcard.ErrConflict) {
		t.Fatal("expired completion accepted", err)
	}
	generator.fail = true
	_, err = s.Generate(ctx, v, uuid.NewString())
	if !errors.Is(err, datasetcard.ErrGeneration) {
		t.Fatal("failure did not propagate", err)
	}
	state, err = s.Get(ctx, v)
	if err != nil || state.Card.Summary != "AI draft" || state.Generations[0].ErrorCode != "LLM_GENERATION_FAILED" {
		t.Fatal("failed history or approved card incorrect", err)
	}
}
