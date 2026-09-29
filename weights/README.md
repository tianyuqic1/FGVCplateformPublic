# Pretrained weights

Only immutable, redistribution-approved pretrained weights belong here. Git LFS tracks the approved DINOv3, ImageNet, and iNat2021-mini distilled `*.safetensors` files; `manifest.json` is the canonical identity and promotion input. Other training outputs, features, reports, datasets, and uploads must go to ArtifactStore instead.

The included DINOv3 ViT-S file and the derived iNat MobileNetV3 weight remain subject to the DINOv3 License copied alongside each. The iNat student also retains the torchvision BSD-3-Clause notice for its ImageNet initialization. The two timm ImageNet files are Apache-2.0 and their upstream license is copied into `weights/pretrained/imagenet/LICENSE`. Runtime containers do not run `git lfs pull`; the `pretrained-weight-init` service verifies every manifest entry and promotes the same SHA-256 object into the S3-compatible ArtifactStore.

Weights that are missing, corrupt, or still Git LFS pointers are reported as explicit warnings and do **not** block stack startup. A task selecting a missing managed weight fails explicitly; no substitute initialization is silently used. Run `scripts/check-weights.sh` (add `--strict` for a non-zero exit) when weight availability matters.

For the outward-facing publishing bundle (release copies, the DINOv3 ViT-B/L family, model cards and license texts), see `pretrained/` at the repository root — that directory is unrelated to runtime weight resolution and is not read by the platform.
