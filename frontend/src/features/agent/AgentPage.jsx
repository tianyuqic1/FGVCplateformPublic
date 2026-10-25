import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiBaseUrl, fetchJson } from "../../api/http.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import { Icon } from "../../components/icons.jsx";
import "./agent.css";

const names = {
  queued: "排队中",
  running: "正在分析",
  waiting_confirmation: "等待你确认",
  waiting_auth: "需要恢复授权",
  unknown: "结果待核对",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已停止",
};
const terminal = new Set(["succeeded", "failed", "cancelled", "unknown"]);
const toolNames = {
  "training.submit_plan": "提交训练任务",
  "training.cancel_run": "取消训练任务",
  "inference.run": "执行样本推理",
};
const eventNames = {
  queued: "进入队列",
  running: "开始执行",
  model_dispatched: "请求模型分析",
  model_response: "模型提出工具调用",
  tools_dispatched: "查询平台数据",
  tool_results: "查询完成",
  tool_result: "平台操作完成",
  plan_validated: "训练方案校验通过",
  proposal: "等待确认方案",
  confirmed: "用户已确认",
  write_dispatched: "提交平台操作",
  write_completed: "已保存操作结果",
  answer: "回答完成",
  cancellation: "收到停止请求",
  cancelled: "执行已停止",
  failed: "执行失败",
  unknown: "需要核对结果",
  waiting_auth: "需要恢复授权",
  recovered: "任务已恢复",
  operation_recovered: "已核对平台结果",
};
const suggestions = [
  "看看当前有哪些数据集，哪些版本可以用来训练",
  "检查最近的训练任务，分析失败原因",
  "看看计算资源是否充足，并推荐一个训练方案",
];
const api = (path, options) => fetchJson(`/api/agent${path}`, options);

export function AgentPage() {
  const location = useLocation();
  return <AgentPanel initialPrompt={location.state?.agentPrompt} />;
}

export function AgentLauncher() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  if (location.pathname === "/agent") return null;
  return (
    <>
      <button type="button" className="agent-launcher" onClick={() => setOpen(true)} aria-label="打开实验助手">
        <Icon name="Sparkles" size={18} /> 实验助手
      </button>
      {open && (
        <aside className="agent-drawer" role="dialog" aria-modal="false" aria-label="实验助手">
          <header>
            <strong>实验助手</strong>
            <div>
              <Link to="/agent" onClick={() => setOpen(false)}>
                完整工作区
              </Link>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭助手">
                ×
              </button>
            </div>
          </header>
          <AgentPanel compact contextPath={location.pathname + location.search} />
        </aside>
      )}
    </>
  );
}

function resourceLink(evidence) {
  const { type, id } = evidence;
  const paths = { run: "/training/", training_run: "/training/", model_version: "/models/", dataset: "/datasets/" };
  return paths[type] ? `${paths[type]}${encodeURIComponent(id)}` : null;
}

