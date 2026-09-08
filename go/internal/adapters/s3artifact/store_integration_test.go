package s3artifact_test

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	awss3 "github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/s3artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

func TestMinIOPutAndMaterializeVerifiesBytes(t *testing.T) {
	endpoint := os.Getenv("FINEVISION_TEST_S3_ENDPOINT")
	if endpoint == "" {
		t.Skip("set FINEVISION_TEST_S3_ENDPOINT to run the MinIO integration test")
	}
	ctx := context.Background()
	configuration, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion("us-east-1"),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider("finevision", "finevision-dev-object-secret", "")),
	)
	if err != nil {
		t.Fatal(err)
	}
	client := awss3.NewFromConfig(configuration, func(options *awss3.Options) {
		options.BaseEndpoint = aws.String(endpoint)
		options.UsePathStyle = true
	})
	store := s3artifact.New(client, "finevision-artifacts", "integration/"+uuid.NewString())
	source := filepath.Join(t.TempDir(), "artifact.bin")
	if err := os.WriteFile(source, []byte("verified-minio-artifact"), 0o600); err != nil {
		t.Fatal(err)
	}
	descriptor, err := store.PutFile(ctx, source, artifact.PutRequest{
		ArtifactID: uuid.NewString(), ArtifactType: "model", ContentType: "application/octet-stream", Producer: "go-integration-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	materialized, err := store.MaterializeVerified(ctx, descriptor, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(materialized)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "verified-minio-artifact" {
		t.Fatalf("materialized bytes = %q", data)
	}
}
