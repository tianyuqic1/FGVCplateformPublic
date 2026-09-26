import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { queryClient } from "../query/queryClient.js";
import * as authAPI from "../api/auth.js";
import { setCsrfToken } from "../api/authState.js";

const AuthContext = createContext(null);

export function homeForRole(role) { return role === "annotator" ? "/annotation" : "/"; }

export function canAccessPath(role, path) {
  const pathname = path.split("?")[0];
  if (role === "admin") return true;
  if (role === "annotator") return pathname === "/annotation" || pathname === "/review" || pathname.startsWith("/review/");
  if (role === "business") return pathname !== "/annotation" && !pathname.startsWith("/admin");
  return false;
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let mounted = true;
    authAPI.currentUser().then(data => {
      if (!mounted) return;
      setCsrfToken(data.csrf_token);
      setUser(data.user);
      setStatus("ready");
    }).catch(() => { if (mounted) { setCsrfToken(""); setUser(null); setStatus("ready"); } });
    const expired = () => { setCsrfToken(""); setUser(null); queryClient.clear(); setStatus("ready"); };
    window.addEventListener("finevision:session-expired", expired);
    return () => { mounted = false; window.removeEventListener("finevision:session-expired", expired); };
  }, []);

  const value = useMemo(() => ({
    user, status,
    async login(email, password) {
      const data = await authAPI.login(email, password);
      queryClient.clear();
      setCsrfToken(data.csrf_token);
      setUser(data.user);
      setStatus("ready");
      return data.user;
    },
    register: authAPI.register,
    async logout() {
      await authAPI.logout();
      setCsrfToken(""); setUser(null); queryClient.clear(); setStatus("ready");
    },
  }), [user, status]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is missing");
  return value;
}
