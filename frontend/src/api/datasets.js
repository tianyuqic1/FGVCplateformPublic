import { APIError, apiBaseUrl, apiErrorFromResponse, fetchJson, withTimeout } from "./http.js";

function firstNumber(...values) {
  const value = values.find((item) => Number.isFinite(Number(item)));
  return value === undefined ? 0 : Number(value);
}

function normalizeStatus(value) {
  if (["production", "calibrating", "training"].includes(value)) return value;
  if (value === "ready") return "ready";
  if (["published", "active"].includes(value)) return "production";
  if (["needs_attention", "needs_calibration", "pending_calibration", "evaluating"].includes(value)) return "calibrating";
  return "training";
}

export function extractDatasetList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.datasets)) return payload.datasets;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

export function extractDataset(payload) {
  return payload?.dataset ?? payload?.data ?? payload;
}

export function extractImportedDataset(payload) {
  return {
    dataset: payload?.dataset ?? null,
    version: payload?.version ?? null,
    upload: payload?.upload ?? null,
  };
}

export function normalizeDatasetCard(raw = {}) {
  return {
    task: raw?.task ?? "image_classification",
    domain: raw?.domain ?? "general",
    summary: raw?.summary ?? "",
    classCount: firstNumber(raw?.classCount, raw?.class_count),
    sampleCount: firstNumber(raw?.sampleCount, raw?.sample_count),
    classPreview: raw?.classPreview ?? raw?.class_preview ?? [],
    classPreviewTruncated: Boolean(raw?.classPreviewTruncated ?? raw?.class_preview_truncated),
    splitTotals: raw?.splitTotals ?? raw?.split_totals ?? {},
    knownConfusions: raw?.knownConfusions ?? raw?.known_confusions ?? [],
    oodPolicy: raw?.oodPolicy ?? raw?.ood_policy ?? "",
    reviewGuidance: raw?.reviewGuidance ?? raw?.review_guidance ?? "",
    generatedFrom: raw?.generatedFrom ?? raw?.generated_from ?? "",
  };
}

export function normalizeDataset(raw) {
  const classList = raw?.classes ?? raw?.class_names ?? raw?.taxonomy?.classes;
  const classCount = Array.isArray(classList) && classList.length ? classList.length : firstNumber(raw?.class_count, raw?.num_classes, raw?.classes);
  const imageCount = firstNumber(raw?.images, raw?.image_count, raw?.sample_count, raw?.num_samples);
  const latestVersion =
    raw?.version ??
    raw?.datasetVersionId ??
    raw?.dataset_version_id ??
    raw?.latest_version_id ??
    raw?.current_version_id ??
    null;

  return {
    id: raw?.id ?? raw?.dataset_id ?? raw?.slug ?? latestVersion ?? "untracked-dataset",
    name: raw?.name ?? raw?.display_name ?? raw?.dataset_id ?? "未命名数据集",
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? latestVersion,
    latestVersionId: raw?.latestVersionId ?? raw?.latest_version_id ?? latestVersion,
    classNames: Array.isArray(classList) ? classList : [],
    classes: classCount,
    images: imageCount,
    version: latestVersion,
    modelVersion: raw?.modelVersion ?? raw?.model_version ?? raw?.production_model_version_id ?? null,
    productionModelVersionId: raw?.productionModelVersionId ?? raw?.production_model_version_id ?? null,
    featureArtifactId: raw?.featureArtifactId ?? raw?.feature_artifact_id ?? null,
    thresholdStrategyId: raw?.thresholdStrategyId ?? raw?.threshold_strategy_id ?? null,
    oodStressAssetId: raw?.oodStressAssetId ?? raw?.ood_stress_asset_id ?? null,
    status: normalizeStatus(raw?.status ?? raw?.readiness_status),
    quality: firstNumber(raw?.quality, raw?.quality_score, raw?.readiness?.quality, 0),
    coverage: firstNumber(raw?.coverage, raw?.expected_coverage, raw?.readiness?.coverage, 0),
    oodRecall: firstNumber(raw?.oodRecall, raw?.ood_recall, raw?.readiness?.ood_recall, 0),
    readiness: raw?.readiness ?? {},
    splitCounts: raw?.splitCounts ?? raw?.split_counts ?? {},
    datasetCard: raw?.datasetCard ? normalizeDatasetCard(raw.datasetCard) : raw?.dataset_card ? normalizeDatasetCard(raw.dataset_card) : null,
    previewSamples: normalizeSamplePreviews(raw?.previewSamples ?? raw?.preview_samples ?? raw?.sample_previews ?? []),
    versionNumber: firstNumber(raw?.version_number, raw?.versionNumber),
    pendingCandidateCount: firstNumber(raw?.pending_candidate_count),
    versions: Array.isArray(raw?.versions) ? raw.versions.map(normalizeDatasetVersion) : [],
    description: raw?.description ?? raw?.summary ?? "数据集已同步，详细描述待补充。",
  };
}

