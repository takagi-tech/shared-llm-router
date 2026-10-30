import { useState } from "react";
import { Link } from "react-router-dom";
import { mutate, setRefresh, useData } from "../api";
import {
  date,
  ErrorBox,
  Field,
  Loading,
  Notice,
  PageHeader,
  Section,
  Table,
} from "../components";
import { useBranding } from "../branding";
import type { Config, DiscoveryResult } from "../types";

function Editor({ config }: { config: Config }) {
  const { updateBranding } = useBranding();
  const [value, setValue] = useState(config),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false),
    [busy, setBusy] = useState(false),
    [hosts, setHosts] = useState(config.discovery_hosts.join("\n"));
  const field = (key: keyof Config, n: number | null) => {
    setSaved(false);
    setValue((v) => ({ ...v, [key]: n }));
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        const discovery_hosts = hosts
          .split(/[\s,]+/)
          .map((h) => h.trim())
          .filter(Boolean);
        void mutate<Config>("/api/admin/settings", "PUT", {
          ...value,
          discovery_hosts,
        })
          .then((c) => {
            setValue(c);
            setHosts(c.discovery_hosts.join("\n"));
            updateBranding(c);
            setSaved(true);
            setRefresh(c.ui_refresh_seconds);
          })
          .catch((e) => setError(e.message))
          .finally(() => setBusy(false));
      }}
    >
      <ErrorBox message={error} />
      {saved && <Notice>設定を保存しました</Notice>}
      <Section title="ロゴと組織名">
        <div className="settings-fields">
          <Field
            label="ロゴ文字"
            hint="demo、DEMOなど。大文字・小文字をそのまま表示します（12文字まで）。"
          >
            <input
              required
              maxLength={12}
              value={value.logo_text}
              onChange={(e) => {
                setSaved(false);
                setValue({ ...value, logo_text: e.target.value });
              }}
            />
          </Field>
          <Field
            label="組織名"
            hint="サイドバー、フッター、ブラウザのタイトルに表示します。"
          >
            <input
              required
              maxLength={60}
              value={value.organization_name}
              onChange={(e) => {
                setSaved(false);
                setValue({ ...value, organization_name: e.target.value });
              }}
            />
          </Field>
          <Field
            label="ワークスペースの説明"
            hint="サイドバーの説明です。空欄にもできます。"
          >
            <input
              maxLength={100}
              value={value.workspace_description}
              onChange={(e) => {
                setSaved(false);
                setValue({ ...value, workspace_description: e.target.value });
              }}
            />
          </Field>
        </div>
        <p className="footnote">
          保存すると表示が切り替わります。再起動後も保持されます。
        </p>
      </Section>
      <Section title="画面の表示">
        <div className="settings-fields">
          <Field
            label="画面の再読み込み周期（秒）"
            hint="開いている画面が、Routerに保存済みの結果を読み直す間隔です。LLMには問い合わせません。"
          >
            <input
              type="number"
              min="1"
              max="300"
              required
              value={value.ui_refresh_seconds}
              onChange={(e) =>
                field("ui_refresh_seconds", Number(e.target.value))
              }
            />
          </Field>
        </div>
      </Section>
      <Section title="監視するマシン">
        <div className="settings-fields">
          <Field
            label="監視するIPアドレス"
            hint="1行に1つ。「更新」を押すと、ここに書いたマシンのポート範囲を確認し、見つかったLLMを「モデル確認」に追加します。"
          >
            <textarea
              rows={5}
              value={hosts}
              placeholder={"192.0.2.20\n192.0.2.40"}
              onChange={(e) => {
                setSaved(false);
                setHosts(e.target.value);
              }}
            />
          </Field>
          <Field label="ポート範囲（開始）">
            <input
              type="number"
              min="1"
              max="65535"
              required
              value={value.discovery_port_start}
              onChange={(e) =>
                field("discovery_port_start", Number(e.target.value))
              }
            />
          </Field>
          <Field label="ポート範囲（終了）">
            <input
              type="number"
              min="1"
              max="65535"
              required
              value={value.discovery_port_end}
              onChange={(e) =>
                field("discovery_port_end", Number(e.target.value))
              }
            />
          </Field>
          <Field
            label="同時に確認するポート数（1台あたり）"
            hint="1〜50。複数のマシンは同時に確認します。"
          >
            <input
              type="number"
              min="1"
              max="50"
              required
              value={value.discovery_concurrency}
              onChange={(e) =>
                field("discovery_concurrency", Number(e.target.value))
              }
            />
          </Field>
        </div>
        <p className="footnote">
          応答がOpenAI形式のモデル一覧の場合だけLLMとみなします。止まったLLMは削除せず、「モデル確認」に「不健全」と表示します。
        </p>
      </Section>
      <Section title="待機とデータ保持">
        <div className="settings-fields">
          <Field
            label="既定のキュー待機期限（秒）"
            hint="空欄は無期限。接続先で個別に設定できます。"
          >
            <input
              type="number"
              min="0.01"
              step="any"
              value={value.default_queue_timeout_seconds ?? ""}
              placeholder="無期限"
              onChange={(e) =>
                field(
                  "default_queue_timeout_seconds",
                  e.target.value ? Number(e.target.value) : null,
                )
              }
            />
          </Field>
          <Field label="リクエスト明細の保持（日）">
            <input
              type="number"
              min="1"
              max="3650"
              required
              value={value.request_retention_days}
              onChange={(e) =>
                field("request_retention_days", Number(e.target.value))
              }
            />
          </Field>
          <Field label="時系列集計の保持（日）">
            <input
              type="number"
              min="1"
              max="3650"
              required
              value={value.metric_retention_days}
              onChange={(e) =>
                field("metric_retention_days", Number(e.target.value))
              }
            />
          </Field>
        </div>
        <p className="footnote">
          保持期間を短縮すると、次の保持処理で古いデータが削除されます。
        </p>
      </Section>
      <div className="save-bar">
        <span className="muted small">変更は保存後に反映されます。</span>
        <button className="button primary" disabled={busy}>
          {busy ? "保存中…" : "設定を保存"}
        </button>
      </div>
    </form>
  );
}

