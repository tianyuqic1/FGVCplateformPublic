"""Image-in/logits-out classifiers. Frozen DINO, optional LoRA, full ImageNet training."""
from copy import deepcopy
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, WEIGHT_ALIASES
from finevision.ml_toolkit.artifacts import write_json
from finevision.ml_toolkit.metrics import classification_report
from finevision.schemas.artifacts import FeatureArtifact, ModelArtifact, TrainingRunReport


def training_mode(backbone_key, config):
    key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
    if key not in MANAGED_WEIGHTS:
        raise ValueError("unsupported image-training backbone")
    enabled = config.get("lora_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("lora_enabled must be boolean")
    rank = config.get("lora_rank", 8)
    if isinstance(rank, bool) or rank not in (8, 16):
        raise ValueError("LoRA rank must be 8 or 16")
    if key.startswith("imagenet_"):
        if enabled:
            raise ValueError("ImageNet requires full-parameter training, not LoRA")
        return "full", 0
    return ("lora", int(rank)) if enabled else ("frozen", 0)


class LoRALinear(nn.Module):
    def __init__(self, base, rank):
        super().__init__()
        self.base = base.requires_grad_(False)
        self.lora_a = nn.Parameter(torch.empty(rank, base.in_features))
        self.lora_b = nn.Parameter(torch.zeros(base.out_features, rank))
        nn.init.kaiming_uniform_(self.lora_a, a=math.sqrt(5))
        self.scale = 2.0  # alpha = 2r, persisted in the checkpoint contract.

    def forward(self, value):
        return self.base(value) + (value @ self.lora_a.T @ self.lora_b.T) * self.scale


def inject_lora(backbone, rank):
    targets = [(name, layer) for name, layer in backbone.named_modules()
               if name.endswith("attn.qkv") and isinstance(layer, nn.Linear)]
    if not targets:
        raise ValueError("approved DINO backbone has no attention qkv LoRA targets")
    for name, layer in targets:
        parent, leaf = name.rsplit(".", 1)
        setattr(backbone.get_submodule(parent), leaf, LoRALinear(layer, rank))


class ImageClassifier(nn.Module):
    def __init__(self, backbone, classes, mode, rank=0):
        super().__init__()
        self.backbone = backbone
        self.mode = mode
        self.backbone.requires_grad_(mode == "full")
        if mode == "lora":
            inject_lora(backbone, rank)
        self.head = nn.Linear(backbone.num_features, classes)

    def train(self, mode=True):
        super().train(mode)
        if self.mode != "full":
            # Keep original DINO buffers/dropout fixed; gradients still reach A/B.
            self.backbone.eval()
        return self

    def forward(self, images):
        if self.mode == "frozen":
            with torch.no_grad():
                features = self.backbone(images)
        else:
            features = self.backbone(images)
        return self.head(features)


def build_classifier(key, classes, mode, rank, *, checkpoint_path=None):
    import timm
    from timm.data import resolve_model_data_config
    key = WEIGHT_ALIASES.get(key, key)
    spec = MANAGED_WEIGHTS[key]
    backbone = timm.create_model(spec.architecture, pretrained=False, num_classes=0)
    # Explicit attention is portable across PyTorch and ONNX runtimes.
    for module in backbone.modules():
        if hasattr(module, "fused_attn"):
            module.fused_attn = False
    if checkpoint_path:
        from safetensors.torch import load_file
        incompatible = backbone.load_state_dict(load_file(str(checkpoint_path)), strict=False)
        unexpected = [name for name in incompatible.unexpected_keys if not name.startswith(("head.", "fc.", "classifier."))]
        if incompatible.missing_keys or unexpected:
            raise ValueError("pretrained checkpoint does not match approved backbone")
    data_config = resolve_model_data_config(backbone)
    preprocessing = {name: data_config[name] for name in ("input_size", "mean", "std", "interpolation", "crop_pct")}
    return ImageClassifier(backbone, classes, mode, rank), preprocessing


def image_transform(preprocessing):
    from timm.data import create_transform
    return create_transform(**preprocessing, is_training=False)


class ImageSamples(Dataset):
    def __init__(self, samples, classes, transform):
        self.samples, self.classes, self.transform = samples, classes, transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        sample = self.samples[index]
        with Image.open(sample.path) as image:
            tensor = self.transform(image.convert("RGB"))
        return tensor, self.classes.index(sample.label)


def merged_classifier(model):
    merged = deepcopy(model).cpu().eval()
    for name, layer in list(merged.named_modules()):
        if isinstance(layer, LoRALinear):
            with torch.no_grad():
                layer.base.weight.add_((layer.lora_b @ layer.lora_a) * layer.scale)
            parent, leaf = name.rsplit(".", 1)
            setattr(merged.get_submodule(parent), leaf, layer.base)
    # No adapters remain; inference does not depend on a separate base weight file.
    merged.mode = "full"
    return merged


def load_classifier(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint.get("format") != "image_classifier_v2":
        raise ValueError("unsupported image checkpoint")
    classes = checkpoint["classes"]
    if not classes or len(classes) != len(set(classes)) or any(not isinstance(c, str) or not c for c in classes):
        raise ValueError("invalid class mapping")
    mode, rank = training_mode(checkpoint["backbone_key"], checkpoint["training_config"])
    if checkpoint.get("merged"):
        mode, rank = "full", 0
    model, _ = build_classifier(checkpoint["backbone_key"], len(classes), mode, rank)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    if any(not torch.isfinite(p).all() for p in model.parameters()):
        raise ValueError("non-finite checkpoint")
    return model.eval(), checkpoint


def train_images(manifest, payload, artifact_root, *, progress, check_control):
    key = WEIGHT_ALIASES.get(payload["backbone_id"], payload["backbone_id"])
    config = dict(payload.get("head_config") or {})
    mode, rank = training_mode(key, config)
    epochs, batch_size = config.get("epochs", 30), config.get("batch_size", 8)
    lr, decay = float(config.get("learning_rate", 1e-3 if mode == "frozen" else 1e-4)), float(config.get("weight_decay", 1e-4))
    if isinstance(epochs, bool) or not float(epochs).is_integer() or not 1 <= epochs <= 1000:
        raise ValueError("epochs must be an integer in [1,1000]")
    if isinstance(batch_size, bool) or not float(batch_size).is_integer() or not 1 <= batch_size <= 128:
        raise ValueError("image batch_size must be an integer in [1,128]")
    if not math.isfinite(lr) or not 0 < lr <= 1 or not math.isfinite(decay) or not 0 <= decay <= 1:
        raise ValueError("invalid optimizer configuration")
    epochs, batch_size = int(epochs), int(batch_size)
    train = [s for s in manifest.samples if s.split == "train"]
    val = [s for s in manifest.samples if s.split == "val"]
    test = [s for s in manifest.samples if s.split == "test"]
    if not train or not val or not test:
        raise ValueError("image training requires separate train, val and test splits")
    torch.manual_seed(42)
    torch.set_num_threads(max(1, int(os.environ.get("FINEVISION_TORCH_THREADS", "2"))))
    weight_path = os.environ.get(MANAGED_WEIGHTS[key].environment_key)
    if not weight_path:
        raise ValueError("verified managed pretrained weight is required")
    progress("weights", 0)
    model, preprocessing = build_classifier(key, len(manifest.classes), mode, rank, checkpoint_path=weight_path)
    size = int(payload.get("image_size") or preprocessing["input_size"][-1])
    if not 128 <= size <= 512 or ("vits" in key and size % 16) or (key.startswith("imagenet_vits") and size != 224):
        raise ValueError("unsupported image input size")
    preprocessing["input_size"] = (3, size, size)
    device = os.environ.get("FINEVISION_COMPUTE_DEVICE", "cpu")
    model.to(device)
    transform = image_transform(preprocessing)
    def loader(samples, shuffle=False):
        return DataLoader(ImageSamples(samples, manifest.classes, transform), batch_size=batch_size, shuffle=shuffle, num_workers=0)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=decay)
    def predict(samples):
        model.eval()
        rows = []
        with torch.inference_mode():
            for images, _ in loader(samples):
                check_control()
                rows.append(model(images.to(device)).cpu().numpy())
        return np.concatenate(rows)
    history = []
    for epoch in range(1, epochs + 1):
        check_control()
        model.train()
        total_loss = 0.0
        for images, labels in loader(train, True):
            check_control()
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(model(images.to(device)), labels.to(device))
            if not torch.isfinite(loss):
                raise ValueError("non-finite training loss")
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(labels)
        val_logits = predict(val)
        val_labels = np.array([manifest.classes.index(s.label) for s in val])
        metrics = {"epoch": epoch, "train_loss": total_loss / len(train), "eval_accuracy": float((val_logits.argmax(1) == val_labels).mean())}
        history.append(metrics)
        progress("training", epoch / epochs * 100, metrics)
    progress("evaluation", 0)
    logits = predict(manifest.samples)
    config.update({"head_type": "image_classifier_v2", "training_mode": mode, "lora_enabled": mode == "lora", "lora_rank": rank or 8,
                   "lora_alpha": rank * 2, "lora_targets": ["attn.qkv"] if rank else [], "epochs": epochs, "batch_size": batch_size,
                   "learning_rate": lr, "weight_decay": decay, "preprocessing": preprocessing,
                   "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
                   "total_parameters": sum(p.numel() for p in model.parameters()), "evaluation_split": "test"})
    run_id = payload["training_run_id"]
    artifact_id = f"{manifest.dataset_version_id}-{run_id}-image-model"
    directory = Path(artifact_root) / "models" / artifact_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "image_classifier.pt"
    torch.save({"format": "image_classifier_v2", "backbone_key": key, "classes": manifest.classes,
                "training_config": config, "preprocessing": preprocessing, "merged": False,
                "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()}}, path)
    model_artifact = ModelArtifact(artifact_id, manifest.dataset_id, manifest.dataset_version_id, "", str(path), manifest.classes,
                                   "image_classifier_v2", model.backbone.num_features, config)
    mask = np.array([s.split == "test" for s in manifest.samples])
    labels = np.array([manifest.classes.index(s.label) for s in manifest.samples])
    report = TrainingRunReport(run_id, manifest.dataset_id, manifest.dataset_version_id, "", artifact_id,
                               {**config, "optimizer_history": history}, classification_report(labels[mask], logits[mask].argmax(1), manifest.classes, config))
    write_json(directory / "training_report.json", report)
    # Label/split context only: no feature matrix or feature artifact is created.
    context = FeatureArtifact("", manifest.dataset_id, manifest.dataset_version_id, key, {"pipeline": "image_classifier_v2"}, 0, "",
                              [s.sample_id for s in manifest.samples], [s.label for s in manifest.samples], [s.split for s in manifest.samples])
    return context, model_artifact, report, logits