export function normalizeDatasetVersion(raw) {
  return {
    id: raw.dataset_version_id,
    number: Number(raw.version_number),
    parentId: raw.parent_version_id || null,
    sourceType: raw.source_type,
    images: Number(raw.sample_count ?? 0),
    classes: Number(raw.class_count ?? 0),
    splitCounts: raw.split_counts ?? {},
    readiness: raw.readiness ?? {},
    changes: raw.change_summary ?? {},
    hasWeights: raw.has_weights === true,
    modelCount: Number(raw.model_count ?? 0),
    models: raw.models ?? [],
    trainingStatus: raw.training_status ?? "untrained",
    createdAt: raw.created_at,
  };
}

export function datasetSnapshots(datasets) {
  return datasets.flatMap(dataset => dataset.versions?.length
    ? dataset.versions.map(version => ({ ...dataset, datasetVersionId: version.id, version: version.id, versionNumber: version.number, images: version.images, classes: version.classes, splitCounts: version.splitCounts, hasWeights: version.hasWeights, models: version.models, readiness: version.readiness }))
    : [dataset]);
}

export function datasetVersionOptions(datasets) {
  return datasets.flatMap(dataset => dataset.versions?.length
    ? dataset.versions.map(version => ({ value: version.id, label: `${dataset.name} · v${version.number}`, detail: version.hasWeights ? `已有 ${version.modelCount} 份权重` : "暂无权重", meta: `${version.classes} 类 · ${version.images} 张图片` }))
    : dataset.datasetVersionId ? [{ value: dataset.datasetVersionId, label: dataset.name, detail: dataset.datasetVersionId, meta: `${dataset.classes} 类 · ${dataset.images} 张图片` }] : []);
}

export function normalizeSamplePreviews(samples) {
  if (!Array.isArray(samples)) return [];
  return samples.map((sample) => ({
    sampleId: sample?.sampleId ?? sample?.sample_id ?? null,
    label: sample?.label ?? "unknown",
    split: sample?.split ?? "unknown",
    imageUrl: sample?.imageUrl ?? sample?.image_url ?? null,
  }));
}

export async function listDatasets() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/datasets", { signal });
    return extractDatasetList(payload).map(normalizeDataset);
  });
}

