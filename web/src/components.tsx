import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  ChevronDown,
  Plus,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useData } from "./api";
import type { Distribution, ResourceMetrics } from "./types";

export const number = (value: number | null | undefined, digits = 0) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ja-JP", { maximumFractionDigits: digits }).format(
        value,
      );
export const ms = (value: number | null | undefined) =>
  value == null
    ? "—"
    : value >= 1000
      ? `${number(value / 1000, 2)} s`
      : `${number(value, 1)} ms`;
export const date = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Date(value * 1000).toLocaleString("ja-JP", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });

const labels: Record<string, string> = {
  healthy: "正常",
  unhealthy: "不健全",
  unknown: "未確認",
  disabled: "無効",
  queued: "待機中",
  running: "実行中",
  completed: "完了",
  failed: "失敗",
  cancelled: "取消",
  interrupted: "中断",
  draining: "既存要求の終了待ち",
  stopping: "停止処理中",
  pending: "準備中",
  preparing: "データ・Tokenizer準備中",
  maintenance: "試験中",
  recovery: "再開確認待ち",
};
export function Status({ value }: { value: string }) {
  const tone = ["healthy", "completed"].includes(value)
    ? "success"
    : ["failed", "unhealthy"].includes(value)
      ? "danger"
      : [
            "running",
            "queued",
            "draining",
            "maintenance",
            "pending",
            "preparing",
            "stopping",
            "interrupted",
            "recovery",
            "unknown",
          ].includes(value)
        ? "warning"
        : "neutral";
  return (
    <span className="status">
      <span className={`status-mark ${tone}`} aria-hidden="true" />
      {labels[value] || value}
    </span>
  );
}

