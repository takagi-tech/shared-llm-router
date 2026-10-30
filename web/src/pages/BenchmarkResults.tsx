import { useState } from "react";
import { Link } from "react-router-dom";
import { useData } from "../api";
import {
  Chart,
  ErrorBox,
  Field,
  Section,
  Status,
  Table,
  ms,
  number,
} from "../components";
import type { Benchmark, ResourceMetrics } from "../types";

export function TokenDistributions({ benchmark: b }: { benchmark: Benchmark }) {
  return (
    <Section title="入力側の計数とサーバ実測の比較">
      <Table
        headers={[
          "段階",
          "入力側の平均tokens",
          "要求出力の平均tokens",
          "usage取得数（入力 / 出力）",
          "入力 平均",
          "入力 P50 / P95 / P99",
          "出力 平均",
          "出力 P50 / P95 / P99",
        ]}
      >
        {b.steps?.map((s) => (
          <tr key={s.id}>
            <td>{s.step}</td>
            <td>{number(s.results.client_input_mean, 1)}</td>
            <td>{number(s.results.requested_output_mean, 1)}</td>
            <td>
              {number(s.results.input_known)} / {number(s.results.output_known)}{" "}
              （成功 {number(s.results.completed)}）
            </td>
            <td>{number(s.results.input_distribution?.mean, 1)}</td>
            <td>
              {[
                s.results.input_distribution?.p50,
                s.results.input_distribution?.p95,
                s.results.input_distribution?.p99,
              ]
                .map((x) => number(x, 1))
                .join(" / ")}
            </td>
            <td>{number(s.results.output_distribution?.mean, 1)}</td>
            <td>
              {[
                s.results.output_distribution?.p50,
                s.results.output_distribution?.p95,
                s.results.output_distribution?.p99,
              ]
                .map((x) => number(x, 1))
                .join(" / ")}
            </td>
          </tr>
        ))}
      </Table>
      <p className="footnote">
        入力側の計数はチャットテンプレートを除きます。入力・出力の分位値はサーバusageが揃った段階のみ表示します。GuideLLM独自の補完を含む集計はJSONのnativeに保存しています。
      </p>
    </Section>
  );
}

export function BenchmarkResources({ bid }: { bid: string }) {
  const data = useData<(ResourceMetrics & { time: number; kind: string })[]>(
    `/api/admin/benchmarks/${bid}/resources`,
  );
  const names: Record<string, [string, string, number]> = {
    DCGM_FI_DEV_GPU_UTIL: ["GPU使用率", "%", 1],
    DCGM_FI_DEV_FB_USED: ["VRAM使用量", "GiB", 1 / 1024],
    node_memory_MemAvailable_bytes: ["利用可能RAM", "GiB", 1 / 1024 ** 3],
    "vllm:kv_cache_usage_perc": ["KVキャッシュ", "%", 100],
    "llamacpp:kv_cache_usage_ratio": ["KVキャッシュ", "%", 100],
  };
  const series = new Map<
    string,
    { label: string; unit: string; points: { x: number; y: number | null }[] }
  >();
  for (const sample of data.data || [])
    for (const [metric, items] of Object.entries(sample.gauges || {})) {
      if (!names[metric]) continue;
      for (const item of items) {
        const key = sample.kind + metric + JSON.stringify(item.labels);
        const [label, unit, scale] = names[metric];
        if (!series.has(key))
          series.set(key, {
            label: label + " " + Object.values(item.labels).join(" / "),
            unit,
            points: [],
          });
        series.get(key)!.points.push({ x: sample.time, y: item.value * scale });
      }
    }
  return (
    <Section title="試験中のリソース">
      <ErrorBox message={data.error} />
      <div className="chart-grid">
        {[...series].map(([key, s]) => (
          <Chart
            key={key}
            label={s.label}
            unit={s.unit}
            points={s.points}
            xFormat={(x) => new Date(x * 1000).toLocaleTimeString("ja-JP")}
          />
        ))}
      </div>
      <p className="footnote">
        試験開始から終了までの観測。GPU・RAMはホスト全体の値です。短い試験や監視未設定の場合は観測がないことがあります。保存期間は監視設定に従います。
      </p>
    </Section>
  );
}

export function BenchmarkComparison({
  current,
  history,
}: {
  current: Benchmark;
  history: Benchmark[];
}) {
  const [otherId, setOtherId] = useState("");
  const other = useData<Benchmark>(
    otherId ? `/api/admin/benchmarks/${otherId}` : null,
    false,
  );
  const eligible = history.filter(
    (b) =>
      b.id !== current.id &&
      b.config.engine === current.config.engine &&
      b.config.engine_version === current.config.engine_version,
  );
  return (
    <Section title="過去の試験と比較">
      <Field label="比較する試験">
        <select value={otherId} onChange={(e) => setOtherId(e.target.value)}>
          <option value="">選択してください</option>
          {eligible.map((b) => (
            <option key={b.id} value={b.id}>
              {b.config.name || b.id.slice(0, 8)} · {b.alias}
            </option>
          ))}
        </select>
      </Field>
      <ErrorBox message={other.error} />
      {other.data && (
        <>
          <p className="footnote">
            同じエンジン・版の結果を並べています。モデル、経路、入力条件の違いを確認してください。
          </p>
          <Table headers={["条件", "今回", "比較対象"]}>
            {["alias", "mode", "capacity_at_start"].map((k) => (
              <tr key={k}>
                <td>{k}</td>
                <td>
                  {String(
                    (current.config as unknown as Record<string, unknown>)[k] ??
                      "—",
                  )}
                </td>
                <td>
                  {String(
                    (other.data!.config as unknown as Record<string, unknown>)[
                      k
                    ] ?? "—",
                  )}
                </td>
              </tr>
            ))}
            {Object.keys(current.config.options || {})
              .filter(
                (k) =>
                  JSON.stringify(
                    (
                      current.config.options as unknown as Record<
                        string,
                        unknown
                      >
                    )[k],
                  ) !==
                  JSON.stringify(
                    (
                      other.data!.config.options as unknown as Record<
                        string,
                        unknown
                      >
                    )[k],
                  ),
              )
              .map((k) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="mono small">
                    {JSON.stringify(
                      (
                        current.config.options as unknown as Record<
                          string,
                          unknown
                        >
                      )[k],
                    )}
                  </td>
                  <td className="mono small">
                    {JSON.stringify(
                      (
                        other.data!.config.options as unknown as Record<
                          string,
                          unknown
                        >
                      )[k],
                    )}
                  </td>
                </tr>
              ))}
          </Table>
          <Table
            headers={[
              "試験",
              "段階",
              "状態",
              "req/s",
              "出力token/s",
              "TTFT P95",
              "E2E P95",
            ]}
          >
            {[current, other.data].flatMap((b) =>
              (b.steps || []).map((s) => (
                <tr key={b.id + s.id}>
                  <td>
                    <Link to={`/benchmarks/${b.id}`}>
                      {b.config.name || b.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>{s.step}</td>
                  <td>
                    <Status value={s.status} />
                  </td>
                  <td>{number(s.results.request_throughput, 2)}</td>
                  <td>{number(s.results.output_token_throughput, 2)}</td>
                  <td>{ms(s.results.ttft_ms?.p95)}</td>
                  <td>{ms(s.results.e2e_ms?.p95)}</td>
                </tr>
              )),
            )}
          </Table>
        </>
      )}
    </Section>
  );
}
