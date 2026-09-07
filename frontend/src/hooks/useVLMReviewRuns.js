import { useCallback, useEffect, useState } from "react";
import {
  cancelVLMReviewRun,
  createVLMReviewRun,
  getVLMReviewCapabilities,
  listVLMReviewRuns,
} from "../api/vlmReviews.js";

export function useVLMReviewRuns() {
  const [state, setState] = useState({
    runs: [],
    capabilities: { assistedEnabled: true, autoEnabled: false },
    loading: true,
    mutating: false,
    error: null,
  });

  const refresh = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [runs, capabilities] = await Promise.all([
        listVLMReviewRuns(),
        getVLMReviewCapabilities(),
      ]);
      setState((current) => ({ ...current, runs, capabilities, loading: false, error: null }));
      return runs;
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error }));
      throw error;
    }
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  useEffect(() => {
    const hasActiveRun = state.runs.some((run) => run.status === "queued" || run.status === "running");
    if (!hasActiveRun) return undefined;
    const timer = window.setInterval(() => {
      Promise.all([listVLMReviewRuns(), getVLMReviewCapabilities()])
        .then(([runs, capabilities]) => {
          setState((current) => ({ ...current, runs, capabilities, error: null }));
        })
        .catch((error) => setState((current) => ({ ...current, error })));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [state.runs]);

  const create = useCallback(async (input) => {
    setState((current) => ({ ...current, mutating: true, error: null }));
    try {
      const run = await createVLMReviewRun(input);
      setState((current) => ({ ...current, runs: [run, ...current.runs], mutating: false }));
      return run;
    } catch (error) {
      setState((current) => ({ ...current, mutating: false, error }));
      throw error;
    }
  }, []);

  const cancel = useCallback(async (runId) => {
    setState((current) => ({ ...current, mutating: true, error: null }));
    try {
      const run = await cancelVLMReviewRun(runId);
      setState((current) => ({
        ...current,
        runs: current.runs.map((item) => (item.id === run.id ? run : item)),
        mutating: false,
      }));
      return run;
    } catch (error) {
      setState((current) => ({ ...current, mutating: false, error }));
      throw error;
    }
  }, []);

  return { ...state, refresh, create, cancel };
}
