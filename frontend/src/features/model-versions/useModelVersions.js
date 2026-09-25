import { getModelVersion, listModelVersions } from "../../api/modelVersions.js";
import { useDomainQuery } from "../../query/useDomainQuery.js";

export function useModelVersions(filters = {}) {
  const query = useDomainQuery({
    queryKey: [
      "model-versions",
      filters.datasetId ?? "",
      filters.datasetVersionId ?? "",
      filters.status ?? "",
      filters.backboneId ?? "",
    ],
    queryFn: () => listModelVersions(filters),
    emptyValue: [],
  });

  return { versions: query.data, ...withoutData(query) };
}

export function useModelVersion(id) {
  const query = useDomainQuery({
    queryKey: ["model-version", id],
    queryFn: () => getModelVersion(id),
    enabled: Boolean(id),
    emptyValue: null,
  });

  return { version: query.data, ...withoutData(query) };
}

function withoutData({ data: _data, ...query }) {
  return query;
}
