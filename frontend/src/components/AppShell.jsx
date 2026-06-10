import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "./icons.jsx";

const navItems = [
  { id: "dashboard", label: "工作台", icon: "LayoutDashboard", to: "/" },
  { id: "datasets", label: "数据集", icon: "Database", to: "/datasets" },
  { id: "training", label: "训练", icon: "FlaskConical", to: "/training" },
  { id: "inference", label: "推理实验室", icon: "ImageUp", to: "/inference" },
  { id: "review", label: "人工复核", icon: "UserCheck", to: "/review" },
  { id: "models", label: "模型版本", icon: "Boxes", to: "/models" },
  { id: "pipelines", label: "流水线", icon: "Route", to: "/pipelines" },
];

function navKey(pathname) {
  if (pathname.startsWith("/datasets")) return "datasets";
  if (pathname.startsWith("/training")) return "training";
  if (pathname.startsWith("/inference")) return "inference";
  if (pathname.startsWith("/review")) return "review";
  if (pathname.startsWith("/models")) return "models";
  if (pathname.startsWith("/pipelines")) return "pipelines";
  return "dashboard";
}

export function AppShell({ title, crumb, children, onToast }) {
  const location = useLocation();
  const navigate = useNavigate();
  const active = navKey(location.pathname);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="ScanSearch" size={20} />
          </div>
          <div>
            <strong>FineVision</strong>
            <span className="small">classification workbench</span>
          </div>
        </div>
        <div className="nav-caption">Main</div>
        <nav className="nav-section">
          {navItems.map((item) => (
            <NavLink className={`nav-button ${active === item.id ? "active" : ""}`} key={item.id} to={item.to}>
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-card">
          <Icon name="ShieldCheck" size={18} />
          <h3>生产策略</h3>
          <p className="small">bird-cls-v4 · DINOv3-L · OOD Gate</p>
          <div className="meter" style={{ "--fill": "#0f766e", "--value": "82%" }}>
            <i />
          </div>
          <div className="row-meta">自动覆盖率 82%</div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <div className="crumb">{crumb}</div>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            <label className="search-box">
              <Icon name="Search" size={16} />
              <input placeholder="搜索数据集、样本、模型版本" />
            </label>
            <button className="secondary-button" onClick={() => navigate("/inference")}>
              <Icon name="ImageUp" size={16} />
              推理
            </button>
            <button className="primary-button" onClick={() => onToast("训练任务创建入口将在下一迭代接入")}>
              <Icon name="Play" size={16} />
              新建训练
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
