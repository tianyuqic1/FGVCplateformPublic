#!/usr/bin/env python3
"""Benchmark Fine-R1 as a candidate-constrained review assistant."""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import time
from pathlib import Path
from typing import Any

import torch
from qwen_vl_utils import process_vision_info
from transformers import (
    AutoProcessor,
    BitsAndBytesConfig,
    Qwen2_5_VLForConditionalGeneration,
)


ANSWER_PATTERN = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)
CHAIN_OF_THOUGHT_TEMPLATE = (
    "You are reviewing a fine-grained image classification sample.\n"
    "Dataset context: {dataset_summary}\n"
    "Question: {question}\n"
    "Candidate labels: {options}\n"
    "First inspect the visible object independently. Compare discriminative shape, color, texture, "
    "parts and context against every candidate. Candidate order is randomized and conveys no rank. "
    "Do not invent a label outside the candidate list. If the image is imperfect, still select the "
    "best supported candidate while stating the uncertainty. Keep the reasoning concise: use at most "
    "180 English words inside <think>.\n"
    "Return exactly:\n<think>visual observations, candidate comparison, uncertainty</think>\n"
    "<answer>one candidate label exactly as written</answer>"
)
DIRECT_ANSWER_TEMPLATE = (
    "Given the question: {question}, select exactly one label from the candidate "
    "labels in {options}. Return only <answer>candidate label</answer>, without "
    "reasoning or any label outside the candidate list."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, action="append", required=True)
    parser.add_argument("--samples-per-file", type=int, default=10)
    parser.add_argument("--precision", choices=("bf16", "nf4"), required=True)
    parser.add_argument(
        "--prompt-style",
        choices=("cot", "direct"),
        default="cot",
    )
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dataset-summary", default="A fine-grained image classification benchmark.")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize_label(value: str | None) -> str | None:
    if value is None:
        return None
    possessive_normalized = re.sub(
        r"\b(\w+)[’']s\b",
        r"\1",
        value.lower(),
        flags=re.UNICODE,
    )
    punctuation_normalized = re.sub(
        r"[^\w]+",
        " ",
        possessive_normalized.replace("_", " "),
        flags=re.UNICODE,
    )
    return " ".join(punctuation_normalized.split())


def load_samples(args: argparse.Namespace) -> list[dict[str, Any]]:
    rng = random.Random(args.seed)
    samples: list[dict[str, Any]] = []
    for prompt_file in args.prompt_file:
        rows = [
            json.loads(line)
            for line in prompt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rng.shuffle(rows)
        selected = rows[: args.samples_per_file]
        for row in selected:
            source_path = Path(row["image_path"])
            if source_path.is_absolute():
                row["resolved_image_path"] = str(source_path)
            else:
                image_suffix = row["image_path"].split("images/", maxsplit=1)[-1]
                row["resolved_image_path"] = str(args.dataset_root / "images" / image_suffix)
            shuffled_options = list(row["options"])
            rng.shuffle(shuffled_options)
            row["options"] = shuffled_options
            row["prompt_source"] = prompt_file.name
            samples.append(row)
    return samples


def load_model(args: argparse.Namespace):
    kwargs: dict[str, Any] = {
        "device_map": "auto",
        "low_cpu_mem_usage": True,
    }
    if args.precision == "bf16":
        kwargs["dtype"] = torch.bfloat16
    else:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.model_path,
        **kwargs,
    )
    model.eval()
    processor = AutoProcessor.from_pretrained(args.model_path)
    processor.tokenizer.padding_side = "left"
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started
    return model, processor, load_seconds


