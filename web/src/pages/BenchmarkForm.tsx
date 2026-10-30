import { useState } from "react";
import { api, mutate, useData } from "../api";
import {
  DetailRows,
  ErrorBox,
  Field,
  Form,
  Modal,
  Section,
  Table,
  number,
} from "../components";
import type { Alias, Benchmark } from "../types";

export interface GuideOptions {
  tokenizer_source: "huggingface" | "local";
  tokenizer: string;
  tokenizer_revision: string;
  data_kind: string;
  dataset_id: string | null;
  dataset_source: string;
  dataset_split: string;
  dataset_config: string;
  dataset_revision: string;
  prompt_column: string;
  output_column: string;
  prompt_tokens: number;
  output_tokens: number;
  prompt_tokens_stdev: number;
  output_tokens_stdev: number;
  samples: number;
  seed: number;
  ignore_eos: boolean;
  profile: string;
  values: number[];
  max_concurrency: number;
  sweep_size: number;
  rampup_seconds: number;
  time_scale: number;
  stop_kind: string;
  requests: number;
  duration_seconds: number;
  max_errors: number;
  extras: Record<string, unknown>;
}
interface Engine {
  id: string;
  name: string;
  version: string;
  form: string;
  schema_version: number;
  schema: { properties: Record<string, { default?: unknown }> };
}
interface Dataset {
  id: string;
  name: string;
  rows: number;
  bytes: number;
  sha256: string;
}
const defaults: GuideOptions = {
  tokenizer_source: "huggingface",
  tokenizer: "",
  tokenizer_revision: "main",
  data_kind: "huggingface",
  dataset_id: null,
  dataset_source: "openai/gsm8k",
  dataset_split: "test",
  dataset_config: "main",
  dataset_revision: "main",
  prompt_column: "question",
  output_column: "",
  prompt_tokens: 256,
  output_tokens: 128,
  prompt_tokens_stdev: 0,
  output_tokens_stdev: 0,
  samples: 100,
  seed: 42,
  ignore_eos: false,
  profile: "concurrent",
  values: [1, 2, 4],
  max_concurrency: 16,
  sweep_size: 5,
  rampup_seconds: 0,
  time_scale: 1,
  stop_kind: "requests",
  requests: 20,
  duration_seconds: 60,
  max_errors: 1,
  extras: {},
};
export const profiles: Record<string, string> = {
  synchronous: "逐次実行",
  concurrent: "固定同時実行数",
  constant: "固定RPS",
  poisson: "Poisson到着",
  throughput: "Throughput",
  sweep: "Sweep",
  replay: "トレース再生",
};

const publicDatasets = [
  {
    id: "openai/gsm8k",
    name: "GSM8K · 英語の短い文章題",
    config: "main",
    split: "test",
    column: "question",
    description:
      "簡易確認用。文章題を送って応答速度を測ります。正解率の採点は行いません。",
  },
  {
    id: "abisee/cnn_dailymail",
    name: "CNN/DailyMail · 英語のニュース記事",
    config: "3.0.0",
    split: "test",
    column: "article",
    description:
      "長めの文章入力用。記事本文を送ります。要約指示の追加や正解要約の採点は行いません。",
  },
];

