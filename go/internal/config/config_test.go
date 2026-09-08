package config_test

import (
	"testing"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/config"
)

func TestControlPlaneConfigRequiresPostgres(t *testing.T) {
	t.Setenv("FINEVISION_DATABASE_URL", "")
	t.Setenv("FINEVISION_LLM_INTERNAL_TOKEN", "test-token")
	setArtifactStoreEnvironment(t)
	if _, err := config.LoadControlPlane(); err == nil {
		t.Fatal("expected missing database configuration to fail")
	}
}

func TestControlPlaneConfigAppliesSafeDefaults(t *testing.T) {
	t.Setenv("FINEVISION_DATABASE_URL", "postgres://finevision:test@postgres/finevision")
	t.Setenv("FINEVISION_LLM_INTERNAL_TOKEN", "test-token")
	setArtifactStoreEnvironment(t)
	t.Setenv("FINEVISION_HTTP_ADDRESS", "")
	configured, err := config.LoadControlPlane()
	if err != nil {
		t.Fatal(err)
	}
	if configured.HTTPAddress != ":8000" || configured.LeaseTTL.Seconds() != 150 {
		t.Fatalf("config = %#v", configured)
	}
}

func setArtifactStoreEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("FINEVISION_S3_ENDPOINT", "http://object-store:9000")
	t.Setenv("FINEVISION_S3_ACCESS_KEY", "access")
	t.Setenv("FINEVISION_S3_SECRET_KEY", "secret")
}
