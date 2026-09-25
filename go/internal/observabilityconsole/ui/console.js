const view = document.querySelector("#view");
const notice = document.querySelector("#notice");
const systemState = document.querySelector("#system-state");
const tabs = [...document.querySelectorAll("[data-tab]")];
const state = { tab: "overview", overview: null, logPage: null, logFilters: { category: "all", level: "", outcome: "", range: "1h", search: "", limit: 50 } };

const escapeHTML = (value = "") => String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
const localTime = value => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const metricValue = metric => `${Number(metric.value || 0).toFixed(metric.unit === "%" || metric.unit === "req/s" ? 2 : 0)}${metric.unit ? ` ${metric.unit}` : ""}`;
const empty = (title, detail) => `<div class="empty"><div><strong>${escapeHTML(title)}</strong><span>${escapeHTML(detail)}</span></div></div>`;

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin" });
  if (response.status === 401) { window.location.reload(); throw new Error("访问凭据已失效"); }
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body?.error?.message || `${response.status} ${response.statusText}`); }
  return response.json();
}
function showError(error) { notice.textContent = error?.message || String(error); notice.classList.remove("hidden"); }
function clearError() { notice.classList.add("hidden"); notice.textContent = ""; }
function setLoading() { clearError(); view.innerHTML = `<div class="panel loading">正在读取观测数据…</div>`; }

function renderOverview(data) {
  state.overview = data;
  systemState.className = `system-state ${data.status || "unknown"}`;
  systemState.innerHTML = `<i></i>${data.status === "healthy" ? "所有核心组件正常" : "部分组件需关注"}`;
  const queues = data.queues || {};
  view.innerHTML = `<div class="metric-grid">${(data.metrics || []).map(metric => `<article class="metric ${escapeHTML(metric.tone)}"><span>${escapeHTML(metric.label)}</span><strong>${escapeHTML(metricValue(metric))}</strong><small>${escapeHTML(metric.caption)}</small></article>`).join("")}</div>
  <div class="two-column"><section class="panel"><header class="panel-head"><div><small>RUNTIME</small><strong>组件状态</strong></div></header><div class="rows">${(data.components || []).map(component => `<div class="status-row"><i class="status-dot ${component.status === "online" ? "online" : ""}"></i><strong>${escapeHTML(component.name)}</strong><span>${component.status === "online" ? "在线" : "不可用"}</span></div>`).join("")}</div></section>
  <section class="panel"><header class="panel-head"><div><small>WORK QUEUES</small><strong>任务与积压</strong></div></header><div class="rows">${queueRow("训练", queues.training)}${queueRow("AI 标注", queues.annotation)}${queueRow("模型部署", queues.deployment)}<div class="queue-row"><strong>事务 Outbox</strong><span>积压 <b>${queues.outbox?.pending || 0}</b></span><span>最老 <b>${Math.round(queues.outbox?.oldest_age_seconds || 0)}s</b></span><span></span></div></div></section></div>`;
}
function queueRow(label, queue = {}) { return `<div class="queue-row"><strong>${label}</strong><span>等待 <b>${queue.queued || 0}</b></span><span>运行 <b>${queue.running || 0}</b></span><span class="${queue.failed > 0 ? "danger" : ""}">异常 <b>${queue.failed || 0}</b></span></div>`; }
async function loadOverview() { setLoading(); try { renderOverview(await api("/api/observability/overview")); } catch (error) { showError(error); view.innerHTML = empty("总览读取失败", "请检查聚合服务与底层组件状态。"); } }

const categoryOptions = [["all","全部模块"],["control","控制面"],["training","训练"],["inference","推理"],["deployment","模型部署"],["annotation","AI 标注"],["llm","大模型"],["hardware","硬件节点"]];
function logShell() {
  const f = state.logFilters;
  view.innerHTML = `<section class="panel"><form id="log-filter" class="filterbar"><input name="search" value="${escapeHTML(f.search)}" placeholder="搜索 request_id、job_id、事件或消息"/><select name="category">${categoryOptions.map(([v,l]) => `<option value="${v}" ${f.category===v?"selected":""}>${l}</option>`).join("")}</select><select name="level"><option value="">全部级别</option>${["ERROR","WARN","INFO","DEBUG"].map(v=>`<option ${f.level===v?"selected":""}>${v}</option>`).join("")}</select><select name="range">${[["15m","15 分钟"],["1h","1 小时"],["6h","6 小时"],["24h","24 小时"],["7d","7 天"]].map(([v,l])=>`<option value="${v}" ${f.range===v?"selected":""}>${l}</option>`).join("")}</select><button class="primary">查询</button></form><div id="log-results"><div class="loading">正在查询日志…</div></div></section>`;
  document.querySelector("#log-filter").addEventListener("submit", event => { event.preventDefault(); const form = new FormData(event.currentTarget); state.logFilters = { ...state.logFilters, search: form.get("search"), category: form.get("category"), level: form.get("level"), range: form.get("range") }; loadLogs(false); });
}
async function loadLogs(append) {
  clearError(); const params = new URLSearchParams(state.logFilters); if (append && state.logPage?.next_end) params.set("end", state.logPage.next_end);
  try { const page = await api(`/api/observability/logs?${params}`); page.items = append ? [...(state.logPage?.items || []), ...(page.items || [])] : page.items; state.logPage = page; renderLogs(page); } catch (error) { showError(error); document.querySelector("#log-results").innerHTML = empty("日志查询失败", "可以稍后重试或检查 Loki 状态。"); }
}
function renderLogs(page) {
  const target = document.querySelector("#log-results");
  if (!page.items?.length) { target.innerHTML = empty("当前范围没有日志", "扩大时间范围或清除筛选条件后重试。"); return; }
  target.innerHTML = `<div class="result-meta"><span>${page.items.length} 条日志 · ${escapeHTML(page.query_summary)}</span><span>高基数字段仅在查询时解析</span></div><div class="log-list">${page.items.map(logRow).join("")}</div>${page.has_more ? '<button id="load-more" class="load-more">加载更早日志</button>' : ""}`;
  target.querySelectorAll("[data-trace]").forEach(button => button.addEventListener("click", () => openTrace(button.dataset.trace)));
  document.querySelector("#load-more")?.addEventListener("click", () => loadLogs(true));
}
function logRow(item) { return `<article class="log-row"><div class="log-gutter"><span class="level ${String(item.level).toLowerCase()}">${escapeHTML(item.level || "INFO")}</span><time>${escapeHTML(localTime(item.timestamp))}</time></div><div class="log-body"><header><strong>${escapeHTML(item.message)}</strong><span>${escapeHTML(item.category)} · ${escapeHTML(item.service)}</span></header><div class="tags">${item.event?`<code>${escapeHTML(item.event)}</code>`:""}${item.outcome?`<span>${escapeHTML(item.outcome)}</span>`:""}${item.request_id?`<code>req · ${escapeHTML(item.request_id)}</code>`:""}${item.trace_id?`<button class="trace-link" data-trace="${escapeHTML(item.trace_id)}">Trace · ${escapeHTML(item.trace_id.slice(0,12))}…</button>`:""}</div></div></article>`; }

