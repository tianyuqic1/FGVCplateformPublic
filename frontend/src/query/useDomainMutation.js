import { useMutation } from "@tanstack/react-query";

export function useDomainMutation(
  mutationFn,
  { pending = "submitting", success = "succeeded", failure = "failed" } = {},
) {
  const mutation = useMutation({ mutationFn });

  let status = "idle";
  if (mutation.isPending) status = pending;
  if (mutation.isSuccess) status = success;
  if (mutation.isError) status = failure;

  return {
    status,
    error: mutation.error ?? null,
    result: mutation.data ?? null,
    run: mutation.mutateAsync,
    reset: mutation.reset,
  };
}
