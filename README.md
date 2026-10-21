# Focodex

FirefoxからNative Messaging経由でCodexへ指示を1件送り、応答全文と終了状態を表示する接続検証版。
現行仕様は[仕様書v0.1.2](specification/focodex仕様書v0.1.2.md)、作業状況は[TODO.md](TODO.md)を参照。

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
6. 再送しても1件ずつ実行されることを確認する。モデル拒否が実際に起きた場合は、指示文を保持したまま別モデルを選んで再送できる。一覧取得に失敗した場合は「モデル一覧を再取得」で再試行できる。拒否されるモデルが候補にない場合、失敗後の再送は模擬試験の確認で足りる。
7. 長く続く実行では「停止」を押す。「停止要求中…」の後、Codexの中断結果または専用プロセス群の終了確認を経て「停止済み」と表示する。自然に完了した場合は「完了」と表示する。

停止後は状態の直下に、Codexから受信した終了状態、中断要求への応答、5秒後の強制終了の有無、専用プロセス群の消滅確認を表示する。詳細な値は「接続・実行情報」の`stopDiagnostics`で確認できる。専用プロセス群の消滅を確認できない場合は失敗として表示し、同じHostからの再実行を拒否する。

従来の試験ではコマンド・ファイル変更承認を表示したが、v0.1.2の権限方針では汎用承認を禁止する。外部読み取りとGit pushには専用の承認経路が必要で、現行の検証版ではまだ利用できない。ファイル変更の差分は対応する`item/started`から取得する。汎用要求では内容を表示しても承認ボタンは出さず、拒否か停止のみ可能。ネットワークや権限変更など、適切に表示できない要求も自動許可しない。追加質問では選択肢または自由文を入力して明示的に送信する。待機中も停止できる。

Native Hostは実行ID、Codex要求に紐付けた一時ID、項目ID、スレッドID、ターンIDを照合する。一般的な承認要求には`accept`を送らない。Codexが要求の解決・失効を通知したら承認画面を閉じ、古い要求への応答を拒否する。

選択モデルは端末内でプロジェクトごとに保存する。一覧は候補であり、アカウントでの利用可否を保証しない。選択肢は固定せずCodexから取得する。

Firefoxを再起動すると一時拡張は再読み込みが必要。拡張コードの変更後も`about:debugging`から再読み込みする。

## 今回の実装範囲

- 固定IDのFirefox拡張 → ページ所有の`runtime.connectNative("focodex")` → Python Host → `codex app-server --listen stdio://`。
- `initialize` → `initialized` → `thread/start` → `turn/start` → `item/completed`／`turn/completed`。
- 指示ごとに一時スレッドを作成し、完了時にapp-serverを終了する。会話の継続・履歴保存はまだ実装しない。
- 登録済みプロジェクトのIDからHost側で作業ディレクトリを決める。拡張から任意のパス・シェルコマンドは受け付けない。
- モデル候補は`model/list`からページをたどって取得する。明示選択したモデルだけを`thread/start`に指定し、実行時には`workspaceWrite`・ネットワーク禁止・`on-request`を指定し、Codexの設定ファイルには書き込まない。外部読み取りの隔離は未実装のため、この試験版では権限方針の完全な強制をまだ保証しない。
- 完了・失敗・中断を区別する。一般的な承認要求は拒否し、追加質問はユーザーの明示操作で応答し、他の未対応要求は自動許可せず`unsupported_request`で終了する。
- 同じ設定ファイルを使うHost同士は実行ロックで排他する。
- 接続断・通常のHost終了・180秒の時間切れで、専用プロセス群へTERMを送り、1秒後も残る場合はKILLする。既存のファイル変更は戻さない。
- 停止ボタンは`turn/interrupt`を送り、5秒以内に中断結果が来なければ専用プロセス群を終了する。終了確認後にのみ停止済みと表示する。
- 新方針でのプロジェクト内書き込み、外部読み取りの隔離と専用承認、追加質問への実応答、異常終了・スリープを含む停止管理、音声エディタ連携、Git操作は次段階。
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
