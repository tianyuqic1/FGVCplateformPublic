import assert from "node:assert/strict";
import { publishedInferenceModels, inferenceModelLabel } from "../src/api/inferenceModels.js";
const model = { id: "model-1", status: "production", backboneKey: "dinov3_vits16_lvd1689m", metrics: { accuracy: 0.927 }, artifacts: [{ artifact_type: "full_onnx" }] };
assert.equal(inferenceModelLabel(model), "vit_dinov3s · acc 92.7% · ONNX-FP32");
assert.deepEqual(publishedInferenceModels([model, ...["candidate", "staging", "archived", "failed", undefined].map(status => ({ ...model, status }))]).map(m => m.modelVersionId), ["model-1"]);
assert.equal(inferenceModelLabel({ ...model, metrics: { accuracy: null }, artifacts: [] }), "vit_dinov3s · acc 待评估");
console.log("Published inference model filtering and labels passed");
