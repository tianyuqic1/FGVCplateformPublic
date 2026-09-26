import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { searchEntities } from "../api/search.js";
import { Icon } from "./icons.jsx";
import { canAccessPath, useAuth } from "../auth/AuthContext.jsx";

const navItems = [
  { id: "dashboard", label: "工作台", icon: "LayoutDashboard", to: "/" },
  { id: "datasets", label: "数据集", icon: "Database", to: "/datasets" },
  { id: "annotation", label: "AI 辅助标注", icon: "ScanSearch", to: "/annotation" },
  { id: "training", label: "训练任务", icon: "FlaskConical", to: "/training" },
  { id: "inference", label: "模型推理", icon: "ImageUp", to: "/inference" },
  { id: "weights", label: "预训练权重", icon: "HardDrive", to: "/weights" },
  { id: "review", label: "人工复核", icon: "UserCheck", to: "/review" },
  { id: "feedback", label: "反馈池", icon: "DatabaseZap", to: "/feedback" },
  { id: "models", label: "模型版本", icon: "Boxes", to: "/models" },
  { id: "pipelines", label: "任务流水线", icon: "Route", to: "/pipelines" },
  { id: "hardware", label: "计算资源", icon: "Cpu", to: "/hardware" },
  { id: "workers", label: "Worker 状态", icon: "Boxes", to: "/workers" },
  { id: "admin", label: "用户管理", icon: "ShieldCheck", to: "/admin/users" },
];

const searchItems = [
  { label: "Worker 状态", hint: "执行服务、实例心跳、负载与当前任务", to: "/workers" },
  { label: "AI 辅助标注", hint: "Top-10 候选、人工确认、图文检索参考", to: "/annotation" },
  { label: "计算资源", hint: "计算节点、GPU、显存、CPU、内存与存储", to: "/hardware" },
  { label: "工作台", hint: "训练状态、待复核样本与模型发布信息", to: "/" },
  { label: "数据集", hint: "导入 ImageFolder、查看类别和样本", to: "/datasets" },
  { label: "训练任务", hint: "查看成功、失败、运行中训练", to: "/training" },
  { label: "模型推理", hint: "选择已发布模型进行图片分类", to: "/inference" },
  { label: "预训练权重", hint: "DINOv3 / ImageNet ViT-S 与 ResNet-50", to: "/weights" },
  { label: "人工复核", hint: "待复核、历史、人工提交", to: "/review?status=pending" },
  { label: "复核历史", hint: "已进入反馈池的复核记录", to: "/review?status=feedbacked" },
  { label: "反馈池", hint: "训练候选、OOD、坏图、争议", to: "/feedback" },
  { label: "模型版本", hint: "模型版本、性能对比与发布记录", to: "/models" },
  { label: "任务流水线", hint: "后台任务、处理阶段与运行记录", to: "/pipelines" },
];

function navKey(pathname) {
  if (pathname.startsWith("/workers")) return "workers";
  if (pathname.startsWith("/admin")) return "admin";
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
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [entityResults, setEntityResults] = useState([]);
  const [searchState, setSearchState] = useState("idle");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [signoutError, setSignoutError] = useState("");
  const active = navKey(location.pathname);
  useEffect(() => {
    if (user?.role === "annotator") return undefined;
    const query = searchQuery.trim();
    if (!searchFocused || query.length < 2) { setEntityResults([]); setSearchState("idle"); return undefined; }
    const controller = new AbortController();
    setSearchState("loading");
    const timer = window.setTimeout(() => searchEntities(query, controller.signal).then((items) => {
      setEntityResults(items);
      setSearchState("ready");
    }).catch(() => { if (!controller.signal.aborted) setSearchState("error"); }), 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [searchQuery, searchFocused, user?.role]);
  const filteredSearchItems = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return searchItems.filter(item => canAccessPath(user?.role, item.to)).slice(0, 5);
    const pages = searchItems.filter((item) => canAccessPath(user?.role, item.to) && `${item.label} ${item.hint}`.toLowerCase().includes(query));
    return [...entityResults.filter(item => canAccessPath(user?.role, item.to)), ...pages].slice(0, 10);
  }, [searchQuery, entityResults, user?.role]);

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
            <span className="small">视觉模型平台</span>
          </div>
        </div>
        <button type="button" className="mobile-menu-button" aria-label={mobileMenuOpen ? "关闭导航菜单" : "打开导航菜单"} aria-expanded={mobileMenuOpen} aria-controls="primary-navigation" onClick={() => setMobileMenuOpen((open) => !open)}><Icon name={mobileMenuOpen ? "X" : "Menu"} size={18} /><span>菜单</span></button>
        <div className="nav-caption">功能导航</div>
        <nav id="primary-navigation" aria-label="主导航" className={`nav-section ${mobileMenuOpen ? "mobile-nav-open" : ""}`}>
          {navItems.filter(item => canAccessPath(user?.role, item.to)).map((item) => (
            <NavLink className={`nav-button ${active === item.id ? "active" : ""}`} key={item.id} to={item.to} onClick={() => setMobileMenuOpen(false)}>
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-card">
          <div className="sidebar-card-kicker"><span /> 数据与模型</div>
          <h3>版本关联</h3>
          <p className="small">查看数据集、训练任务和模型的关联记录。</p>
          <div className="sidebar-card-meta"><span>关联关系</span><strong>数据集 → 模型</strong></div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <div className="crumb">{crumb}</div>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            {user?.role !== "annotator" && <div className="search-wrap">
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
            </div>}
            {user?.role !== "annotator" && <button type="button" className="secondary-button" onClick={() => navigate("/inference")}>
              <Icon name="ImageUp" size={16} />
              推理
            </button>}
            <div className="fv-topbar-user"><strong>{user?.display_name}</strong><small>{({ admin: "管理员", annotator: "标注员", business: "业务人员" })[user?.role]}</small></div>
            {signoutError && <span className="fv-signout-error" role="alert">{signoutError}</span>}
            <button type="button" className="secondary-button fv-signout" onClick={async () => { setSignoutError(""); try { await logout(); } catch { setSignoutError("退出失败，请稍后重试"); } }}>退出</button>
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
