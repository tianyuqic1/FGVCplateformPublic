import { useCallback, useEffect, useMemo, useState } from "react";
import { getDataset, listDatasetSamplePreviews, listDatasets } from "../api/datasets.js";

function mergeDataset(apiDataset) {
  return apiDataset;
}

function fallbackDataset(datasetId) {
  return datasetId ? { id: datasetId, name: datasetId, description: "正在连接 Control-plane API" } : null;
}

export function useDatasets() {
  const [state, setState] = useState({
    datasets: [],
    source: "loading",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;

    listDatasets()
      .then((items) => {
        if (!active) return;
        setState({
          datasets: items.map(mergeDataset),
          source: "api",
          loading: false,
          error: null,
        });
      })
      .catch((error) => {
        if (!active) return;
        setState({ datasets: [], source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useDataset(datasetId) {
  const initialDataset = useMemo(() => fallbackDataset(datasetId), [datasetId]);
  const [state, setState] = useState({
    dataset: initialDataset,
    source: "loading",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const nextFallback = fallbackDataset(datasetId);
    setState({ dataset: nextFallback, source: "loading", loading: true, error: null });

    getDataset(datasetId)
      .then((item) => {
        if (!active) return;
        setState({ dataset: mergeDataset(item), source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        if (String(error?.message ?? "").startsWith("404 ")) {
          setState({ dataset: null, source: "api", loading: false, error });
          return;
        }
        setState({ dataset: null, source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [datasetId]);

  return state;
}

export function useDatasetSamplePreviews(datasetVersionId, limit = 6) {
  const [state, setState] = useState({
    samples: [],
    loading: Boolean(datasetVersionId),
    error: null,
  });

  useEffect(() => {
    if (!datasetVersionId) {
      setState({ samples: [], loading: false, error: null });
      return undefined;
    }

    let active = true;
    setState({ samples: [], loading: true, error: null });
    listDatasetSamplePreviews(datasetVersionId, limit)
      .then((samples) => {
        if (!active) return;
        setState({ samples, loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ samples: [], loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [datasetVersionId, limit]);

  return state;
}
