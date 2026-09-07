package config

import (
	"fmt"
	"os"
	"time"
)

type ControlPlane struct {
	HTTPAddress      string
	GRPCAddress      string
	DatabaseURL      string
	LeaseTTL         time.Duration
	LLMGatewayURL    string
	LLMInternalToken string
	S3Endpoint       string
	S3Region         string
	S3AccessKey      string
	S3SecretKey      string
	ArtifactBucket   string
}

type OutboxRelay struct {
	DatabaseURL string
	RabbitURL   string
	Exchange    string
	Queue       string
	RoutingKey  string
	DLX         string
	DLQ         string
}

type LLMGateway struct {
	HTTPAddress   string
	ProviderURL   string
	ProviderName  string
	Model         string
	APIKey        string
	InternalToken string
	Timeout       time.Duration
}

func LoadControlPlane() (ControlPlane, error) {
	configuration := ControlPlane{
		HTTPAddress:      valueOrDefault("FINEVISION_HTTP_ADDRESS", ":8000"),
		GRPCAddress:      valueOrDefault("FINEVISION_GRPC_ADDRESS", ":9000"),
		DatabaseURL:      os.Getenv("FINEVISION_DATABASE_URL"),
		LeaseTTL:         150 * time.Second,
		LLMGatewayURL:    valueOrDefault("FINEVISION_LLM_GATEWAY_URL", "http://go-llm-gateway:8080"),
		LLMInternalToken: os.Getenv("FINEVISION_LLM_INTERNAL_TOKEN"),
		S3Endpoint:       os.Getenv("FINEVISION_S3_ENDPOINT"),
		S3Region:         valueOrDefault("FINEVISION_S3_REGION", "us-east-1"),
		S3AccessKey:      os.Getenv("FINEVISION_S3_ACCESS_KEY"),
		S3SecretKey:      os.Getenv("FINEVISION_S3_SECRET_KEY"),
		ArtifactBucket:   valueOrDefault("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"),
	}
	if configuration.DatabaseURL == "" || configuration.LLMInternalToken == "" || configuration.S3Endpoint == "" || configuration.S3AccessKey == "" || configuration.S3SecretKey == "" {
		return ControlPlane{}, fmt.Errorf("database, LLM internal token, and S3-compatible ArtifactStore configuration are required")
	}
	if configured := os.Getenv("FINEVISION_LEASE_TTL"); configured != "" {
		parsed, err := time.ParseDuration(configured)
		if err != nil || parsed < 30*time.Second {
			return ControlPlane{}, fmt.Errorf("FINEVISION_LEASE_TTL must be a duration of at least 30s")
		}
		configuration.LeaseTTL = parsed
	}
	return configuration, nil
}

func LoadOutboxRelay() (OutboxRelay, error) {
	configuration := OutboxRelay{
		DatabaseURL: os.Getenv("FINEVISION_DATABASE_URL"),
		RabbitURL:   os.Getenv("FINEVISION_RABBITMQ_URL"),
		Exchange:    valueOrDefault("FINEVISION_TRAINING_EXCHANGE", "finevision.training.v1"),
		Queue:       valueOrDefault("FINEVISION_TRAINING_QUEUE", "finevision.training.v1"),
		RoutingKey:  valueOrDefault("FINEVISION_TRAINING_ROUTING_KEY", "training.ready"),
		DLX:         valueOrDefault("FINEVISION_TRAINING_DLX", "finevision.training.dlx.v1"),
		DLQ:         valueOrDefault("FINEVISION_TRAINING_DLQ", "finevision.training.dlq.v1"),
	}
	if configuration.DatabaseURL == "" || configuration.RabbitURL == "" {
		return OutboxRelay{}, fmt.Errorf("FINEVISION_DATABASE_URL and FINEVISION_RABBITMQ_URL are required")
	}
	return configuration, nil
}

func LoadLLMGateway() (LLMGateway, error) {
	configuration := LLMGateway{
		HTTPAddress:  valueOrDefault("FINEVISION_LLM_GATEWAY_ADDRESS", ":8080"),
		ProviderURL:  valueOrDefault("FINEVISION_LLM_BASE_URL", "https://api.openai.com/v1"),
		ProviderName: valueOrDefault("FINEVISION_LLM_PROVIDER", "OpenAI-compatible"),
		Model:        os.Getenv("FINEVISION_LLM_MODEL"), APIKey: os.Getenv("FINEVISION_LLM_API_KEY"),
		InternalToken: os.Getenv("FINEVISION_LLM_INTERNAL_TOKEN"), Timeout: 75 * time.Second,
	}
	if configuration.Model == "" || configuration.InternalToken == "" {
		return LLMGateway{}, fmt.Errorf("FINEVISION_LLM_MODEL and FINEVISION_LLM_INTERNAL_TOKEN are required")
	}
	return configuration, nil
}

func valueOrDefault(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
