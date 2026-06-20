import { useCallback, useEffect, useState } from "react";
import { listModelWeights } from "../api/modelWeights.js";

export function useModelWeights() {
  const [state, setState] = useState({
    weights: [],
    source: "loading",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;
    setState((current) => ({ ...current, source: "loading", loading: true, error: null }));

    listModelWeights()
      .then((weights) => {
        if (!active) return;
        setState({ weights, source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ weights: [], source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}
