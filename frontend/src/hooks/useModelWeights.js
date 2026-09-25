import { listModelWeights } from "../api/modelWeights.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

export function useModelWeights() {
  const query = useDomainQuery({
    queryKey: ["model-weights"],
    queryFn: listModelWeights,
    emptyValue: [],
  });

  return { weights: query.data, ...withoutData(query) };
}

function withoutData({ data: _data, ...query }) {
  return query;
}
