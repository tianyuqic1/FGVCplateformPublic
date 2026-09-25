import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { searchEntities } from "../api/search.js";
import { Icon } from "./icons.jsx";

const navItems = [
  { id: "dashboard", label: "工作台", icon: "LayoutDashboard", to: "/" },
  { id: "datasets", label: "数据集", icon: "Database", to: "/datasets" },
  { id: "annotation", label: "AI 标注", icon: "ScanSearch", to: "/annotation" },
  { id: "training", label: "训练", icon: "FlaskConical", to: "/training" },
  { id: "inference", label: "推理实验室", icon: "ImageUp", to: "/inference" },
  { id: "weights", label: "权重", icon: "HardDrive", to: "/weights" },
  { id: "review", label: "人工复核", icon: "UserCheck", to: "/review" },
  { id: "feedback", label: "反馈池", icon: "DatabaseZap", to: "/feedback" },
  { id: "models", label: "模型版本", icon: "Boxes", to: "/models" },
  { id: "pipelines", label: "流水线", icon: "Route", to: "/pipelines" },
  { id: "hardware", label: "硬件监控", icon: "Cpu", to: "/hardware" },
];

const searchItems = [
  { label: "AI 标注", hint: "Top-10 候选、人工确认、图文检索记忆", to: "/annotation" },
  { label: "硬件监控", hint: "计算节点、GPU、显存、CPU、内存与存储", to: "/hardware" },
  { label: "工作台", hint: "运营概览、优先任务、低置信样本", to: "/" },
  { label: "数据集", hint: "导入 ImageFolder、查看类别和样本", to: "/datasets" },
  { label: "训练队列", hint: "查看成功、失败、运行中训练", to: "/training" },
  { label: "推理实验室", hint: "上传图片运行 scoped inference", to: "/inference" },
  { label: "权重管理", hint: "DINOv3 / ImageNet ViT-S 与 ResNet-50", to: "/weights" },
  { label: "人工复核", hint: "待复核、历史、人工提交", to: "/review?status=pending" },
  { label: "复核历史", hint: "已进入反馈池的复核记录", to: "/review?status=feedbacked" },
  { label: "反馈池", hint: "训练候选、OOD、坏图、争议", to: "/feedback" },
  { label: "模型版本", hint: "候选模型、发布门禁", to: "/models" },
  { label: "流水线", hint: "任务节点、worker 边界", to: "/pipelines" },
];

function navKey(pathname) {
  if (pathname.startsWith("/annotation")) return "annotation";
  if (pathname === "/hardware") return "hardware";
  if (pathname.startsWith("/datasets")) return "datasets";
  if (pathname.startsWith("/training")) return "training";
  if (pathname.startsWith("/inference")) return "inference";
  if (pathname.startsWith("/weights")) return "weights";
  if (pathname.startsWith("/review")) return "review";
  if (pathname.startsWith("/feedback")) return "feedback";
  if (pathname.startsWith("/models")) return "models";
  if (pathname.startsWith("/pipelines")) return "pipelines";
  return "dashboard";
}

export function AppShell({ title, crumb, children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [entityResults, setEntityResults] = useState([]);
  const [searchState, setSearchState] = useState("idle");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const active = navKey(location.pathname);
  useEffect(() => {
    const query = searchQuery.trim();
    if (!searchFocused || query.length < 2) { setEntityResults([]); setSearchState("idle"); return undefined; }
    const controller = new AbortController();
    setSearchState("loading");
    const timer = window.setTimeout(() => searchEntities(query, controller.signal).then((items) => {
      setEntityResults(items);
      setSearchState("ready");
    }).catch(() => { if (!controller.signal.aborted) setSearchState("error"); }), 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [searchQuery, searchFocused]);
  const filteredSearchItems = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return searchItems.slice(0, 5);
    const pages = searchItems.filter((item) => `${item.label} ${item.hint}`.toLowerCase().includes(query));
    return [...entityResults, ...pages].slice(0, 10);
  }, [searchQuery, entityResults]);

  function goSearch(item) {
    navigate(item.to);
    setSearchQuery("");
    setSearchFocused(false);
  }

  function handleSearchKeyDown(event) {
    if (event.key !== "Enter") return;
    const first = filteredSearchItems[0];
    if (!first) return;
    event.preventDefault();
    goSearch(first);
  }

  return (
    <div className="app-shell refreshed-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="ScanSearch" size={20} />
          </div>
          <div>
            <strong>FineVision</strong>
            <span className="small">Research Console</span>
          </div>
        </div>
        <button type="button" className="mobile-menu-button" aria-label={mobileMenuOpen ? "关闭导航菜单" : "打开导航菜单"} aria-expanded={mobileMenuOpen} aria-controls="primary-navigation" onClick={() => setMobileMenuOpen((open) => !open)}><Icon name={mobileMenuOpen ? "X" : "Menu"} size={18} /><span>菜单</span></button>
        <div className="nav-caption">Workspace</div>
        <nav id="primary-navigation" aria-label="主导航" className={`nav-section ${mobileMenuOpen ? "mobile-nav-open" : ""}`}>
          {navItems.map((item) => (
            <NavLink className={`nav-button ${active === item.id ? "active" : ""}`} key={item.id} to={item.to} onClick={() => setMobileMenuOpen(false)}>
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-card">
          <div className="sidebar-card-kicker"><span /> Governed workspace</div>
          <h3>可追溯实验空间</h3>
          <p className="small">数据版本、训练运行、模型产物与发布记录保持明确关联。</p>
          <div className="sidebar-card-meta"><span>Trace scope</span><strong>Dataset → Model</strong></div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <div className="crumb">{crumb}</div>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            <div className="search-wrap">
              <label className="search-box">
                <Icon name="Search" size={16} />
                <input
                  aria-label="全局搜索"
                  value={searchQuery}
                  onBlur={() => window.setTimeout(() => setSearchFocused(false), 120)}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onFocus={() => setSearchFocused(true)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder="搜索页面或真实数据集、训练、模型、复核"
                />
              </label>
              {searchFocused && (
                <div className="search-results">
                  {filteredSearchItems.length > 0 ? (
                    filteredSearchItems.map((item) => (
                      <button type="button" key={item.to} onMouseDown={(event) => event.preventDefault()} onClick={() => goSearch(item)}>
                        <Icon name="Search" size={15} />
                        <span><strong>{item.label}</strong><small>{item.hint}</small></span>
                      </button>
                    ))
                  ) : (
                    <div className="search-empty">{searchState === "loading" ? "正在搜索业务记录…" : searchState === "error" ? "业务搜索暂不可用；页面导航仍可用" : "没有匹配项"}</div>
                  )}
                </div>
              )}
            </div>
            <button type="button" className="secondary-button" onClick={() => navigate("/inference")}>
              <Icon name="ImageUp" size={16} />
              推理
            </button>
          </div>
        </header>
        <section className="page">{children}</section>
      </main>
    </div>
  );
}

export function PageHero({ title, description, actions }) {
  return (
    <div className="page-hero">
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {actions && <div className="toolbar">{actions}</div>}
    </div>
  );
}
