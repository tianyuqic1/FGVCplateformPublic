import { Children, useEffect, useId, useRef, useState } from "react";
import { Icon } from "../../components/icons.jsx";
import { selectPage, selectedPage } from "./selectPagination.js";
import "./paginated-select.css";

const optionText = value => Array.isArray(value) ? value.map(optionText).join("") : String(value ?? "");

// Supports option children or richer { value, label, detail, meta } options.
export function PaginatedSelect({ options, children, value, onChange, disabled = false, placeholder = "请选择", "aria-label": label = "选择选项" }) {
  const entries = options ?? Children.toArray(children).map(child => ({ value: child.props.value, label: optionText(child.props.children), disabled: child.props.disabled }));
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const root = useRef(null);
  const trigger = useRef(null);
  const list = useRef(null);
  const id = useId();
  const selected = entries.find(item => item.value === value);
  const view = selectPage(entries, query, page);
  useEffect(() => {
    const dismiss = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  useEffect(() => { setPage(view.page); }, [view.page]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => { if (list.current) list.current.scrollTop = 0; }, [view.page, query]);
  return <div className="fv-paginated-select" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }} onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
  }}>
    <button type="button" ref={trigger} className="fv-select-trigger" aria-label={label} aria-expanded={open && !disabled} aria-controls={open ? id : undefined} disabled={disabled} onClick={() => {
      setOpen(!open); setQuery("");
      setPage(selectedPage(entries, value));
    }}><span><strong title={selected?.label}>{selected?.label ?? placeholder}</strong>{selected?.detail && <small title={selected.detail}>{selected.detail}</small>}</span><Icon name="ChevronDown" size={16} /></button>
    {open && !disabled && <div className="fv-select-menu" id={id}>
      <input autoFocus aria-label={`搜索${label}`} placeholder="搜索名称 / 编号…" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} />
      <div className="fv-select-options" ref={list}>
        {view.items.map(item => <button type="button" key={item.value} disabled={item.disabled} aria-pressed={item.value === value} onClick={() => {
          onChange({ target: { value: item.value } }); setOpen(false); trigger.current?.focus();
        }}><strong title={item.label}>{item.label}</strong>{item.detail && <small title={item.detail}>{item.detail}</small>}{item.meta && <span>{item.meta}</span>}</button>)}
        {!view.total && <p>没有匹配的选项</p>}
      </div>
      <div className="fv-select-pagination" aria-label={`${label}分页`}>
        <span aria-live="polite">{view.start}–{view.end} / {view.total} 项</span>
        <button type="button" aria-label={`${label}上一页`} disabled={view.page === 1} onClick={() => setPage(view.page - 1)}><Icon name="ChevronLeft" size={14} /></button>
        <span>{view.page} / {view.pageCount}</span>
        <button type="button" aria-label={`${label}下一页`} disabled={view.page === view.pageCount} onClick={() => setPage(view.page + 1)}><Icon name="ChevronRight" size={14} /></button>
      </div>
    </div>}
  </div>;
}
