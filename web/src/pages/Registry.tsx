import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Pencil, RefreshCw, Search, Trash2 } from "lucide-react";
import { mutate, useData } from "../api";
import {
  AddButton,
  Chart,
  DetailLink,
  DetailRows,
  Empty,
  ErrorBox,
  Field,
  Form,
  HoursSelect,
  Loading,
  Modal,
  PageHeader,
  ResourceTable,
  ResourceHistory,
  Section,
  Select,
  StatStrip,
  Status,
  Table,
  Updated,
  date,
  number,
} from "../components";
import { useNotice } from "../main";
import type {
  Alias,
  Config,
  DiscoveryResult,
  Endpoint,
  Metrics,
  Server,
} from "../types";

function ServerEditor({
  server,
  close,
  saved,
}: {
  server?: Server;
  close: () => void;
  saved: () => void;
}) {
  const [name, setName] = useState(server?.name || ""),
    [hostname, setHostname] = useState(server?.hostname || ""),
    [gpu, setGPU] = useState(server?.gpu_notes || ""),
    [metrics, setMetrics] = useState(server?.metrics_url || "");
  return (
    <Modal
      title={server ? "サーバを編集" : "サーバを登録"}
      description="物理サーバの識別情報を登録します。推論プロセスの起動・停止は行いません。"
      onClose={close}
    >
      <Form
        onClose={close}
        onSave={async () => {
          await mutate(
            `/api/admin/servers${server ? "/" + server.id : ""}`,
            server ? "PUT" : "POST",
            { name, hostname, gpu_notes: gpu, metrics_url: metrics || null },
          );
          saved();
        }}
      >
        <Field label="管理名">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="dev-server"
            autoFocus
          />
        </Field>
        <Field label="ホスト名 / IP">
          <input
            required
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder="198.51.100.13"
          />
        </Field>
        <Field label="GPU・資源のメモ">
          <input
            value={gpu}
            onChange={(e) => setGPU(e.target.value)}
            placeholder="GPUの型番・台数など（任意）"
          />
        </Field>
        <Field
          label="ハードウェアMetrics URL（任意）"
          hint="DCGM / Node Exporter等のPrometheus形式のURL。"
        >
          <input
            type="url"
            value={metrics}
            onChange={(e) => setMetrics(e.target.value)}
            placeholder="http://server:9400/metrics"
          />
        </Field>
      </Form>
    </Modal>
  );
}

