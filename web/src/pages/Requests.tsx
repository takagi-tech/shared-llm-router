import { useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { mutate, useData } from "../api";
import {
  DetailRows,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageHeader,
  Select,
  Status,
  Table,
  Updated,
  date,
  ms,
  number,
} from "../components";
import { useNotice } from "../main";
import type { Endpoint, RequestRow, User } from "../types";

export function Requests() {
  const [status, setStatus] = useState(""),
    [endpoint, setEndpoint] = useState(""),
    [user, setUser] = useState(""),
    [source, setSource] = useState("normal");
  const [search, setSearch] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string | null>(null),
    [error, setError] = useState("");
  const query = new URLSearchParams({
    source,
    offset: String(offset),
    limit: "50",
    search,
  });
  if (status) query.set("status", status);
  if (endpoint) query.set("endpoint_id", endpoint);
  if (user) query.set("user_id", user);
  const rows = useData<{ items: RequestRow[]; total: number }>(
    `/api/admin/requests?${query}`,
  );
  const detail = useData<RequestRow>(
    selected ? `/api/admin/requests/${selected}` : null,
  );
  const endpoints = useData<Endpoint[]>("/api/admin/endpoints"),
    users = useData<User[]>("/api/admin/users"),
    notice = useNotice();
  async function cancel(id: string) {
    if (!confirm("このリクエストをキャンセルしますか？")) return;
    setError("");
    try {
      await mutate(`/api/admin/requests/${id}/cancel`, "POST");
      void rows.reload();
      void detail.reload();
      notice("キャンセルを要求しました");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const item = detail.data;
  return (
    <>
      <PageHeader
        section="OBSERVABILITY / REQUESTS"
        title="リクエスト"
        description="待機・実行状態と利用履歴を確認します。プロンプトや生成本文は保存しません。"
        actions={<Updated value={rows.updated} />}
      />
      <div className="toolbar wrap">
        <div className="search">
          <Search size={15} />
          <input
            aria-label="リクエスト検索"
            placeholder="ID・モデルで検索"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <div className="filters">
          <Select
            label="状態"
            value={status}
            onChange={(v) => {
              setStatus(v);
              setOffset(0);
            }}
          >
            <option value="">すべての状態</option>
            <option value="queued">待機中</option>
            <option value="running">実行中</option>
            <option value="completed">完了</option>
            <option value="failed">失敗</option>
            <option value="cancelled">取消</option>
            <option value="interrupted">中断</option>
          </Select>
          <Select
            label="接続先"
            value={endpoint}
            onChange={(v) => {
              setEndpoint(v);
              setOffset(0);
            }}
          >
            <option value="">すべての接続先</option>
            {endpoints.data?.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </Select>
          <Select
            label="利用者"
            value={user}
            onChange={(v) => {
              setUser(v);
              setOffset(0);
            }}
          >
            <option value="">すべての利用者</option>
            {users.data?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </Select>
          <Select
            label="リクエスト種別"
            value={source}
            onChange={(v) => {
              setSource(v);
              setOffset(0);
            }}
          >
            <option value="normal">通常利用</option>
            <option value="benchmark">負荷試験</option>
          </Select>
        </div>
      </div>
      <ErrorBox message={error || rows.error} />
      {!rows.data ? (
        <Loading />
      ) : (
        <>
          <Table
            headers={[
              "リクエスト / 受付時刻",
              "利用者 / モデル",
              "状態",
              "待機時間",
              "E2E",
              "入力 / 出力token",
              "操作",
            ]}
            empty={
              !rows.data.items.length && (
                <Empty
                  title="該当するリクエストがありません"
                  detail="推論APIへの要求がここに表示されます。フィルタも確認してください。"
                />
              )
            }
          >
            {rows.data.items.map((r) => (
              <tr key={r.id}>
                <td>
                  <button
                    className="text-button mono"
                    onClick={() => setSelected(r.id)}
                  >
                    {r.id.slice(0, 8)}
                  </button>
                  <span className="cell-sub">{date(r.requested_at)}</span>
                </td>
                <td>
                  {r.user_name}
                  <span className="cell-sub mono">
                    {r.alias} {r.streaming ? "· SSE" : ""}
                  </span>
                </td>
                <td>
                  <Status value={r.status} />
                </td>
                <td className="numeric">{ms(r.queue_ms)}</td>
                <td className="numeric">{ms(r.e2e_ms)}</td>
                <td className="numeric">
                  {number(r.input_tokens)}
                  <span className="muted"> / </span>
                  {number(r.output_tokens)}
                </td>
                <td>
                  {["queued", "running"].includes(r.status) ? (
                    <button
                      className="text-button"
                      onClick={() => void cancel(r.id)}
                    >
                      取消
                    </button>
                  ) : (
                    <button
                      className="text-button"
                      onClick={() => setSelected(r.id)}
                    >
                      詳細
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
          <div className="pagination">
            <span>
              {number(rows.data.total)} 件中{" "}
              {rows.data.items.length ? offset + 1 : 0}–
              {offset + rows.data.items.length}
            </span>
            <div>
              <button
                className="icon-button"
                disabled={!offset}
                aria-label="前のページ"
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                className="icon-button"
                disabled={offset + 50 >= rows.data.total}
                aria-label="次のページ"
                onClick={() => setOffset(offset + 50)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </>
      )}
      {selected && (
        <Modal title="リクエスト詳細" onClose={() => setSelected(null)}>
          <div className="detail-body">
            <ErrorBox message={detail.error} />
            {item ? (
              <>
                <DetailRows
                  items={[
                    ["ID", <span className="mono break">{item.id}</span>],
                    ["クライアント指定ID", item.client_request_id || "—"],
                    ["状態", <Status value={item.status} />],
                    ["モデル", item.alias],
                    ["上流モデル", item.upstream_model],
                    ["応答形式", item.streaming ? "Streaming / SSE" : "JSON"],
                    ["受付", date(item.requested_at)],
                    ["実行開始", date(item.started_at)],
                    ["終了", date(item.completed_at)],
                    ["キュー待機", ms(item.queue_ms)],
                    ["TTFT", ms(item.ttft_ms)],
                    ["E2E", ms(item.e2e_ms)],
                    [
                      "入力 / 出力token",
                      `${number(item.input_tokens)} / ${number(item.output_tokens)}`,
                    ],
                    ["HTTPステータス", item.http_status ?? "—"],
                    ["終了理由", item.error_type || "—"],
                  ]}
                />
                {["queued", "running"].includes(item.status) && (
                  <div className="form-footer">
                    <button
                      className="button"
                      onClick={() => void cancel(item.id)}
                    >
                      キャンセルする
                    </button>
                  </div>
                )}
              </>
            ) : (
              <Loading />
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
