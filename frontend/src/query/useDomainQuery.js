import { useQuery } from "@tanstack/react-query";

export function useDomainQuery({
  queryKey,
  queryFn,
  enabled = true,
  emptyValue,
  refetchInterval = false,
  placeholderData,
}) {
  const query = useQuery({
    queryKey,
    queryFn,
    enabled,
    refetchInterval,
    placeholderData,
  });

  const loading = enabled && query.isPending;
  return {
    data: query.data ?? emptyValue,
    source: !enabled || query.data !== undefined ? "api" : query.error ? "unavailable" : "loading",
    loading,
    refreshing: query.isFetching && !loading,
    error: query.error ?? null,
    refresh: query.refetch,
  };
}