function GuideLLMFields({
  value: o,
  change,
}: {
  value: GuideOptions;
  change: (value: GuideOptions) => void;
}) {
  const datasets = useData<Dataset[]>("/api/admin/benchmark-datasets");
  const tokenizers = useData<{ id: string; name: string }[]>(
    "/api/admin/benchmark-tokenizers",
  );
  const [values, setValues] = useState(o.values.join(", "));
  const [extras, setExtras] = useState(JSON.stringify(o.extras, null, 2));
  const [error, setError] = useState("");
  function set<K extends keyof GuideOptions>(key: K, value: GuideOptions[K]) {
    change({ ...o, [key]: value });
  }
  function numeric(
    key: keyof GuideOptions,
    label: string,
    min = 0,
    max = 1000000,
    hint?: string,
  ) {
    return (
      <Field label={label} hint={hint}>
        <input
          type="number"
          required
          min={min}
          max={max}
          step={
            key === "duration_seconds" ||
            key === "rampup_seconds" ||
            key === "time_scale"
              ? "any"
              : "1"
          }
          value={o[key] as number}
          onChange={(e) => set(key, Number(e.target.value))}
        />
      </Field>
    );
  }
  const datasetFileFields = (
    <>
      <Field label="データファイル">
        <select
          required
          value={o.dataset_id || ""}
          onChange={(e) => set("dataset_id", e.target.value)}
        >
          <option value="">選択してください</option>
          {datasets.data?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.rows}行)
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="データを登録"
        hint="UTF-8のJSON配列・JSONL・CSV、20MBまで。明示的に登録した試験用データとして保存します。"
      >
        <input
          type="file"
          accept=".json,.jsonl,.csv"
          onChange={async (e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            try {
              const d = await api<Dataset>(
                `/api/admin/benchmark-datasets?name=${encodeURIComponent(f.name)}`,
                {
                  method: "POST",
                  headers: { "Content-Type": "application/octet-stream" },
                  body: f,
                },
              );
              change({
                ...o,
                dataset_id: d.id,
                samples: Math.min(o.samples, d.rows),
              });
              void datasets.reload();
              setError("");
            } catch (ex) {
              setError((ex as Error).message);
            }
          }}
        />
      </Field>
      {o.data_kind === "trace" && (
        <p className="footnote">
          列:
          timestamp、input_length、output_length。入力本文を含めず到着間隔と長さを再現できます。
        </p>
      )}
    </>
  );
  return (
    <>
      <Section title="入力データ・トークン計数">
        <Field label="入力データ">
          <select
            value={
              ["file", "huggingface"].includes(o.data_kind) ? o.data_kind : ""
            }
            onChange={(e) =>
              change({
                ...o,
                data_kind: e.target.value,
                prompt_column:
                  e.target.value === "file"
                    ? "prompt"
                    : publicDatasets.find((d) => d.id === o.dataset_source)
                        ?.column || "prompt",
                output_column: "",
                profile: o.profile === "replay" ? "concurrent" : o.profile,
              })
            }
          >
            {!["file", "huggingface"].includes(o.data_kind) && (
              <option value="" disabled>
                詳細設定の入力を使用中
              </option>
            )}
            <option value="huggingface">公開データセットから選ぶ</option>
            <option value="file">独自ファイルを使う</option>
          </select>
        </Field>
        <p className="footnote">
          実際の文章を使って応答速度・処理量を測定します。入力を選び、負荷条件と出力上限を設定してください。
        </p>
        {o.data_kind === "file" && datasetFileFields}
        {o.data_kind === "huggingface" && (
          <>
            <Field label="公開データセット">
              <select
                value={
                  publicDatasets.some((d) => d.id === o.dataset_source)
                    ? o.dataset_source
                    : "custom"
                }
                onChange={(e) => {
                  const preset = publicDatasets.find(
                    (d) => d.id === e.target.value,
                  );
                  change({
                    ...o,
                    dataset_source: preset?.id || "",
                    dataset_config: preset?.config || "",
                    dataset_split: preset?.split || "train",
                    prompt_column: preset?.column || "prompt",
                    output_column: "",
                  });
                }}
              >
                {publicDatasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
                <option value="custom">その他のHugging Faceデータセット</option>
              </select>
            </Field>
            <p className="footnote">
              {
                publicDatasets.find((d) => d.id === o.dataset_source)
                  ?.description
              }{" "}
              初回の事前確認・実行時に必要なデータを取得します。選択だけではダウンロードしません。利用件数はダウンロード量の上限ではありません。
            </p>
            <details
              open={
                !publicDatasets.some((d) => d.id === o.dataset_source)
                  ? true
                  : undefined
              }
            >
              <summary>公開データセットの取得設定</summary>
              <div className="form-columns">
                <Field label="データセットID">
                  <input
                    required
                    value={o.dataset_source}
                    onChange={(e) => set("dataset_source", e.target.value)}
                    placeholder="org/dataset"
                  />
                </Field>
                <Field label="Split">
                  <input
                    required
                    value={o.dataset_split}
                    onChange={(e) => set("dataset_split", e.target.value)}
                  />
                </Field>
              </div>
            </details>
          </>
        )}
        {["file", "huggingface"].includes(o.data_kind) && (
          <>
            <div className="form-columns">
              <Field label="入力本文の列名">
                <input
                  required
                  value={o.prompt_column}
                  onChange={(e) => set("prompt_column", e.target.value)}
                />
              </Field>
              <Field
                label="出力トークン数の列名"
                hint="数値のトークン数の列です。正解文章の列は指定できません。省略すると下の出力上限を使用します。"
              >
                <input
                  value={o.output_column}
                  onChange={(e) => set("output_column", e.target.value)}
                />
              </Field>
            </div>
            {!o.output_column &&
              numeric("output_tokens", "要求する出力上限トークン数", 1)}
          </>
        )}
        <Field label="Tokenizerの取得元">
          <select
            value={o.tokenizer_source}
            onChange={(e) =>
              change({
                ...o,
                tokenizer_source: e.target
                  .value as GuideOptions["tokenizer_source"],
                tokenizer: "",
              })
            }
          >
            <option value="huggingface">Hugging Face</option>
            <option value="local">登録したTokenizerファイル</option>
          </select>
        </Field>
        {o.tokenizer_source === "huggingface" ? (
          <Field
            label="Tokenizer"
            hint="モデルと同じHugging Face IDを指定します。別Tokenizerへの自動代替はしません。"
          >
            <input
              required
              value={o.tokenizer}
              placeholder="org/model"
              onChange={(e) => set("tokenizer", e.target.value)}
            />
          </Field>
        ) : (
          <>
            <Field label="登録済みTokenizer">
              <select
                required
                value={o.tokenizer}
                onChange={(e) => set("tokenizer", e.target.value)}
              >
                <option value="">選択してください</option>
                {tokenizers.data?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="TokenizerのZIPを登録"
              hint="tokenizer.json、tokenizer_config.json等をZIP直下に含めます。重みファイルは不要です。40MBまで。"
            >
              <input
                type="file"
                accept=".zip"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    const t = await api<{ id: string }>(
                      `/api/admin/benchmark-tokenizers?name=${encodeURIComponent(file.name)}`,
                      {
                        method: "POST",
                        headers: { "Content-Type": "application/octet-stream" },
                        body: file,
                      },
                    );
                    set("tokenizer", t.id);
                    void tokenizers.reload();
                    setError("");
                  } catch (ex) {
                    setError((ex as Error).message);
                  }
                }}
              />
            </Field>
          </>
        )}
        <details
          open={["synthetic", "trace"].includes(o.data_kind) ? true : undefined}
        >
          <summary>詳細な入力設定：合成入力・トレース再生</summary>
          <Field label="詳細な入力方式">
            <select
              value={
                ["synthetic", "trace"].includes(o.data_kind) ? o.data_kind : ""
              }
              onChange={(e) =>
                change({
                  ...o,
                  data_kind: e.target.value || "huggingface",
                  profile:
                    e.target.value === "trace"
                      ? "replay"
                      : o.profile === "replay"
                        ? "concurrent"
                        : o.profile,
                })
              }
            >
              <option value="">使用しない</option>
              <option value="synthetic">長さを揃えた合成入力</option>
              <option value="trace">記録した時刻・長さを再生</option>
            </select>
          </Field>
          <p className="footnote">
            合成入力は長さの影響を比較するためのダミー文章で、意味の通った質問ではありません。トレースは利用履歴の時刻と長さから合成入力を作ります。通常の試験には上の2種類を使用します。
          </p>
          {o.data_kind === "synthetic" && (
            <div className="form-columns">
              {numeric("prompt_tokens", "目標入力トークン数", 1)}
              {numeric("output_tokens", "目標出力トークン数", 1)}
            </div>
          )}
          {o.data_kind === "trace" && datasetFileFields}
        </details>
        <p className="footnote">
          指定した長さとサーバのusageは別に表示します。チャットテンプレート、EOS、キャッシュの影響を含みます。
        </p>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={o.ignore_eos}
            onChange={(e) => set("ignore_eos", e.target.checked)}
          />
          EOSを無視して指定長まで生成する（vLLM）
        </label>
      </Section>
      <Section title="負荷・終了条件">
        <Field label="負荷方式">
          <select
            value={o.profile}
            onChange={(e) => set("profile", e.target.value)}
          >
            {Object.entries(profiles)
              .filter(([k]) =>
                o.data_kind === "trace" ? k === "replay" : k !== "replay",
              )
              .map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
          </select>
        </Field>
        {["concurrent", "constant", "poisson"].includes(o.profile) && (
          <Field
            label={
              o.profile === "concurrent" ? "同時実行数の段階" : "到着RPSの段階"
            }
            hint="カンマ区切り。各段階に終了条件を適用します。"
          >
            <input
              required
              value={values}
              onChange={(e) => {
                setValues(e.target.value);
                set("values", e.target.value.split(",").map(Number));
              }}
            />
          </Field>
        )}
        {!["synchronous", "replay"].includes(o.profile) &&
          numeric("max_concurrency", "最大同時実行数", 1, 10000)}
        {o.profile === "sweep" && numeric("sweep_size", "Sweepの段階数", 3, 30)}
        {o.profile === "replay" &&
          numeric("time_scale", "到着間隔の倍率", 0.001, 1000)}
        <Field label="各段階の終了条件">
          <select
            value={o.stop_kind}
            onChange={(e) => set("stop_kind", e.target.value)}
          >
            <option value="requests">要求数</option>
            <option value="duration">負荷投入時間</option>
          </select>
        </Field>
        {o.stop_kind === "requests"
          ? numeric("requests", "各段階の要求数", 1)
          : numeric(
              "duration_seconds",
              "各段階の負荷投入時間（秒）",
              0.01,
              86400,
              "時間に達すると投入を止め、処理中の応答を待ちます。応答待ちタイムアウトではありません。",
            )}
        {numeric("max_errors", "エラーによる停止件数", 1, 100000)}
      </Section>
      <details>
        <summary>詳細設定・再現性</summary>
        {o.data_kind === "huggingface" && (
          <div className="form-columns">
            <Field label="データセットのconfig（任意）">
              <input
                value={o.dataset_config}
                onChange={(e) => set("dataset_config", e.target.value)}
              />
            </Field>
            <Field label="データセットのrevision">
              <input
                value={o.dataset_revision}
                onChange={(e) => set("dataset_revision", e.target.value)}
              />
            </Field>
          </div>
        )}

        <Field label="Tokenizerのrevision">
          <input
            required
            value={o.tokenizer_revision}
            onChange={(e) => set("tokenizer_revision", e.target.value)}
          />
        </Field>
        <div className="form-columns">
          {numeric(
            "samples",
            "読み込むデータの件数",
            1,
            100000,
            "ファイル・データセット用。データを使い切った場合も終了します。合成入力は要求数・時間に応じて生成します。",
          )}
          {numeric("seed", "乱数seed", 0, 2147483647)}
        </div>
        {o.data_kind === "synthetic" && (
          <div className="form-columns">
            {numeric("prompt_tokens_stdev", "入力長の標準偏差（0は固定）")}
            {numeric("output_tokens_stdev", "出力長の標準偏差（0は固定）")}
          </div>
        )}
        {!["synchronous", "replay"].includes(o.profile) &&
          numeric("rampup_seconds", "負荷立ち上げ時間（秒）", 0, 3600)}
        <Field
          label="生成パラメータ（JSON）"
          hint='temperature、top_p、top_k等。思考モード例: {"chat_template_kwargs":{"enable_thinking":false}}'
        >
          <textarea
            rows={5}
            className="mono"
            value={extras}
            onChange={(e) => {
              setExtras(e.target.value);
              try {
                const parsed = JSON.parse(e.target.value);
                if (
                  !parsed ||
                  Array.isArray(parsed) ||
                  typeof parsed !== "object"
                )
                  throw Error();
                set("extras", parsed);
                e.target.setCustomValidity("");
              } catch {
                e.target.setCustomValidity(
                  "JSONオブジェクトで入力してください",
                );
              }
            }}
          />
        </Field>
      </details>
      <ErrorBox message={error || datasets.error} />
    </>
  );
}

