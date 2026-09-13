"""Network-disabled, one-model GPU worker. stdin is a whitelisted job only."""

import importlib.util
import json
import sys

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


def architecture(name):
    spec = importlib.util.spec_from_file_location(
        "restoration_arch", "/models/sources/" + name + ".py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiled(model, x, scale=1, tile=256, overlap=32):
    _, _, h, w = x.shape
    pad_h = (-h) % 8
    pad_w = (-w) % 8
    x = F.pad(x, (0, pad_w, 0, pad_h), mode="replicate")
    hp, wp = x.shape[-2:]
    out = torch.zeros((1, 3, hp * scale, wp * scale), device=x.device)
    count = torch.zeros_like(out)
    ys = list(range(0, max(1, hp - tile + 1), tile - overlap))
    xs = list(range(0, max(1, wp - tile + 1), tile - overlap))
    ys = sorted(set(ys + [max(0, hp - tile)]))
    xs = sorted(set(xs + [max(0, wp - tile)]))
    for y in ys:
        for z in xs:
            patch = x[:, :, y : y + tile, z : z + tile]
            pred = model(patch)
            ph, pw = pred.shape[-2:]
            out[:, :, y * scale : y * scale + ph, z * scale : z * scale + pw] += pred
            count[:, :, y * scale : y * scale + ph, z * scale : z * scale + pw] += 1
    return (out / count.clamp_min(1))[:, :, : h * scale, : w * scale]


def main(job):
    torch.set_num_threads(2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    op = job["operation"]
    if op == "clip_text":
        from clip_text_worker import encode
        return {"vectors": encode(job["texts"])}
    if op in ("clip", "clip_batch"):
        from transformers import CLIPModel, CLIPProcessor

        processor = CLIPProcessor.from_pretrained("/models/clip", local_files_only=True)
        model = (
            CLIPModel.from_pretrained("/models/clip", local_files_only=True)
            .to(device)
            .eval()
        )
        count = job.get("count", 1)
        if type(count) is not int or not 1 <= count <= 512:
            raise ValueError("invalid embedding batch")
        vectors = []
        for start in range(0, count, 8):
            images = []
            for i in range(start, min(start + 8, count)):
                with Image.open(
                    "/job/" + (str(i) + ".png" if op == "clip_batch" else "input.png")
                ) as source:
                    images.append(source.convert("RGB"))
            inputs = processor(images=images, return_tensors="pt")
            with torch.inference_mode():
                features = model.get_image_features(
                    pixel_values=inputs["pixel_values"].to(device)
                )
            if not isinstance(features, torch.Tensor):
                features = features.pooler_output
            features = F.normalize(features.float(), dim=-1)
            vectors.extend(features.cpu().tolist())
        return {"vector": vectors[0]} if op == "clip" else {"vectors": vectors}
    im = Image.open("/job/input.png").convert("RGB")
    if op == "sam2":
        from transformers import Sam2Model, Sam2Processor

        sam_path = job.get("sam_path", "/sam")
        processor = Sam2Processor.from_pretrained(sam_path, local_files_only=True)
        model = (
            Sam2Model.from_pretrained(sam_path, local_files_only=True).to(device).eval()
        )
        box = job["box"]
        inputs = processor(images=im, input_boxes=[[box]], return_tensors="pt")
        sizes = inputs["original_sizes"].cpu()
        inputs = {
            k: v.to(device) if isinstance(v, torch.Tensor) else v
            for k, v in inputs.items()
        }
        with torch.inference_mode():
            pred = model(**inputs, multimask_output=True)
        masks = processor.post_process_masks(
            pred.pred_masks.float().cpu(), sizes, binarize=True
        )[0]
        scores = pred.iou_scores.float().cpu()[0, 0]
        best = int(scores.argmax())
        mask = masks[0, best].numpy().astype(bool)
        area = float(mask.mean())
        if float(scores[best]) < 0.5 or not 0.004 <= area <= 0.95:
            raise ValueError("SAM mask guard")
        ys, xs = np.where(mask)
        return {
            "bounds": [
                int(xs.min()),
                int(ys.min()),
                int(xs.max() + 1),
                int(ys.max() + 1),
            ],
            "score": float(scores[best]),
            "area": area,
        }
    if op not in ("sr_x2", "lowlight", "denoise", "deblur_motion", "deblur_defocus"):
        raise ValueError("unregistered tool")
    if op == "sr_x2":
        model = architecture("swinir").SwinIR(
            upscale=2,
            in_chans=3,
            img_size=48,
            window_size=8,
            img_range=1.0,
            depths=[6] * 6,
            embed_dim=180,
            num_heads=[6] * 6,
            mlp_ratio=2,
            upsampler="pixelshuffle",
            resi_connection="1conv",
        )
    elif op == "lowlight":
        model = architecture("retinex").RetinexFormer(
            in_channels=3, out_channels=3, n_feat=40, stage=1, num_blocks=[1, 2, 2]
        )
    else:
        model = architecture("restormer").Restormer(
            inp_channels=3,
            out_channels=3,
            dim=48,
            num_blocks=[4, 6, 6, 8],
            num_refinement_blocks=4,
            heads=[1, 2, 4, 8],
            ffn_expansion_factor=2.66,
            bias=False,
            LayerNorm_type="BiasFree" if op == "denoise" else "WithBias",
            dual_pixel_task=False,
        )
    checkpoint = torch.load(
        "/models/weights/" + op + ".pth", map_location="cpu", weights_only=True
    )
    state = checkpoint.get("params_ema", checkpoint.get("params", checkpoint))
    model.load_state_dict(state, strict=True)
    model = model.to(device).eval()
    x = (
        torch.from_numpy(np.asarray(im, dtype=np.float32).copy() / 255)
        .permute(2, 0, 1)
        .unsqueeze(0)
        .to(device)
    )
    with torch.inference_mode():
        y = tiled(model, x, 2 if op == "sr_x2" else 1)
    if not torch.isfinite(y).all():
        raise ValueError("nonfinite restoration")
    outside = float(((y < -0.1) | (y > 1.1)).float().mean())
    if outside > 0.15:
        raise ValueError("excessive output clipping")
    a = (y.clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)
    Image.fromarray(a).save("/job/output.png")
    return {"width": a.shape[1], "height": a.shape[0], "clipped_fraction": outside}


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **main(json.load(sys.stdin))}), flush=True)
    except Exception as error:
        print(
            json.dumps(
                {"ok": False, "error": type(error).__name__ + ": " + str(error)[:300]}
            ),
            flush=True,
        )
        sys.exit(1)