function DiscoveryPanel() {
  const last = useData<DiscoveryResult>("/api/admin/discovery", false);
  const [result, setResult] = useState<DiscoveryResult | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const shown = result || last.data;
  return (
    <Section
      title="前回の更新結果"
      aside={
        <button
          className="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setError("");
            void mutate<DiscoveryResult>("/api/admin/discovery/scan", "POST")
              .then(setResult)
              .catch((e) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? "更新中…" : "更新"}
        </button>
      }
    >
      <ErrorBox message={error || last.error} />
      <p className="footnote">
        保存済みの設定で更新します。最終更新:{" "}
        {shown?.completed_at ? date(shown.completed_at) : "未実行"}
      </p>
      {shown && shown.hosts.length > 0 && (
        <Table headers={["IPアドレス", "ポート", "モデル", "登録"]}>
          {shown.hosts.map((h) =>
            h.found.length ? (
              h.found.map((f) => (
                <tr key={`${h.host}:${f.port}`}>
                  <td className="mono">{h.host}</td>
                  <td className="mono">{f.port}</td>
                  <td className="mono">
                    {f.models.map((m) => m.id).join(", ")}
                  </td>
                  <td>
                    <Link to={`/endpoints/${f.endpoint_id}`}>
                      {f.new ? "新規登録" : "登録済み"}
                    </Link>
                  </td>
                </tr>
              ))
            ) : (
              <tr key={h.host}>
                <td className="mono">{h.host}</td>
                <td colSpan={3} className="muted">
                  LLMは見つかりませんでした
                </td>
              </tr>
            ),
          )}
        </Table>
      )}
    </Section>
  );
}

export function SettingsPage() {
  const config = useData<Config>("/api/admin/settings", false);
  return (
    <>
      <PageHeader
        section="WORKSPACE / SETTINGS"
        title="運用設定"
        description="ロゴ・組織名、監視するマシン、データの保存期間を設定します。"
      />
      <ErrorBox message={config.error} retry={() => void config.reload()} />
      {config.data ? <Editor config={config.data} /> : <Loading />}
      <DiscoveryPanel />
    </>
  );
}
