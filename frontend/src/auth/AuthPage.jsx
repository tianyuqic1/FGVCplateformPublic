import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "../components/icons.jsx";
import { canAccessPath, homeForRole, useAuth } from "./AuthContext.jsx";
import "./auth.css";

function messageFrom(error) { return error?.payload?.detail || "暂时无法完成请求，请稍后重试。"; }

export function AuthPage({ mode }) {
  // Keep notices, credentials and pending requests local to each form visit.
  return <AuthForm key={mode} mode={mode} />;
}

function AuthForm({ mode }) {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const isRegister = mode === "register";

  async function submit(event) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      if (isRegister) {
        await register(email, name, password);
        setPassword("");
        setNotice("注册已提交，请等待平台管理员审核并分配角色。审核完成后即可登录。");
      } else {
        const user = await login(email, password);
        const requested = location.state?.from;
        navigate(typeof requested === "string" && requested.startsWith("/") && canAccessPath(user.role, requested) ? requested : homeForRole(user.role), { replace: true });
      }
    } catch (failure) { setError(messageFrom(failure)); }
    finally { setBusy(false); }
  }

  return <main className="fv-auth-screen">
    <section className="fv-auth-intro"><div className="fv-auth-mark"><Icon name="ScanSearch" size={25} /></div><span>FINEVISION · RESEARCH CONSOLE</span><h1>让每一次标注、训练与发布都有明确的责任人。</h1><p>账号审核后即可进入你的工作区。任务、模型与数据版本继续保持可追溯。</p><div className="fv-auth-line"><span>01 / 数据资产</span><span>02 / 视觉训练</span><span>03 / 人工复核</span></div></section>
    <section className="fv-auth-panel"><div className="fv-auth-kicker">{isRegister ? "CREATE ACCOUNT" : "WELCOME BACK"}</div><h2>{isRegister ? "注册账号" : "登录工作台"}</h2><p>{isRegister ? "新账号不会自动获得业务权限，需管理员审核。" : "使用已审核的账号继续工作。"}</p>
      <form onSubmit={submit}>
        {isRegister && <label>显示名称<input autoComplete="name" maxLength={80} required value={name} onChange={event => setName(event.target.value)} placeholder="例如：陈凯文" /></label>}
        <label>邮箱<input type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} placeholder="name@example.com" /></label>
        <label>密码<input type="password" autoComplete={isRegister ? "new-password" : "current-password"} minLength={isRegister ? 12 : undefined} required value={password} onChange={event => setPassword(event.target.value)} placeholder={isRegister ? "至少 12 位" : "输入密码"} /></label>
        {error && <div className="fv-auth-alert" role="alert">{error}</div>}
        {notice && <div className="fv-auth-notice" role="status">{notice}</div>}
        <button className="fv-auth-primary" type="submit" disabled={busy}>{busy ? "正在处理…" : isRegister ? "提交注册" : "登录"}</button>
      </form>
      <div className="fv-auth-switch">{isRegister ? <>已有账号？ <Link to="/login">去登录</Link></> : <>还没有账号？ <Link to="/register">申请注册</Link></>}</div>
    </section>
  </main>;
}
