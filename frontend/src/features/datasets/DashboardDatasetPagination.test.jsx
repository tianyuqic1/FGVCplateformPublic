import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardDatasetPagination } from "./DashboardDatasetPagination.jsx";

const datasets = [
  { id: "cub", name: "CUB-200", status: "ready", versions: [] },
  { id: "cars", name: "Stanford Cars", status: "training", versions: [] },
];

describe("DashboardDatasetPagination", () => {
  it("filters by name and exposes the true filtered total", () => {
    render(
      <DashboardDatasetPagination items={datasets} showStatus={false}>
        {(items) => (
          <div>
            {items.map((item) => (
              <span key={item.id}>{item.name}</span>
            ))}
          </div>
        )}
      </DashboardDatasetPagination>,
    );

    fireEvent.change(screen.getByLabelText("搜索数据集"), { target: { value: "cars" } });

    expect(screen.queryByText("CUB-200")).not.toBeInTheDocument();
    expect(screen.getByText("Stanford Cars")).toBeInTheDocument();
    expect(screen.getByText(/共 1 个/)).toBeInTheDocument();
  });
});
