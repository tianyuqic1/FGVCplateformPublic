import { afterEach, describe, expect, it, vi } from "vitest";
import { canAccessPath, homeForRole } from "./AuthContext.jsx";
import { fetchForm, fetchJson } from "../api/http.js";
import { setCsrfToken } from "../api/authState.js";

afterEach(() => { setCsrfToken(""); vi.unstubAllGlobals(); });

describe("three-role frontend navigation", () => {
  it("keeps annotation and account administration outside business access", () => {
    expect(canAccessPath("business", "/training/run-id")).toBe(true);
    expect(canAccessPath("business", "/annotation")).toBe(false);
    expect(canAccessPath("business", "/admin/users")).toBe(false);
  });

  it("limits annotators to annotation and review, including direct URLs", () => {
    expect(homeForRole("annotator")).toBe("/annotation");
    expect(canAccessPath("annotator", "/annotation")).toBe(true);
    expect(canAccessPath("annotator", "/review/item-id")).toBe(true);
    expect(canAccessPath("annotator", "/models")).toBe(false);
    expect(canAccessPath("annotator", "/")).toBe(false);
    expect(canAccessPath("admin", "/admin/users")).toBe(true);
  });
});

describe("session CSRF header", () => {
  it("sends the token on JSON writes and multipart uploads but not reads", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
      calls.push(options);
      return { ok: true, status: 200, json: async () => ({}) };
    }));
    setCsrfToken("test-csrf-token");
    await fetchJson("/api/datasets");
    await fetchJson("/api/auth/logout", { method: "POST" });
    await fetchForm("/api/inference/upload", new FormData());
    expect(calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[1].headers["X-CSRF-Token"]).toBe("test-csrf-token");
    expect(calls[2].headers["X-CSRF-Token"]).toBe("test-csrf-token");
  });
});
