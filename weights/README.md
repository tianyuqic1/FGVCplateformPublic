# Pretrained weights

Only immutable, redistribution-approved upstream pretrained weights belong here. Git LFS tracks the approved DINOv3 and ImageNet `*.safetensors` files; `manifest.json` is the canonical identity and promotion input. Training outputs, features, reports, datasets, and uploads must go to ArtifactStore instead.

The included DINOv3 ViT-S file remains subject to the DINOv3 License copied alongside it. The two timm ImageNet files are Apache-2.0 and their upstream license is copied into `pretrained/imagenet/LICENSE`. Runtime containers do not run `git lfs pull`; deployment verifies every manifest entry and promotes the same SHA-256 object into the S3-compatible ArtifactStore.
