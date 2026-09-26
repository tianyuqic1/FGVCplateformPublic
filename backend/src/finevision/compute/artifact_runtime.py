"""Dataset and model artifact operations, isolated from online inference."""
import os
import logging
from concurrent import futures

import grpc

from finevision.compute.artifacts import VerifiedArtifactReader, create_s3_client
from finevision.compute.dataset_compute import DatasetComputeService
from finevision.compute.v1 import dataset_compute_pb2_grpc
from finevision.compute.v1 import model_export_pb2_grpc
from finevision.compute.model_export import ModelExportService
from finevision.artifact_store import S3ArtifactStore
from finevision.observability import configure_logging
from finevision.observability.grpc_metrics import MetricsServerInterceptor
from finevision.observability.metrics import start_metrics_server
from finevision.observability.tracing import configure_tracing


LOG = logging.getLogger(__name__)


def main():
    from finevision.compute.worker_reporter import WorkerReporter, grpc_interceptor
    reporter = WorkerReporter("artifact", 2).start()
    configure_logging("python-artifact-runtime", force=True)
    trace_provider = configure_tracing("python-artifact-runtime")
    metrics = start_metrics_server("python-artifact-runtime", 9303)
    import torch
    torch.set_num_threads(max(1, int(os.environ.get("FINEVISION_TORCH_THREADS", "2"))))
    reader = VerifiedArtifactReader(os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache"), create_s3_client())
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2), interceptors=(MetricsServerInterceptor(metrics), grpc_interceptor(reporter)))
    dataset_compute_pb2_grpc.add_DatasetComputeServicer_to_server(DatasetComputeService(reader), server)
    store = S3ArtifactStore(client=reader.s3_client, bucket=os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"))
    model_export_pb2_grpc.add_ModelExportServicer_to_server(ModelExportService(reader, store), server)
    address = os.environ.get("FINEVISION_ARTIFACT_GRPC_ADDRESS", "[::]:9200")
    server.add_insecure_port(address)
    server.start()
    reporter.state("ready", "数据校验与模型导出 RPC 已启动；对象存储按请求访问")
    LOG.info("Artifact runtime started", extra={"event": "runtime_started", "address": address})
    try:
        server.wait_for_termination()
    finally:
        reporter.close()
    if trace_provider is not None:
        trace_provider.shutdown()


if __name__ == "__main__":
    main()