function Trace({ events = [], runId }) {
  const [saved, setSaved] = useState([]);
  const [error, setError] = useState("");
  const items = events.length ? events : saved;
  return (
    <details
      className="agent-trace"
      onToggle={(event) => {
        if (event.currentTarget.open && !items.length)
          api(`/runs/${runId}/events?format=json`)
            .then((data) => setSaved(data.items))
            .catch((e) => setError(e.message));
      }}
    >
      <summary>执行记录与证据{items.length ? ` · ${items.length} 条` : ""}</summary>
      {error && <p>{error}</p>}
      <ol>
        {items.map((event) => (
          <li key={event.id}>
            <span>{eventNames[event.kind] || event.kind}</span>
            <time>{new Date(event.created_at).toLocaleTimeString()}</time>
            {["tool_results", "tool_result", "plan_validated", "operation_recovered"].includes(event.kind) && (
              <details>
                <summary>查看查询结果</summary>
                <pre>{JSON.stringify(event.data, null, 2)}</pre>
                {(Array.isArray(event.data) ? event.data : [event.data])
                  .flatMap((item) => item.result?.evidence || item.evidence || [])
                  .map((evidence, index) => {
                    const href = resourceLink(evidence);
                    return href ? (
                      <Link key={`${evidence.id}-${index}`} to={href}>
                        {evidence.type} · {evidence.id}
                      </Link>
                    ) : null;
                  })}
              </details>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}

export function AgentPanel({ compact = false, contextPath = "", initialPrompt = "" }) {
  const [conversations, setConversations] = useState([]);
  const { user } = useAuth();
  const selectionKey = `finevision:agent:${user.id}`;
  const [selected, setSelected] = useState(() => initialPrompt ? "" : sessionStorage.getItem(selectionKey) || "");
  const [runs, setRuns] = useState([]);
  const [traces, setTraces] = useState({});
  const [input, setInput] = useState(() => typeof initialPrompt === "string" ? initialPrompt : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [operation, setOperation] = useState(null);
  const pending = useRef(null);
  const current = useRef("");
  const bottom = useRef(null);
  const active = runs.find((run) => !terminal.has(run.status));
  const latest = runs.at(-1);

  const loadList = useCallback(async () => {
    const data = await api("/conversations");
    setConversations(data.items);
  }, []);
  const loadRuns = useCallback(async (id) => {
    const data = await api(`/conversations/${id}`);
    if (current.current === id) setRuns(data.items);
  }, []);
  useEffect(() => {
    let alive = true;
    loadList().catch((e) => {
      if (alive) setError(e.message);
    });
    return () => {
      alive = false;
    };
  }, [loadList]);
  useEffect(() => {
    current.current = selected;
    sessionStorage.setItem(selectionKey, selected);
    setRuns([]);
    setTraces({});
    setOperation(null);
    if (!selected) return undefined;
    const controller = new AbortController();
    api(`/conversations/${selected}`, { signal: controller.signal })
      .then((data) => setRuns(data.items))
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [selected, selectionKey]);
  useEffect(() => {
    if (!latest) return undefined;
    let alive = true;
    const source = new EventSource(`${apiBaseUrl()}/api/agent/runs/${latest.id}/events`);
    const onUpdate = (event) => {
      if (!alive) return;
      const value = JSON.parse(event.data);
      setTraces((previous) => {
        const items = previous[latest.id] || [];
        return { ...previous, [latest.id]: items.some((item) => item.id === value.id) ? items : [...items, value] };
      });
      if (
        ["answer", "proposal", "failed", "unknown", "waiting_auth", "cancelled", "recovered", "confirmed"].includes(
          value.kind,
        )
      )
        loadRuns(selected).catch((e) => {
          if (alive) setError(e.message);
        });
    };
    source.addEventListener("update", onUpdate);
    const timer = window.setInterval(() => {
      if (!terminal.has(latest.status))
        loadRuns(selected).catch((e) => {
          if (alive) setError(e.message);
        });
    }, 2500);
    return () => {
      alive = false;
      source.close();
      window.clearInterval(timer);
    };
  }, [latest?.id, latest?.status, selected, loadRuns]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [runs.length, latest?.status]);

  async function send(event) {
    event?.preventDefault();
    if (!input.trim() || busy || active) return;
    setBusy(true);
    setError("");
    const text = input.trim();
    const fullInput = contextPath ? `${text}\n\n当前页面：${contextPath}` : text;
    try {
      let id = selected;
      if (!id) {
        const conversation = await api("/conversations", { method: "POST", body: { title: text.slice(0, 30) } });
        id = conversation.id;
        current.current = id;
        setSelected(id);
      }
      if (!pending.current || pending.current.fullInput !== fullInput || pending.current.id !== id)
        pending.current = { fullInput, id, key: crypto.randomUUID() };
      const run = await api("/runs", {
        method: "POST",
        body: {
          conversation_id: id,
          request_key: pending.current.key,
          input: fullInput,
        },
      });
      if (current.current === id)
        setRuns((previous) => (previous.some((item) => item.id === run.id) ? previous : [...previous, run]));
      setInput("");
      pending.current = null;
      await loadList();
      await loadRuns(id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function action(run, command) {
    setBusy(true);
    setError("");
    try {
      await api(`/runs/${run.id}/${command}`, {
        method: "POST",
        ...(command === "confirm" ? { body: { operation_id: run.proposal.operation_id } } : {}),
      });
      await loadRuns(selected);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function inspect(run) {
    try {
      setOperation(await api(`/runs/${run.id}/operation`));
    } catch (e) {
      setError(e.message);
    }
  }
  function choose(id) {
    current.current = id;
    setSelected(id);
    setError("");
    pending.current = null;
  }

  return (
    <section className={`agent-workspace ${compact ? "agent-compact" : ""}`}>
      {!compact && (
        <nav className="agent-history" aria-label="助手历史对话">
          <div className="agent-history-heading">
            <Icon name="Sparkles" size={20} />
            <strong>实验助手</strong>
          </div>
          <button type="button" className="agent-new" onClick={() => choose("")}>
            ＋ 新建对话
          </button>
          {conversations.length === 0 && <p>对话会自动保存在这里</p>}
          {conversations.map((item) => (
            <button
              type="button"
              key={item.id}
              className={selected === item.id ? "selected" : ""}
              onClick={() => choose(item.id)}
            >
              {item.title}
              <small>{new Date(item.updated_at).toLocaleDateString()}</small>
            </button>
          ))}
        </nav>
      )}
      <div className="agent-chat">
        <header className="agent-chat-heading">
          <div>
            <span className="agent-eyebrow">FINEVISION ASSISTANT</span>
            <h2>{conversations.find((c) => c.id === selected)?.title || "把实验交给更清晰的思路"}</h2>
          </div>
          {compact && (
            <select aria-label="选择历史对话" value={selected} onChange={(e) => choose(e.target.value)}>
              <option value="">新建对话</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          )}
        </header>
        {selected && <WatchPanel key={selected} conversationId={selected} onAnalyze={(text) => setInput(text)} />}
        <div className="agent-messages" aria-live="polite">
          {runs.length === 0 && (
            <div className="agent-welcome">
              <div className="agent-welcome-icon">
                <Icon name="Sparkles" size={28} />
              </div>
              <h3>从一个问题开始</h3>
              <p>查询数据、诊断训练、比较模型，或一起准备下一个实验。提交训练、取消任务和推理前，都会请你确认。</p>
              <div className="agent-suggestions">
                {suggestions.map((text) => (
                  <button type="button" key={text} onClick={() => setInput(text)}>
                    {text}
                    <span>↗</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {runs.map((run) => (
            <article className="agent-turn" key={run.id}>
              <div className="agent-user-message">{run.input}</div>
              <div className="agent-answer">
                <span className={`agent-status agent-status-${run.status}`}>{names[run.status]}</span>
                {run.output && <div className="agent-prose"><Markdown skipHtml remarkPlugins={[remarkGfm]} allowedElements={["p", "a", "strong", "em", "ul", "ol", "li", "pre", "code", "blockquote", "hr", "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "del", "br"]} components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>, table: ({ children }) => <div className="agent-table"><table>{children}</table></div> }}>{run.output}</Markdown></div>}
                {run.error && <p className="agent-run-error">{run.error}</p>}
                {run.status === "waiting_confirmation" && run.proposal && (
                  <section className="agent-proposal">
                    <strong>{toolNames[run.proposal.call.function.name] || run.proposal.call.function.name}</strong>
                    <p>请核对以下完整参数。确认后会实际调用平台。</p>
                    <pre>{JSON.stringify(run.proposal.arguments, null, 2)}</pre>
                    <small>操作编号：{run.proposal.operation_id}</small>
                    <div>
                      <button
                        type="button"
                        disabled={busy}
                        className="agent-primary"
                        onClick={() => action(run, "confirm")}
                      >
                        确认执行
                      </button>
                      <button type="button" disabled={busy} onClick={() => action(run, "cancel")}>
                        拒绝方案
                      </button>
                    </div>
                  </section>
                )}
                {run.status === "waiting_auth" && (
                  <button type="button" disabled={busy} onClick={() => action(run, "resume")}>
                    恢复当前登录授权
                  </button>
                )}
                {run.status === "unknown" && run.proposal && (
                  <button type="button" onClick={() => inspect(run)}>
                    核对平台操作结果
                  </button>
                )}
                <Trace events={traces[run.id]} runId={run.id} />
                {run.status === "succeeded" && (
                  <small className="agent-usage">
                    {run.rounds} 轮分析 · {run.tools} 次工具调用 · {run.input_tokens + run.output_tokens} tokens
                  </small>
                )}
              </div>
            </article>
          ))}
          {operation && (
            <section className="agent-operation">
              <strong>平台操作结果</strong>
              <pre>{JSON.stringify(operation, null, 2)}</pre>
            </section>
          )}
          <div ref={bottom} />
        </div>
        <footer className="agent-composer">
          {error && (
            <div role="alert" className="agent-error">
              {error}
              <button
                type="button"
                onClick={() => {
                  setError("");
                  loadList().catch((e) => setError(e.message));
                  if (selected) loadRuns(selected).catch((e) => setError(e.message));
                }}
              >
                刷新
              </button>
            </div>
          )}
          {contextPath && <small>当前页面：{contextPath}</small>}
          <form onSubmit={send}>
            <label className="sr-only" htmlFor={compact ? "agent-drawer-input" : "agent-input"}>
              向实验助手提问
            </label>
            <textarea
              id={compact ? "agent-drawer-input" : "agent-input"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              maxLength={6000}
              rows={3}
              placeholder="例如：最近训练为什么失败？帮我检查数据和资源。"
              disabled={busy}
            />
            <div>
              <span>回答基于实时工具结果 · 写操作需要确认</span>
              {active && ["queued", "running", "waiting_auth"].includes(active.status) && (
                <button
                  type="button"
                  disabled={busy || active.cancel_requested}
                  onClick={() => action(active, "cancel")}
                >
                  {active.cancel_requested ? "正在停止…" : "停止"}
                </button>
              )}
              <button type="submit" className="agent-primary" disabled={busy || !!active || !input.trim()}>
                {busy ? "提交中…" : "发送"}
              </button>
            </div>
          </form>
        </footer>
      </div>
    </section>
  );
}

function WatchPanel({ conversationId, onAnalyze }) {
  const [open, setOpen] = useState(false);
  const [runId, setRunId] = useState("");
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const data = await api(`/watches?conversation_id=${conversationId}`);
    setItems(data.items);
  }, [conversationId]);
  useEffect(() => {
    let alive = true;
    const update = () => {
      api(`/watches?conversation_id=${conversationId}`)
        .then((data) => {
          if (alive) setItems(data.items);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    };
    update();
    const timer = window.setInterval(update, 5000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [conversationId]);
  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/watches", { method: "POST", body: { conversation_id: conversationId, run_id: runId.trim() } });
      setRunId("");
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function change(id, command) {
    try {
      await api(`/watches/${id}/${command}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <details className="agent-watch" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        训练跟踪
        {items.filter((item) => item.status === "active").length
          ? ` · ${items.filter((item) => item.status === "active").length} 项进行中`
          : ""}
      </summary>
      <p>每 30 秒查询一次，最长 24 小时。后台授权 30 分钟后需手动恢复。只跟踪状态，不自动提交新实验。</p>
      <form onSubmit={create}>
        <input
          aria-label="要跟踪的训练运行 ID"
          placeholder="粘贴训练运行 ID"
          value={runId}
          onChange={(event) => setRunId(event.target.value)}
          required
        />
        <button type="submit" disabled={busy}>
          开始跟踪
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {items.map((item) => (
        <div className="agent-watch-item" key={item.id}>
          <Link to={`/training/${encodeURIComponent(item.training_run_id)}`}>{item.training_run_id}</Link>
          <span>
            {
              {
                active: "跟踪中",
                waiting_auth: "需要授权",
                completed: "跟踪完成",
                expired: "已到期",
                cancelled: "已停止",
              }[item.status]
            }{" "}
            · {item.observed_status || "等待首次查询"}
          </span>
          {item.error && <small>{item.error}</small>}
          {item.status === "active" && (
            <button type="button" onClick={() => change(item.id, "cancel")}>
              停止跟踪
            </button>
          )}
          {item.status === "waiting_auth" && (
            <>
              <button type="button" onClick={() => change(item.id, "resume")}>
                恢复授权
              </button>
              <button type="button" onClick={() => change(item.id, "cancel")}>
                停止跟踪
              </button>
            </>
          )}
          {item.status === "completed" && item.observed_status && (
            <button
              type="button"
              onClick={() => onAnalyze(`分析训练 ${item.training_run_id} 的最终结果，查询其指标并给出建议。`)}
            >
              分析结果
            </button>
          )}
        </div>
      ))}
    </details>
  );
}
