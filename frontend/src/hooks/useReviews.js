import { useCallback, useEffect, useState } from "react";
import { getReviewItem, listReviewItems, submitReviewOutcome } from "../api/reviews.js";

export function useReviewItems(filters = {}) {
  const [state, setState] = useState({
    reviewItems: [],
    source: "api",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));

    listReviewItems(filters)
      .then((items) => {
        if (!active) return;
        setState({ reviewItems: items, source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ reviewItems: [], source: "api", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [filters.status, filters.datasetId, filters.limit]);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useReviewItem(reviewItemId) {
  const [state, setState] = useState({
    reviewItem: null,
    source: "api",
    loading: true,
    error: null,
  });

  const refresh = useCallback(() => {
    let active = true;
    setState({ reviewItem: null, source: "api", loading: true, error: null });

    getReviewItem(reviewItemId)
      .then((item) => {
        if (!active) return;
        setState({ reviewItem: item, source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ reviewItem: null, source: "api", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [reviewItemId]);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useSubmitReviewOutcome(reviewItemId) {
  const [state, setState] = useState({ status: "idle", error: null });

  const submit = useCallback(
    async (input) => {
      setState({ status: "submitting", error: null });
      try {
        const result = await submitReviewOutcome(reviewItemId, input);
        setState({ status: "succeeded", error: null });
        return result;
      } catch (error) {
        setState({ status: "failed", error });
        throw error;
      }
    },
    [reviewItemId],
  );

  return { ...state, submit };
}
