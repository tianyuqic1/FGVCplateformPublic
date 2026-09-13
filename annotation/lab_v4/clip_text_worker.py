"""Offline text encoder. Mounted inputs contain public class prompts only."""

import contextlib
import json
import sys


def validate_texts(job):
    if not isinstance(job, dict) or set(job) != {"texts"}:
        raise ValueError("invalid text job")
    texts = job["texts"]
    if not isinstance(texts, list) or not 1 <= len(texts) <= 4096:
        raise ValueError("invalid text count")
    if any(not isinstance(t, str) or not t.strip() or len(t) > 256 for t in texts):
        raise ValueError("invalid prompt")
    return texts


def encode(texts):
    import torch
    import torch.nn.functional as F
    from transformers import CLIPModel, CLIPTokenizer

    torch.set_num_threads(2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = CLIPTokenizer.from_pretrained("/models/clip", local_files_only=True)
    model = (
        CLIPModel.from_pretrained("/models/clip", local_files_only=True)
        .to(device)
        .eval()
    )
    rows = []
    for start in range(0, len(texts), 32):
        inputs = tokenizer(
            texts[start : start + 32],
            padding=True,
            truncation=False,
            return_tensors="pt",
        )
        if (
            inputs["input_ids"].shape[1]
            > model.config.text_config.max_position_embeddings
        ):
            raise ValueError("prompt exceeds CLIP context; no silent truncation")
        with torch.inference_mode():
            features = model.get_text_features(
                **{k: v.to(device) for k, v in inputs.items()}
            )
        if not isinstance(features, torch.Tensor):
            features = features.pooler_output
        rows.extend(F.normalize(features.float(), dim=-1).cpu().tolist())
    return rows


if __name__ == "__main__":
    raw = sys.stdin.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("text job too large")
    texts = validate_texts(json.loads(raw))
    with contextlib.redirect_stdout(sys.stderr):
        rows = encode(texts)
    print(json.dumps({"vectors": rows}, allow_nan=False))