export async function getDataset(datasetId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/datasets/${encodeURIComponent(datasetId)}`, { signal });
    return normalizeDataset(extractDataset(payload));
  });
}


export async function listDatasetSamplePreviews(datasetVersionId, limit = 6) {
  return withTimeout(async (signal) => {
    const params = new URLSearchParams({ limit: String(limit) });
    const payload = await fetchJson(`/api/dataset-versions/${encodeURIComponent(datasetVersionId)}/sample-previews?${params.toString()}`, { signal });
    return normalizeSamplePreviews(payload?.samples);
  });
}

export async function importImagefolder(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/datasets/import-imagefolder", { method: "POST", body: input, signal });
    return extractImportedDataset(payload);
  }, 10000);
}

export async function listDatasetImports() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/dataset-imports", { signal });
    return Array.isArray(payload?.jobs) ? payload.jobs : [];
  }, 5000);
}

export async function uploadImagefolder(input) {
  const { signal, onProgress = () => {} } = input;
  signal?.throwIfAborted();
  if (!input.files?.length || input.files.length > 100000) throw new Error("请选择 1–100000 张图片");
  let totalBytes = 0;
  for (const file of input.files) {
    if (file.size > 32 * 1024 * 1024) throw new Error("单张图片上限 32 MiB");
    totalBytes += file.size;
  }
  if (totalBytes > 5 * 1024 * 1024 * 1024) throw new Error("数据集上限 5 GiB");

  const form = new FormData();
  form.append("name", input.name);
  form.append("request_id", input.request_id);
  // Yield in bounded batches so the spinner, progress and cancel button paint
  // even for large folders. Files stay as Blob references; no base64 copies.
  for (let start = 0; start < input.files.length; start += 100) {
    onProgress({ stage: "preparing", percent: Math.round(start / input.files.length * 100) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    signal?.throwIfAborted();
    for (let index = start; index < Math.min(start + 100, input.files.length); index++) {
      const file = input.files[index];
      form.append("files", file, file.webkitRelativePath || file.name);
    }
  }

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", abort);
    const fail = (error) => { cleanup(); reject(error); };
    xhr.open("POST", `${apiBaseUrl()}/api/datasets/upload-imagefolder`);
    xhr.timeout = 30 * 60 * 1000;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (event) => {
      onProgress({ stage: "uploading", percent: event.lengthComputable ? Math.round(event.loaded / event.total * 100) : null });
    };
    xhr.upload.onload = () => onProgress({ stage: "persisting", percent: null });
    xhr.onload = () => {
      cleanup();
      let payload;
      try { payload = JSON.parse(xhr.responseText); } catch { reject(new Error(`上传响应异常（${xhr.status}），请查看后台导入任务后再重试。`)); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        const message = payload?.error?.message ?? payload?.detail?.message ?? payload?.detail;
        reject(new APIError(typeof message === "string" ? message : `上传失败（${xhr.status}）`, {
          status: xhr.status,
          code: payload?.error?.code,
          requestId: payload?.error?.request_id || xhr.getResponseHeader("X-Request-ID") || "",
          method: "POST",
          path: "/api/datasets/upload-imagefolder",
          payload,
        }));
        return;
      }
      if (xhr.status === 202 && payload?.job?.id) { resolve({ job: payload.job }); return; }
      // Keep compatibility with the older Python API while deployments roll out.
      if (xhr.status === 201) { resolve(extractImportedDataset(payload)); return; }
      reject(new Error("上传响应缺少任务编号，请查看后台导入任务。"));
    };
    xhr.onerror = () => fail(new Error("上传连接中断，请查看后台导入任务后再重试。"));
    xhr.ontimeout = () => fail(new Error("上传超时，请查看后台导入任务后再重试。"));
    xhr.onabort = () => fail(new DOMException("已停止上传；若服务器已接收，请查看后台导入任务。", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { fail(signal.reason); return; }
    onProgress({ stage: "uploading", percent: 0 });
    xhr.send(form);
  });
}

export async function listTrainingCandidates(datasetId) {
  const payload = await fetchJson(`/api/datasets/${encodeURIComponent(datasetId)}/training-candidates`);
  return payload.candidates ?? [];
}

export async function expandDataset(datasetId, { baseVersionId, requestId, files = [], feedbackIds = [] }) {
  const form = new FormData();
  form.append("base_version_id", baseVersionId);
  form.append("request_id", requestId);
  form.append("feedback_ids", JSON.stringify(feedbackIds));
  files.forEach(file => form.append("files", file, file.webkitRelativePath || file.name));
  const path = `/api/datasets/${encodeURIComponent(datasetId)}/versions`;
  const response = await fetch(`${apiBaseUrl()}${path}`, { method: "POST", body: form });
  if (!response.ok) throw await apiErrorFromResponse(response, { method: "POST", path, fallback: "生成版本失败" });
  const payload = await response.json();
  return extractImportedDataset(payload);
}
