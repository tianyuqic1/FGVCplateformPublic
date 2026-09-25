export function datasetStatus(dataset) {
  if (dataset.status === "production") return { label: "生产可推理", tone: "default" };
  if (dataset.status === "ready") return { label: "可训练", tone: "default" };
  if (dataset.status === "calibrating") return { label: "待校准", tone: "warn" };
  return { label: "训练中", tone: "info" };
}

export function datasetStatusLabel(status) {
  return datasetStatus({ status }).label;
}

export function datasetFilterMatch(dataset, filter) {
  if (filter === "production") {
    return dataset.status === "production" || dataset.versions?.some((version) => version.hasWeights && version.models.some((model) => model.status !== "archived"));
  }
  if (filter === "ready") return dataset.status === "ready";
  return true;
}
