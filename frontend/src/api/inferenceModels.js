import { listModelVersions } from "./modelVersions.js";

export function publishedInferenceModels(models) {
  return models.filter(model => model.status === "production").map(model => ({
    ...model,
    modelVersionId: model.id,
  }));
}

export function inferenceModelLabel(model) {
  const name = (model.backboneKey || model.name || "未命名模型")
    .replace(/^dinov3_vit([sbl])(?:16)?(?:_.*)?$/i, "vit_dinov3$1");
  const accuracy = model.metrics?.accuracy;
  const acc = accuracy != null && accuracy !== "" && Number.isFinite(Number(accuracy))
    ? `acc ${(Number(accuracy) * 100).toFixed(1)}%` : "acc 待评估";
  const onnx = model.artifacts?.find(a => ["head_onnx", "full_onnx"].includes(a.artifact_type));
  // Published ONNX exports in this project use float32 tensors.
  const format = onnx ? `ONNX-${onnx.metadata?.precision || "FP32"}` : "";
  return `${name} · ${[acc, format].filter(Boolean).join(" · ")}`;
}

export async function listInferenceModels() {
  return publishedInferenceModels(await listModelVersions({ status: "production" }));
}
