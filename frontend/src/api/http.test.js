import { afterEach, describe, expect, it, vi } from "vitest";
import { withTimeout } from "./http.js";

afterEach(() => vi.useRealTimers());

describe("request cancellation", () => {
  const request = (signal) => new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true }));

  it("reports a timeout instead of a generic network failure", async () => {
    vi.useFakeTimers();
    const pending = withTimeout(request, 1000);
    const rejected = expect(pending).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
  });

  it("propagates a user abort", async () => {
    const controller = new AbortController();
    const pending = withTimeout(request, 10000, controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await Promise.resolve();
    controller.abort();
    await rejected;
  });
});
