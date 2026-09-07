# Pretrained weights

Only immutable, redistribution-approved upstream pretrained weights belong here. Git LFS tracks the DINOv3 `*.safetensors` file; `manifest.json` is the canonical identity and promotion input. Training outputs, features, reports, datasets, and uploads must go to ArtifactStore instead.

The included DINOv3 ViT-S file remains subject to the DINOv3 License copied alongside it. Runtime containers do not run `git lfs pull`; deployment verifies the manifest and promotes the same SHA-256 object into the S3-compatible ArtifactStore.
