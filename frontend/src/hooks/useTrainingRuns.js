import { useCallback, useEffect, useState } from "react";
import { getTrainingRun, listTrainingRuns } from "../api/trainingRuns.js";

function mergeTrainingRun(apiRun) {
  return apiRun;
}

export function useTrainingRuns() {
  const [state, setState] = useState({
    trainingRuns: [],
    source: "loading",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;
    setState({ trainingRuns: [], source: "loading", loading: true, error: null });

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
        setState({ trainingRuns: [], source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useTrainingRun(runId) {
  const [state, setState] = useState({
    trainingRun: null,
    source: "loading",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    let pollTimer = null;
    if (!runId) {
      setState({ trainingRun: null, source: "api", loading: false, error: null });
      return undefined;
    }
    setState({ trainingRun: null, source: "loading", loading: true, error: null });

    function loadRun({ keepCurrent = false } = {}) {
      if (!runId) return;
      getTrainingRun(runId)
        .then((item) => {
          if (!active) return;
          const nextRun = mergeTrainingRun(item);
          setState({ trainingRun: nextRun, source: "api", loading: false, error: null });
          if (["queued", "paused", "running", "loading"].includes(nextRun.status)) return;
          if (pollTimer) window.clearInterval(pollTimer);
          pollTimer = null;
        })
        .catch((error) => {
          if (!active) return;
          if (String(error?.message ?? "").startsWith("404 ")) {
            setState({ trainingRun: null, source: "api", loading: false, error });
            return;
          }
          setState((current) => ({
            trainingRun: keepCurrent ? current.trainingRun : null,
            source: "unavailable",
            loading: false,
            error,
          }));
        });
    }

    loadRun();
    pollTimer = window.setInterval(() => {
      loadRun({ keepCurrent: true });
    }, 3000);

    return () => {
      active = false;
      if (pollTimer) window.clearInterval(pollTimer);
    };
  }, [runId]);

  return state;
}