function timelineShell() { view.innerHTML = `<section class="panel"><header class="panel-head"><div><small>ENTITY CORRELATION</small><strong>按业务实体还原完整过程</strong></div></header><form id="timeline-form" class="timeline-form"><label>实体类型<select name="entity_type"><option value="training_job">训练任务</option><option value="annotation_task">标注任务</option><option value="deployment">部署任务</option></select></label><label>实体 ID<input required name="entity_id" placeholder="粘贴任务或部署 UUID"/></label><label>日志范围<select name="range"><option value="1h">1 小时</option><option value="6h">6 小时</option><option value="24h" selected>24 小时</option><option value="7d">7 天</option></select></label><button class="primary">生成时间线</button></form><div id="timeline-results">${empty("等待业务实体", "系统将合并 PostgreSQL 审计事实与 Loki 运行日志。")}</div></section>`; document.querySelector("#timeline-form").addEventListener("submit", loadTimeline); }
async function loadTimeline(event) { event.preventDefault(); clearError(); const params = new URLSearchParams(new FormData(event.currentTarget)); const target = document.querySelector("#timeline-results"); target.innerHTML = `<div class="loading">正在生成时间线…</div>`; try { const result = await api(`/api/observability/timeline?${params}`); target.innerHTML = result.items?.length ? `<div class="timeline">${result.items.map(item => `<article class="timeline-item ${item.signal}"><time>${escapeHTML(localTime(item.timestamp))}</time><i></i><div><h3>${escapeHTML(item.message)}</h3><p>${escapeHTML(item.event)}${item.service?` · ${escapeHTML(item.service)}`:""}${item.actor?` · ${escapeHTML(item.actor)}`:""}</p>${item.trace_id?`<button class="trace-link" data-trace="${escapeHTML(item.trace_id)}">查看 Trace</button>`:""}</div></article>`).join("")}</div>` : empty("没有找到时间线事件", "确认实体 ID 与类型是否匹配。"); target.querySelectorAll("[data-trace]").forEach(button => button.addEventListener("click", () => openTrace(button.dataset.trace))); } catch (error) { showError(error); target.innerHTML = empty("时间线生成失败", "确认实体 ID 后重试。"); } }

async function loadAlerts() { setLoading(); try { const result = await api("/api/observability/alerts"); view.innerHTML = `<section class="panel"><header class="panel-head"><div><small>PROMETHEUS ALERTS</small><strong>当前活动告警</strong></div></header>${result.items?.length ? `<div class="alert-list">${result.items.map(item => `<article class="alert-row"><div class="alert-icon ${item.severity}">!</div><div><strong>${escapeHTML(item.summary)}</strong><p>${escapeHTML(item.name)}</p><small>开始于 ${escapeHTML(localTime(item.active_at))}</small></div><span class="level ${item.severity==='critical'?'error':'warn'}">${escapeHTML(item.severity)}</span></article>`).join("")}</div>` : empty("当前没有活动告警", "预置运行规则均未触发。")}</section>`; } catch (error) { showError(error); view.innerHTML = empty("告警读取失败", "请检查 Prometheus 状态。"); } }

async function openTrace(traceId) { const modal = document.querySelector("#trace-modal"); modal.classList.remove("hidden"); document.querySelector("#trace-title").textContent = traceId; const body = document.querySelector("#trace-body"); body.textContent = "正在读取 Tempo…"; try { body.textContent = JSON.stringify(await api(`/api/observability/traces/${encodeURIComponent(traceId)}`), null, 2); } catch (error) { body.textContent = error.message; } }
document.querySelector("#trace-close").addEventListener("click", () => document.querySelector("#trace-modal").classList.add("hidden"));

async function activate(tab) { state.tab = tab; tabs.forEach(button => button.classList.toggle("active", button.dataset.tab === tab)); if (tab === "overview") await loadOverview(); if (tab === "logs") { logShell(); await loadLogs(false); } if (tab === "timeline") timelineShell(); if (tab === "alerts") await loadAlerts(); }
tabs.forEach(button => button.addEventListener("click", () => activate(button.dataset.tab)));
document.querySelector("#refresh").addEventListener("click", () => activate(state.tab));
activate("overview");
window.setInterval(() => { if (state.tab === "overview") loadOverview(); }, 15000);
