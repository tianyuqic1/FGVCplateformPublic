import { getDataset, listDatasetSamplePreviews, listDatasets } from "../api/datasets.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

export function useDatasets() {
  const query = useDomainQuery({
    queryKey: ["datasets"],
    queryFn: listDatasets,
    emptyValue: [],
  });

  return { datasets: query.data, ...withoutData(query) };
}

export function useDataset(datasetId) {
  const query = useDomainQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => getDataset(datasetId),
    enabled: Boolean(datasetId),
    emptyValue: null,
  });

  const source = isNotFound(query.error) ? "api" : query.source;
  return { dataset: query.data, ...withoutData(query), source };
}

export function useDatasetSamplePreviews(datasetVersionId, limit = 6) {
  const query = useDomainQuery({
    queryKey: ["dataset-sample-previews", datasetVersionId, limit],
    queryFn: () => listDatasetSamplePreviews(datasetVersionId, limit),
    enabled: Boolean(datasetVersionId),
    emptyValue: [],
  });

  return {
    samples: query.data,
    loading: query.loading,
    refreshing: query.refreshing,
    error: query.error,
    refresh: query.refresh,
  };
}

function withoutData({ data: _data, ...query }) {
  return query;
}

function isNotFound(error) {
  return Number(error?.status) === 404 || String(error?.message ?? "").startsWith("404 ");
}
