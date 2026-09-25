import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ServerPagination } from "./ServerPagination.jsx";

describe("server pagination", () => {
  it("renders server total and requests the selected page", () => {
    const onPageChange = vi.fn();
    render(<ServerPagination pagination={{ total: 25, limit: 6, offset: 6 }} onPageChange={onPageChange} unit="个" />);
    expect(screen.getByText(/共 25 个/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});
