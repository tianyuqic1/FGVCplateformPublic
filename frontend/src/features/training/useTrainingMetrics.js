import { useEffect, useRef, useState } from "react";
import { getTrainingRunMetrics } from "../../api/trainingRuns.js";

export const TERMINAL_TRAINING_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export function mergeMetricPoints(current, incoming) {
  const byIdentity = new Map(current.map((point) => [`${point.attemptId}:${point.name}:${point.step}`, point]));
  incoming.forEach((point) => byIdentity.set(`${point.attemptId}:${point.name}:${point.step}`, point));
  return [...byIdentity.values()].sort((left, right) => left.id - right.id || left.step - right.step);
}

export function useTrainingMetrics(runId, selectedAttemptId = "") {
  const cursor = useRef(0);
  const [state, setState] = useState({
    points: [],
    attempts: [],
    runStatus: "queued",
    loading: Boolean(runId),
    error: null,
  });

  useEffect(() => {
    if (!runId) {
      setState({ points: [], attempts: [], runStatus: "queued", loading: false, error: null });
      return undefined;
    }
    let active = true;
    let timer = null;
    let controller = null;
    cursor.current = 0;
    setState({ points: [], attempts: [], runStatus: "queued", loading: true, error: null });

    async function poll() {
      controller = new AbortController();
      try {
        const result = await getTrainingRunMetrics(
          runId,
          { attemptId: selectedAttemptId, afterId: cursor.current, limit: 1000 },
          { signal: controller.signal },
        );
        if (!active) return;
        cursor.current = Math.max(cursor.current, result.nextCursor);
        setState((current) => ({
          points: mergeMetricPoints(current.points, result.points),
          attempts: result.attempts,
          runStatus: result.runStatus,
          loading: false,
          error: null,
        }));
        if (TERMINAL_TRAINING_STATUSES.has(result.runStatus)) return;
        const delay = document.visibilityState === "hidden" ? Math.max(8000, result.pollAfterMs) : result.pollAfterMs;
        timer = window.setTimeout(poll, delay);
      } catch (error) {
        if (!active || error?.name === "AbortError") return;
        setState((current) => ({ ...current, loading: false, error }));
        timer = window.setTimeout(poll, document.visibilityState === "hidden" ? 10000 : 4000);
      }
    }

    poll();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [runId, selectedAttemptId]);

  return state;
}
