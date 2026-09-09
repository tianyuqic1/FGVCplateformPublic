"""Opt-in publication acceptance against an isolated PostgreSQL DB and MinIO."""
from concurrent import futures
from dataclasses import asdict
from datetime import datetime, UTC
import json
import os
import subprocess
import time
from uuid import uuid4

import boto3
import grpc
import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from botocore.config import Config

from finevision.artifact_store import S3ArtifactStore
from finevision.compute.artifacts import VerifiedArtifactReader, descriptor_from_dict
from finevision.compute.model_export import ModelExportService
from finevision.compute.v1 import model_export_pb2_grpc
from finevision.db.schema import artifacts, model_versions


@pytest.mark.parametrize("image_model", [False, True])
def test_http_publish_exports_checkpoint_onnx_and_commits_verified_minio_artifacts(tmp_path, image_model):
    database=os.environ.get("PUBLICATION_TEST_DATABASE_URL", "")
    binary=os.environ.get("PUBLICATION_TEST_CONTROL_PLANE", "")
    if not database or not binary:
        pytest.skip("set PUBLICATION_TEST_DATABASE_URL and PUBLICATION_TEST_CONTROL_PLANE")
    assert sa.engine.make_url(database).database.startswith("publication_test_"), "isolated test database required"
    engine=sa.create_engine(database)
    endpoint=os.environ.get("PUBLICATION_TEST_S3_ENDPOINT", "http://localhost:9000")
    client=boto3.client("s3",endpoint_url=endpoint,region_name="us-east-1",aws_access_key_id="finevision",aws_secret_access_key="finevision-dev-object-secret",config=Config(s3={"addressing_style":"path"}))
    store=S3ArtifactStore(client=client,bucket="finevision-artifacts",prefix="publication-test/"+str(uuid4()))
    reader=VerifiedArtifactReader(tmp_path / "cache",client)
    server=grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    model_export_pb2_grpc.add_ModelExportServicer_to_server(ModelExportService(reader,store),server)
    port=server.add_insecure_port("127.0.0.1:0")
    server.start()
    with engine.connect() as connection:
        template=dict(connection.execute(sa.select(model_versions).limit(1)).mappings().one())
    version_id=str(uuid4())
    dv,tr=str(template["dataset_version_id"]),str(template["training_run_id"])
    source=tmp_path / "head.npz"
    rng=np.random.default_rng(7)
    np.savez(source,weights=rng.normal(size=(384,3)).astype(np.float32),bias=np.zeros(3,np.float32),feature_mean=np.zeros(384,np.float32),feature_std=np.ones(384,np.float32))
    if image_model:
        import torch
        from pathlib import Path
        from finevision.ml_toolkit.image_training import build_classifier
        torch.set_num_threads(2)
        key = "dinov3_vits16_lvd1689m"
        path = Path(__file__).resolve().parents[2] / "weights/pretrained/dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors"
        model, preprocessing = build_classifier(key, 3, "lora", 8, checkpoint_path=path)
        model.train()
        optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-3)
        torch.nn.functional.cross_entropy(model(torch.zeros(2, *preprocessing["input_size"])),torch.tensor([0,1])).backward()
        optimizer.step()
        source = tmp_path / "image_classifier.pt"
        torch.save({"format":"image_classifier_v2", "backbone_key":key, "classes":["red","green","blue"], "state_dict":model.state_dict(),
                    "preprocessing":preprocessing,"merged":False,"training_config":{"training_mode":"lora","lora_enabled":True,"lora_rank":8}},source)
    head=store.put_file(source,artifact_id=str(uuid4()),artifact_type="model",content_type="application/octet-stream",producer="acceptance",dataset_version_id=dv,training_run_id=tr)
    bundle_path=tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps({"model_format":"image_classifier_v2" if image_model else "linear_head_v1", "model":asdict(head),"model_artifact":{"dataset_version_id":dv,"feature_dim":384,"classes":["red","green","blue"]}}))
    bundle=store.put_file(bundle_path,artifact_id=str(uuid4()),artifact_type="model_bundle",content_type="application/json",producer="acceptance",dataset_version_id=dv,training_run_id=tr)
    def record(a):
        return dict(id=a.artifact_id,artifact_key=a.artifact_id,artifact_type=a.artifact_type,uri=a.uri,checksum=a.sha256,size_bytes=a.size_bytes,
                    content_type=a.content_type,producer=a.producer,storage_version=a.storage_version,schema_version=1,
                    dataset_version_id=dv,training_run_id=tr,verified_at=datetime.now(UTC),created_at=datetime.now(UTC),artifact_metadata=a.metadata)
    template.update(id=version_id,model_key="publication-acceptance-"+version_id,name="ONNX 发布验收",status="candidate",model_artifact_id=head.artifact_id,updated_at=datetime.now(UTC),created_at=datetime.now(UTC))
    template["head_type"] = "image_classifier_v2" if image_model else "ridge_linear"
    with engine.begin() as connection:
        connection.execute(artifacts.insert(),[record(head),record(bundle)])
        connection.execute(model_versions.insert(),template)
    env={**os.environ,"FINEVISION_DATABASE_URL":database.replace("postgresql+psycopg:","postgresql:"),"FINEVISION_HTTP_ADDRESS":"127.0.0.1:18091","FINEVISION_GRPC_ADDRESS":"127.0.0.1:19091",
         "FINEVISION_DATASET_GRPC":f"127.0.0.1:{port}","FINEVISION_LLM_INTERNAL_TOKEN":"test-only","FINEVISION_S3_ENDPOINT":endpoint,
         "FINEVISION_S3_ACCESS_KEY":"finevision","FINEVISION_S3_SECRET_KEY":"finevision-dev-object-secret","FINEVISION_ARTIFACT_BUCKET":"finevision-artifacts"}
    process=subprocess.Popen([binary],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url="http://127.0.0.1:18091",timeout=125) as http:
            for _ in range(100):
                try:
                    if http.get("/api/health").status_code==200: break
                except httpx.ConnectError: pass
                time.sleep(.1)
            else: pytest.fail("test control plane did not become ready")
            path=f"/api/model-versions/{version_id}/promote"
            response=http.post(path,json={"target_status":"production","actor":"acceptance","reason":"verified 384-dimensional head"})
            assert response.status_code==200,response.text
            version=response.json()["model_version"]
            assert version["status"]=="production"
            output=[a for a in version["artifacts"] if (a.get("metadata") or {}).get("model_version_id")==version_id]
            assert {a["artifact_type"] for a in output}==({"full_pt","full_onnx"} if image_model else {"head_pt","head_onnx"})
            for a in output:
                assert a["uri"].startswith("s3://finevision-artifacts/") and len(a["sha256"])==64
                assert reader.materialize(descriptor_from_dict(a)).stat().st_size==a["size_bytes"]
            retry=http.post(path,json={"target_status":"production","actor":"acceptance","reason":"retry"})
            assert retry.status_code==200
            assert len(retry.json()["model_version"]["artifacts"])==len(version["artifacts"])
            # Own test fixture only: a corrupt descriptor must not allow publication.
            failed_id=str(uuid4())
            template.update(id=failed_id,model_key="publication-failure-"+failed_id,status="candidate")
            with engine.begin() as connection:
                connection.execute(model_versions.insert(),template)
                connection.execute(artifacts.update().where(artifacts.c.id==bundle.artifact_id).values(checksum="0"*64))
            failure=http.post(f"/api/model-versions/{failed_id}/promote",json={"target_status":"production","actor":"acceptance","reason":"corruption test"})
            assert failure.status_code==409
            assert http.get(f"/api/model-versions/{failed_id}").json()["model_version"]["status"]=="candidate"
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.stop(0).wait()
        engine.dispose()
