import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { fetchJson } from "../../api/http.js";
import { PageHeading, Panel, EmptyState } from "../../design-system/components/Workbench.jsx";
import { ServerPagination } from "../../design-system/components/ServerPagination.jsx";
import "./workers.css";

export const workerKinds = { training: "训练", inference: "模型推理", annotation: "AI 标注", artifact: "数据校验 / 模型导出", deployment: "部署构建" };
const connections = { online: "在线", delayed: "心跳延迟", offline: "失联 / 已停止" };
const readiness = { ready: "服务已就绪", initializing: "初始化中", dependency_error: "依赖异常", unknown: "就绪状态未知", stopped: "服务已停止" };
const timestamp = value => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未上报";
export function heartbeatAge(value, now) {
  const seconds = Math.max(0, Math.floor((Date.parse(now) - Date.parse(value)) / 1000));
  if (!Number.isFinite(seconds)) return "未上报";
  return seconds < 60 ? `${seconds} 秒前` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 3600)} 小时前`;
}
export function WorkerStatus({ worker }) {
  return <span className={`worker-status worker-status--${worker.connection}`}><i aria-hidden="true" />{worker.readiness === "stopped" ? "已停止" : worker.connection === "offline" ? "失联" : connections[worker.connection] ?? "未知"}</span>;
}

function useWorkers(query, id, automatic, revision) {
  const key = `${query}:${id ?? ""}`;
  const [state, setState] = useState({ key: "", data: null, detail: null, error: "", detailError: "", loading: true });
  useEffect(() => {
    let active = true, timer, controller;
    async function refresh() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const [list, detail] = await Promise.allSettled([
          fetchJson(`/api/workers?${query}`, { signal: controller.signal }),
          id ? fetchJson(`/api/workers/${encodeURIComponent(id)}`, { signal: controller.signal }) : Promise.resolve(null),
        ]);
        if (active) setState({ key, data: list.status === "fulfilled" ? list.value : null,
          detail: detail.status === "fulfilled" ? detail.value : null,
          error: list.status === "rejected" ? list.reason.message : "",
          detailError: detail.status === "rejected" ? detail.reason.message : "", loading: false });
      } finally {
        clearTimeout(timeout);
        if (active && automatic) timer = setTimeout(refresh, document.hidden ? 30000 : 10000);
      }
    }
    refresh();
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, [query, id, automatic, revision, key]);
  return state.key === key ? state : { data: null, detail: null, error: "", detailError: "", loading: true };
}

function Detail({ data, error, loading, closeTo }) {
  const worker = data?.worker;
  const admin = worker?.task_visibility === "all";
  const technicalRows = worker ? [["实例 ID", worker.id], ["运行节点", worker.node_id || "未关联"], ["程序版本", worker.version], ["执行后端", worker.backend],
    ...(admin ? [["启动会话", worker.session_id], ["上报序号", worker.sequence]] : [])] : [];
  const technical = <><dl>{technicalRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "未上报"}</dd></div>)}</dl>
    {worker?.node_id && <Link className="secondary-button" to={`/hardware?node=${encodeURIComponent(worker.node_id)}`}>查看节点计算资源</Link>}</>;
  return <aside className="worker-detail" aria-label="Worker 详情">
    <Panel title="实例详情" aside={<Link to={closeTo}>关闭详情</Link>}>
      {loading ? <p className="worker-note" role="status">正在读取实例状态…</p> : error ? <p className="worker-error" role="alert">{error}</p> : worker && <div className="worker-detail-body">
        <h3>{worker.name}</h3><WorkerStatus worker={worker} />
        <p>{readiness[worker.readiness]} · {worker.can_accept ? "有可用执行槽位" : "当前不计为可接任务"}</p>
        <p className="worker-note">{worker.reason || "暂无状态说明"}</p>
        {worker.connection !== "online" && <p className="worker-warning">以下负载与任务为最后一次上报，不代表当前仍在执行。失联不等于进程已停止。</p>}
        <dl>{[
          ["服务类型", workerKinds[worker.kind]],
          ["计算设备", worker.device === "unknown" ? "未上报" : worker.device], ["并发上限", worker.capacity],
          ["本次启动登记", timestamp(worker.registered_at)], ["最近心跳", timestamp(worker.received_at)],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "未上报"}</dd></div>)}</dl>
        {admin ? technical : <details className="worker-technical"><summary>技术信息（排障时查看）</summary>{technical}</details>}
        <h4>{admin ? "当前任务 / 请求" : "我的训练任务"}</h4>
        {worker.tasks?.length ? <ul className="worker-task-list">{worker.tasks.map(task => <li key={task.id}>
          <strong>{task.stage}</strong><code>{task.id}</code><span>开始于 {timestamp(task.started_at)}</span>
          {task.kind === "training" && <Link to={`/training/${encodeURIComponent(task.id)}`}>查看训练任务 →</Link>}
        </li>)}</ul> : <p className="worker-note">{!admin ? "此实例当前没有可确认属于你的训练任务。其他任务仅计入总负载。" : worker.connection === "online" ? "暂无执行中的任务或请求。" : "最后一次上报无执行任务。"}</p>}
        <p className="worker-note">{admin ? "训练进度与结果以任务详情为准。推理和导出的编号是请求标识，不是模型版本号。" : "仅展示归属已核实的本人训练任务；历史归属不明的任务，以及推理、标注、导出请求的明细不展示。"}</p>
        <h4>{admin ? "诊断说明" : "遇到异常怎么办"}</h4><p className="worker-note">{admin ? "心跳只证明上报链路可达；未独立探测的依赖不显示为正常。可在任务详情或独立日志控制台按实例 ID、任务 ID 排查。" : "可先查看自己的训练任务详情；服务持续不可用时，请向管理员提供实例名称和发生时间。此页面不提供重启或停止服务的操作。"}</p>
      </div>}
    </Panel>
  </aside>;
}

export function WorkersPage() {
  const { workerId } = useParams();
  const [params, setParams] = useSearchParams();
  const [automatic, setAutomatic] = useState(true);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const query = new URLSearchParams({ limit: "6", offset: params.get("offset") || "0", q: params.get("q") || "", kind: params.get("kind") || "", status: params.get("status") || "", device: params.get("device") || "" }).toString();
  const state = useWorkers(query, workerId, automatic, revision);
  useEffect(() => { setSearch(params.get("q") ?? ""); }, [params]);
  function filter(name, value) {
    setParams(previous => { const next = new URLSearchParams(previous); value ? next.set(name, value) : next.delete(name); next.delete("offset"); return next; });
  }
  const summary = state.data?.summary;
  const admin = state.data?.view === "admin";
  const suffix = params.size ? `?${params}` : "";
  return <div className="workers-page">
    <PageHeading title="Worker 状态" description="查看执行服务是否在线、当前负载与关联任务。此页只读，不会重启服务或调整任务租约。" />
    <p className="worker-note">{admin ? "管理员视图 · 全部实例诊断与关联任务" : "业务只读视图 · 查看共享服务负载与本人训练任务，技术信息默认折叠"}</p>
    <div className="worker-toolbar"><span>最近读取：{state.data ? timestamp(state.data.server_time) : "尚未读取"}</span><div>
      <label><input type="checkbox" checked={automatic} onChange={e => setAutomatic(e.target.checked)} />自动刷新（10 秒）</label>
      <button type="button" className="secondary-button" onClick={() => setRevision(v => v + 1)}>刷新</button>
    </div></div>
    <section className="worker-summary" aria-label="筛选结果汇总">{[["total", "已登记实例"], ["available", "可接任务"], ["busy", "执行中"], ["abnormal", "状态异常"]].map(([key, label]) => <div key={key}><span>{label}</span><strong>{summary ? summary[key] : "—"}</strong></div>)}</section>
    <p className="worker-note">数量按全部筛选结果统计，不限本页。“可接任务”表示心跳正常、服务已就绪且未满载，不保证每个模型或依赖均可用。</p>
    <div className={`worker-layout ${workerId ? "worker-layout--detail" : ""}`}>
      <Panel title="实例列表" aside={<span className="worker-note">异常优先 · 每页 6 个</span>}>
        <div className="worker-filters">
          <form onSubmit={e => { e.preventDefault(); filter("q", search.trim()); }}><input aria-label="搜索实例" placeholder="搜索名称、实例 ID 或节点" value={search} onChange={e => setSearch(e.target.value)} /><button className="secondary-button" type="submit">搜索</button></form>
          <select aria-label="服务类型" value={params.get("kind") || ""} onChange={e => filter("kind", e.target.value)}><option value="">全部类型</option>{Object.entries(workerKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <select aria-label="连接状态" value={params.get("status") || ""} onChange={e => filter("status", e.target.value)}><option value="">全部连接状态</option>{Object.entries(connections).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <select aria-label="计算设备" value={params.get("device") || ""} onChange={e => filter("device", e.target.value)}><option value="">全部设备</option><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="npu">NPU</option><option value="unknown">未上报</option></select>
          <button type="button" className="secondary-button" onClick={() => setParams({})}>重置筛选</button>
        </div>
        {state.loading ? <p className="worker-note" role="status">正在读取 Worker 状态…</p> : state.error ? <p className="worker-error" role="alert">无法读取 Worker 状态：{state.error}。请重试；不能据此判断服务离线。</p> : state.data?.items.length ? <>
          <div className="worker-table-scroll"><table className="worker-table"><thead><tr><th>实例 / 类型</th><th>{admin ? "节点 / 设备" : "计算设备"}</th><th>连接 / 就绪</th><th>总负载</th><th>最近心跳</th><th>操作</th></tr></thead><tbody>{state.data.items.map(worker => <tr key={worker.id} className={workerId === worker.id ? "is-selected" : ""}>
            <td><strong>{worker.name}</strong><small>{workerKinds[worker.kind]}</small>{admin && <code title={worker.id}>{worker.id}</code>}</td>
            <td>{admin && (worker.node_id || "未关联节点")}<small>{worker.device === "unknown" ? "设备未上报" : worker.device} · {worker.backend}</small></td>
            <td><WorkerStatus worker={worker} /><small>{readiness[worker.readiness]}</small></td>
            <td><span className="worker-count">{worker.active_count} / {worker.capacity}</span><small>{worker.connection !== "online" ? "最后上报值" : worker.active_count >= worker.capacity ? "容量已满" : worker.active_count ? "执行中" : "空闲"}</small></td>
            <td title={timestamp(worker.received_at)}>{heartbeatAge(worker.received_at, state.data.server_time)}</td>
            <td><Link aria-label={`查看 ${worker.name}`} to={`/workers/${encodeURIComponent(worker.id)}${suffix}`}>查看详情</Link></td>
          </tr>)}</tbody></table></div>
          <ServerPagination pagination={state.data.pagination} unit="个" onPageChange={page => setParams(previous => { const next = new URLSearchParams(previous); next.set("offset", String((page - 1) * 6)); return next; })} />
        </> : <EmptyState title="暂无匹配的 Worker" description="可清除筛选重试。尚未接入实例登记的服务不会自动出现在列表中，也不能据此认定它未运行。" />}
      </Panel>
      {workerId && <Detail data={state.detail} error={state.detailError} loading={state.loading} closeTo={`/workers${suffix}`} />}
    </div>
    <p className="worker-note">上报间隔约 10 秒，超过 30 秒标记心跳延迟，超过 90 秒标记失联。页面不会将节点整体资源占用归到单个 Worker；历史实例保留用于排查。</p>
  </div>;
}
