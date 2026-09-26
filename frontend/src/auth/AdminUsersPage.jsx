import { useEffect, useState } from "react";
import { listUsers, updateUser } from "../api/auth.js";
import { useAuth } from "./AuthContext.jsx";
import "./auth.css";

const roleLabels = { admin: "平台管理员", annotator: "标注员", business: "业务人员" };
const statusLabels = { pending: "待审核", active: "已启用", disabled: "已停用" };

export function AdminUsersPage() {
  const { user: self } = useAuth();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [edits, setEdits] = useState({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => { setPage(1); setQuery(search.trim()); }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    let live = true;
    setLoading(true);
    listUsers(page, query, statusFilter).then(result => { if (live) { setData(result); setError(""); } }).catch(err => { if (live) setError(err.payload?.detail || err.message); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [page, query, statusFilter, revision]);

  function edit(id, changes) { setEdits(current => ({ ...current, [id]: { ...current[id], ...changes } })); }
  async function save(row) {
    const next = edits[row.id] || {};
    const reason = (next.reason || "").trim();
    if (!reason) { setError("请填写变更原因，便于后续审计。"); return; }
    setBusy(row.id); setError(""); setNotice("");
    try {
      await updateUser(row.id, next.role || row.role, next.status || row.status, reason);
      setNotice(`已更新 ${row.display_name}，该账号的旧会话已失效。`);
      setEdits(current => { const copy = { ...current }; delete copy[row.id]; return copy; });
      setRevision(value => value + 1);
    } catch (err) { setError(err.payload?.detail || err.message); }
    finally { setBusy(""); }
  }

  return <div className="fv-users-page"><header className="fv-users-heading"><div><span>ACCESS CONTROL</span><h2>用户管理</h2><p>审核新账号、分配三种角色并停用账号。修改后立即撤销该用户的现有会话。</p></div><strong>{data.total} 位用户</strong></header>
    {error && <div className="fv-auth-alert" role="alert">{error}</div>}{notice && <div className="fv-auth-notice" role="status">{notice}</div>}
    <div className="fv-users-filters"><label>查找用户<input type="search" value={search} maxLength={100} placeholder="搜索姓名或邮箱" onChange={event => setSearch(event.target.value)} /></label><label>账号状态<select value={statusFilter} onChange={event => { setPage(1); setStatusFilter(event.target.value); }}><option value="">全部状态</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    {loading ? <p>正在读取用户…</p> : <div className="fv-users-list">{data.items.map(row => <article key={row.id} className="fv-user-card"><div className="fv-user-identity"><span className="fv-user-avatar">{row.display_name.slice(0, 1)}</span><div><strong>{row.display_name}</strong><small>{row.email}</small></div><span className={`fv-user-status ${row.status}`}>{statusLabels[row.status]}</span></div>
      <div className="fv-user-controls"><label>角色<select aria-label={`${row.display_name}的角色`} disabled={row.id === self?.id} value={edits[row.id]?.role || row.role} onChange={event => edit(row.id, { role: event.target.value })}>{Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>账号状态<select aria-label={`${row.display_name}的状态`} disabled={row.id === self?.id} value={edits[row.id]?.status || row.status} onChange={event => edit(row.id, { status: event.target.value })}>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="fv-user-reason">变更原因<input aria-label={`${row.display_name}的变更原因`} disabled={row.id === self?.id} maxLength={500} value={edits[row.id]?.reason || ""} onChange={event => edit(row.id, { reason: event.target.value })} placeholder="例如：审核通过，分配业务人员角色" /></label><button type="button" disabled={busy === row.id || row.id === self?.id || !edits[row.id]?.reason?.trim()} onClick={() => save(row)}>{busy === row.id ? "保存中…" : "保存变更"}</button></div>
      {row.id === self?.id && <small className="fv-user-hint">当前账号不能在此修改自己的角色或状态。</small>}</article>)}{!data.items.length && <p className="fv-user-empty">没有符合条件的用户，请调整搜索或状态筛选。</p>}</div>}
    <div className="fv-users-pager"><span>第 {page} / {Math.max(1, Math.ceil(data.total / 20))} 页</span><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><button type="button" disabled={page * 20 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div>
  </div>;
}
