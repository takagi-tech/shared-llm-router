import { useState } from "react";
import { Copy, KeyRound, Plus } from "lucide-react";
import { mutate, useData } from "../api";
import {
  AddButton,
  Empty,
  ErrorBox,
  Field,
  Form,
  HoursSelect,
  Modal,
  PageHeader,
  Section,
  Status,
  Table,
  date,
  number,
} from "../components";
import { useNotice } from "../main";
import type { APIKey, Metrics, User } from "../types";

function UserEditor({
  user,
  close,
  saved,
}: {
  user?: User;
  close: () => void;
  saved: () => void;
}) {
  const [name, setName] = useState(user?.name || ""),
    [enabled, setEnabled] = useState(user ? !!user.enabled : true);
  return (
    <Modal title={user ? "利用者を編集" : "利用者を登録"} onClose={close}>
      <Form
        onClose={close}
        onSave={async () => {
          await mutate(
            `/api/admin/users${user ? "/" + user.id : ""}`,
            user ? "PUT" : "POST",
            { name, enabled },
          );
          saved();
        }}
      >
        <Field label="利用者名">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </Field>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          有効にする
        </label>
        <p className="footnote">
          無効化すると新規利用と待機中の要求を停止します。実行中の要求はリクエスト画面から取り消せます。
        </p>
      </Form>
    </Modal>
  );
}

function KeyManager({
  user,
  close,
  saved,
}: {
  user: User;
  close: () => void;
  saved: () => void;
}) {
  const keys = useData<APIKey[]>(`/api/admin/users/${user.id}/keys`);
  const [name, setName] = useState(""),
    [secret, setSecret] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [copied, setCopied] = useState(false);
  async function issue() {
    setBusy(true);
    setError("");
    try {
      const value = await mutate<{ api_key: string }>(
        `/api/admin/users/${user.id}/keys`,
        "POST",
        { name },
      );
      setSecret(value.api_key);
      setName("");
      setCopied(false);
      void keys.reload();
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function revoke(id: string) {
    if (!confirm("このAPI Keyを失効しますか？")) return;
    try {
      await mutate(`/api/admin/keys/${id}`, "DELETE");
      void keys.reload();
      saved();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <Modal
      title={`${user.name} / API Keys`}
      description="キーは利用者に紐づきます。複数発行しても実行順の優先度は変わりません。"
      onClose={close}
    >
      <div className="detail-body">
        <ErrorBox message={error || keys.error} />
        {secret && (
          <div className="secret-result">
            <strong>発行したキーはこの画面で一度だけ表示します。</strong>
            <div>
              <code>{secret}</code>
              <button
                className="icon-button"
                aria-label="キーをコピー"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(secret)
                    .then(() => setCopied(true))
                    .catch(() =>
                      setError(
                        "コピーできませんでした。キーを選択してコピーしてください。",
                      ),
                    );
                }}
              >
                <Copy size={16} />
              </button>
            </div>
            <small>
              {copied
                ? "コピーしました"
                : "閉じる前に必要な場所へ保存してください。"}
            </small>
          </div>
        )}
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void issue();
          }}
        >
          <Field label="新しいキーの名前">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="research-script"
            />
          </Field>
          <button className="button primary" disabled={busy || !user.enabled}>
            <Plus size={14} />
            発行
          </button>
        </form>
        <Table
          headers={["キー名 / 識別子", "発行 / 最終利用", "状態", "操作"]}
          empty={
            !keys.data?.length && (
              <Empty
                title="キーは未発行です"
                detail="新しいキーを発行してください。"
              />
            )
          }
        >
          {keys.data?.map((k) => (
            <tr key={k.id}>
              <td>
                {k.name}
                <span className="cell-sub mono">{k.prefix}…</span>
              </td>
              <td className="small">
                {date(k.created_at)}
                <span className="cell-sub">
                  最終利用 {date(k.last_used_at)}
                </span>
              </td>
              <td>
                <Status value={k.revoked_at ? "disabled" : "healthy"} />
              </td>
              <td>
                {!k.revoked_at && (
                  <button
                    className="text-button"
                    onClick={() => void revoke(k.id)}
                  >
                    失効
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </div>
    </Modal>
  );
}

export function Users() {
  const users = useData<User[]>("/api/admin/users");
  const [hours, setHours] = useState("24"),
    [editor, setEditor] = useState<User | "new" | null>(null),
    [keys, setKeys] = useState<User | null>(null);
  const metrics = useData<Metrics>(`/api/admin/metrics?hours=${hours}`),
    notice = useNotice();
  return (
    <>
      <PageHeader
        section="ACCESS / USERS & KEYS"
        title="利用者とAPI Key"
        description="利用者ごとの認証と利用量を管理します。管理画面のログインとは独立しています。"
        actions={
          <AddButton onClick={() => setEditor("new")}>利用者を登録</AddButton>
        }
      />
      <ErrorBox message={users.error || metrics.error} />
      <Section
        title="利用者一覧"
        aside={<HoursSelect value={hours} onChange={setHours} />}
      >
        <Table
          headers={[
            "利用者",
            "状態",
            "有効なキー",
            "要求件数",
            "入力token",
            "出力token",
            "操作",
          ]}
          empty={
            !users.data?.length && (
              <Empty
                title="利用者は未登録です"
                detail="利用者を作成し、推論APIに接続するキーを発行してください。"
              />
            )
          }
        >
          {users.data?.map((u) => {
            const m = metrics.data?.users.find((x) => x.id === u.id);
            return (
              <tr key={u.id}>
                <td className="row-title">
                  {u.name}
                  <span className="cell-sub">登録 {date(u.created_at)}</span>
                </td>
                <td>
                  <Status value={u.enabled ? "healthy" : "disabled"} />
                </td>
                <td className="numeric">{u.active_keys}</td>
                <td className="numeric">
                  {metrics.data ? number(m?.total || 0) : "—"}
                </td>
                <td className="numeric">
                  {m?.input_known ? number(m.input_tokens) : "—"}
                </td>
                <td className="numeric">
                  {m?.output_known ? number(m.output_tokens) : "—"}
                </td>
                <td>
                  <div className="row-actions">
                    <button className="text-button" onClick={() => setKeys(u)}>
                      <KeyRound size={13} />
                      キー管理
                    </button>
                    <button
                      className="text-button"
                      onClick={() => setEditor(u)}
                    >
                      編集
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </Table>
        <p className="footnote">
          利用量は選択期間に終了した通常要求の集計です。上流からトークン数が返らなかった場合は「—」で表示します。
        </p>
      </Section>
      {editor && (
        <UserEditor
          user={editor === "new" ? undefined : editor}
          close={() => setEditor(null)}
          saved={() => {
            void users.reload();
            notice("利用者を保存しました");
          }}
        />
      )}
      {keys && (
        <KeyManager
          user={keys}
          close={() => setKeys(null)}
          saved={() => void users.reload()}
        />
      )}
    </>
  );
}
