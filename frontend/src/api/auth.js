import { fetchJson } from "./http.js";

export const currentUser = () => fetchJson("/api/auth/me");
export const login = (email, password) => fetchJson("/api/auth/login", { method: "POST", body: { email, password } });
export const register = (email, displayName, password) => fetchJson("/api/auth/register", { method: "POST", body: { email, display_name: displayName, password } });
export const logout = () => fetchJson("/api/auth/logout", { method: "POST" });
export const listUsers = (page = 1, query = "", status = "") => fetchJson(`/api/auth/users?${new URLSearchParams({ page: String(page), q: query, status })}`);
export const updateUser = (id, role, status, reason) => fetchJson(`/api/auth/users/${encodeURIComponent(id)}`, { method: "PATCH", body: { role, status, reason } });
