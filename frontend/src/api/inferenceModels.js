import { listModelVersions } from "./modelVersions.js";

export function publishedInferenceModels(models) {
  return models.filter(model => model.status === "production").map(model => ({
    ...model,
    modelVersionId: model.id,
  }));
}

export function inferenceModelLabel(model) {
  const name = (model.name || model.backboneKey || "未命名模型")
    .replace(/^dinov3_vit([sbl])(?:16)?(?:_.*)?$/i, "vit_dinov3$1");
  const accuracy = model.metrics?.accuracy;
  const acc = accuracy != null && accuracy !== "" && Number.isFinite(Number(accuracy))
    ? `acc ${(Number(accuracy) * 100).toFixed(1)}%` : "acc 待评估";
  const formats = [...new Set((model.artifacts || []).filter(a => ["head_onnx", "full_onnx"].includes(a.artifact_type) && a.metadata?.model_version_id === model.id).map(a => a.metadata?.precision || "FP32"))].sort().reverse();
  return `${name} · ${model.releaseVersion || "历史发布"} · ${[acc, formats.length ? `ONNX ${formats.join(" / ")}` : ""].filter(Boolean).join(" · ")}`;
}

export async function listInferenceModels() {
  return publishedInferenceModels(await listModelVersions({ status: "production" }));
}
