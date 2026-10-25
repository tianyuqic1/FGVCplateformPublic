package postgres_test

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/testerrors"
	"os"
	"strings"
	"testing"
)

func TestTestErrorAnalysisReadsVerifiedRunScopedArtifact(t *testing.T) {
	dsn := os.Getenv("FINEVISION_TEST_GO_DATABASE_URL")
	if dsn == "" {
		t.Skip("set a disposable FINEVISION_TEST_GO_DATABASE_URL")
	}
	ctx := context.Background()
	admin, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal(e)
	}
	defer admin.Close()
	schema := "test_errors_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, e = admin.Exec(ctx, `CREATE SCHEMA `+schema); e != nil {
		t.Fatal(e)
	}
	defer admin.Exec(ctx, `DROP SCHEMA `+schema+` CASCADE`)
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		t.Fatal(e)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	_, e = pool.Exec(ctx, `CREATE TABLE dataset_versions(id uuid PRIMARY KEY,version_key text,split_summary jsonb);
 CREATE TABLE artifacts(id uuid PRIMARY KEY,uri text,checksum text,size_bytes bigint,verified_at timestamptz,training_run_id uuid,artifact_type text,created_at timestamptz DEFAULT now());
 CREATE TABLE training_runs(id uuid PRIMARY KEY,run_key text,status text,dataset_version_id uuid,report_artifact_id uuid);`)
	if e != nil {
		t.Fatal(e)
	}
	raw, e := os.ReadFile("../../testerrors/testdata/python-report.json")
	if e != nil {
		t.Fatal(e)
	}
	var doc testerrors.Document
	if e = json.Unmarshal(raw, &doc); e != nil {
		t.Fatal(e)
	}
	source := t.TempDir() + "/report.json"
	if e = os.WriteFile(source, raw, 0600); e != nil {
		t.Fatal(e)
	}
	store := artifact.NewLocalStore(t.TempDir())
	d, e := store.PutFile(ctx, source, artifact.PutRequest{ArtifactID: uuid.NewString(), ArtifactType: "report"})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = pool.Exec(ctx, `INSERT INTO dataset_versions VALUES($1,'version-key','{}');`, doc.DatasetVersionID); e != nil {
		t.Fatal(e)
	}
	if _, e = pool.Exec(ctx, `INSERT INTO artifacts(id,uri,checksum,size_bytes,verified_at,training_run_id,artifact_type) VALUES($1,$2,$3,$4,now(),$5,'report')`, d.ArtifactID, d.URI, d.SHA256, d.SizeBytes, doc.RunID); e != nil {
		t.Fatal(e)
	}
	if _, e = pool.Exec(ctx, `INSERT INTO training_runs VALUES($1,'run-key','succeeded',$2,$3)`, doc.RunID, doc.DatasetVersionID, d.ArtifactID); e != nil {
		t.Fatal(e)
	}
	reader := postgresadapter.NewTrainingTestErrors(pool, store)
	report, e := reader.Analyze(ctx, doc.RunID, 1, 1)
	if e != nil || report.Availability != "available" || report.Evidence.Samples != 3 || len(report.Evidence.Examples) != 1 {
		t.Fatalf("verified report %+v %v", report, e)
	}
	if _, e = reader.Analyze(ctx, uuid.NewString(), 1, 1); e != testerrors.ErrNotFound {
		t.Fatal("missing run did not return not-found", e)
	}
	if _, e = pool.Exec(ctx, `UPDATE training_runs SET status='running'`); e != nil {
		t.Fatal(e)
	}
	report, e = reader.Analyze(ctx, doc.RunID, 1, 1)
	if e != nil || report.Availability != "unavailable" {
		t.Fatal("unfinished report accepted", e)
	}
	_, _ = pool.Exec(ctx, `UPDATE training_runs SET status='succeeded'`)
	path, e := artifact.LocalPath(d.URI)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(path, []byte("tampered"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = reader.Analyze(ctx, doc.RunID, 1, 1); e == nil {
		t.Fatal("corrupted artifact accepted")
	}
}
