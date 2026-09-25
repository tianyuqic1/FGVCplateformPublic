import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useDomainQuery } from "./useDomainQuery.js";

function wrapper({ children }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useDomainQuery", () => {
  it("turns a successful request into domain state", async () => {
    const queryFn = vi.fn().mockResolvedValue([{ id: "cub" }]);
    const { result } = renderHook(() => useDomainQuery({ queryKey: ["datasets-test"], queryFn, emptyValue: [] }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual([{ id: "cub" }]);
    expect(result.current.source).toBe("api");
    expect(result.current.error).toBeNull();
  });

  it("does not call disabled resources", () => {
    const queryFn = vi.fn();
    const { result } = renderHook(
      () => useDomainQuery({ queryKey: ["disabled-test"], queryFn, enabled: false, emptyValue: [] }),
      { wrapper },
    );

    expect(queryFn).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual([]);
  });
});
