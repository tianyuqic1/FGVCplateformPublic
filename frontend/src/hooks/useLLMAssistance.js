import { generateLLMAssistance, generateReviewAssistance } from "../api/llm.js";
import { useDomainMutation } from "../query/useDomainMutation.js";

export function useReviewAssistance(reviewItemId) {
  const mutation = useDomainMutation(
    (input = {}) => generateReviewAssistance(reviewItemId, input),
    { pending: "generating", success: "ready" },
  );

  return {
    status: mutation.status,
    assistance: mutation.result?.assistance ?? null,
    error: mutation.error,
    generate: mutation.run,
    reset: mutation.reset,
  };
}

export function useLLMAssistance() {
  const mutation = useDomainMutation(
    ({ task, context }) => generateLLMAssistance({ task, context }),
    { pending: "generating", success: "ready" },
  );

  return {
    status: mutation.status,
    assistance: mutation.result,
    error: mutation.error,
    generate: mutation.run,
    reset: mutation.reset,
  };
}
