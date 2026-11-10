// Small SVG charts in one visual system (the dataviz method): a single series color for one measure, the reserved
// status palette for run states (always with a legend), a one-hue blue ramp for scores, thin marks with 4px rounded
// data-ends, recessive axes, a hover tooltip on every mark, and a table view for every chart.
import { ReactNode, useState } from "react";

export const STATUS_ORDER = ["SUCCESS", "PARTIAL", "FAIL", "INCOMPLETE", "ERROR"] as const;
export const STATUS_VAR: Record<string, string> = {
  SUCCESS: "var(--st-good)", PARTIAL: "var(--st-warning)", FAIL: "var(--st-serious)", ERROR: "var(--st-critical)",
  INCOMPLETE: "var(--st-neutral)",
};
// Sequential blue ramp (steps 100..700); text on a fill flips to white from step 450.
const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
export function rampColor(v: number): { fill: string; ink: string } {
  const i = Math.max(0, Math.min(RAMP.length - 1, Math.round(v * (RAMP.length - 1))));
  return { fill: RAMP[i], ink: i >= 7 ? "#ffffff" : "#0b0b0b" };
}

interface Tip { x: number; y: number; body: ReactNode }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return <div className="viz-tip" style={{ left: tip.x + 12, top: tip.y + 12 }}>{tip.body}</div>;
}

