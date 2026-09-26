import { useEffect, useId, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Icon } from "../../components/icons.jsx";
import { PageHeading, Panel, EmptyState } from "../../design-system/components/Workbench.jsx";
import { EChart } from "../../design-system/charts/EChart.jsx";
import { useHardware } from "./useHardware.js";
import { chartOption, freshness, gib, gpuSummary, isMetric, percent, ratio, trendData } from "./metrics.js";
import "./hardware.css";

const windows = { "15m": 15 * 60000, "1h": 3600000, "24h": 86400000 };
const statuses = { online: "采集在线", stale: "数据已过期", offline: "采集离线" };
const timeLabel = (time) => new Date(time).toLocaleString("zh-CN", { hour12: false });
function Meter({ value, label }) {
  return <div className="hw-meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={isMetric(value) ? value : undefined} aria-valuetext={isMetric(value) ? percent(value) : "未采集"}><i style={{ width: `${isMetric(value) ? Math.min(100, Math.max(0, value)) : 0}%` }} /></div>;
}
function Sparkline({ history, getter, step }) {
  const points = trendData(history, getter, step).slice(-40);
  const sections = [[]];
  points.forEach(([, value], index) => { if (value === null) sections.push([]); else sections.at(-1).push(`${index / Math.max(1, points.length - 1) * 140},${32 - value * .28}`); });
  return <svg className="hw-sparkline" viewBox="0 0 140 36" aria-hidden="true">{sections.filter((part) => part.length > 1).map((part, index) => <polyline key={index} points={part.join(" ")} fill="none" stroke="currentColor" strokeWidth="2" />)}</svg>;
}
function Tile({ label, value, caption, icon, history, getter, step }) {
  return <article className="hw-tile"><div className="hw-tile-label"><span>{label}</span><Icon name={icon} size={17} /></div><strong>{value}</strong><span className="hw-caption">{caption}</span><Sparkline history={history} getter={getter} step={step} /></article>;
}
function Reading({ value, unit }) { return <>{isMetric(value) ? `${value.toFixed(0)} ${unit}` : "未采集"}</>; }
function GPUCard({ gpu, index }) {
  return <article className="hw-gpu-card">
    <div className="hw-gpu-heading"><div className="hw-chip"><Icon name="Cpu" size={22} /></div><div><span className="hw-eyebrow">GPU {index}</span><h3>{gpu.name}</h3></div><span className="hw-device-state">{!isMetric(gpu.percent) ? "利用率未采集" : gpu.percent > 0 ? "使用中" : "空闲"}</span></div>
    <div className="hw-gpu-meters"><div><div className="hw-between"><span>GPU 利用率</span><strong>{percent(gpu.percent)}</strong></div><Meter value={gpu.percent} label={`${gpu.name} 利用率`} /></div><div><div className="hw-between"><span>显存</span><strong>{gib(gpu.memory.used_bytes)} / {gib(gpu.memory.total_bytes)} GiB</strong></div><Meter value={ratio(gpu.memory)} label={`${gpu.name} 显存占用`} /></div></div>
    <div className="hw-gpu-footer"><span><Icon name="Thermometer" size={15} /><Reading value={gpu.temperature_c} unit="°C" /></span><span><Icon name="Power" size={15} /><Reading value={gpu.power_w} unit="W" /> / <Reading value={gpu.power_limit_w} unit="W" /></span></div>
    <div className="hw-uuid" title={gpu.id}>{gpu.id}</div>
  </article>;
}
export function HardwarePage() {
  const chartGroup = useId();
  const [params, setParams] = useSearchParams();
  const nodeId = params.get("node") ?? "";
  const [window, setWindow] = useState("15m");
  const [automatic, setAutomatic] = useState(true);
  const [revision, setRevision] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const state = useHardware(nodeId, window, automatic, revision);
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    if (!nodeId && state.nodes.length) {
      setParams((previous) => { const next = new URLSearchParams(previous); next.set("node", state.nodes[0].node_id); return next; }, { replace: true });
    }
  }, [nodeId, state.nodes, setParams]);
  const data = state.data;
  const snapshot = data?.snapshot;
  const history = data?.history ?? [];
  const summary = gpuSummary(snapshot);
  const now = Number.isFinite(state.serverTime) ? state.serverTime + clock - state.loadedAt : clock;
  const { status, age } = freshness(snapshot, now);
  const end = data ? Date.parse(data.server_time) : now;
  const step = data?.step_seconds ?? 5;
  const gpuOption = useMemo(() => {
    const devices = new Map();
    for (const sample of data?.history ?? []) for (const gpu of sample.gpus) devices.set(gpu.id, gpu.name);
    const metrics = [...devices].flatMap(([id, name]) => [
      [`GPU · ${id.slice(-6)} 利用率`, (sample) => sample.gpus.find((gpu) => gpu.id === id)?.percent],
      [`GPU · ${id.slice(-6)} 显存`, (sample) => ratio(sample.gpus.find((gpu) => gpu.id === id)?.memory), true],
    ]);
    return chartOption(data?.history ?? [], metrics, step, end - windows[window], end);
  }, [data, step, end, window]);
  const hostOption = useMemo(() => chartOption(data?.history ?? [], [["CPU", (sample) => sample.cpu.percent], ["内存", (sample) => ratio(sample.memory), true]], step, end - windows[window], end), [data, step, end, window]);
  function chooseNode(value) { setParams((previous) => { const next = new URLSearchParams(previous); next.set("node", value); return next; }); }
  const gpuHint = snapshot?.gpu_status === "none" ? "未发现 NVIDIA GPU" : snapshot?.gpu_status === "unavailable" ? "GPU 采集不可用" : `${snapshot?.gpus.length ?? 0} 张 GPU · 算术平均`;
  const errors = snapshot?.errors ?? [];
  return <div className="fv-feature-page hw-page">
    <PageHeading title="计算资源" description="查看训练与推理节点的资源使用情况，关注计算余量与持续的资源压力。" actions={<span className="hw-scope"><Icon name="RadioTower" size={16} />宿主机资源</span>} />
    <div className="hw-toolbar">
      <div className="hw-controls"><label>计算节点<select aria-label="计算节点" value={nodeId} onChange={(event) => chooseNode(event.target.value)}><option value="" disabled>选择节点</option>{nodeId && !state.nodes.some((node) => node.node_id === nodeId) && <option value={nodeId}>{nodeId}</option>}{state.nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.name} · {node.node_id}</option>)}</select></label>{snapshot && <span className={`hw-status ${status}`}><i />{statuses[status]}</span>}</div>
      <div className="hw-controls"><select aria-label="历史时间范围" value={window} onChange={(event) => setWindow(event.target.value)}><option value="15m">最近 15 分钟</option><option value="1h">最近 1 小时</option><option value="24h">最近 24 小时</option></select><button className="secondary-button" aria-pressed={automatic} onClick={() => setAutomatic(!automatic)}><Icon name={automatic ? "Pause" : "Play"} size={15} />自动刷新：{automatic ? "开" : "关"}</button><button className="secondary-button" disabled={state.loading} onClick={() => setRevision((value) => value + 1)}><Icon name="RefreshCw" size={15} />{state.loading ? "刷新中" : "刷新"}</button></div>
    </div>
    {state.error && <div className="hw-notice danger" role="alert"><Icon name="AlertTriangle" size={18} /><div><strong>监控数据读取失败</strong><p>{snapshot ? "保留上次成功读取的数据，请留意采集时间。" : "请检查节点是否已接入，以及监控服务是否可用。"}</p></div><button className="secondary-button" onClick={() => setRevision((value) => value + 1)}>重试</button></div>}
    {!snapshot ? <Panel title="计算节点">{state.loading ? <EmptyState icon="LoaderCircle" title="正在读取硬件状态" description="首次采集后将显示节点和资源曲线。" /> : <EmptyState icon="RadioTower" title={nodeId ? "暂时无法读取此节点" : "尚无计算节点接入"} description={nodeId ? "可切换其他节点，或等待采集服务恢复后重试。" : "启动部署中的 hardware-collector 后，节点会自动出现在这里。"} />}</Panel> : <>
      <div className="hw-meta"><span>{snapshot.name} <code>{snapshot.node_id}</code></span><span>采集于 {timeLabel(snapshot.sampled_at)} · {Math.floor(age)} 秒前收到{!automatic ? " · 自动刷新已暂停" : " · 每 5 秒刷新"}</span></div>
      {status !== "online" && <div className="hw-notice" role="status"><Icon name="AlertTriangle" size={18} /><div><strong>{statuses[status]}，以下为最后一次读数</strong><p>未将缺失指标计为零；恢复采集后会自动更新。{!automatic && "自动刷新已关闭，可点击刷新获取最新状态。"}</p></div></div>}
      <div className="hw-tiles">
        <Tile label="GPU 利用率" value={percent(summary.percent)} caption={gpuHint} icon="Cpu" history={history} getter={(sample) => gpuSummary(sample).percent} step={step} />
        <Tile label="显存使用" value={<>{gib(summary.memory.used_bytes)}<small> / {gib(summary.memory.total_bytes)} GiB</small></>} caption={isMetric(summary.memory.used_bytes) && isMetric(summary.memory.total_bytes) ? `剩余 ${gib(summary.memory.total_bytes - summary.memory.used_bytes)} GiB · 所有 GPU 合计` : "缺失指标不参与总量估算"} icon="Gauge" history={history} getter={(sample) => ratio(gpuSummary(sample).memory)} step={step} />
        <Tile label="CPU 利用率" value={percent(snapshot.cpu.percent)} caption={`${snapshot.cpu.cores || "—"} 个逻辑核心 · 整机口径`} icon="CircleGauge" history={history} getter={(sample) => sample.cpu.percent} step={step} />
        <Tile label="内存使用" value={<>{gib(snapshot.memory.used_bytes)}<small> / {gib(snapshot.memory.total_bytes)} GiB</small></>} caption={`已用 ${percent(ratio(snapshot.memory))} · 基于可用内存计算`} icon="Database" history={history} getter={(sample) => ratio(sample.memory)} step={step} />
      </div>
      <Panel title="GPU 设备" aside={<span className="hw-caption">NVIDIA · {snapshot.gpus.length} 张可见设备</span>}>
        {snapshot.gpus.length ? <div className="hw-gpu-grid">{snapshot.gpus.map((gpu, index) => <GPUCard key={gpu.id} gpu={gpu} index={index} />)}</div> : <EmptyState icon="Cpu" title={snapshot.gpu_status === "none" ? "此节点未发现 NVIDIA GPU" : "GPU 指标采集不可用"} description={snapshot.gpu_status === "none" ? "CPU、内存与存储监控仍可正常使用。" : "请检查驱动和采集器的 GPU 可见性。未知状态不会显示为空闲。"} />}
        <p className="hw-footnote">GPU 数值是设备总占用；当前仅关联任务所在节点，不将整卡占用归属于某个任务。温度和功耗不支持时显示“未采集”。</p>
      </Panel>
      <div className="hw-chart-grid"><Panel title="GPU 与显存趋势" aside={<span className="hw-caption">实线：利用率 · 虚线：显存</span>}>{gpuOption.series.length ? <EChart group={chartGroup} option={gpuOption} ariaLabel="GPU 与显存占用趋势" /> : <EmptyState icon="LineChart" title="暂无 GPU 历史数据" description="接入可采集的 GPU 后开始记录。" />}</Panel><Panel title="CPU 与内存趋势" aside={<span className="hw-caption">{window === "24h" ? "每 5 分钟取末次读数" : window === "1h" ? "每 15 秒取末次读数" : "原始采样"}</span>}><EChart group={chartGroup} option={hostOption} ariaLabel="CPU 与内存使用趋势" /></Panel></div>
      <Panel title="存储空间" aside={<span className="hw-caption">按文件系统去重 · GiB</span>}>
        {snapshot.disks.length ? <div className="hw-disk-grid">{snapshot.disks.map((disk) => <article className="hw-disk" key={disk.id}><div className="hw-between"><span><Icon name="HardDrive" size={17} />{disk.paths.join(" / ")}</span><strong>{percent(ratio(disk.memory))}</strong></div><Meter value={ratio(disk.memory)} label={`${disk.paths.join("、")} 已用空间`} /><div className="hw-between hw-caption"><span>已用 {gib(disk.memory.used_bytes)} / {gib(disk.memory.total_bytes)}</span><span>可用 {gib(disk.available_bytes)}</span></div></article>)}</div> : <EmptyState icon="HardDrive" title="暂无存储指标" description="请检查采集器配置的磁盘路径与读取权限。" />}
      </Panel>
      <Panel title="运行中的训练任务" aside={<span className="hw-caption">{data.tasks.length} 个 · 节点级关联</span>}>
        {data.tasks.length ? <div className="hw-table-wrap"><table className="hw-table"><thead><tr><th>任务名称</th><th>类型</th><th>运行节点</th><th>开始时间</th><th>操作</th></tr></thead><tbody>{data.tasks.map((task) => <tr key={task.id}><td><strong>{task.name}</strong><code>{task.id}</code></td><td>训练</td><td>{snapshot.name}</td><td>{timeLabel(task.started_at)}</td><td><Link to={`/training/${encodeURIComponent(task.id)}`}>查看详情 →</Link></td></tr>)}</tbody></table></div> : <EmptyState icon="FlaskConical" title="暂无已关联的运行中训练" description="仅展示已绑定此节点且运行租约有效的任务；推理服务与其他进程也可能占用资源。" />}
      </Panel>
      <Panel title="资源提醒" aside={<span className="hw-caption">连续 60 秒超阈值才触发</span>}>
        {errors.length > 0 && <div className="hw-notice"><Icon name="AlertTriangle" size={18} /><div><strong>部分指标未能采集</strong><p>{errors.map((error) => error === "cpu_unavailable" ? "CPU 指标不可用" : error === "memory_unavailable" ? "内存指标不可用" : error === "gpu_unavailable" ? "GPU 采集不可用" : error.startsWith("disk_unavailable:") ? `存储路径不可用：${error.slice(17)}` : "部分采集能力不可用").join("；")}</p></div></div>}
        {status === "online" && !state.error && data.alerts.map((alert) => <div className="hw-notice" key={alert.code}><Icon name="AlertTriangle" size={18} /><span>{alert.message}</span></div>)}
        {(status !== "online" || state.error) ? <p className="hw-caption">数据未更新，暂不判断当前资源是否正常。</p> : !data.alerts.length && !errors.length && <div className="hw-clear"><Icon name="ShieldCheck" size={19} />暂无持续性资源提醒<span>观察内存、显存和磁盘可用空间</span></div>}
      </Panel>
      <p className="hw-footnote">保留最近 24 小时历史；中断采集的时段显示为空缺。设备指标无法单独确认训练瓶颈，可结合训练详情中的运行进度判断。</p>
    </>}
  </div>;
}
