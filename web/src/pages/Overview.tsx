import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, RefreshCw } from "lucide-react";
import { useData } from "../api";
import {
  Chart,
  DetailLink,
  Empty,
  ErrorBox,
  HoursSelect,
  Loading,
  MetricDistribution,
  PageHeader,
  Section,
  StatStrip,
  Status,
  Table,
  Updated,
  ms,
  number,
} from "../components";
import type { Endpoint, Metrics } from "../types";

export function Overview() {
  const [hours, setHours] = useState("1");
  const endpoints = useData<Endpoint[]>("/api/admin/endpoints");
  const metrics = useData<Metrics>(`/api/admin/metrics?hours=${hours}`);
  const m = metrics.data;
  return (
    <>
      <PageHeader
        section="WORKSPACE / OVERVIEW"
        title="推論環境の概況"
        description="接続先の稼働状態と、Routerを経由するリクエストの動きを確認します。"
        actions={
          <>
            <Updated value={metrics.updated} />
            <button
              className="icon-button"
              aria-label="再読み込み"
              onClick={() => {
                void metrics.reload();
                void endpoints.reload();
              }}
            >
              <RefreshCw size={16} />
            </button>
          </>
        }
      />
      <ErrorBox
        message={endpoints.error || metrics.error}
        retry={() => {
          void metrics.reload();
          void endpoints.reload();
        }}
      />
      <StatStrip
        items={[
          {
            label: "ENDPOINTS",
            value: endpoints.data
              ? `${endpoints.data.filter((e) => e.health === "healthy" && e.enabled && !e.recovery_required).length} / ${endpoints.data.length}`
              : "—",
            detail: "正常・有効 / 登録済み",
          },
          {
            label: "RUNNING",
            value: number(m?.live.running),
            detail: "現在実行中のリクエスト",
          },
          {
            label: "QUEUED",
            value: number(m?.live.queued),
            detail: "実行枠の空きを待機",
          },
          {
            label: "COMPLETED",
            value: number(m?.summary.completed),
            detail: `選択期間の完了件数`,
          },
        ]}
      />
      <Section
        title="接続先"
        aside={<DetailLink to="/">接続先を管理</DetailLink>}
      >
        {!endpoints.data ? (
          <Loading />
        ) : (
          <Table
            headers={[
              "接続先 / モデル",
              "状態",
              "実行中 / 上限",
              "待機",
              "接続先URL",
              "",
            ]}
            empty={
              !endpoints.data.length && (
                <Empty
                  title="最初の接続先を登録しましょう"
                  detail="稼働中のvLLMのURLとモデルエイリアスを登録すると、ここから状態を確認できます。"
                  action={
                    <Link className="button" to="/">
                      接続先を登録
                      <ArrowRight size={14} />
                    </Link>
                  }
                />
              )
            }
          >
            {endpoints.data.map((e) => (
              <tr key={e.id}>
                <td>
                  <Link className="row-title" to={`/endpoints/${e.id}`}>
                    {e.name}
                  </Link>
                  <span className="cell-sub">
                    {e.observed_models.map((x) => x.id).join(", ") ||
                      "モデル未確認"}
                  </span>
                </td>
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
                <td className="numeric">
                  {e.running}
                  <span className="muted"> / {e.capacity}</span>
                </td>
                <td className="numeric">{e.queued}</td>
                <td className="mono muted">{e.base_url}</td>
                <td>
                  <DetailLink to={`/endpoints/${e.id}`}>詳細</DetailLink>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Section>
      <Section
        title="トラフィック"
        aside={<HoursSelect value={hours} onChange={setHours} />}
      >
        {m ? (
          <>
            <div className="traffic-summary">
              <span>
                完了した要求 <strong>{number(m.summary.total)}</strong> 件
              </span>
              <span>
                出力トークン{" "}
                <strong>
                  {m.summary.output_known
                    ? number(m.summary.output_tokens)
                    : "—"}
                </strong>
              </span>
              <span>
                失敗 <strong>{number(m.summary.failed)}</strong>
              </span>
              <span>
                取消 <strong>{number(m.summary.cancelled)}</strong>
              </span>
            </div>
            <div className="chart-grid">
              <Chart
                label="リクエスト完了数"
                unit={`件 / ${m.bucket_seconds / 60}分`}
                points={m.series.map((p) => ({ x: p.time, y: p.total }))}
                start={m.since}
                end={m.until}
              />
              <Chart
                label="応答時間（平均）"
                unit="ms"
                points={m.series.map((p) => ({ x: p.time, y: p.e2e_ms.mean }))}
                start={m.since}
                end={m.until}
                format={(n) => number(n)}
              />
            </div>
            <p className="footnote">
              通常利用のみ・完了時刻で集計。本文は保存しません。トークン数は上流が返した値のみを集計します。
            </p>
          </>
        ) : (
          <Loading />
        )}
      </Section>
      <Section
        title="応答性能"
        aside={
          <span className="muted small">
            成功したリクエスト / 単位は自動変換
          </span>
        }
      >
        {m && (
          <Table
            headers={["指標", "平均", "P50 ≈", "P95 ≈", "P99 ≈", "標本数"]}
          >
            <tr>
              <td>キュー待機時間</td>
              <MetricDistribution value={m.summary.queue_ms} />
            </tr>
            <tr>
              <td>
                最初の生成内容まで <span className="muted small">TTFT</span>
              </td>
              <MetricDistribution value={m.summary.ttft_ms} />
            </tr>
            <tr>
              <td>
                応答全体 <span className="muted small">E2E</span>
              </td>
              <MetricDistribution value={m.summary.e2e_ms} />
            </tr>
          </Table>
        )}
        <p className="footnote">
          分位値はヒストグラムからの近似です。「—」は未取得。非StreamingのTTFTは計測しません。
          {m?.summary.e2e_ms.mean != null &&
            `平均E2E ${ms(m.summary.e2e_ms.mean)}。`}
        </p>
      </Section>
    </>
  );
}