export function ChartFrame({ title, sub, legend, table, children }: {
  title: string; sub?: string; legend?: ReactNode; table: { headers: string[]; rows: (string | number)[][] }; children: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <div className="panel viz">
      <div className="row"><div className="grow"><b>{title}</b>{sub && <div className="small muted">{sub}</div>}</div>
        <button className="link small" onClick={() => setAsTable(!asTable)}>{asTable ? "Show chart" : "Show table"}</button></div>
      {legend && !asTable && <div className="row small" style={{ margin: "6px 0" }}>{legend}</div>}
      {asTable ? (
        <div style={{ overflow: "auto", maxHeight: 320 }}><table className="t"><thead><tr>{table.headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>{table.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div>
      ) : children}
    </div>
  );
}

export function LegendItem({ color, label }: { color: string; label: string }) {
  return <span className="row" style={{ gap: 5 }}><i style={{ width: 10, height: 10, borderRadius: 3, background: color, display: "inline-block" }} />{label}</span>;
}

/** Runs per day, stacked by status. */
export function StatusBars({ days }: { days: { day: string; statuses: Record<string, number> }[] }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const W = 720, H = 180, L = 30, B = 22, T = 8;
  const max = Math.max(1, ...days.map((d) => Object.values(d.statuses).reduce((a, b) => a + b, 0)));
  const bw = (W - L) / days.length;
  const y = (v: number) => H - B - (v / max) * (H - B - T);
  const ticks = [0, Math.ceil(max / 2), max];
  const present = STATUS_ORDER.filter((s) => days.some((d) => d.statuses[s]));
  return (
    <ChartFrame title="Runs per day" sub="By how each run ended. Hover a bar for the counts."
      legend={present.map((s) => <LegendItem key={s} color={STATUS_VAR[s]} label={s} />)}
      table={{ headers: ["day", ...STATUS_ORDER], rows: days.map((d) => [d.day, ...STATUS_ORDER.map((s) => d.statuses[s] ?? 0)]) }}>
      <div style={{ position: "relative" }} onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Runs per day by status">
          {ticks.map((t) => <g key={t}><line x1={L} x2={W} y1={y(t)} y2={y(t)} className="viz-grid" /><text x={L - 6} y={y(t) + 4} textAnchor="end" className="viz-axis">{t}</text></g>)}
          {days.map((d, i) => {
            let acc = 0;
            const segs = STATUS_ORDER.filter((s) => d.statuses[s]);
            const x = L + i * bw + bw * 0.18, w = bw * 0.64;
            return (
              <g key={d.day} onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, body: (
                <div><b>{d.day}</b>{segs.length ? segs.map((s) => <div key={s}><b>{d.statuses[s]}</b> {s}</div>) : <div>no runs</div>}</div>) })}>
                <rect x={L + i * bw} y={T} width={bw} height={H - B - T} fill="transparent" />
                {segs.map((s, k) => {
                  const v = d.statuses[s];
                  const y0 = y(acc), y1 = y(acc + v);
                  acc += v;
                  const top = k === segs.length - 1;
                  const h = Math.max(0, y0 - y1 - (top ? 0 : 2));
                  return top ? <path key={s} fill={STATUS_VAR[s]} d={`M${x},${y0} V${y1 + Math.min(4, h)} q0,-4 4,-4 H${x + w - 4} q4,0 4,4 V${y0} Z`} />
                    : <rect key={s} x={x} y={y1 + 2} width={w} height={h} fill={STATUS_VAR[s]} />;
                })}
                {i % Math.ceil(days.length / 8) === 0 && <text x={L + i * bw + bw / 2} y={H - 6} textAnchor="middle" className="viz-axis">{d.day.slice(5)}</text>}
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} />
      </div>
    </ChartFrame>
  );
}

/** One measure over days (mean E0), with a crosshair. */
export function LineByDay({ points, label }: { points: { day: string; v: number | null }[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 720, H = 150, L = 34, B = 22, T = 10;
  const xs = (i: number) => L + (i + 0.5) * ((W - L) / points.length);
  const y = (v: number) => H - B - v * (H - B - T);
  const path = points.map((p, i) => (p.v === null ? null : `${xs(i)},${y(p.v)}`)).filter(Boolean) as string[];
  const h = hover !== null ? points[hover] : null;
  return (
    <ChartFrame title={label} sub="Mean E0 of scored runs on each day (0 to 1)." table={{ headers: ["day", "mean E0"], rows: points.map((p) => [p.day, p.v === null ? "–" : p.v.toFixed(3)]) }}>
      <div style={{ position: "relative" }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}
             onMouseMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
               const px = (e.clientX - r.left) * (W / r.width); setHover(Math.max(0, Math.min(points.length - 1, Math.floor((px - L) / ((W - L) / points.length))))); }}
             onMouseLeave={() => setHover(null)}>
          {[0, 0.5, 1].map((t) => <g key={t}><line x1={L} x2={W} y1={y(t)} y2={y(t)} className="viz-grid" /><text x={L - 6} y={y(t) + 4} textAnchor="end" className="viz-axis">{t}</text></g>)}
          {path.length > 1 && <polyline points={path.join(" ")} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />}
          {points.map((p, i) => p.v !== null && <circle key={i} cx={xs(i)} cy={y(p.v)} r={hover === i ? 5 : 3} fill="var(--series-1)" stroke="var(--panel)" strokeWidth={2} />)}
          {hover !== null && <line x1={xs(hover)} x2={xs(hover)} y1={T} y2={H - B} className="viz-cross" />}
          {points.map((p, i) => i % Math.ceil(points.length / 8) === 0 && <text key={p.day} x={xs(i)} y={H - 6} textAnchor="middle" className="viz-axis">{p.day.slice(5)}</text>)}
        </svg>
        {h && <div className="viz-tip" style={{ left: `${(xs(hover!) / W) * 100}%`, top: 4 }}><b>{h.v === null ? "no scored runs" : h.v.toFixed(3)}</b> <span className="muted">{h.day}</span></div>}
      </div>
    </ChartFrame>
  );
}

/** A ranked list of one measure (0..1), one bar each. */
export function RankBars({ title, sub, rows, onPick }: {
  title: string; sub?: string; rows: { id: string; value: number; note?: string }[]; onPick?: (id: string) => void;
}) {
  const [tip, setTip] = useState<Tip | null>(null);
  return (
    <ChartFrame title={title} sub={sub} table={{ headers: ["", "mean E0", ""], rows: rows.map((r) => [r.id, r.value.toFixed(3), r.note ?? ""]) }}>
      <div style={{ position: "relative" }} onMouseLeave={() => setTip(null)}>
        {rows.map((r) => (
          <div key={r.id} className="rank" onClick={() => onPick?.(r.id)} style={{ cursor: onPick ? "pointer" : "default" }}
               onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX + (e.currentTarget as HTMLDivElement).offsetLeft,
                 y: (e.currentTarget as HTMLDivElement).offsetTop, body: <div><b>{r.value.toFixed(3)}</b> mean E0<div className="muted">{r.id}{r.note ? ` · ${r.note}` : ""}</div></div> })}>
            <span className="rank-id" title={r.id}>{r.id}</span>
            <span className="rank-bar"><i style={{ width: `${Math.max(1, r.value * 100)}%` }} /></span>
            <span className="rank-v">{r.value.toFixed(2)}</span>
            <span className="rank-note muted small">{r.note}</span>
          </div>
        ))}
        <Tooltip tip={tip} />
      </div>
    </ChartFrame>
  );
}

