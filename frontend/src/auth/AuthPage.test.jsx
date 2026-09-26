import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthPage } from "./AuthPage.jsx";

const auth = vi.hoisted(() => ({ login: vi.fn(), register: vi.fn() }));
vi.mock("./AuthContext.jsx", () => ({
  useAuth: () => auth,
  canAccessPath: () => true,
  homeForRole: () => "/",
}));

// Match App's shared AuthPage across client-side login/register navigation.
function AuthRoutes() {
  const { pathname } = useLocation();
  return <AuthPage mode={pathname === "/register" ? "register" : "login"} />;
}

function openAuth(path = "/register") {
  render(<MemoryRouter initialEntries={[path]}><AuthRoutes /></MemoryRouter>);
  return userEvent.setup();
}

async function fillForm(user, register = true) {
  if (register) await user.type(screen.getByLabelText("显示名称"), "测试用户");
  await user.type(screen.getByLabelText("邮箱"), "test@example.com");
  await user.type(screen.getByLabelText("密码"), "test-password-only");
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

describe("auth page navigation state", () => {
  it("clears the registration notice and form when switching to login and back", async () => {
    auth.register.mockResolvedValue({});
    const user = openAuth();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "提交注册" }));
    expect(await screen.findByRole("status")).toHaveTextContent("注册已提交");
    await user.click(screen.getByRole("link", { name: "去登录" }));
    expect(screen.getByRole("heading", { name: "登录工作台" })).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByLabelText("邮箱")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveValue("");
    await user.click(screen.getByRole("link", { name: "申请注册" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByLabelText("显示名称")).toHaveValue("");
  });

  it.each([true, false])("clears request errors on a mode change (register=%s)", async (register) => {
    auth[register ? "register" : "login"].mockRejectedValue({ payload: { detail: "测试请求失败" } });
    const user = openAuth(register ? "/register" : "/login");
    await fillForm(user, register);
    await user.click(screen.getByRole("button", { name: register ? "提交注册" : "登录", exact: true }));
    expect(await screen.findByRole("alert")).toHaveTextContent("测试请求失败");
    await user.click(screen.getByRole("link", { name: register ? "去登录" : "申请注册" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  it("does not carry a pending registration or its late response into login", async () => {
    let finish;
    auth.register.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const user = openAuth();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "提交注册" }));
    expect(screen.getByRole("button", { name: "正在处理…" })).toBeDisabled();
    await user.click(screen.getByRole("link", { name: "去登录" }));
    expect(screen.getByRole("button", { name: "登录", exact: true })).toBeEnabled();
    await act(async () => finish({}));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
