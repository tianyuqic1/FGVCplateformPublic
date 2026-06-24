import { useCallback, useState } from "react";
import { generateLLMAssistance, generateReviewAssistance } from "../api/llm.js";

export function useReviewAssistance(reviewItemId) {
  const [state, setState] = useState({ status: "idle", assistance: null, error: null });

  const generate = useCallback(
    async (input = {}) => {
      setState((current) => ({ ...current, status: "generating", error: null }));
      try {
        const result = await generateReviewAssistance(reviewItemId, input);
        setState({ status: "ready", assistance: result.assistance, error: null });
        return result;
      } catch (error) {
        setState((current) => ({ ...current, status: "failed", error }));
        throw error;
      }
    },
    [reviewItemId],
  );

  const reset = useCallback(() => setState({ status: "idle", assistance: null, error: null }), []);

  return { ...state, generate, reset };
}

export function useLLMAssistance() {
  const [state, setState] = useState({ status: "idle", assistance: null, error: null });

  const generate = useCallback(async ({ task, context }) => {
    setState((current) => ({ ...current, status: "generating", error: null }));
    try {
      const assistance = await generateLLMAssistance({ task, context });
      setState({ status: "ready", assistance, error: null });
      return assistance;
    } catch (error) {
      setState((current) => ({ ...current, status: "failed", error }));
      throw error;
    }
  }, []);

  const reset = useCallback(() => setState({ status: "idle", assistance: null, error: null }), []);

  return { ...state, generate, reset };
}