// Engine-specific forms are registered here; common lifecycle UI stays unchanged.
const forms = { "guidellm-v1": GuideLLMFields };
export function BenchmarkEditor({
  aliases,
  initial,
  close,
  started,
}: {
  aliases: Alias[];
  initial?: Benchmark;
  close: () => void;
  started: (id: string) => void;
}) {
  const engines = useData<Engine[]>("/api/admin/benchmark-engines", false);
  const [name, setName] = useState(
    initial?.config.name ? initial.config.name + " の複製" : "GuideLLM試験",
  );
  const [notes, setNotes] = useState(initial?.config.notes || "");
  const [alias, setAlias] = useState(
    initial?.alias || aliases.find((a) => a.enabled)?.alias || "",
  );
  const [mode, setMode] = useState(initial?.config.mode || "direct");
  const [engine, setEngine] = useState("guidellm");
  const [options, setOptions] = useState<GuideOptions>(
    initial?.config.options || { ...defaults },
  );
  const [confirmed, setConfirmed] = useState(false);
  const [importError, setImportError] = useState("");
  const [formVersion, setFormVersion] = useState(0);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewConfig, setPreviewConfig] = useState("");
  const [previewError, setPreviewError] = useState("");
  const preview = useData<{
    status: string;
    error_type?: string;
    error_fields?: string[];
    preview?: { input_tokens: number; output_tokens: number }[];
  }>(previewId ? `/api/admin/benchmark-previews/${previewId}` : null);

  const descriptor = engines.data?.find((e) => e.id === engine);
  const Fields = descriptor
    ? forms[descriptor.form as keyof typeof forms]
    : null;
  const config = {
    engine,
    schema_version: 1,
    name,
    notes,
    alias,
    mode,
    options,
  };
  return (
    <Modal
      title="負荷試験を開始"
      description="GuideLLMで測定し、条件と結果を保存します。"
      onClose={close}
    >
      <Form
        onClose={close}
        submit="試験を開始"
        onSave={async () => {
          if (!confirmed || !Fields)
            throw new Error("入力内容を確認してください");
          const result = await mutate<{ id: string }>(
            "/api/admin/benchmarks",
            "POST",
            config,
          );
          started(result.id);
          close();
        }}
      >
        <Section title="共通設定">
          <Field label="試験名">
            <input
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="試験エンジン">
            <select value={engine} onChange={(e) => setEngine(e.target.value)}>
              {engines.data?.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} {e.version}
                </option>
              ))}
            </select>
          </Field>
          <Field label="対象モデル">
            <select
              required
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
            >
              <option value="">選択してください</option>
              {aliases
                .filter((a) => a.enabled)
                .map((a) => (
                  <option key={a.alias} value={a.alias}>
                    {a.alias} → {a.upstream_model}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="測定経路">
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="direct">Endpoint直接（推論サーバの性能）</option>
              <option value="router">
                Router経由（Capacity・キューを含む）
              </option>
            </select>
          </Field>
          <Field label="メモ">
            <textarea
              value={notes}
              maxLength={1000}
              onChange={(e) => setNotes(e.target.value)}
            />
          </Field>
        </Section>
        {Fields && (
          <Fields
            key={engine + formVersion}
            value={options}
            change={setOptions}
          />
        )}
        <Section title="入力の事前確認">
          <button
            type="button"
            className="button"
            disabled={
              !!previewId &&
              !["completed", "failed", "interrupted", "cancelled"].includes(
                preview.data?.status || "preparing",
              )
            }
            onClick={async () => {
              try {
                setPreviewError("");
                const r = await mutate<{ id: string }>(
                  "/api/admin/benchmark-previews",
                  "POST",
                  config,
                );
                setPreviewId(r.id);
                setPreviewConfig(JSON.stringify(config));
              } catch (e) {
                setPreviewError((e as Error).message);
              }
            }}
          >
            Tokenizerと入力長を確認
          </button>
          <p className="footnote">
            推論を送らず、先頭最大3サンプルを計数します。初回はTokenizerの取得に時間がかかります。
          </p>
          <ErrorBox
            message={
              previewError ||
              preview.error ||
              (preview.data?.status === "failed"
                ? `事前確認失敗: ${preview.data.error_type} ${(preview.data.error_fields || []).join(", ")}`
                : "")
            }
          />
          {previewId && (
            <p className="small">
              {preview.data?.status || "準備中"}
              {previewConfig !== JSON.stringify(config)
                ? " · 設定が変わったため再確認してください"
                : ""}
            </p>
          )}
          {preview.data?.preview && (
            <Table
              headers={[
                "サンプル",
                "生成・読込後の入力tokens",
                "要求する出力tokens",
              ]}
            >
              {preview.data.preview.map((r, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td>{r.input_tokens}</td>
                  <td>{r.output_tokens ?? options.output_tokens}</td>
                </tr>
              ))}
            </Table>
          )}
        </Section>
        <details>
          <summary>設定JSON・インポート</summary>
          <pre className="code-block">{JSON.stringify(config, null, 2)}</pre>
          <Field
            label="設定JSONを読み込む"
            hint="設定オブジェクトまたは過去のJSON結果を選択します。"
          >
            <input
              type="file"
              accept=".json"
              onChange={async (e) => {
                try {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  const raw = JSON.parse(await f.text());
                  const incoming = raw.config || raw;
                  const c = await mutate<{
                    engine: string;
                    name: string;
                    notes: string;
                    alias: string;
                    mode: string;
                    options: GuideOptions;
                  }>("/api/admin/benchmark-config/validate", "POST", {
                    engine: incoming.engine,
                    schema_version: incoming.schema_version || 1,
                    name: incoming.name,
                    notes: incoming.notes,
                    alias: incoming.alias,
                    mode: incoming.mode,
                    options: incoming.options,
                  });
                  if (c.engine !== "guidellm" || !c.options)
                    throw new Error("GuideLLMの設定JSONを選択してください");
                  setOptions({ ...defaults, ...c.options });
                  setFormVersion((v) => v + 1);
                  setName(c.name || "インポート試験");
                  setNotes(c.notes || "");
                  setAlias(c.alias);
                  setMode(c.mode);
                  setImportError("");
                } catch (ex) {
                  setImportError((ex as Error).message);
                }
              }}
            />
          </Field>
        </details>
        <ErrorBox message={importError || engines.error} />
        <div className="form-note">
          試験中は対象の通常受付を停止します。待機要求を終了させ、実行中の要求が終わってから開始します。Capacityの自動変更・合否判定は行いません。
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            required
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          この接続先で負荷試験を実施できることを確認しました
        </label>
      </Form>
    </Modal>
  );
}

export function DatasetManager() {
  const list = useData<Dataset[]>("/api/admin/benchmark-datasets");
  const [error, setError] = useState("");
  return (
    <Section title="登録した試験用データ">
      <ErrorBox message={error || list.error} />
      <Table headers={["ファイル", "行数", "サイズ", "識別情報", ""]}>
        {list.data?.map((d) => (
          <tr key={d.id}>
            <td>{d.name}</td>
            <td>{number(d.rows)}</td>
            <td>{number(d.bytes)} B</td>
            <td className="mono small">{d.sha256.slice(0, 16)}</td>
            <td>
              <button
                className="text-button"
                onClick={async () => {
                  if (
                    !confirm(
                      "登録したデータファイルを削除しますか？過去の測定結果は残ります。",
                    )
                  )
                    return;
                  try {
                    await mutate(
                      `/api/admin/benchmark-datasets/${d.id}`,
                      "DELETE",
                    );
                    void list.reload();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                削除
              </button>
            </td>
          </tr>
        ))}
      </Table>
    </Section>
  );
}
export function GuideConditions({ b }: { b: Benchmark }) {
  const o = b.config.options!;
  return (
    <DetailRows
      items={[
        ["試験エンジン", `${b.config.engine} ${b.config.engine_version}`],
        ["負荷方式", profiles[o.profile]],
        [
          "Tokenizer",
          b.config.tokenizer_asset
            ? `${b.config.tokenizer_asset.name} · SHA256 ${b.config.tokenizer_asset.sha256.slice(0, 16)}`
            : `${o.tokenizer} @ ${o.tokenizer_revision}`,
        ],
        [
          "データ",
          o.data_kind === "synthetic"
            ? "合成入力"
            : b.config.dataset?.name || o.dataset_source || o.dataset_id,
        ],
        [
          "目標入力 / 目標出力",
          `${number(o.prompt_tokens)} / ${number(o.output_tokens)} tokens`,
        ],
        [
          "終了条件",
          o.stop_kind === "requests"
            ? `${o.requests}要求 / 段階`
            : `${o.duration_seconds}秒投入 / 段階・応答は完了まで待機`,
        ],
        [
          "入力件数 / seed",
          `${o.data_kind === "synthetic" ? "負荷条件に応じて生成" : o.samples} / ${o.seed}`,
        ],
        ["EOS無視", o.ignore_eos ? "有効" : "無効"],
        ["メモ", b.config.notes || "—"],
      ]}
    />
  );
}
