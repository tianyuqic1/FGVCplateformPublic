import { getTrainingRun, getTrainingRunSummary, listTrainingRunPage, listTrainingRuns } from "../api/trainingRuns.js";
import { keepPreviousData } from "@tanstack/react-query";
import { useDomainQuery } from "../query/useDomainQuery.js";

const ACTIVE_STATUSES = new Set(["queued", "paused", "running", "loading"]);

export function useTrainingRuns() {
  const query = useDomainQuery({
    queryKey: ["training-runs"],
    queryFn: listTrainingRuns,
    emptyValue: [],
  });

  return { trainingRuns: query.data, ...withoutData(query) };
}

export function useTrainingRunPage(filters) {
  const query = useDomainQuery({ queryKey: ["training-run-page", filters], queryFn: () => listTrainingRunPage(filters), emptyValue: { items: [], pagination: { total: 0, limit: filters.limit, offset: filters.offset } }, placeholderData: keepPreviousData });
  return { trainingRuns: query.data.items, pagination: query.data.pagination, ...withoutData(query) };
}

export function useTrainingRunSummary() {
  const query = useDomainQuery({ queryKey: ["training-run-summary"], queryFn: getTrainingRunSummary, emptyValue: {} });
  return { counts: query.data, ...withoutData(query) };
}

export function useTrainingRun(runId) {
  const query = useDomainQuery({
    queryKey: ["training-run", runId],
    queryFn: () => getTrainingRun(runId),
    enabled: Boolean(runId),
    emptyValue: null,
    refetchInterval: (state) => (ACTIVE_STATUSES.has(state.state.data?.status) ? 3_000 : false),
  });

  const source = isNotFound(query.error) ? "api" : query.source;
  return { trainingRun: query.data, ...withoutData(query), source };
}

function withoutData({ data: _data, ...query }) {
  return query;
}

function isNotFound(error) {
  return Number(error?.status) === 404 || String(error?.message ?? "").startsWith("404 ");
}
