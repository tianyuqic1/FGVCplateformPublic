package main

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/jackc/pgx/v5/pgxpool"
	computev1 "github.com/tianyuqic1/FGVCplateformPublic/go/api/proto/finevision/compute/v1"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/einocard"
	grpcadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/grpc"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/llmgatewayclient"
	postgresadapter "github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/postgres"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/adapters/s3artifact"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/annotation"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/config"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/dataset"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/datasetcard"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/deployment"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/hardware"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/httpapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/llm"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/modelregistry"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/observability"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func main() {
	observability.Bootstrap("go-control-plane")
	configuration, err := config.LoadControlPlane()
	if err != nil {
		slog.Error("invalid control plane configuration", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	shutdownTracing, traceErr := observability.SetupTracing(ctx, "go-control-plane")
	if traceErr != nil {
		slog.Warn("tracing exporter unavailable; continuing without export", "error", traceErr)
	} else {
		defer func() {
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = shutdownTracing(shutdownCtx)
		}()
	}
	pool, err := pgxpool.New(ctx, configuration.DatabaseURL)
	if err != nil {
		slog.Error("connect PostgreSQL", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		slog.Error("ping PostgreSQL", "error", err)
		os.Exit(1)
	}
	repository := postgresadapter.NewTrainingRepository(pool)
	awsConfiguration, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(configuration.S3Region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(configuration.S3AccessKey, configuration.S3SecretKey, "")),
	)
	if err != nil {
		slog.Error("configure S3-compatible ArtifactStore", "error", err)
		os.Exit(1)
	}
	s3Client := s3.NewFromConfig(awsConfiguration, func(options *s3.Options) {
		options.BaseEndpoint = aws.String(configuration.S3Endpoint)
		options.UsePathStyle = true
	})
	artifactVerifier := s3artifact.New(s3Client, configuration.ArtifactBucket, "")
	authService := auth.New(&auth.PostgresStore{Pool: pool})
	authService.CookieSecure = os.Getenv("FINEVISION_ENVIRONMENT") != "local"
	lifecycle := training.NewServiceWithVerifier(repository, artifactVerifier, time.Now, configuration.LeaseTTL)
	llmGateway := llmgatewayclient.New(configuration.LLMGatewayURL, configuration.LLMInternalToken, &http.Client{Timeout: 90 * time.Second, Transport: observability.TraceHTTPTransport(nil)})
	llmApplication := llm.NewApplication(llmGateway)
	cardGenerator, err := einocard.New(ctx, llmGateway)
	if err != nil {
		slog.Error("compile dataset card workflow")
		os.Exit(1)
	}
	cards := &datasetcard.Service{Repository: &postgresadapter.DatasetCardRepository{Pool: pool}, Generator: cardGenerator}
	computeAddress := os.Getenv("FINEVISION_DATASET_GRPC")
	if computeAddress == "" {
		computeAddress = "python-artifact-runtime:9200"
	}
	computeConnection, err := grpc.NewClient(computeAddress, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithStatsHandler(otelgrpc.NewClientHandler()), grpc.WithChainUnaryInterceptor(observability.UnaryClientMetricsInterceptor()))
	if err != nil {
		slog.Error("connect compute runtime", "error", err)
		os.Exit(1)
	}
	defer computeConnection.Close()
	inferenceAddress := os.Getenv("FINEVISION_INFERENCE_GRPC")
	if inferenceAddress == "" {
		inferenceAddress = "python-inference-runtime:9100"
	}
	inferenceConnection, err := grpc.NewClient(inferenceAddress, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithStatsHandler(otelgrpc.NewClientHandler()), grpc.WithChainUnaryInterceptor(observability.UnaryClientMetricsInterceptor()))
	if err != nil {
		slog.Error("configure inference connection", "error", err)
		os.Exit(1)
	}
	defer inferenceConnection.Close()
	targets := []deployment.Target{}
	runtimeClients := map[string]computev1.InferenceRuntimeClient{}
	for _, spec := range []struct{ runtime, prefix string }{{"tensorrt", "FINEVISION_TENSORRT"}, {"ascend_acl", "FINEVISION_ASCEND"}} {
		address, profile := os.Getenv(spec.prefix+"_GRPC"), os.Getenv(spec.prefix+"_TARGET")
		if address == "" {
			continue
		}
		if profile == "" || os.Getenv("FINEVISION_DEPLOYMENT_TOKEN") == "" {
			slog.Error("target profile and deployment token required", "runtime", spec.runtime)
			os.Exit(1)
		}
		connection, err := grpc.NewClient(address, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithStatsHandler(otelgrpc.NewClientHandler()), grpc.WithChainUnaryInterceptor(observability.UnaryClientMetricsInterceptor()))
		if err != nil {
			slog.Error("invalid runtime address")
			os.Exit(1)
		}
		defer connection.Close()
		runtimeClients[spec.runtime] = computev1.NewInferenceRuntimeClient(connection)
		targets = append(targets, deployment.Target{Runtime: spec.runtime, Profile: profile, Address: address})
	}
	deployments := &postgresadapter.DeploymentRepository{Pool: pool, Store: artifactVerifier, Targets: targets}
	datasetImport := &dataset.Service{UploadRoot: os.Getenv("FINEVISION_UPLOAD_DIR"), Store: artifactVerifier, Scanner: grpcadapter.DatasetScanner{Client: computev1.NewDatasetComputeClient(computeConnection)}, Repository: postgresadapter.DatasetRepository{Pool: pool}}
	hardwareStore := &hardware.PostgresStore{Pool: pool}
	go hardwareStore.RunRetention(ctx)
	datasetQueue := &dataset.ImportQueue{Service: datasetImport, Repository: &postgresadapter.DatasetImportQueue{Pool: pool}}
	annotationRepo := &annotation.Repository{Pool: pool}
	annotationPublisher := &annotation.Publisher{Repo: annotationRepo, Datasets: datasetImport}
	publicationDone := make(chan struct{})
	go func() { defer close(publicationDone); annotationPublisher.Run(ctx) }()
	defer func() { stop(); <-publicationDone }()
	workerDone := make(chan struct{})
	go func() { defer close(workerDone); datasetQueue.Run(ctx) }()
	defer func() { stop(); <-workerDone }()
	server := &http.Server{
		Addr: configuration.HTTPAddress,
		Handler: observability.TracePublicHTTPHandler(httpapi.NewRouter(httpapi.Dependencies{
			Auth:        authService,
			Annotation:  &annotation.Handler{Repo: annotationRepo, Store: artifactVerifier, Token: configuration.LLMInternalToken, Publisher: annotationPublisher},
			Deployments: deployments, DeploymentToken: os.Getenv("FINEVISION_DEPLOYMENT_TOKEN"),
			Inference:     &postgresadapter.InferenceService{LegacyUploadRoot: os.Getenv("FINEVISION_UPLOAD_DIR"), Pool: pool, Store: artifactVerifier, Preview: &dataset.PreviewService{Repository: postgresadapter.DatasetRepository{Pool: pool}, Store: artifactVerifier}, Client: computev1.NewInferenceRuntimeClient(inferenceConnection), Deployments: deployments, RuntimeClients: runtimeClients},
			Policies:      &postgresadapter.PolicyRepository{Pool: pool},
			Reviews:       &postgresadapter.ReviewRepository{Pool: pool},
			Hardware:      hardware.Handler{Store: hardwareStore, Token: os.Getenv("FINEVISION_HARDWARE_TOKEN")},
			DatasetCards:  cards,
			DatasetImport: datasetImport,
			DatasetQueue:  datasetQueue,
			Lifecycle:     lifecycle, ReadModels: postgresadapter.NewReadModels(pool), LLMApplication: llmApplication,
			ModelRegistry: modelregistry.NewService(postgresadapter.NewModelRegistryRepository(pool)).WithPublication(grpcadapter.HeadExporter{Client: computev1.NewModelExportClient(computeConnection)}, artifactVerifier),
		}), "finevision.http"),
		ReadHeaderTimeout: 5 * time.Second,
	}
	listener, err := net.Listen("tcp", configuration.GRPCAddress)
	if err != nil {
		slog.Error("listen for internal gRPC", "error", err)
		os.Exit(1)
	}
	grpcServer := grpc.NewServer(grpc.StatsHandler(otelgrpc.NewServerHandler()), grpc.ChainUnaryInterceptor(observability.UnaryServerMetricsInterceptor()))
	computev1.RegisterTrainingLifecycleServer(grpcServer, grpcadapter.NewTrainingLifecycleServer(lifecycle))
	go func() {
		if err := grpcServer.Serve(listener); err != nil {
			slog.Error("internal gRPC stopped", "error", err)
			stop()
		}
	}()
	go runReaper(ctx, lifecycle)
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		grpcServer.GracefulStop()
		_ = server.Shutdown(shutdownCtx)
	}()
	slog.Info("starting FineVision Go Control Plane", "address", configuration.HTTPAddress)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("control plane stopped", "error", err)
		os.Exit(1)
	}
}

func runReaper(ctx context.Context, lifecycle *training.Service) {
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			reaped, err := lifecycle.ReapExpired(ctx, 100)
			if err != nil {
				slog.Error("reap expired training attempts", "error", err)
				continue
			}
			if len(reaped) > 0 {
				slog.Warn("requeued expired training attempts", "count", len(reaped))
			}
		}
	}
}
