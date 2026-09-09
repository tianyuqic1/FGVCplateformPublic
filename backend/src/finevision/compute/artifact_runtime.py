"""Dataset and model artifact operations, isolated from online inference."""
import os
from concurrent import futures

import grpc

from finevision.compute.artifacts import VerifiedArtifactReader, create_s3_client
from finevision.compute.dataset_compute import DatasetComputeService
from finevision.compute.v1 import dataset_compute_pb2_grpc
from finevision.compute.v1 import model_export_pb2_grpc
from finevision.compute.model_export import ModelExportService
from finevision.artifact_store import S3ArtifactStore


def main():
    import torch
    torch.set_num_threads(max(1, int(os.environ.get("FINEVISION_TORCH_THREADS", "2"))))
    reader = VerifiedArtifactReader(os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache"), create_s3_client())
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    dataset_compute_pb2_grpc.add_DatasetComputeServicer_to_server(DatasetComputeService(reader), server)
    store = S3ArtifactStore(client=reader.s3_client, bucket=os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"))
    model_export_pb2_grpc.add_ModelExportServicer_to_server(ModelExportService(reader, store), server)
    server.add_insecure_port(os.environ.get("FINEVISION_ARTIFACT_GRPC_ADDRESS", "[::]:9200"))
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()
