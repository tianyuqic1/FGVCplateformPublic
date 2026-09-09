import { fetchJson, withTimeout } from "./http.js";

function numberOrNull(value) {
  return value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
}

export function normalizeModelVersion(raw) {
  return {
    id: raw?.model_version_id ?? raw?.id ?? "",
    modelKey: raw?.model_key ?? "",
    name: raw?.name ?? raw?.model_key ?? "未命名模型",
    description: raw?.description ?? "",
    datasetId: raw?.dataset_id ?? "",
    datasetName: raw?.dataset_name ?? "数据集",
    datasetVersionId: raw?.dataset_version_id ?? "",
    datasetVersionKey: raw?.dataset_version_key ?? raw?.dataset_version_id ?? "",
    trainingRunId: raw?.training_run_id ?? "",
    status: raw?.status ?? "candidate",
    aliases: Array.isArray(raw?.aliases) ? raw.aliases : [],
    backboneKey: raw?.backbone_key ?? "",
    architecture: raw?.architecture ?? "",
    pretrainingMethod: raw?.pretraining_method ?? "",
    pretrainingDataset: raw?.pretraining_dataset ?? "",
    inputSize: numberOrNull(raw?.input_size),
    featureDim: numberOrNull(raw?.feature_dim),
    parameterCount: numberOrNull(raw?.parameter_count),
    pooling: raw?.pooling ?? "",
    headType: raw?.head_type ?? "",
    metrics: raw?.metrics ?? {},
    evaluationContext: raw?.evaluation_context ?? {},
    artifacts: Array.isArray(raw?.artifacts) ? raw.artifacts : [],
    events: Array.isArray(raw?.events) ? raw.events : [],
    createdAt: raw?.created_at ?? null,
    updatedAt: raw?.updated_at ?? null,
  };
}

function filtersQuery(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) params.set(key, String(value));
  });
  return params.size ? `?${params}` : "";
}

export async function listModelVersions(filters = {}) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/model-versions${filtersQuery(filters)}`, { signal });
    return (payload?.model_versions ?? []).map(normalizeModelVersion);
  });
}

export async function getModelVersion(id) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/model-versions/${encodeURIComponent(id)}`, { signal });
    return normalizeModelVersion(payload?.model_version);
  });
}

export async function compareModelVersions(ids) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/model-version-comparisons", {
      method: "POST",
      body: { model_version_ids: ids },
      signal,
    });
    const comparison = payload?.comparison ?? {};
    return {
      versions: (comparison?.model_versions ?? []).map(normalizeModelVersion),
      comparable: Boolean(comparison?.comparable),
      warnings: Array.isArray(comparison?.warnings) ? comparison.warnings : [],
      datasetVersionId: comparison?.dataset_version_id ?? "",
      protocolFingerprint: comparison?.protocol_fingerprint ?? "",
    };
  }, 8000);
}

export async function promoteModelVersion(id, targetStatus, reason, actor = "local-user") {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/model-versions/${encodeURIComponent(id)}/promote`, {
      method: "POST",
      body: { target_status: targetStatus, actor, reason },
      signal,
    });
    return normalizeModelVersion(payload?.model_version);
  }, 125000);
}

export async function archiveModelVersion(id, reason, actor = "local-user") {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/model-versions/${encodeURIComponent(id)}/archive`, {
      method: "POST",
      body: { actor, reason },
      signal,
    });
    return normalizeModelVersion(payload?.model_version);
  });
}

export async function setModelAlias(datasetId, alias, modelVersionId, reason, actor = "local-user") {
  return withTimeout(async (signal) => fetchJson(`/api/model-aliases/${encodeURIComponent(alias)}`, {
    method: "PUT",
    body: { dataset_id: datasetId, model_version_id: modelVersionId, actor, reason },
    signal,
  }));
}
