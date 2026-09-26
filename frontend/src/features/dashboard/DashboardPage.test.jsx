import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage.jsx";

const data = vi.hoisted(() => ({ reviews: {}, training: {}, models: {} }));
vi.mock("../../hooks/useReviews.js", () => ({ useReviewItems: () => data.reviews }));
vi.mock("../../hooks/useTrainingRuns.js", () => ({ useTrainingRuns: () => data.training }));
vi.mock("../model-versions/useModelVersions.js", () => ({ useModelVersions: () => data.models }));

beforeEach(() => {
  data.reviews = { reviewItems: [], pagination: { totalKnown: true, total: 1477 } };
  data.training = { trainingRuns: [] };
  data.models = { versions: [{ id: "model-1", datasetId: "dataset-1", datasetName: "测试数据集", status: "production", releaseVersion: "v1.0.0", artifacts: [{ artifact_type: "model" }, { artifact_type: "report" }, { artifact_type: "calibration" }, { artifact_type: "threshold_strategy" }] }] };
});
afterEach(cleanup);
function openPage() { render(<MemoryRouter><DashboardPage /></MemoryRouter>); }

describe("dashboard copy reflects available evidence", () => {
  it("labels associations without implying integrity verification or automated release approval", () => {
    openPage();
    expect(screen.getByRole("heading", { name: "平台概览" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "待处理事项" })).toBeVisible();
    expect(screen.getAllByText("已关联")).toHaveLength(3);
    expect(screen.queryByText("已验证")).not.toBeInTheDocument();
    expect(screen.getByText("仅表示记录已关联，不代表文件校验通过")).toBeVisible();
    expect(screen.getAllByText("建议检查")).toHaveLength(2);
    expect(screen.getByText("仅右侧发布信息随模型切换；统计不按模型筛选")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "平台状态摘要" })).getByText("1477")).toBeVisible();
    expect(screen.getByText("流程说明；不代表某次任务的执行进度")).toBeVisible();
  });

  it("shows loaded counts when totals are unavailable and does not turn missing confidence into zero", () => {
    data.reviews = { reviewItems: [{ id: "review-1", decision: { confidence: null, margin: 0.0304, thresholds: { margin: 0.05 } } }], pagination: { totalKnown: false } };
    openPage();
    const summary = within(screen.getByRole("region", { name: "平台状态摘要" }));
    expect(summary.getByText("已读取 1 条")).toBeVisible();
    expect(screen.getByText("3.04 个百分点")).toBeVisible();
    expect(screen.getByText("阈值 5.00 个百分点")).toBeVisible();
    expect(screen.getByText("未采集", { exact: true })).toBeVisible();
    expect(screen.queryByText("0.0%", { exact: true })).not.toBeInTheDocument();
  });

  it("distinguishes failed loads and an unselected model from zero counts or missing artifacts", () => {
    data.reviews.error = new Error("offline");
    data.training.error = new Error("offline");
    data.models = { versions: [], error: new Error("offline") };
    openPage();
    expect(within(screen.getByRole("region", { name: "平台状态摘要" })).getAllByText("加载失败")).toHaveLength(4);
    expect(screen.getByText("暂无可查看的模型。请选择模型版本后查看关联材料。")).toBeVisible();
    expect(screen.queryByText("未关联模型文件")).not.toBeInTheDocument();
  });
});
