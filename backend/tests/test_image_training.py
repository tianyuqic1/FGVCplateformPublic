from pathlib import Path

import numpy as np
import pytest
import torch
from torch import nn

from finevision.ml_toolkit.image_training import ImageClassifier, LoRALinear, merged_classifier, training_mode


class TinyBackbone(nn.Module):
    num_features = 12
    def __init__(self):
        super().__init__()
        self.attn = nn.Module()
        self.attn.qkv = nn.Linear(4, 12)
        self.norm = nn.BatchNorm1d(12)
    def forward(self, images):
        return self.norm(self.attn.qkv(images))


@pytest.mark.parametrize("mode,rank", [("frozen", 0), ("lora", 8), ("lora", 16), ("full", 0)])
def test_gradient_ownership_and_merge(mode, rank):
    torch.manual_seed(42)
    model = ImageClassifier(TinyBackbone(), 3, mode, rank)
    before = {n: p.clone() for n, p in model.named_parameters()}
    buffers = {n: p.clone() for n, p in model.named_buffers()}
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=.01)
    x = torch.randn(4, 4)
    for _ in range(2):
        model.train()
        optimizer.zero_grad()
        nn.functional.cross_entropy(model(x), torch.tensor([0, 1, 2, 0])).backward()
        optimizer.step()
    changed = {n for n, p in model.named_parameters() if not torch.equal(p, before[n])}
    assert "head.weight" in changed
    if mode == "frozen":
        assert all(n.startswith("head.") for n in changed)
    elif mode == "lora":
        assert any("lora_a" in n for n in changed) and any("lora_b" in n for n in changed)
        assert all("lora_" in n or n.startswith("head.") for n in changed)
    else:
        assert "backbone.attn.qkv.weight" in changed
    if mode != "full":
        assert all(torch.equal(p, buffers[n]) for n, p in model.named_buffers())
    model.eval()
    merged = merged_classifier(model)
    assert not any(isinstance(m, LoRALinear) for m in merged.modules())
    torch.testing.assert_close(model(x), merged(x))


@pytest.mark.parametrize("key,config", [("imagenet_vits", {"lora_enabled": True}), ("dinov3_vits", {"lora_rank": 4}), ("dinov3_vits", {"lora_enabled": "true"})])
def test_invalid_modes_rejected(key, config):
    with pytest.raises(ValueError):
        training_mode(key, config)


