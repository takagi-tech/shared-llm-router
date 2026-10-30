# GuideLLM負荷試験

新規試験はGuideLLM 0.7.3で実行する。以前の独自試験は旧エンジンとして閲覧できる。推奨Capacity・合否判定・Capacityの自動変更は行わない。

## 操作

1. 「負荷試験」で新規試験を開き、登録済みモデルと直接接続／Router経由を選ぶ。
2. 基本の入力は「公開データセットから選ぶ」「独自ファイルを使う」。公開データにはGSM8K（英語文章題）とCNN/DailyMail（英語記事）の取得設定を用意し、その他のHugging Face IDも指定できる。独自ファイルはJSON、JSONL、CSVを登録して入力列・数値の出力長列を指定する。正解文章の採点・出力長への自動変換は行わない。
3. 対象モデルと一致するTokenizerを指定する。Hugging Face IDとrevision、またはローカルTokenizer ZIPを使える。GGUF名やRouter Aliasから無関係なTokenizerを自動選択しない。

4. 「入力を事前確認」で先頭の最大3件を計数する。この操作は推論を実行しない。本文は表示・履歴保存しない。
5. 負荷方式と各段階の終了条件を設定して開始する。実行中の進捗・停止、完了後の設定複製、JSON／CSV／HTML出力を同じ画面から操作できる。

合成入力とトレースは「詳細な入力設定」にまとめる。合成入力はFakerのダミー文章を指定トークン数に切ったもので、意味の通った質問を保証しない。長さを揃えた比較やストレス試験向け。トレースは要求の時刻と長さを再現する用途。新規試験は公開データを既定とし、旧試験の複製・設定JSONでは元の入力方式を維持する。

公開データは取得設定のプリセットであり、同梱・事前ダウンロード済みではない。選択だけでは取得せず、事前確認／実行時にrunnerが取得・キャッシュする。利用件数はダウンロード量を制限しない。ShareGPTは会話からの入力抽出が必要なため標準の選択肢には含めない。

ローカルTokenizer ZIPは直下に`tokenizer.json`を含め、必要に応じて`tokenizer_config.json`、`special_tokens_map.json`、`vocab.json`、`merges.txt`等を含める。モデル重み・Pythonコードは不要。ZIPは40 MB、データセットは20 MB・10万行まで。Tokenizer取得時のremote code実行は無効。

JSONLの例（入力列`prompt`、出力長列`output_tokens`）:

```json
{"prompt":"研究の概要を説明してください。","output_tokens":128}
```

Replayはトレースと組み合わせる。各行に秒単位の`timestamp`、`input_length`、`output_length`を指定する。

```json
{"timestamp":0,"input_length":256,"output_length":128}
{"timestamp":0.5,"input_length":512,"output_length":64}
```

## 負荷方式と終了

逐次、固定同時実行、固定RPS、Poisson、Throughput、Sweep、Replayに対応する。固定同時実行とRPSは複数値を段階として指定できる。各段階は要求数または負荷投入時間で終了する。有限のデータセットはデータを使い切ると先に終了することがある。

負荷投入時間を過ぎたら新規送信を止め、送信済みの応答を期限なしで待つ。接続確立のタイムアウトは5秒。長いprefill・生成に独自のread timeoutを設けない。手動停止・エラー・監視接続断による停止は別に扱う。

出力長は生成上限であり、EOSによって短くなる。対応する上流では「EOSを無視」を選択できる。生成パラメータの対応可否は上流依存で、アプリはコンテキスト超過の自動切り詰めをしない。入力計数には上流のチャットテンプレート分が含まれないため、usageとは差が生じる。

## 結果の読み方

- 入力側のTokenizer計数、指定出力長、サーバusageを別々に表示する。サーバusageが欠けたトークン総数・token/s・分布は欠測とする。目標出力長を実測値へ代入しない。
- token/sはGuideLLMのトークン到着時刻列から求める時間加重平均レート。試験全体の総トークン数÷経過時間ではない。
- TTFT、E2E、TPOT、ITLはGuideLLMの集計を使用する。TPOTは出力usageが欠ける場合に欠測とする。GuideLLM独自の補完値はJSONの`native`に残るため、画面のサーバ実測列とは区別する。
- GPU・VRAM・RAMは試験時間帯の監視履歴。他のプロセスによる利用も含まれる。
- 過去試験との比較は同じエンジン・版を対象とし、設定差も表示する。旧試験をGuideLLM測定値へ変換しない。
- 結果に入力・応答本文、APIキー、上流のエラー本文を保存しない。エラーは種別・HTTPステータスだけを残す。明示的に登録したデータセットは本文を持つ別資産として保存し、一覧から削除できる。

## 実行と復旧

Routerが排他・認証・履歴を管理し、CPU用runnerがGuideLLMを実行する。直接接続も対象の通常要求が終了してから始める。Router経由は試験専用の一時キーを使い、通常のHTTP経路とCapacity・キューを通る。終了時に一時キーを失効させる。

Routerとの監視接続が切れたジョブはrunnerが停止する。プロセス再起動時は自動再実行しない。停止後に上流の処理残存を否定できない場合、接続先は「要確認」で保留する。管理者が上流の状態を確認して再開する。

`runner`は127.0.0.1:18083で待受け、`.local/runner.key`による内部認証を必須とする。Docker socket・GPUアクセスは不要。導入手順は[Docker運用](Docker運用.md)。CPU依存関係は`runner/requirements.lock`に固定する。

DBバックアップには登録データセット・Tokenizerも含める。GuideLLMの集計はDBに取り込まれる。runnerのキャッシュ・ジョブ一時領域は`.local/guidellm`に分離する。

## 拡張・検証

`router/engines.py`のregistryが設定schema・版・フォームID・adapterを提供し、`web/src/pages/BenchmarkForm.tsx`がフォームを選ぶ。新規エンジンは専用の設定検証・実行adapter・結果変換を追加する。現在の選択肢はGuideLLMのみ。

通常テストは`.venv/bin/pytest -q`。実際のGuideLLMでのCPU統合テストは`LLM_ROUTER_GUIDELLM_PYTHON=.local/guidellm-venv/bin/python .venv/bin/pytest tests/test_guidellm_runtime.py -q`。模擬HTTPサーバで全負荷方式、遅延応答のdrain、停止、接続断、データ列、Replay、usage欠測を確認する。ブラウザE2Eもrunnerの起動とgpt2 Tokenizerの取得が必要。
