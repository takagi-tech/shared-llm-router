# Docker Composeでの起動

Linuxホスト用の構成。FastAPIとビルド済みReact画面を1つのイメージにまとめ、SQLiteと暗号鍵はホストの`.local/data`へ永続化する。イメージにパスワード、DB、上流API Keyは含めない。

現在の開発環境はDockerへ切替済み。既存データと管理者アカウントを引き継いでいる。

## 起動・停止

リポジトリのルートで実行する。

```bash
python3 scripts/prepare_runner.py
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 router
```

```bash
docker compose down
```

`restart: unless-stopped`により、プロセス異常終了やDocker再起動後に自動復帰する。Docker自体のOS起動設定はホスト側に依存する。DBと暗号鍵はコンテナ削除後も残る。

管理画面: [http://127.0.0.1:18080/admin](http://127.0.0.1:18080/admin)（`/` はログイン不要の閲覧画面）

## 新しいホストでの初期構築

Docker EngineとComposeプラグインが必要。初期ディレクトリと内部接続キーの作成にPython 3を使用する。Node.jsは不要。

```bash
python3 scripts/prepare_runner.py
export LLM_ROUTER_UID="$(id -u)"
export LLM_ROUTER_GID="$(id -g)"
docker compose build router runner
docker compose run --rm --no-deps router admin --username admin
docker compose up -d router runner
```

パスワードは対話入力。`LLM_ROUTER_UID`と`LLM_ROUTER_GID`を`.env`に保存すれば毎回のexportは不要。例は`.env.docker.example`。データ所有者と同じUID/GIDで動かす。今回の環境では1006:1006を設定済み。

## GuideLLM runner

`runner`はCPU専用の別イメージでGuideLLM 0.7.3を実行する。初回ビルドではCPU版PyTorch等を取得する。`.local/runner.key`をRouterと共有し、127.0.0.1:18083で内部RPCを受ける。保存先は`.local/guidellm`、登録データは`.local/data/datasets`と`tokenizers`。`prepare_runner.py`は既存キーを維持する。操作方法は[GuideLLM運用](GuideLLM運用.md)。

## GPU監視

NVIDIAドライバとNVIDIA Container Toolkitが設定済みのホストで使う。

```bash
docker compose --profile hardware up -d
```

今回の`.env`では`COMPOSE_PROFILES=hardware`を設定し、通常の`up -d`で監視も起動する。監視コンテナはGPUのutility機能を使って指標を読み取り、推論・GPU設定変更を行わない。`127.0.0.1:18082/metrics`をサーバのMetrics URLに指定する。

GPU監視不要の新環境ではhardwareプロファイルを有効にしない。Router本体にはGPUアクセスは不要。

## 接続先とネットワーク

この構成はLinuxのhostネットワークを使用する。既存登録の`127.0.0.1:18020`（上流vLLM）と`127.0.0.1:18082`（監視）をそのまま使える。アプリは既定でlocalhostにだけ待ち受ける。

Docker Desktopやbridgeネットワークへ移す場合は、その環境のホスト到達先とポート公開を別途設定する。このComposeを複数台・複数workerへそのままスケールしない。

## 管理者の変更・バックアップ

```bash
docker compose exec router llm-router admin --username admin
```

パスワード変更時は既存セッションが失効する。

```bash
docker compose exec router llm-router backup --output /data/backups/backup-YYYYMMDD
```

出力はホストの`.local/data/backups/backup-YYYYMMDD`。DBと対応する暗号鍵、登録データセット・Tokenizerを一緒に保管する。切替前の今回のバックアップは`.local/backups/pre-docker-20260918`に保存済み。
