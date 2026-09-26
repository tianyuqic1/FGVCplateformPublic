import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { WorkersPage, heartbeatAge } from "./WorkersPage.jsx";
import { canAccessPath } from "../../auth/AuthContext.jsx";

const api = vi.hoisted(() => ({ fetchJson: vi.fn() }));
vi.mock("../../api/http.js", () => api);
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const worker = { id: "training-1", name: "训练实例 01", kind: "training", connection: "online", readiness: "ready", capacity: 1, active_count: 0, received_at: "2026-09-26T10:00:00Z", tasks: [] };
function open(path = "/workers") { render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/workers" element={<WorkersPage />} /><Route path="/workers/:workerId" element={<WorkersPage />} /></Routes></MemoryRouter>); }
it("uses server totals and resets pagination on filtering", async () => {
  api.fetchJson.mockResolvedValue({ items: [worker], summary: { total: 20, available: 19, busy: 0, abnormal: 1 }, pagination: { total: 20, limit: 6, offset: 6 }, server_time: "2026-09-26T10:00:05Z" });
  open("/workers?offset=6");
  expect(await screen.findByText("训练实例 01")).toBeVisible();
  expect(within(screen.getByRole("region", { name: "筛选结果汇总" })).getByText("20")).toBeVisible();
  fireEvent.change(screen.getByLabelText("服务类型"), { target: { value: "training" } });
  await waitFor(() => expect(api.fetchJson.mock.calls.at(-1)[0]).toContain("offset=0"));
  expect(api.fetchJson.mock.calls.at(-1)[0]).toContain("kind=training");
});
it("does not label API errors as offline or show zero totals", async () => {
  api.fetchJson.mockRejectedValue(new Error("network unavailable"));open();
  expect(await screen.findByRole("alert")).toHaveTextContent("不能据此判断服务离线");
  expect(within(screen.getByRole("region", { name: "筛选结果汇总" })).getAllByText("—")).toHaveLength(4);
});
it("supports direct detail URLs and marks old task observations", async () => {
  api.fetchJson.mockImplementation(path => Promise.resolve(path.includes("/workers/training-1") ? { worker: { ...worker, connection: "offline", active_count: 1, tasks: [{ id: "run-1", kind: "training", stage: "训练执行中" }] } } : { items: [], summary: {}, pagination: { total: 0 } }));
  open("/workers/training-1");
  expect(await screen.findByText(/以下负载与任务为最后一次上报/)).toBeVisible();
  expect(screen.getByRole("link", { name: "查看训练任务 →" })).toHaveAttribute("href", "/training/run-1");
});
it("restricts annotators and handles absent heartbeat", () => {
  expect(canAccessPath("annotator", "/workers")).toBe(false);
  expect(canAccessPath("business", "/workers/test")).toBe(true);
  expect(heartbeatAge(null, null)).toBe("未上报");
});

it("folds technical details for business users and explains task scope", async () => {
  api.fetchJson.mockImplementation(path => Promise.resolve(path.includes("/workers/training-1") ? { worker: { ...worker, task_visibility: "own_training_only", node_id: "gpu-a" } } : { items: [], summary: {}, view: "business" }));
  open("/workers/training-1");
  expect(await screen.findByRole("heading", { name: "我的训练任务" })).toBeVisible();
  expect(screen.getByRole("link", { name: "查看节点计算资源", hidden: true })).not.toBeVisible();
  expect(screen.queryByText("启动会话")).not.toBeInTheDocument();
  expect(screen.getByText(/其他任务仅计入总负载/)).toBeVisible();
});

it("keeps complete administrator diagnostics expanded", async () => {
  api.fetchJson.mockImplementation(path => Promise.resolve(path.includes("/workers/training-1") ? { worker: { ...worker, task_visibility: "all", session_id: "admin-session", sequence: 5, node_id: "gpu-a" } } : { items: [], summary: {}, view: "admin" }));
  open("/workers/training-1");
  expect(await screen.findByText("admin-session")).toBeVisible();
  expect(screen.getByRole("heading", { name: "当前任务 / 请求" })).toBeVisible();
  expect(screen.getByRole("link", { name: "查看节点计算资源" })).toBeVisible();
});
