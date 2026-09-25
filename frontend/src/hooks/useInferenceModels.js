import { listInferenceModels } from "../api/inferenceModels.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

export function useInferenceModels() {
  const query = useDomainQuery({
    queryKey: ["inference-models"],
    queryFn: listInferenceModels,
    emptyValue: [],
  });

  return {
    models: query.data,
    source: query.source,
    loading: query.loading,
    refreshing: query.refreshing,
    error: query.error,
    refresh: query.refresh,
  };
}
