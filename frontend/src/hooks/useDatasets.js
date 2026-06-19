import { useCallback, useEffect, useMemo, useState } from "react";
import { getDataset, listDatasets } from "../api/datasets.js";
import { datasets as mockDatasets } from "../data/mockData.js";

function mergeDataset(apiDataset) {
  const fallback = mockDatasets.find((item) => item.id === apiDataset.id) ?? {};
  return { ...fallback, ...apiDataset };
}

function fallbackDataset(datasetId) {
  return mockDatasets.find((item) => item.id === datasetId) ?? mockDatasets[0];
}

export function useDatasets() {
  const [state, setState] = useState({
    datasets: mockDatasets,
    source: "mock",
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
        setState({ datasets: mockDatasets, source: "mock", loading: false, error });
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
    source: "mock",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const nextFallback = fallbackDataset(datasetId);
    setState({ dataset: nextFallback, source: "mock", loading: true, error: null });

    getDataset(datasetId)
      .then((item) => {
        if (!active) return;
        setState({ dataset: mergeDataset(item), source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ dataset: nextFallback, source: "mock", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [datasetId]);

  return state;
}
