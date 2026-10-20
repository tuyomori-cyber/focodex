# Focodex

FirefoxからNative Messaging経由でCodexへ指示を1件送り、応答全文と終了状態を表示する接続検証版。
現行仕様は[仕様書v0.1.1](specification/focodex仕様書v0.1.1.md)、作業状況は[TODO.md](TODO.md)を参照。

## 前提

- Linuxの通常インストール版Firefox、Python 3（標準ライブラリのみ）、Codex CLI。
- 確認したCLI版：初回`0.145.0`、モデル選択の追加検証時`0.163.0`。異なる版ではapp-serverプロトコルの再確認が必要。
- Firefoxを起動するユーザーでCodexの認証を済ませ、Firefoxで選択するモデルをその認証で利用できること。
- Flatpak・Snap版FirefoxのHost配置／実行制約はこの検証の対象外。

## Hostの登録

リポジトリのルートで実行する。

```bash
python3 scripts/install_host.py
```

`codex`をPATHから検出できない場合は、`--codex /absolute/path/to/codex`を指定する。
Hostは`~/.local/lib/focodex/`にコピーし、Firefoxへの登録は`~/.mozilla/native-messaging-hosts/focodex.json`へ保存する。
Pythonの実行パスも登録時に固定する。Hostのコードを変更したら再実行する。

初回に`~/.config/focodex/config.json`を作成する。既存の設定は上書きしない。
最初の登録対象はこの`focodex`リポジトリ。プロジェクトの追加・移動やCodexのパス変更は、この設定を手動編集する。

```json
{
  "codex": "/absolute/path/to/codex",
  "projects": {
    "focodex": {"name": "Focodex", "path": "/absolute/path/to/focodex"}
  }
}
```

## Firefoxでの検証

1. `about:debugging#/runtime/this-firefox`を開き、「一時的なアドオンを読み込む」から`extension/manifest.json`を選ぶ。
2. ツールバーの「Focodex 接続検証」を押して画面を開く。
3. 「Hostへ接続」を押し、登録済みプロジェクトが表示されることを確認する。
4. 自動取得された一覧からモデルを選び、初期入力済みの、ツール使用・ファイル変更を求めない指示を送信する。このPCでは`gpt-5.6-sol`で正常応答を確認済み。
5. 「完了」と応答本文「Focodex接続成功」を確認する。「接続・実行情報」でstatus・threadId・turnId・適用モデル／権限を確認できる。
6. 再送しても1件ずつ実行されることを確認する。モデル拒否時は指示文を保持したまま別モデルを選んで再送する。一覧取得に失敗した場合は「モデル一覧を再取得」で再試行できる。

選択モデルは端末内でプロジェクトごとに保存する。一覧は候補であり、アカウントでの利用可否を保証しない。選択肢は固定せずCodexから取得する。

Firefoxを再起動すると一時拡張は再読み込みが必要。拡張コードの変更後も`about:debugging`から再読み込みする。

## 今回の実装範囲

- 固定IDのFirefox拡張 → ページ所有の`runtime.connectNative("focodex")` → Python Host → `codex app-server --listen stdio://`。
- `initialize` → `initialized` → `thread/start` → `turn/start` → `item/completed`／`turn/completed`。
- 指示ごとに一時スレッドを作成し、完了時にapp-serverを終了する。会話の継続・履歴保存はまだ実装しない。
- 登録済みプロジェクトのIDからHost側で作業ディレクトリを決める。拡張から任意のパス・シェルコマンドは受け付けない。
- モデル候補は`model/list`からページをたどって取得する。明示選択したモデルだけを`thread/start`に指定し、サンドボックス・承認方針はCodexの既存設定を継承する。Codexの設定ファイルには書き込まない。設定が広い権限を持つ場合も継承するため、初回は入力済みの確認文で試す。
- 完了・失敗・中断を区別する。承認／追加質問が来たら自動許可せず、`unsupported_request`で実行を終了する。
- 同じ設定ファイルを使うHost同士は実行ロックで排他する。
- 接続断・通常のHost終了・180秒の時間切れで、専用プロセス群へTERMを送り、1秒後も残る場合はKILLする。既存のファイル変更は戻さない。
- 停止ボタン、承認／質問への回答、異常終了・スリープを含む停止状態管理の完成、音声エディタ連携、Git操作は次段階。
- 応答は完了したagentMessageを本文として保持する。合計16万文字超はエラーにする。逐次ログ・生のプロトコルイベントは通常表示しない。

## ブラウザを使わないHost試験

```bash
python3 -m unittest discover -s tests -v
node tests/test_probe.mjs
node --check extension/probe.js
node --check extension/background.js
```

認証済みの実Codexを使う一往復（モデル通信が発生する）：

```bash
python3 scripts/probe_host.py --list-models
python3 scripts/probe_host.py --model gpt-5.6-sol
```

必要なら`--config /absolute/path/to/config.json`で設定を指定する。
この試験はFirefoxと同じ長さ付きJSONでHostを起動する。Firefoxの拡張読み込み・権限・Host探索まで成功したことの代用にはしない。

検証記録：[docs/verification.md](docs/verification.md)

## 参照した通信仕様

- [OpenAI公式：Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Mozilla：Native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)

実装時にはCLI 0.145.0の`generate-json-schema`でも利用フィールドを確認した。