def run_one(
    *,
    model,
    processor,
    sample: dict[str, Any],
    max_new_tokens: int,
    prompt_style: str,
) -> dict[str, Any]:
    image_path = Path(sample["resolved_image_path"])
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    template = (
        CHAIN_OF_THOUGHT_TEMPLATE
        if prompt_style == "cot"
        else DIRECT_ANSWER_TEMPLATE
    )
    prompt = template.format(
        question=sample["question"],
        options=json.dumps(sample["options"], ensure_ascii=False),
        dataset_summary=sample.get("dataset_summary") or "A fine-grained image classification benchmark.",
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": f"file://{image_path}"},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(next(model.parameters()).device)

    torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            use_cache=True,
        )
    torch.cuda.synchronize()
    latency_seconds = time.perf_counter() - started

    input_tokens = inputs.input_ids.shape[-1]
    generated_tokens = generated.shape[-1] - input_tokens
    output = processor.decode(
        generated[0, input_tokens:],
        skip_special_tokens=True,
    )
    match = ANSWER_PATTERN.search(output)
    normalized_options = {normalize_label(str(option)) for option in sample["options"]}
    bare_answer = output.strip()
    if match:
        answer = match.group(1).strip()
        response_format = "tagged"
    elif normalize_label(bare_answer) in normalized_options:
        answer = bare_answer
        response_format = "bare_candidate"
    else:
        answer = None
        response_format = "invalid"
    normalized_answer = normalize_label(answer)
    normalized_gt = normalize_label(str(sample["ground_truth"]))

    return {
        "prompt_source": sample["prompt_source"],
        "image_path": str(image_path),
        "ground_truth": sample["ground_truth"],
        "options": sample["options"],
        "answer": answer,
        "correct": normalized_answer == normalized_gt,
        "format_compliant": normalized_answer in normalized_options,
        "response_format": response_format,
        "latency_seconds": latency_seconds,
        "input_tokens": input_tokens,
        "generated_tokens": generated_tokens,
        "output": output,
    }


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
    return ordered[index]


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    samples = load_samples(args)
    for sample in samples:
        sample["dataset_summary"] = args.dataset_summary
    model, processor, load_seconds = load_model(args)
    model_memory_gib = torch.cuda.memory_allocated() / (1024**3)
    torch.cuda.reset_peak_memory_stats()

    results = []
    for index, sample in enumerate(samples, start=1):
        result = run_one(
            model=model,
            processor=processor,
            sample=sample,
            max_new_tokens=args.max_new_tokens,
            prompt_style=args.prompt_style,
        )
        results.append(result)
        print(
            f"[{index}/{len(samples)}] correct={result['correct']} "
            f"format={result['format_compliant']} "
            f"latency={result['latency_seconds']:.2f}s "
            f"answer={result['answer']!r}",
            flush=True,
        )

    latencies = [float(result["latency_seconds"]) for result in results]
    generated_tokens = [int(result["generated_tokens"]) for result in results]
    summary = {
        "model_path": str(args.model_path),
        "precision": args.precision,
        "prompt_style": args.prompt_style,
        "samples": len(results),
        "accuracy": sum(bool(result["correct"]) for result in results) / len(results),
        "format_compliance_rate": (
            sum(bool(result["format_compliant"]) for result in results) / len(results)
        ),
        "load_seconds": load_seconds,
        "model_memory_gib": model_memory_gib,
        "inference_peak_memory_gib": torch.cuda.max_memory_allocated() / (1024**3),
        "latency_mean_seconds": statistics.mean(latencies),
        "latency_median_seconds": statistics.median(latencies),
        "latency_p95_seconds": percentile(latencies, 0.95),
        "generated_tokens_mean": statistics.mean(generated_tokens),
        "max_new_tokens": args.max_new_tokens,
        "seed": args.seed,
        "dataset_summary": args.dataset_summary,
        "by_prompt_source": {
            source: {
                "samples": len(source_results),
                "accuracy": sum(bool(item["correct"]) for item in source_results) / len(source_results),
                "format_compliance_rate": (
                    sum(bool(item["format_compliant"]) for item in source_results) / len(source_results)
                ),
            }
            for source in sorted({str(item["prompt_source"]) for item in results})
            if (source_results := [item for item in results if item["prompt_source"] == source])
        },
    }
    payload = {"summary": summary, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
