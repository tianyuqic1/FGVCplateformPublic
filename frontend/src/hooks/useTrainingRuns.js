import { useCallback, useEffect, useMemo, useState } from "react";
import { getTrainingRun, listTrainingRuns } from "../api/trainingRuns.js";
import { trainingRuns as mockTrainingRuns } from "../data/mockData.js";

function normalizeMockStatus(run) {
  return run.status === "done" ? { ...run, status: "succeeded", progress: 100 } : run;
}

function mergeTrainingRun(apiRun) {
  const fallback = mockTrainingRuns.find((item) => item.id === apiRun.id) ?? {};
  return { ...fallback, ...apiRun };
}

function fallbackTrainingRun(runId) {
  return normalizeMockStatus(mockTrainingRuns.find((item) => item.id === runId) ?? mockTrainingRuns[0]);
}

export function useTrainingRuns() {
  const fallbackRuns = useMemo(() => mockTrainingRuns.map(normalizeMockStatus), []);
  const [state, setState] = useState({
    trainingRuns: fallbackRuns,
    source: "mock",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;
    setState({ trainingRuns: fallbackRuns, source: "mock", loading: true, error: null });

    listTrainingRuns()
      .then((items) => {
        if (!active) return;
        setState({
          trainingRuns: items.map(mergeTrainingRun),
          source: "api",
          loading: false,
          error: null,
        });
      })
      .catch((error) => {
        if (!active) return;
        setState({ trainingRuns: fallbackRuns, source: "mock", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [fallbackRuns]);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useTrainingRun(runId) {
  const initialRun = useMemo(() => fallbackTrainingRun(runId), [runId]);
  const [state, setState] = useState({
    trainingRun: initialRun,
    source: "mock",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const nextFallback = fallbackTrainingRun(runId);
    setState({ trainingRun: nextFallback, source: "mock", loading: true, error: null });

    getTrainingRun(runId)
      .then((item) => {
        if (!active) return;
        setState({ trainingRun: mergeTrainingRun(item), source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ trainingRun: nextFallback, source: "mock", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [runId]);

  return state;
}
