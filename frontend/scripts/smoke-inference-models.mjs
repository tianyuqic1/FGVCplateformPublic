import assert from "node:assert/strict";
import { publishedInferenceModels, inferenceModelLabel } from "../src/api/inferenceModels.js";
const model = { id: "model-1", name: "鸟类实验", releaseVersion: "v1.2.0", status: "production", backboneKey: "dinov3_vits16_lvd1689m", metrics: { accuracy: 0.927 }, artifacts: [{ artifact_type: "full_onnx", metadata: { model_version_id: "model-1", precision: "FP32" } }] };
assert.equal(inferenceModelLabel(model), "鸟类实验 · v1.2.0 · acc 92.7% · ONNX FP32");
assert.deepEqual(publishedInferenceModels([model, ...["candidate", "staging", "archived", "failed", undefined].map(status => ({ ...model, status }))]).map(m => m.modelVersionId), ["model-1"]);
assert.equal(inferenceModelLabel({ ...model, releaseVersion: "", metrics: { accuracy: null }, artifacts: [] }), "鸟类实验 · 历史发布 · acc 待评估");
assert.match(inferenceModelLabel({ ...model, artifacts: [...model.artifacts, { artifact_type: "full_onnx", metadata: { model_version_id: "model-1", precision: "FP16" } }] }), /FP32 \/ FP16/);
console.log("Published inference model filtering and labels passed");