export function PageHeader({
  section,
  title,
  description,
  actions,
}: {
  section: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <div className="eyebrow">{section}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="page-actions">{actions}</div>
    </header>
  );
}
export function Section({
  title,
  aside,
  children,
  className = "",
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`section ${className}`}>
      <div className="section-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function ErrorBox({
  message,
  retry,
}: {
  message?: string;
  retry?: () => void;
}) {
  if (!message) return null;
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={16} />
      <span>{message}</span>
      {retry && (
        <button className="text-button" onClick={retry}>
          再試行
        </button>
      )}
    </div>
  );
}
export function Empty({
  title = "まだデータがありません",
  detail = "データが取得されると、ここに表示されます。",
  action,
}: {
  title?: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-symbol" aria-hidden="true">
        —
      </div>
      <strong>{title}</strong>
      <p>{detail}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      読み込み中<span>…</span>
    </div>
  );
}
export function Updated({ value }: { value: number | null }) {
  return (
    <span className="updated">
      {value ? `表示 ${new Date(value).toLocaleTimeString("ja-JP")}` : "取得中"}
    </span>
  );
}
export function AddButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="button primary" onClick={onClick}>
      <Plus size={15} />
      {children}
    </button>
  );
}
export function Table({
  headers,
  children,
  empty,
  className = "",
}: {
  headers: (string | ReactNode)[];
  children: ReactNode;
  empty?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`table-wrap ${className}`}>
      <table>
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
      {empty}
    </div>
  );
}
export function StatStrip({
  items,
}: {
  items: { label: string; value: ReactNode; detail?: string }[];
}) {
  return (
    <div className="stat-strip">
      {items.map((i) => (
        <div className="stat-item" key={i.label}>
          <span className="stat-label">{i.label}</span>
          <strong>{i.value}</strong>
          {i.detail && <span className="stat-detail">{i.detail}</span>}
        </div>
      ))}
    </div>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {Children.map(children, (child) =>
        isValidElement(child) &&
        typeof child.type === "string" &&
        ["input", "select", "textarea"].includes(child.type)
          ? cloneElement(
              child as ReactElement<{
                id?: string;
                "aria-describedby"?: string;
              }>,
              { id, "aria-describedby": hint ? `${id}-hint` : undefined },
            )
          : child,
      )}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}
export function Select({
  value,
  onChange,
  children,
  label,
  ...props
}: {
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="select-wrap">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...props}
      >
        {children}
      </select>
      <ChevronDown size={13} aria-hidden="true" />
    </div>
  );
}
export function HoursSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Select label="表示期間" value={value} onChange={onChange}>
      <option value="1">直近1時間</option>
      <option value="6">直近6時間</option>
      <option value="24">直近24時間</option>
      <option value="168">直近7日</option>
      <option value="720">直近30日</option>
      <option value="2160">直近90日</option>
    </Select>
  );
}
export function Modal({
  title,
  description,
  onClose,
  children,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog ref={ref} className="dialog" onCancel={onClose} aria-label={title}>
      <div className="dialog-header">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        <button className="icon-button" aria-label="閉じる" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function Form({
  onSave,
  onClose,
  children,
  submit = "保存する",
}: {
  onSave: () => Promise<void>;
  onClose: () => void;
  children: ReactNode;
  submit?: string;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={save} className="form">
      <fieldset disabled={busy}>{children}</fieldset>
      <ErrorBox message={error} />
      <div className="form-footer">
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={onClose}
        >
          キャンセル
        </button>
        <button className="button primary" disabled={busy} type="submit">
          {busy ? "処理中…" : submit}
        </button>
      </div>
    </form>
  );
}
export function DetailRows({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="detail-rows">
      {items.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
export function MetricDistribution({ value }: { value: Distribution }) {
  return (
    <>
      <td className="numeric">{ms(value.mean)}</td>
      <td className="numeric">{ms(value.p50)}</td>
      <td className="numeric">{ms(value.p95)}</td>
      <td className="numeric">{ms(value.p99)}</td>
      <td className="numeric muted">{number(value.count)}</td>
    </>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <Check size={15} />
      {children}
    </div>
  );
}
export function DetailLink({
  to,
  children,
}: {
  to: string;
  children: ReactNode;
}) {
  return (
    <Link to={to} className="detail-link">
      {children}
      <ArrowUpRight size={14} />
    </Link>
  );
}

export function Chart({
  points,
  label,
  unit = "",
  format = (n: number) => number(n, 1),
  start,
  end,
  xFormat,
}: {
  points: { x: number; y: number | null }[];
  label: string;
  unit?: string;
  format?: (n: number) => string;
  start?: number;
  end?: number;
  xFormat?: (n: number) => string;
}) {
  const valid = points.filter((p) => p.y != null);
  const minX = start ?? points[0]?.x ?? 0,
    maxX = end ?? points.at(-1)?.x ?? 1;
  const maxY = Math.max(1, ...valid.map((p) => p.y!)) * 1.15;
  const sx = (x: number) => 55 + ((x - minX) / (maxX - minX || 1)) * 780;
  const sy = (y: number) => 145 - (y / maxY) * 120;
  let path = "",
    pen = false;
  for (const p of points) {
    if (p.y == null) {
      pen = false;
      continue;
    }
    path += `${pen ? "L" : "M"}${sx(p.x)},${sy(p.y)} `;
    pen = true;
  }
  return (
    <div className="chart">
      <div className="chart-label">
        {label}
        <span>{unit}</span>
      </div>
      <svg
        viewBox="0 0 860 190"
        role="img"
        aria-label={`${label}。${valid.length ? `${valid.length}個の観測点` : "観測値なし"}`}
      >
        {[0, 0.5, 1].map((r) => (
          <g key={r}>
            <line
              x1="55"
              y1={sy(maxY * r)}
              x2="835"
              y2={sy(maxY * r)}
              stroke="#e9e9e9"
              strokeDasharray={r ? "3 4" : undefined}
            />
            <text x="43" y={sy(maxY * r) + 4} textAnchor="end">
              {format(maxY * r)}
            </text>
          </g>
        ))}
        {valid.length ? (
          <>
            <path
              d={path}
              fill="none"
              stroke="#262626"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
            {valid.map((p, i) => (
              <circle key={i} cx={sx(p.x)} cy={sy(p.y!)} r="2.5" fill="#262626">
                <title>{`${new Date(p.x * 1000).toLocaleString("ja-JP")}: ${format(p.y!)} ${unit}`}</title>
              </circle>
            ))}
          </>
        ) : (
          <text x="445" y="88" textAnchor="middle" className="chart-empty">
            この期間の観測値はありません
          </text>
        )}
        {[minX, (minX + maxX) / 2, maxX].map((x, i) => (
          <text
            key={i}
            x={sx(x)}
            y="173"
            textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
          >
            {xFormat
              ? xFormat(x)
              : new Date(x * 1000).toLocaleTimeString("ja-JP", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
          </text>
        ))}
      </svg>
    </div>
  );
}
const resourceLabels: Record<string, [string, string, number]> = {
  "llamacpp:requests_processing": ["上流で実行中", "件", 1],
  "llamacpp:requests_deferred": ["上流で待機中", "件", 1],
  "llamacpp:prompt_tokens_seconds": ["上流の入力処理速度", "token/s", 1],
  "llamacpp:predicted_tokens_seconds": ["上流の生成速度", "token/s", 1],
  "llamacpp:n_busy_slots_per_decode": ["平均使用スロット数", "", 1],
  DCGM_FI_DEV_GPU_UTIL: ["GPU使用率", "%", 1],
  DCGM_FI_DEV_FB_USED: ["VRAM使用量", "GiB", 1024],
  DCGM_FI_DEV_FB_FREE: ["VRAM空き", "GiB", 1024],
  DCGM_FI_DEV_GPU_TEMP: ["GPU温度", "°C", 1],
  DCGM_FI_DEV_POWER_USAGE: ["GPU電力", "W", 1],
  node_load1: ["CPUロード平均（1分）", "load", 1],
  llm_router_cpu_count: ["論理CPU数", "", 1],
  llm_router_gpu_scrape_success: ["GPU指標取得（1:成功 / 0:失敗）", "", 1],
  node_memory_MemTotal_bytes: ["RAM合計", "GiB", 1024 ** 3],
  node_memory_MemAvailable_bytes: ["利用可能RAM", "GiB", 1024 ** 3],
  "vllm:num_requests_running": ["上流で実行中", "件", 1],
  "vllm:num_requests_waiting": ["上流で待機中", "件", 1],
  "vllm:kv_cache_usage_perc": ["KVキャッシュ使用率", "%", 0.01],
  "vllm:gpu_cache_usage_perc": ["KVキャッシュ使用率", "%", 0.01],
};
export function ResourceTable({ data }: { data: ResourceMetrics }) {
  const rows = Object.entries(data.gauges || {}).flatMap(([name, values]) =>
    values.map((v, i) => (
      <tr key={`${name}-${i}`}>
        <td title={name}>{resourceLabels[name]?.[0] || name}</td>
        <td>{Object.values(v.labels).join(" / ") || "—"}</td>
        <td className="numeric">
          {number(v.value / (resourceLabels[name]?.[2] || 1), 2)}{" "}
          {resourceLabels[name]?.[1]}
        </td>
      </tr>
    )),
  );
  return (
    <>
      <Table
        headers={["指標", "デバイス / モデル", "観測値"]}
        empty={
          !rows.length && (
            <Empty
              title={
                data.error
                  ? "メトリクスを取得できません"
                  : "追加メトリクスは未取得です"
              }
              detail="Exporterまたは上流のMetrics URLを設定すると表示されます。"
            />
          )
        }
      >
        {rows}
      </Table>
      {data.observed_at && (
        <p className="footnote">
          取得 {date(data.observed_at)} ・
          GPUはホスト全体の値です。CPUロード平均はCPU使用率とは異なります。
        </p>
      )}
    </>
  );
}

export function ResourceHistory({
  kind,
  id,
}: {
  kind: "server" | "endpoint";
  id: string;
}) {
  const history = useData<(ResourceMetrics & { time: number })[]>(
    `/api/admin/resources/${kind}/${id}?hours=1`,
  );
  const series = new Map<
    string,
    { metric: string; labels: string; label: string; unit: string }
  >();
  const names: Record<string, [string, string]> = {
    DCGM_FI_DEV_GPU_UTIL: ["GPU使用率", "%"],
    DCGM_FI_DEV_FB_USED: ["VRAM使用量", "GiB"],
    DCGM_FI_DEV_GPU_TEMP: ["GPU温度", "°C"],
    DCGM_FI_DEV_POWER_USAGE: ["GPU電力", "W"],
    node_load1: ["CPUロード平均（1分）", "load"],
    node_memory_MemAvailable_bytes: ["利用可能RAM", "GiB"],
    "vllm:kv_cache_usage_perc": ["KVキャッシュ使用率", "%"],
    "vllm:gpu_cache_usage_perc": ["KVキャッシュ使用率", "%"],
  };
  for (const sample of history.data || []) {
    for (const [metric, values] of Object.entries(sample.gauges || {})) {
      if (!names[metric]) continue;
      for (const value of values) {
        const labels = JSON.stringify(value.labels);
        series.set(metric + labels, {
          metric,
          labels,
          label:
            names[metric][0] + " " + Object.values(value.labels).join(" / "),
          unit: names[metric][1],
        });
      }
    }
  }
  return (
    <>
      <ErrorBox message={history.error} />
      <p className="footnote">
        直近1時間・最新500観測。GPUはホスト全体の値で、他の処理も含みます。
      </p>
      <div className="chart-grid">
        {[...series].map(([key, s]) => (
          <Chart
            key={key}
            label={s.label}
            unit={s.unit}
            points={(history.data || []).map((sample) => {
              const raw = sample.gauges?.[s.metric]?.find(
                (v) => JSON.stringify(v.labels) === s.labels,
              )?.value;
              const y =
                raw == null
                  ? null
                  : s.unit === "GiB"
                    ? raw /
                      (s.metric.startsWith("node_memory") ? 1024 ** 3 : 1024)
                    : s.metric.startsWith("vllm:")
                      ? raw * 100
                      : raw;
              return { x: sample.time, y };
            })}
          />
        ))}
      </div>
    </>
  );
}
