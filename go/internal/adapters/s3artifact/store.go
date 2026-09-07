package s3artifact

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awss3 "github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type Client interface {
	PutObject(context.Context, *awss3.PutObjectInput, ...func(*awss3.Options)) (*awss3.PutObjectOutput, error)
	HeadObject(context.Context, *awss3.HeadObjectInput, ...func(*awss3.Options)) (*awss3.HeadObjectOutput, error)
	GetObject(context.Context, *awss3.GetObjectInput, ...func(*awss3.Options)) (*awss3.GetObjectOutput, error)
}

type Store struct {
	client Client
	bucket string
	prefix string
}

func New(client Client, bucket, prefix string) *Store {
	return &Store{client: client, bucket: bucket, prefix: strings.Trim(prefix, "/")}
}

func (store *Store) PutFile(ctx context.Context, source string, request artifact.PutRequest) (artifact.Descriptor, error) {
	sha, size, err := artifact.InspectFile(ctx, source)
	if err != nil {
		return artifact.Descriptor{}, err
	}
	key := strings.Join(nonEmpty(store.prefix, request.ArtifactType, sha[:2], sha), "/")
	file, err := os.Open(source)
	if err != nil {
		return artifact.Descriptor{}, err
	}
	_, putErr := store.client.PutObject(ctx, &awss3.PutObjectInput{
		Bucket: aws.String(store.bucket), Key: aws.String(key), Body: file,
		ContentType: aws.String(request.ContentType), Metadata: map[string]string{"sha256": sha},
	})
	closeErr := file.Close()
	if putErr != nil {
		return artifact.Descriptor{}, putErr
	}
	if closeErr != nil {
		return artifact.Descriptor{}, closeErr
	}
	head, err := store.client.HeadObject(ctx, &awss3.HeadObjectInput{Bucket: aws.String(store.bucket), Key: aws.String(key)})
	if err != nil {
		return artifact.Descriptor{}, err
	}
	if head.ContentLength == nil || *head.ContentLength != size {
		return artifact.Descriptor{}, fmt.Errorf("%w after S3 HEAD", artifact.ErrIntegrity)
	}
	object, err := store.client.GetObject(ctx, &awss3.GetObjectInput{Bucket: aws.String(store.bucket), Key: aws.String(key)})
	if err != nil {
		return artifact.Descriptor{}, err
	}
	verifyErr := artifact.VerifyReader(ctx, object.Body, sha, size)
	closeErr = object.Body.Close()
	if verifyErr != nil {
		return artifact.Descriptor{}, verifyErr
	}
	if closeErr != nil {
		return artifact.Descriptor{}, closeErr
	}
	now := time.Now().UTC()
	metadata := request.Metadata
	if metadata == nil {
		metadata = map[string]any{}
	}
	return artifact.Descriptor{
		ArtifactID: request.ArtifactID, ArtifactType: request.ArtifactType,
		URI: fmt.Sprintf("s3://%s/%s", store.bucket, key), SHA256: sha, SizeBytes: size,
		ContentType: request.ContentType, StorageVersion: "s3-v1", Producer: request.Producer,
		DatasetVersionID: request.DatasetVersionID, TrainingRunID: request.TrainingRunID,
		AttemptID: request.AttemptID, SchemaVersion: 1, CreatedAt: now, VerifiedAt: now, Metadata: metadata,
	}, nil
}

func (store *Store) MaterializeVerified(ctx context.Context, descriptor artifact.Descriptor, cacheRoot string) (string, error) {
	prefix := "s3://" + store.bucket + "/"
	if !strings.HasPrefix(descriptor.URI, prefix) {
		return "", fmt.Errorf("artifact URI does not belong to configured S3 bucket")
	}
	key := strings.TrimPrefix(descriptor.URI, prefix)
	return artifact.MaterializeReader(ctx, descriptor, cacheRoot, func() (io.ReadCloser, error) {
		object, err := store.client.GetObject(ctx, &awss3.GetObjectInput{Bucket: aws.String(store.bucket), Key: aws.String(key)})
		if err != nil {
			return nil, err
		}
		return object.Body, nil
	})
}

func (store *Store) Verify(ctx context.Context, descriptor artifact.Descriptor) error {
	prefix := "s3://" + store.bucket + "/"
	if !strings.HasPrefix(descriptor.URI, prefix) {
		return fmt.Errorf("artifact URI does not belong to configured S3 bucket")
	}
	object, err := store.client.GetObject(ctx, &awss3.GetObjectInput{
		Bucket: aws.String(store.bucket), Key: aws.String(strings.TrimPrefix(descriptor.URI, prefix)),
	})
	if err != nil {
		return err
	}
	defer object.Body.Close()
	return artifact.VerifyReader(ctx, object.Body, descriptor.SHA256, descriptor.SizeBytes)
}

func nonEmpty(values ...string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		if value != "" {
			result = append(result, value)
		}
	}
	return result
}
