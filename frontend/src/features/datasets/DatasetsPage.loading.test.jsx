import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DatasetsPage } from "./DatasetsPage.jsx";

vi.mock("../../hooks/useDatasets.js", () => ({
  useDatasetPage: () => ({
    datasets: [],
    pagination: { total: 0, limit: 6, offset: 0 },
    source: "loading",
    loading: true,
    refreshing: false,
    error: null,
    refresh: vi.fn(),
  }),
}));
vi.mock("./DatasetImportJobs.jsx", () => ({ DatasetImportJobs: () => null }));

describe("dataset cold loading", () => {
  it("does not present a false empty state or zero total", () => {
    render(<MemoryRouter><DatasetsPage showToast={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText("正在读取数据集…")).toBeInTheDocument();
    expect(screen.queryByText("暂无数据集")).not.toBeInTheDocument();
    expect(screen.queryByText(/共 0 个匹配数据集/)).not.toBeInTheDocument();
  });
});