/** Task × harness grid of a score (0..1), on the blue ramp, value in each cell. */
export function Heatmap({ rows, cols, value, title, sub, onCell }: {
  rows: string[]; cols: string[]; value: (r: string, c: string) => { v: number | null; label?: string } | undefined;
  title: string; sub?: string; onCell?: (r: string, c: string) => void;
}) {
  const [tip, setTip] = useState<Tip | null>(null);
  return (
    <ChartFrame title={title} sub={sub}
      legend={<span className="row small" style={{ gap: 4 }}>0 {[0, 0.25, 0.5, 0.75, 1].map((v) => <i key={v} style={{ width: 22, height: 10, background: rampColor(v).fill, display: "inline-block", borderRadius: 2 }} />)} 1 · mean E0</span>}
      table={{ headers: ["task", ...cols], rows: rows.map((r) => [r, ...cols.map((c) => { const x = value(r, c); return x?.v == null ? "–" : x.v.toFixed(3); })]) }}>
      <div style={{ position: "relative", overflow: "auto" }} onMouseLeave={() => setTip(null)}>
        <table className="heat"><thead><tr><th />{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r}><th>{r}</th>{cols.map((c) => {
              const x = value(r, c);
              const col = x?.v == null ? null : rampColor(x.v);
              return (
                <td key={c} onClick={() => x && onCell?.(r, c)}
                    onMouseMove={(e) => setTip({ x: (e.currentTarget as HTMLElement).offsetLeft + 20, y: (e.currentTarget as HTMLElement).offsetTop + 30,
                      body: <div><b>{x?.v == null ? "not run" : x.v.toFixed(3)}</b><div className="muted">{r} × {c}</div>{x?.label && <div>{x.label}</div>}</div> })}
                    style={{ background: col?.fill ?? "var(--soft)", color: col?.ink ?? "var(--muted)", cursor: x && onCell ? "pointer" : "default" }}>
                  {x?.v == null ? "–" : x.v.toFixed(2)}</td>
              );
            })}</tr>
          ))}</tbody></table>
        <Tooltip tip={tip} />
      </div>
    </ChartFrame>
  );
}

/** Paired differences against a baseline: mean Δ with its 95% interval, around zero. */
export function DeltaBars({ rows }: { rows: { label: string; mean: number; lo: number; hi: number; n: number; verdict: string }[] }) {
  const span = Math.max(0.05, ...rows.flatMap((r) => [Math.abs(r.lo), Math.abs(r.hi)]));
  const W = 360, x = (v: number) => W / 2 + (v / span) * (W / 2 - 8);
  return (
    <ChartFrame title="Difference against the first harness" sub="Same task, seed and model, paired: mean ΔE0 and its 95% interval. A verdict only when the interval excludes 0."
      table={{ headers: ["", "mean ΔE0", "95% interval", "pairs", "verdict"], rows: rows.map((r) => [r.label, r.mean.toFixed(3), `${r.lo.toFixed(3)} … ${r.hi.toFixed(3)}`, r.n, r.verdict]) }}>
      {rows.map((r) => (
        <div key={r.label} className="row" style={{ margin: "6px 0" }}>
          <span style={{ width: 220 }} className="small">{r.label}</span>
          <svg viewBox={`0 0 ${W} 24`} width={W} height={24} role="img" aria-label={`${r.label}: ${r.mean.toFixed(3)}`}>
            <line x1={W / 2} x2={W / 2} y1={0} y2={24} className="viz-axisline" />
            <line x1={x(r.lo)} x2={x(r.hi)} y1={12} y2={12} stroke="var(--series-1)" strokeWidth={2} />
            <line x1={x(r.lo)} x2={x(r.lo)} y1={7} y2={17} stroke="var(--series-1)" strokeWidth={2} />
            <line x1={x(r.hi)} x2={x(r.hi)} y1={7} y2={17} stroke="var(--series-1)" strokeWidth={2} />
            <circle cx={x(r.mean)} cy={12} r={5} fill="var(--series-1)" stroke="var(--panel)" strokeWidth={2}><title>{r.mean.toFixed(3)}</title></circle>
          </svg>
          <span className="small"><b>{r.mean >= 0 ? "+" : ""}{r.mean.toFixed(3)}</b> <span className="muted">n={r.n}</span></span>
          <span className={`badge ${r.verdict === "better" ? "good" : r.verdict === "worse" ? "bad" : ""}`}>{r.verdict}</span>
        </div>
      ))}
    </ChartFrame>
  );
}

export function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="panel stat"><div className="small muted">{label}</div><div className="stat-v">{value}</div>{sub && <div className="small muted">{sub}</div>}</div>;
}
