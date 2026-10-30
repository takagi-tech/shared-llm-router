import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Square } from "lucide-react";
import { mutate, useData } from "../api";
import {
  AddButton,
  Chart,
  DetailRows,
  Empty,
  ErrorBox,
  Loading,
  PageHeader,
  Section,
  StatStrip,
  Status,
  Table,
  Updated,
  date,
  ms,
  number,
} from "../components";
import { useNotice } from "../main";
import {
  TokenDistributions,
  BenchmarkResources,
  BenchmarkComparison,
} from "./BenchmarkResults";
import {
  BenchmarkEditor,
  GuideConditions,
  DatasetManager,
  profiles,
} from "./BenchmarkForm";
import type { Alias, Benchmark } from "../types";

export function Benchmarks() {
  const { id } = useParams(),
    navigate = useNavigate(),
    notice = useNotice();
  const history = useData<Benchmark[]>("/api/admin/benchmarks"),
    aliases = useData<Alias[]>("/api/admin/aliases");
  const detail = useData<Benchmark>(id ? `/api/admin/benchmarks/${id}` : null);
  const [editor, setEditor] = useState(false),
    [error, setError] = useState("");
  const b = detail.data;
  const active =
    b &&
    ["pending", "draining", "preparing", "running", "stopping"].includes(
      b.status,
    );
  async function stop() {
    if (!b || !confirm("実行中の負荷試験を停止しますか？")) return;
    try {
      await mutate(`/api/admin/benchmarks/${b.id}/stop`, "POST");
      void detail.reload();
      notice("停止を要求しました");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function remove() {
    if (!b || !confirm("この試験結果を削除しますか？")) return;
    try {
      await mutate(`/api/admin/benchmarks/${b.id}`, "DELETE");
      navigate("/benchmarks");
      void history.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      {id && (
        <Link className="back-link" to="/benchmarks">
          <ArrowLeft size={14} />
          試験一覧
        </Link>
      )}
      <PageHeader
        section="PERFORMANCE / BENCHMARKS"
        title={id ? b?.config.name || "負荷試験の結果" : "負荷試験"}
        description="負荷条件ごとの性能を数値で把握し、管理者が同時実行数を判断します。"
        actions={
          id ? (
            <>
              <Updated value={detail.updated} />
              {active ? (
                <button className="button" onClick={() => void stop()}>
                  <Square size={13} />
                  試験を停止
                </button>
              ) : (
                <>
                  {["json", "csv", "html"].map((f) => (
                    <a
                      key={f}
                      className="button"
                      href={`/api/admin/benchmarks/${id}/export/${f}`}
                    >
                      {f.toUpperCase()}保存
                    </a>
                  ))}
                  {b?.config.engine === "guidellm" && (
                    <button className="button" onClick={() => setEditor(true)}>
                      設定を複製
                    </button>
                  )}
                </>
              )}
            </>
          ) : (
            <AddButton onClick={() => setEditor(true)}>
              新しい負荷試験
            </AddButton>
          )
        }
      />
      <ErrorBox message={error || history.error || detail.error} />
      {!id ? (
        <Section title="実行履歴" aside={<Updated value={history.updated} />}>
          <Table
            headers={[
              "試験 / モデル",
              "経路",
              "状態",
              "条件",
              "完了要求数",
              "開始",
              "",
            ]}
            empty={
              !history.data?.length && (
                <Empty
                  title="負荷試験はまだありません"
                  detail="対象と負荷条件を選択して、最初の測定を開始してください。"
                />
              )
            }
          >
            {history.data?.map((r) => (
              <tr key={r.id}>
                <td>
                  <Link className="row-title mono" to={`/benchmarks/${r.id}`}>
                    {r.config.name || r.id.slice(0, 8)}
                  </Link>
                  <span className="cell-sub">{r.alias}</span>
                </td>
                <td>
                  {r.config.mode === "direct" ? "Endpoint直接" : "Router経由"}
                </td>
                <td>
                  <Status value={r.status} />
                </td>
                <td className="mono small">
                  {r.config.options ? (
                    <>
                      {profiles[r.config.options.profile]}
                      <span className="cell-sub">
                        GuideLLM {r.config.engine_version}
                      </span>
                      <span className="cell-sub">
                        {r.config.options.prompt_tokens} /{" "}
                        {r.config.options.output_tokens} 目標tokens
                      </span>
                    </>
                  ) : (
                    <>
                      {r.config.load_mode === "concurrency"
                        ? r.config.concurrency.join(" → ")
                        : r.config.arrival_rates.join(" → ")}
                      <span className="cell-sub">旧試験 router-http-v1</span>
                      <span className="cell-sub">
                        {r.config.input_words} words /{" "}
                        {r.config.max_output_tokens} output tokens
                      </span>
                    </>
                  )}
                </td>
                <td className="numeric">{r.completed_requests}</td>
                <td className="small">{date(r.created_at)}</td>
                <td>
                  <Link className="text-button" to={`/benchmarks/${r.id}`}>
                    結果
                  </Link>
                </td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : !b ? (
        <Loading />
      ) : (
        <>
          <StatStrip
            items={[
              {
                label: "STATUS",
                value: <Status value={b.status} />,
                detail: b.error_type || "測定状態",
              },
              {
                label: "PROGRESS",
                value: b.config.options
                  ? `${b.current_step} 段階目`
                  : `${b.current_step} / ${(b.config.load_mode === "concurrency" ? b.config.concurrency : b.config.arrival_rates).length}`,
                detail: "現在の負荷段階",
              },
              {
                label: "ELAPSED",
                value: b.started_at
                  ? `${number((b.completed_at ?? Date.now() / 1000) - b.started_at, 0)} s`
                  : "—",
                detail: "試験開始からの経過時間",
              },
              {
                label: "REQUESTS",
                value: number(b.completed_requests),
                detail: "終了した要求数",
              },
            ]}
          />
          {b.progress?.counts && (
            <p className="footnote">
              現在段階の途中集計: 成功 {b.progress.counts.successful} · エラー{" "}
              {b.progress.counts.errored} · 取消・未完了{" "}
              {b.progress.counts.incomplete}
            </p>
          )}
          <Section
            title="試験条件"
            aside={
              <Link className="text-button" to={`/endpoints/${b.endpoint_id}`}>
                接続先・Capacity設定へ →
              </Link>
            }
          >
            <DetailRows
              items={[
                ["モデル", b.alias],
                ["上流モデル", b.config.upstream_model || "—"],
                [
                  "経路",
                  b.config.mode === "direct"
                    ? "Endpoint直接"
                    : "Router経由・Capacityとキューを含む",
                ],
                ["試験開始時のCapacity", b.config.capacity_at_start ?? "—"],
                [
                  "開始 / 終了",
                  `${date(b.started_at)} / ${date(b.completed_at)}`,
                ],
              ]}
            />
            {b.config.options ? (
              <GuideConditions b={b} />
            ) : (
              <DetailRows
                items={[
                  ["旧試験エンジン", "router-http-v1"],
                  [
                    "入力単語数 / 最大出力",
                    `${b.config.input_words} words / ${b.config.max_output_tokens} tokens`,
                  ],
                ]}
              />
            )}
          </Section>
          <Section
            title="測定値"
            aside={
              <span className="muted small">
                合否・推奨値の判定は行いません
              </span>
            }
          >
            <Table
              headers={[
                "負荷",
                "状態",
                "成功 / 終了",
                "入力tokens",
                "出力tokens",
                "測定時間",
                "req/s",
                "出力token/s",
                "TTFT P50",
                "TTFT P95",
                "E2E P95",
                "TPOT P95",
                "ITL P95",
                "エラー数",
                "取消 / 未送信",
              ]}
              empty={
                !b.steps?.length && (
                  <Empty
                    title={
                      active
                        ? "測定の開始を待っています"
                        : "測定結果はありません"
                    }
                    detail={
                      b.status === "draining"
                        ? "既存の要求が終了すると開始します。停止も可能です。"
                        : "完了した段階から順に結果が表示されます。"
                    }
                  />
                )
              }
            >
              {b.steps?.map((s) => (
                <tr key={s.id}>
                  <td className="numeric">
                    {s.concurrency ??
                      s.arrival_rate ??
                      s.results.native?.strategy?.type_?.toString() ??
                      s.step}
                    <span className="cell-sub">
                      {s.concurrency != null
                        ? "concurrent"
                        : s.arrival_rate != null
                          ? "req/s"
                          : "段階"}
                    </span>
                  </td>
                  <td>
                    <Status value={s.status} />
                  </td>
                  <td className="numeric">
                    {number(s.results.completed)} / {number(s.results.total)}
                  </td>
                  <td className="numeric">{number(s.results.input_tokens)}</td>
                  <td className="numeric">{number(s.results.output_tokens)}</td>
                  <td className="numeric">
                    {number(s.results.duration_seconds, 1)} s
                  </td>
                  <td className="numeric">
                    {number(s.results.request_throughput, 2)}
                  </td>
                  <td className="numeric">
                    {number(s.results.output_token_throughput, 2)}
                  </td>
                  <td className="numeric">{ms(s.results.ttft_ms?.p50)}</td>
                  <td className="numeric">{ms(s.results.ttft_ms?.p95)}</td>
                  <td className="numeric">{ms(s.results.e2e_ms?.p95)}</td>
                  <td className="numeric">{ms(s.results.tpot_ms?.p95)}</td>
                  <td className="numeric">{ms(s.results.itl_ms?.p95)}</td>
                  <td className="numeric">
                    {s.results.failed == null
                      ? "—"
                      : s.results.failed + (s.results.submission_errors || 0)}
                  </td>
                  <td className="numeric">
                    {number(s.results.cancelled)} / {number(s.results.not_sent)}
                  </td>
                </tr>
              ))}
            </Table>
            <p className="footnote">
              トークン数とtoken/sは成功要求のサーバusageを使います。usageが欠ける段階は「—」。token/sはGuideLLMのトークン到着時刻に基づく平均レートで、総トークン数÷試験時間とは異なります。遅延・分位値もGuideLLMの定義です。入力側の計数は下の表に分けて表示します。プレフィックスキャッシュの影響を含み、Capacityは自動変更されません。
            </p>
          </Section>
          {!!b.steps?.length && (
            <Section title="性能の推移">
              <div className="chart-grid">
                <Chart
                  label="リクエストスループット"
                  unit="req/s"
                  points={b.steps.map((s) => ({
                    x: s.concurrency ?? s.arrival_rate ?? s.step,
                    y: s.results.request_throughput ?? null,
                  }))}
                  xFormat={(n) => number(n, 1)}
                />
                <Chart
                  label="TTFT P95"
                  unit="ms"
                  points={b.steps.map((s) => ({
                    x: s.concurrency ?? s.arrival_rate ?? s.step,
                    y: s.results.ttft_ms?.p95 ?? null,
                  }))}
                  xFormat={(n) => number(n, 1)}
                />
              </div>
              <p className="footnote">
                横軸:{" "}
                {b.config.options
                  ? "負荷値（固定負荷以外は段階番号）"
                  : b.config.load_mode === "concurrency"
                    ? "指定同時実行数"
                    : "到着RPS"}
                。詳細な分位値、欠測数、中止理由はJSONにも保存されます。
              </p>
            </Section>
          )}
          {b.config.options && (
            <>
              <TokenDistributions benchmark={b} />
              <BenchmarkComparison current={b} history={history.data || []} />
            </>
          )}
          <BenchmarkResources bid={b.id} />
          {!active && (
            <div className="bottom-actions">
              <button className="text-button" onClick={() => void remove()}>
                この試験結果を削除
              </button>
            </div>
          )}
        </>
      )}
      {!id && <DatasetManager />}
      {editor && (
        <BenchmarkEditor
          aliases={aliases.data || []}
          initial={id ? b || undefined : undefined}
          close={() => setEditor(false)}
          started={(bid) => {
            void history.reload();
            navigate(`/benchmarks/${bid}`);
            notice("負荷試験を受け付けました");
          }}
        />
      )}
    </>
  );
}