export function EndpointEditor({
  endpoint,
  servers,
  close,
  saved,
}: {
  endpoint?: Endpoint;
  servers: Server[];
  close: () => void;
  saved: () => void;
}) {
  const [name, setName] = useState(endpoint?.name || ""),
    [server, setServer] = useState(endpoint?.server_id || servers[0]?.id || ""),
    [url, setURL] = useState(endpoint?.base_url || ""),
    [backend, setBackend] = useState(endpoint?.backend || "vllm"),
    [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false),
    [enabled, setEnabled] = useState(Boolean(endpoint?.enabled)),
    [capacity, setCapacity] = useState(endpoint?.capacity || 1),
    [queue, setQueue] = useState(
      endpoint?.queue_timeout_seconds?.toString() || "",
    ),
    [metrics, setMetrics] = useState(endpoint?.metrics_url || "");
  return (
    <Modal
      title={endpoint ? "接続先を編集" : "接続先を登録"}
      description="稼働中のvLLM・llama.cppへ接続します。上流のAPI Keyは暗号化して保存されます。"
      onClose={close}
    >
      <Form
        onClose={close}
        onSave={async () => {
          await mutate(
            `/api/admin/endpoints${endpoint ? "/" + endpoint.id : ""}`,
            endpoint ? "PUT" : "POST",
            {
              server_id: server,
              name,
              base_url: url,
              backend,
              api_key: clearKey ? "" : key || null,
              enabled,
              capacity,
              queue_timeout_seconds: queue ? Number(queue) : null,
              metrics_url: metrics || null,
            },
          );
          saved();
        }}
      >
        <Field label="管理名">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="dev-qwen"
            autoFocus
          />
        </Field>
        <Field label="推論エンジン">
          <select value={backend} onChange={(e) => setBackend(e.target.value)}>
            <option value="vllm">vLLM</option>
            <option value="llamacpp">llama.cpp</option>
          </select>
        </Field>
        <Field label="所属サーバ">
          <select
            required
            value={server}
            onChange={(e) => setServer(e.target.value)}
          >
            <option value="" disabled>
              サーバを選択
            </option>
            {servers.map((s) => (
              <option value={s.id} key={s.id}>
                {s.name} / {s.hostname}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Base URL" hint="末尾に /v1 がない場合は補完します。">
          <input
            type="url"
            required
            value={url}
            onChange={(e) => setURL(e.target.value)}
            placeholder="http://127.0.0.1:18020/v1"
          />
        </Field>
        <Field
          label="上流API Key"
          hint={
            endpoint?.has_credential
              ? "登録済み。空欄では現在のキーを維持します。"
              : "上流で認証が不要な場合は空欄。"
          }
        >
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="new-password"
            disabled={clearKey}
          />
        </Field>
        {endpoint?.has_credential && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={clearKey}
              onChange={(e) => setClearKey(e.target.checked)}
            />
            登録済みの上流キーを削除する
          </label>
        )}
        <div className="form-columns">
          <Field
            label="同時実行数（Capacity）"
            hint="未試験の場合の初期値は1。管理者が指定します。"
          >
            <input
              type="number"
              required
              min="1"
              value={capacity}
              onChange={(e) => setCapacity(Number(e.target.value))}
            />
          </Field>
          <Field
            label="キュー待機期限（秒）"
            hint="空欄は共通設定に従います。既定は無期限。"
          >
            <input
              type="number"
              min="0.01"
              step="any"
              value={queue}
              onChange={(e) => setQueue(e.target.value)}
              placeholder="無期限 / 共通設定"
            />
          </Field>
        </div>
        <Field label="vLLM Metrics URL（任意）">
          <input
            type="url"
            value={metrics}
            onChange={(e) => setMetrics(e.target.value)}
            placeholder="http://server:8000/metrics"
          />
        </Field>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          このCapacityで有効にする
        </label>
        <p className="footnote">
          有効化後もヘルスチェックで正常と確認できるまで転送しません。長さや応答待ち時間の上限は設定しません。
        </p>
      </Form>
    </Modal>
  );
}

// Sort key that orders IPv4 addresses numerically (192.0.2.9 before 192.0.2.20).
function hostKey(host: string) {
  const parts = host.split(".");
  return parts.length === 4 && parts.every((p) => /^\d+$/.test(p))
    ? parts.map((p) => p.padStart(3, "0")).join(".")
    : `~${host}`;
}

function port(url: string) {
  try {
    const u = new URL(url);
    return Number(u.port) || (u.protocol === "https:" ? 443 : 80);
  } catch {
    return 0;
  }
}

function groupByServer(
  rows: Endpoint[],
  servers: Server[],
  monitored: string[],
  discovery: DiscoveryResult | null,
) {
  const byId = new Map(servers.map((s) => [s.id, s]));
  const groups = new Map<
    string,
    {
      id: string;
      name: string;
      hostname: string;
      rows: Endpoint[];
      checked?: number | null;
    }
  >();
  for (const e of rows) {
    const s = byId.get(e.server_id);
    const g = groups.get(e.server_id) || {
      id: e.server_id,
      name: s?.name || e.server_name || "",
      hostname: s?.hostname || "",
      rows: [],
    };
    g.rows.push(e);
    groups.set(e.server_id, g);
  }
  // Monitored IPs without any LLM still get a row, so "nothing found" is visible.
  const shown = new Set(
    [...groups.values()].flatMap((g) => [g.hostname, g.name]),
  );
  for (const host of monitored) {
    if (shown.has(host)) continue;
    const scanned = discovery?.hosts.some((h) => h.host === host);
    groups.set(`host:${host}`, {
      id: `host:${host}`,
      name: host,
      hostname: host,
      rows: [],
      checked: scanned ? discovery?.completed_at : null,
    });
  }
  for (const g of groups.values())
    g.rows.sort((a, b) => port(a.base_url) - port(b.base_url));
  return [...groups.values()].sort((a, b) =>
    hostKey(a.hostname || a.name).localeCompare(hostKey(b.hostname || b.name)),
  );
}

export function Registry() {
  const endpoints = useData<Endpoint[]>("/api/admin/endpoints"),
    servers = useData<Server[]>("/api/admin/servers"),
    config = useData<Config>("/api/admin/settings"),
    discovery = useData<DiscoveryResult>("/api/admin/discovery");
  const [tab, setTab] = useState("endpoints"),
    [search, setSearch] = useState("");
  const [editor, setEditor] = useState<Endpoint | "new" | null>(null),
    [serverEditor, setServerEditor] = useState<Server | "new" | null>(null),
    [error, setError] = useState(""),
    [scanning, setScanning] = useState(false);
  const notice = useNotice();
  const rows = endpoints.data?.filter((e) =>
    `${e.name} ${e.base_url} ${e.server_name} ${e.observed_models.map((m) => m.id).join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const groups = groupByServer(
    rows || [],
    servers.data || [],
    search ? [] : config.data?.discovery_hosts || [],
    discovery.data,
  );
  async function scan() {
    setScanning(true);
    setError("");
    try {
      const result = await mutate<DiscoveryResult>(
        "/api/admin/discovery/scan",
        "POST",
      );
      const found = result.hosts.flatMap((h) => h.found);
      const added = found.filter((f) => f.new).length;
      notice(
        result.hosts.length
          ? `更新しました（LLM ${found.length}件、新規 ${added}件）`
          : "監視するIPアドレスが未設定です。設定画面で登録してください",
      );
      void endpoints.reload();
      void servers.reload();
      void discovery.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  }
  async function probe(id: string) {
    setError("");
    try {
      const r = await mutate<{ probe_ok: boolean; error_type?: string }>(
        `/api/admin/endpoints/${id}/probe`,
        "POST",
      );
      notice(
        r.probe_ok
          ? "接続とモデル一覧を確認しました"
          : `接続確認: ${r.error_type}`,
      );
      void endpoints.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function remove(id: string) {
    if (
      !confirm("この接続先を削除しますか？ 履歴は残り、Aliasは無効になります。")
    )
      return;
    try {
      await mutate(`/api/admin/endpoints/${id}`, "DELETE");
      void endpoints.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHeader
        section="REGISTRY / ENDPOINTS"
        title="モデル確認"
        description="各マシンで稼働しているモデルと、その状態・混み具合を確認します。"
        actions={
          <>
            <button className="button" disabled={scanning} onClick={scan}>
              <RefreshCw size={15} />
              {scanning ? "更新中…" : "更新"}
            </button>
            <AddButton
              onClick={() =>
                tab === "servers" || !servers.data?.length
                  ? setServerEditor("new")
                  : setEditor("new")
              }
            >
              {tab === "servers" || !servers.data?.length
                ? "サーバを登録"
                : "接続先を登録"}
            </AddButton>
          </>
        }
      />
      <div className="tabs">
        <button
          className={tab === "endpoints" ? "active" : ""}
          onClick={() => setTab("endpoints")}
        >
          接続先 <span>{endpoints.data?.length || 0}</span>
        </button>
        <button
          className={tab === "servers" ? "active" : ""}
          onClick={() => setTab("servers")}
        >
          サーバ <span>{servers.data?.length || 0}</span>
        </button>
      </div>
      <ErrorBox message={error || endpoints.error || servers.error} />
      {tab === "endpoints" ? (
        <>
          <div className="toolbar">
            <div className="search">
              <Search size={15} />
              <input
                aria-label="接続先を検索"
                placeholder="名前・モデル・URL・サーバで検索"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Updated value={endpoints.updated} />
          </div>
          {!rows ? (
            <Loading />
          ) : (
            <Table
              headers={[
                "接続先",
                "稼働中のモデル",
                "URL",
                "状態",
                "実行中",
                "待機",
                "最終更新",
                "操作",
              ]}
              empty={
                !groups.length && (
                  <Empty
                    title={
                      search
                        ? "該当する接続先がありません"
                        : "接続先は未登録です"
                    }
                    detail="サーバを登録してから、推論サービスのURLを追加してください。"
                  />
                )
              }
            >
              {groups.map((g) => [
                <tr key={g.id} className="group-row">
                  <td colSpan={8}>
                    <strong>{g.name}</strong>
                    {g.hostname !== g.name && (
                      <span className="muted mono"> {g.hostname}</span>
                    )}
                    <span className="muted">
                      {" "}
                      ·{" "}
                      {g.rows.length
                        ? `${g.rows.length}件`
                        : g.checked
                          ? `LLMは見つかりませんでした（最終更新 ${date(g.checked)}）`
                          : "まだ更新していません"}
                    </span>
                  </td>
                </tr>,
                ...g.rows.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <Link className="row-title" to={`/endpoints/${e.id}`}>
                        {e.name}
                      </Link>
                      <span className="cell-sub">{e.backend}</span>
                    </td>
                    <td className="mono">
                      {e.observed_models.length ? (
                        e.observed_models.map((m) => (
                          <div key={m.id}>{m.id}</div>
                        ))
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="mono small">{e.base_url}</td>
                    <td>
                      <Status
                        value={
                          !e.enabled
                            ? "disabled"
                            : e.recovery_required
                              ? "recovery"
                              : e.benchmark_id
                                ? "maintenance"
                                : e.health
                        }
                      />
                    </td>
                    <td className="numeric">{e.upstream.running ?? "—"}</td>
                    <td className="numeric">{e.upstream.queued ?? "—"}</td>
                    <td className="small muted">{date(e.last_checked_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="text-button"
                          onClick={() => void probe(e.id)}
                        >
                          接続確認
                        </button>
                        <button
                          className="icon-button"
                          aria-label={`${e.name}を編集`}
                          onClick={() => setEditor(e)}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          className="icon-button"
                          aria-label={`${e.name}を削除`}
                          onClick={() => void remove(e.id)}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )),
              ])}
            </Table>
          )}
        </>
      ) : (
        <Section title="登録サーバ">
          <Table
            headers={["サーバ", "ホスト", "GPU / 資源", "接続先数", "操作"]}
            empty={
              !servers.data?.length && (
                <Empty
                  title="サーバは未登録です"
                  detail="まず推論サービスが稼働するサーバを登録してください。"
                />
              )
            }
          >
            {servers.data?.map((s) => (
              <tr key={s.id}>
                <td className="row-title">{s.name}</td>
                <td className="mono">{s.hostname}</td>
                <td>{s.gpu_notes || "—"}</td>
                <td className="numeric">{s.endpoint_count}</td>
                <td>
                  <button
                    className="text-button"
                    onClick={() => setServerEditor(s)}
                  >
                    編集
                  </button>
                </td>
              </tr>
            ))}
          </Table>
          {servers.data
            ?.filter((s) => s.metrics_url)
            .map((s) => (
              <Section key={s.id} title={`${s.name} / Hardware`}>
                <ResourceTable data={s.hardware_metrics} />
                <ResourceHistory kind="server" id={s.id} />
              </Section>
            ))}
        </Section>
      )}
      {editor && (
        <EndpointEditor
          endpoint={editor === "new" ? undefined : editor}
          servers={servers.data || []}
          close={() => setEditor(null)}
          saved={() => {
            void endpoints.reload();
            void servers.reload();
            notice("接続先を保存しました");
          }}
        />
      )}
      {serverEditor && (
        <ServerEditor
          server={serverEditor === "new" ? undefined : serverEditor}
          close={() => setServerEditor(null)}
          saved={() => {
            void servers.reload();
            notice("サーバを保存しました");
          }}
        />
      )}
    </>
  );
}

function AliasEditor({
  alias,
  endpoints,
  close,
  saved,
}: {
  alias?: Alias;
  endpoints: Endpoint[];
  close: () => void;
  saved: () => void;
}) {
  const [name, setName] = useState(alias?.alias || ""),
    [eid, setEid] = useState(alias?.endpoint_id || endpoints[0]?.id || ""),
    [model, setModel] = useState(alias?.upstream_model || ""),
    [enabled, setEnabled] = useState(alias ? !!alias.enabled : true);
  const observed = endpoints.find((e) => e.id === eid)?.observed_models || [];
  const selectedModel = model || (observed.length === 1 ? observed[0].id : "");
  const automatic = observed.length === 1 && selectedModel === observed[0].id;
  return (
    <Modal
      title={alias ? "モデルエイリアスを編集" : "モデルエイリアスを登録"}
      description="利用者はこのエイリアスを model に指定して接続します。"
      onClose={close}
    >
      <Form
        onClose={close}
        onSave={async () => {
          await mutate(
            `/api/admin/aliases${alias ? "/" + alias.id : ""}`,
            alias ? "PUT" : "POST",
            {
              alias: name,
              endpoint_id: eid,
              upstream_model: selectedModel,
              enabled,
            },
          );
          saved();
        }}
      >
        <Field label="エイリアス">
          <input
            value={name}
            required
            onChange={(e) => setName(e.target.value)}
            placeholder="qwen-main"
            autoFocus
          />
        </Field>
        <Field label="接続先">
          <select
            required
            value={eid}
            onChange={(e) => {
              setEid(e.target.value);
              setModel("");
            }}
          >
            <option value="" disabled>
              選択してください
            </option>
            {endpoints.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </select>
        </Field>
        {automatic ? (
          <div className="form-note">
            使用するモデル：<strong>{selectedModel}</strong>
            <br />
            接続先のモデルが1つのため、自動で設定します。
          </div>
        ) : observed.length > 1 ? (
          <Field
            label="使用するモデル"
            hint="この接続先には複数のモデルがあります。利用するモデルを選んでください。"
          >
            <select
              required
              value={selectedModel}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="" disabled>
                モデルを選択
              </option>
              {selectedModel &&
                !observed.some((m) => m.id === selectedModel) && (
                  <option value={selectedModel}>
                    {selectedModel}（現在の設定・一覧に未検出）
                  </option>
                )}
              {observed.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field
            label="上流モデル名"
            hint={
              observed.length
                ? "保存済みのモデル名が接続先の一覧にありません。設定を確認してください。"
                : "モデル一覧が未取得です。接続先で接続確認を行うか、モデル名を入力してください。"
            }
          >
            <input
              required
              value={selectedModel}
              onChange={(e) => setModel(e.target.value)}
              list="observed-models"
              placeholder="qwen3.8-27b"
            />
            <datalist id="observed-models">
              {observed.map((m) => (
                <option key={m.id} value={m.id} />
              ))}
            </datalist>
          </Field>
        )}
        <label className="checkbox">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          有効にする
        </label>
      </Form>
    </Modal>
  );
}

export function Aliases() {
  const aliases = useData<Alias[]>("/api/admin/aliases"),
    endpoints = useData<Endpoint[]>("/api/admin/endpoints");
  const [editor, setEditor] = useState<Alias | "new" | null>(null),
    [error, setError] = useState("");
  const notice = useNotice();
  async function remove(a: Alias) {
    if (!confirm(`${a.alias} を削除しますか？`)) return;
    try {
      await mutate(`/api/admin/aliases/${a.id}`, "DELETE");
      void aliases.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <PageHeader
        section="REGISTRY / MODEL ALIASES"
        title="モデルエイリアス"
        description="利用者向けの一意な名前を、接続先と上流モデルへ割り当てます。"
        actions={
          <AddButton onClick={() => setEditor("new")}>
            エイリアスを登録
          </AddButton>
        }
      />
      <ErrorBox message={error || aliases.error || endpoints.error} />
      <Section title="登録モデル" aside={<Updated value={aliases.updated} />}>
        <Table
          headers={["エイリアス", "上流モデル", "接続先", "状態", "操作"]}
          empty={
            !aliases.data?.length && (
              <Empty
                title="モデルエイリアスは未登録です"
                detail="接続先を登録後、利用者向けのモデル名を追加してください。"
              />
            )
          }
        >
          {aliases.data?.map((a) => (
            <tr key={a.id}>
              <td className="mono row-title">{a.alias}</td>
              <td className="mono">{a.upstream_model}</td>
              <td>
                <DetailLink to={`/endpoints/${a.endpoint_id}`}>
                  {a.endpoint_name}
                </DetailLink>
              </td>
              <td>
                <Status value={a.enabled ? "healthy" : "disabled"} />
              </td>
              <td>
                <div className="row-actions">
                  <button className="text-button" onClick={() => setEditor(a)}>
                    編集
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`${a.alias}を削除`}
                    onClick={() => void remove(a)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </Table>
        <p className="footnote">
          同じ接続先に複数のエイリアスを割り当てても、同時実行枠は共通です。
        </p>
      </Section>
      {editor && (
        <AliasEditor
          alias={editor === "new" ? undefined : editor}
          endpoints={endpoints.data || []}
          close={() => setEditor(null)}
          saved={() => {
            void aliases.reload();
            notice("エイリアスを保存しました");
          }}
        />
      )}
    </>
  );
}

export function EndpointDetail() {
  const { id } = useParams();
  const endpoint = useData<Endpoint>(`/api/admin/endpoints/${id}`),
    servers = useData<Server[]>("/api/admin/servers");
  const [hours, setHours] = useState("1"),
    [editing, setEditing] = useState(false),
    [capacity, setCapacity] = useState(""),
    [capacityOpen, setCapacityOpen] = useState(false);
  const metrics = useData<Metrics>(
    `/api/admin/metrics?endpoint_id=${id}&hours=${hours}`,
  );
  const e = endpoint.data,
    notice = useNotice();
  return (
    <>
      <Link to="/" className="back-link">
        <ArrowLeft size={14} />
        モデル確認
      </Link>
      <PageHeader
        section="REGISTRY / ENDPOINT"
        title={e?.name || "接続先"}
        description="稼働状態・実行枠・上流の情報を確認します。"
        actions={
          <button
            className="button"
            disabled={!e}
            onClick={() => setEditing(true)}
          >
            接続設定
          </button>
        }
      />
      <ErrorBox message={endpoint.error || metrics.error} />
      {e ? (
        <>
          <StatStrip
            items={[
              {
                label: "STATUS",
                value: (
                  <Status
                    value={
                      !e.enabled
                        ? "disabled"
                        : e.recovery_required
                          ? "recovery"
                          : e.benchmark_id
                            ? "maintenance"
                            : e.health
                    }
                  />
                ),
                detail: `最終更新 ${date(e.last_checked_at)}`,
              },
              {
                label: "RUNNING",
                value: `${e.running} / ${e.capacity}`,
                detail: "実行中 / 同時実行数",
              },
              { label: "QUEUED", value: e.queued, detail: "現在待機中" },
            ]}
          />
          <Section
            title="接続情報"
            aside={
              <button
                className="text-button"
                onClick={() => {
                  setCapacity(String(e.capacity));
                  setCapacityOpen(true);
                }}
              >
                同時実行数を変更
              </button>
            }
          >
            <DetailRows
              items={[
                ["Base URL", <span className="mono">{e.base_url}</span>],
                ["Backend", e.backend],
                ["上流認証", e.has_credential ? "登録済み（非表示）" : "なし"],
                [
                  "キュー待機期限",
                  e.queue_timeout_seconds == null
                    ? "共通設定に従う（既定: 無期限）"
                    : `${e.queue_timeout_seconds} 秒`,
                ],
                ["応答待ちの期限", "なし"],
                ["直近の確認エラー", e.last_error || "—"],
                [
                  "再起動後の受付",
                  e.recovery_required ? (
                    <button
                      className="text-button"
                      onClick={() => {
                        if (
                          confirm(
                            "上流に前回の処理が残っていないことを確認しましたか？ 受付を再開します。",
                          )
                        )
                          void mutate(
                            `/api/admin/endpoints/${id}/resume`,
                            "POST",
                          )
                            .then(() => {
                              void endpoint.reload();
                              notice("受付を再開しました");
                            })
                            .catch((err) => notice(err.message));
                      }}
                    >
                      上流の残存処理を確認して受付を再開
                    </button>
                  ) : (
                    "受付可能"
                  ),
                ],
              ]}
            />
          </Section>
          <Section title="上流が報告したモデル">
            <Table
              headers={["モデル名", "所有者", "最大コンテキスト長（観測値）"]}
              empty={
                !e.observed_models.length && (
                  <Empty
                    title="モデルは未確認です"
                    detail="接続確認を行ってください。"
                  />
                )
              }
            >
              {e.observed_models.map((m) => (
                <tr key={m.id}>
                  <td className="mono">{m.id}</td>
                  <td>{m.owned_by || "—"}</td>
                  <td className="numeric">{number(m.max_model_len)}</td>
                </tr>
              ))}
            </Table>
            <p className="footnote">
              上流の情報です。Routerが入力や生成長にこの値を強制することはありません。
            </p>
          </Section>
          <Section
            title="利用状況"
            aside={<HoursSelect value={hours} onChange={setHours} />}
          >
            {metrics.data ? (
              <div className="chart-grid">
                <Chart
                  label="完了件数"
                  points={metrics.data.series.map((p) => ({
                    x: p.time,
                    y: p.total,
                  }))}
                  start={metrics.data.since}
                  end={metrics.data.until}
                />
                <Chart
                  label="平均E2E"
                  unit="ms"
                  points={metrics.data.series.map((p) => ({
                    x: p.time,
                    y: p.e2e_ms.mean,
                  }))}
                  start={metrics.data.since}
                  end={metrics.data.until}
                />
              </div>
            ) : (
              <Loading />
            )}
          </Section>
          <Section title="vLLM追加メトリクス">
            <ResourceTable data={e.native_metrics} />
            <ResourceHistory kind="endpoint" id={e.id} />
          </Section>
        </>
      ) : (
        <Loading />
      )}
      {editing && e && (
        <EndpointEditor
          endpoint={e}
          servers={servers.data || []}
          close={() => setEditing(false)}
          saved={() => {
            void endpoint.reload();
            notice("接続設定を保存しました");
          }}
        />
      )}
      {capacityOpen && e && (
        <Modal
          title="同時実行数を変更"
          description="測定結果を参考に、管理者が値を指定してください。"
          onClose={() => setCapacityOpen(false)}
        >
          <Form
            onClose={() => setCapacityOpen(false)}
            onSave={async () => {
              await mutate(`/api/admin/endpoints/${id}/capacity`, "PUT", {
                capacity: Number(capacity),
              });
              void endpoint.reload();
              notice("同時実行数を変更しました");
            }}
          >
            <Field label="Capacity">
              <input
                type="number"
                min="1"
                value={capacity}
                onChange={(x) => setCapacity(x.target.value)}
                required
                autoFocus
              />
            </Field>
            <p className="footnote">
              値を下げても実行中の要求は中断しません。実行中数が新しい上限を下回るまで次の要求を待機させます。
            </p>
          </Form>
        </Modal>
      )}
    </>
  );
}
