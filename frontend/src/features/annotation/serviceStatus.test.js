import { expect, it } from "vitest";
import { annotationServiceLabel } from "./serviceStatus.js";

it("distinguishes initialization, unknown and unavailable without exposing worker internals", () => {
  expect(annotationServiceLabel({ online: null })).toContain("暂未确认");
  expect(annotationServiceLabel({ service_state: "initializing", online: false })).toContain("初始化");
  expect(annotationServiceLabel({ service_state: "ready", online: true })).toBe("AI 服务已就绪");
  expect(annotationServiceLabel({ online: true })).toBe("AI 服务已连接");
  expect(annotationServiceLabel({ service_state: "unavailable", online: false })).toContain("可人工标注");
});
