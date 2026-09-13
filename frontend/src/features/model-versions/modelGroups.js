export function modelScope(model) {
  return model.datasetId && model.datasetVersionId ? JSON.stringify([model.datasetId, model.datasetVersionId]) : null;
}

export function groupModels(models) {
  const datasets = new Map();
  for (const model of models) {
    // Unknown identities must never be aggregated by a display name.
    const datasetKey = model.datasetId || `unknown:${model.id}`;
    if (!datasets.has(datasetKey)) datasets.set(datasetKey, { id: datasetKey, name: model.datasetName || "未记录数据集", models: [], versions: new Map() });
    const dataset = datasets.get(datasetKey);
    dataset.models.push(model);
    const key = modelScope(model) || `unknown:${model.id}`;
    if (!dataset.versions.has(key)) dataset.versions.set(key, { key, id: model.datasetVersionId, number: model.datasetVersionNumber, models: [] });
    dataset.versions.get(key).models.push(model);
  }
  return [...datasets.values()].map(dataset => ({ ...dataset, versions: [...dataset.versions.values()].sort((a, b) => (b.number || 0) - (a.number || 0)) }));
}

export function toggleModelSelection(current, model, models) {
  const scope = modelScope(model);
  if (!scope) return [];
  const compatible = current.filter(id => models.some(item => item.id === id && modelScope(item) === scope));
  return compatible.includes(model.id) ? compatible.filter(id => id !== model.id) : compatible.length < 5 ? [...compatible, model.id] : compatible;
}