@pytest.mark.parametrize("key,rank", [("dinov3_vits16_lvd1689m", 0), ("dinov3_vits16_lvd1689m", 8), ("dinov3_vits16_lvd1689m", 16), ("imagenet_vits16_augreg_in21k_ft_in1k", 0)])
@pytest.mark.parametrize("precision", ["FP32", "FP16"])
@pytest.mark.parametrize("image_size", [224, 320])
def test_real_vit_small_training_and_full_export(tmp_path, monkeypatch, key, rank, precision, image_size):
    """Opt-in bounded acceptance: actual managed weights, all modes, 1 epoch."""
    import os
    if os.environ.get("RUN_IMAGE_MODEL_ACCEPTANCE") != "1":
        pytest.skip("set RUN_IMAGE_MODEL_ACCEPTANCE=1 for actual ViT-S acceptance")
    from PIL import Image
    from finevision.compute.image_export import export_image_classifier
    from finevision.compute.pretrained_weights import MANAGED_WEIGHTS
    from finevision.ml_toolkit.datasets import scan_imagefolder
    from finevision.ml_toolkit.image_training import train_images, load_classifier
    root = Path(__file__).resolve().parents[2]
    filename = "dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors" if key.startswith("dino") else "imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors"
    monkeypatch.setenv(MANAGED_WEIGHTS[key].environment_key, str(root / "weights/pretrained" / filename))
    monkeypatch.setenv("FINEVISION_COMPUTE_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
    torch.set_num_threads(2)
    for split in ("train", "val", "test"):
        for label, color in (("red", "red"), ("blue", "blue")):
            folder = tmp_path / "dataset" / split / label
            folder.mkdir(parents=True)
            Image.new("RGB", (224, 224), color).save(folder / "sample.png")
    manifest = scan_imagefolder(tmp_path / "dataset", "acceptance", "snapshot")
    events = []
    config = {"lora_enabled": bool(rank), "lora_rank": rank or 8, "epochs": 1, "batch_size": 2}
    config["head_learning_rate"] = 1e-3
    if key.startswith("imagenet_"): config["backbone_learning_rate"] = 1e-5
    elif rank: config["lora_learning_rate"] = 2e-4
    if image_size == 320:
        from finevision.ml_toolkit.image_training import AUGMENTATION_KEYS
        config["augmentations"] = dict.fromkeys(AUGMENTATION_KEYS, True)
    context, artifact, report, logits = train_images(manifest, {"backbone_id": key, "training_run_id": "run", "head_config": config, "image_size": image_size}, tmp_path,
        progress=lambda *args: events.append(args), check_control=lambda: None)
    assert context.features_path == "" and artifact.feature_artifact_id == ""
    assert logits.shape == (6, 2) and np.isfinite(logits).all()
    assert report.evaluation.run_config["evaluation_split"] == "test"
    assert report.evaluation.run_config["head_learning_rate"] == 1e-3
    assert report.evaluation.run_config["image_size"] == image_size
    assert {e[0] for e in events} == {"weights", "training", "evaluation"}
    pt, onnx, metadata = export_image_classifier(Path(artifact.model_path), tmp_path / "export", manifest.classes, "version", precision=precision)
    assert metadata["parity_passed"] and metadata["model_format"] == "image_classifier_v2"
    minimum = 40_000_000 if precision == "FP16" else 80_000_000
    assert pt.stat().st_size > minimum and onnx.stat().st_size > minimum
    assert metadata["precision"] == precision
    state = torch.load(pt, weights_only=True)["state_dict"]
    assert state["head.weight"].dtype == (torch.float16 if precision == "FP16" else torch.float32)
    merged, checkpoint = load_classifier(pt)
    assert checkpoint["merged"] and not any(isinstance(m, LoRALinear) for m in merged.modules())
    import onnxruntime as ort
    from finevision.ml_toolkit.image_training import image_transform
    with Image.open(manifest.samples[0].path) as image:
        image_tensor = image_transform(checkpoint["preprocessing"])(image.convert("RGB")).unsqueeze(0).numpy()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(onnx), sess_options=options, providers=["CPUExecutionProvider"])
    from finevision.compute.export_precision import check_parity
    check_parity(session.run(["logits"], {"images": image_tensor})[0], logits[:1], precision)
    if rank == 8 and precision == "FP32" and image_size == 224:
        # Exercise the actual worker orchestration, artifact registration and
        # image runtime, not just the numerical training helper.
        from types import SimpleNamespace
        from uuid import uuid4
        from dataclasses import replace
        import json
        from finevision.artifact_store import LocalFilesystemArtifactStore
        from finevision.compute.queue_worker import RemoteTrainingStore, _descriptor_message
        from finevision.compute.inference_runtime import InferenceRuntimeService
        from finevision.compute.v1 import inference_runtime_pb2
        from finevision.compute.artifacts import descriptor_from_proto
        from finevision.worker.jobs import _run_train_classifier
        requests, progress_points = [], []
        run_id, dv = str(uuid4()), str(uuid4())
        lifecycle = SimpleNamespace(
            Complete=lambda request, **kw: (requests.append(request) or SimpleNamespace(training_run_id=run_id, model_version_id=str(uuid4()))),
            ReportProgress=lambda request, **kw: progress_points.append(request),
            Fail=lambda *args, **kw: pytest.fail("unexpected lifecycle failure"),
        )
        objects = LocalFilesystemArtifactStore(tmp_path / "objects")
        remote = RemoteTrainingStore(lifecycle=lifecycle, artifact_store=objects, job_id=str(uuid4()), training_run_id=run_id,
                                     dataset_version_id=dv, attempt_id=str(uuid4()), execution_epoch=1)
        monkeypatch.setenv("FINEVISION_ARTIFACT_DIR", str(tmp_path / "pipeline"))
        manifest = replace(manifest, dataset_version_id=dv)
        result = _run_train_classifier({"dataset_version_id":dv,"training_run_id":run_id,"backbone_id":key,"image_size":224,
                                       "head_config":{**config,"head_type":"image_classifier_v2"}},
                                      SimpleNamespace(get_dataset_version=lambda _:manifest), remote)
        assert result["feature_artifact_id"] is None
        assert "features" not in {d.artifact_type for d in requests[0].artifacts}
        assert all(s["id"] != "features" for p in progress_points for s in dict(p.progress)["stages"])
        bundle = next(d for d in requests[0].artifacts if d.artifact_type == "model_bundle")
        image = objects.put_file(Path(manifest.samples[0].path), artifact_id=str(uuid4()), artifact_type="input_image",content_type="image/png",producer="test")
        prediction = InferenceRuntimeService(tmp_path / "infer-cache").Predict(
            inference_runtime_pb2.PredictRequest(model_bundle=bundle,input_image=_descriptor_message(image)),
            SimpleNamespace(abort=lambda code,message: pytest.fail(message)))
        assert len(prediction.top_k) == 2
        assert abs(sum(item.score for item in prediction.top_k) - 1) < 1e-5
        assert list(dict(prediction.evidence)["nearest_neighbors"]) == []
