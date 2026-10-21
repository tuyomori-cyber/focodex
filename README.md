# Focodex

FirefoxからNative Messaging経由でCodexへ指示を1件送り、応答全文と終了状態を表示する接続検証版。
現行仕様は[仕様書v0.1.3](specification/focodex仕様書v0.1.3.md)、作業状況は[TODO.md](TODO.md)を参照。

## 前提

- Linux版Firefox、Python 3（標準ライブラリのみ）、Codex CLI。
- 確認したCLI版：初回`0.145.0`、現在のpermission profile検証時`0.163.0`。異なる版ではapp-serverプロトコルの再確認が必要。
- Firefoxを起動するユーザーでCodexの認証を済ませ、Firefoxで選択するモデルをその認証で利用できること。
- Firefoxの導入形態によってNative Hostの起動環境が変わる。XubuntuのSnap版では`bwrap`問題を経て、permission profile適用後の一往復を確認済み。

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

従来の試験ではコマンド・ファイル変更承認を表示したが、v0.1.2の権限方針では汎用承認を禁止する。外部読み取りの承認UIはMVP対象外とし、Git pushは専用操作を実装するまで利用できない。ファイル変更の差分は対応する`item/started`から取得する。汎用要求では内容を表示しても承認ボタンは出さず、拒否か停止のみ可能。ネットワークや権限変更など、適切に表示できない要求も自動許可しない。追加質問では選択肢または自由文を入力して明示的に送信する。待機中も停止できる。

Native Hostは実行ID、Codex要求に紐付けた一時ID、項目ID、スレッドID、ターンIDを照合する。一般的な承認要求には`accept`を送らない。Codexが要求の解決・失効を通知したら承認画面を閉じ、古い要求への応答を拒否する。

選択モデルは端末内でプロジェクトごとに保存する。一覧は候補であり、アカウントでの利用可否を保証しない。選択肢は固定せずCodexから取得する。

Firefoxを再起動すると一時拡張は再読み込みが必要。拡張コードの変更後も`about:debugging`から再読み込みする。

画面下部の「Gitの状態と差分」では、選択中のプロジェクトについて「Git状態を取得」を押すと、変更ファイル名を一覧表示する。各ファイル名をタップすると、そのファイルの未ステージ・未追跡差分とステージ済み差分を開く。Codex App Serverを経由せず、Native Hostが読み取り専用のGitコマンドを実行する。通常リポジトリとGit worktreeを扱い、Git管理ディレクトリの実体を確認する。Codex実行中は取得しない。外部ヘルパーを設定したリポジトリや表示上限を超える差分はエラーにする。未追跡ファイルのプレビューはプロジェクト内の通常ファイルかつUTF-8テキストに限り、1ファイル10万バイトまで表示する。ステージング・コミット・pushはまだ利用できない。XubuntuのFirefox画面で取得完了と未追跡の`test.txt`の表示を確認済み。ファイル単位の展開表示は画面で未確認。

## permission profileの適用状態と確認結果

確認日：2026-10-21

FocodexはCodex実行ごとに`focodex-project` permission profileをCLI起動引数で定義し、`thread/start.permissions`で選択する。プロジェクト内の読み書きと`.git`の読み取りを許可し、プロジェクト外の読み書き、`/tmp`と一時ディレクトリ、外部ネットワークは禁止する。汎用の権限拡大承認も許可しない。

Native Hostはターン開始前に、Codexの応答に含まれる`activePermissionProfile.id`が`focodex-project`であること、実行ルートが選択プロジェクトだけであること、承認方針が`on-request`であることを照合する。いずれかが一致しなければ、Codexに指示を実行させない。permission profileと従来の`workspace-write` sandbox指定は併用しない。

CLI 0.163.0とapp-serverで、プロジェクト内の読み書き、プロジェクト外の既知ファイルの読み取り拒否、外部実ファイルへの書き込みが反映されないこと、外部通信の遮断を確認した。Python子プロセス、シェルスクリプト、Make経由のPython、プロジェクト内の外部向けシンボリックリンクにも同じ境界が適用された。通常リポジトリでは`.git`の読み取りは可能だが書き込みは失敗し、Git worktreeでは管理情報がプロジェクト外にあるためCodex側の`git status`も使えない。

XubuntuのFirefox経由では、profile適用後にCodexがプロジェクト内の`test.txt`を作成して正常終了することを確認した。これはHostのprofile照合が通ったことを示す。一方、Firefox経由でプロジェクト外読み取りの失敗と外部通信の失敗を改めて実演したわけではない。これらはCLIとapp-serverで確認済みであり、Firefoxも同じHost設定を使う。SurfaceのFedoraでは、Firefox経由を含む境界試験を別途行う。

## Linuxの`bwrap`とOS別の確認事項

Codexのpermission profileはLinuxで`bubblewrap`と`seccomp`を使う。隔離を開始できない場合、Focodexは指示を実行せずエラーで終了する。端末からのHost試験とFirefox経由のNative Host試験は、起動元の権限が異なり得るため、両方確認する。[OpenAI公式：Sandbox](https://learn.chatgpt.com/docs/sandboxing)、[Permissions](https://learn.chatgpt.com/docs/permissions)

### Xubuntu 24.04（現在の開発機）

2026-10-21、端末からのCodex CLI 0.163.0の一往復は成功した。Snap版Firefoxから起動したNative Hostは当初`thread/start`時に`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`で失敗した。端末は`vscode (unconfined)`、Native Hostは`unconfined`というAppArmorの状態の差があり、`aa-exec -p unconfined`で同じ失敗を再現した。`/usr/bin/bwrap`は存在し、当時は`kernel.apparmor_restrict_unprivileged_userns=1`、`bwrap-userns-restrict`プロファイルが未導入だった。その後ユーザーがFirefox画面から再試験し、「完了」と「Focodex接続成功」を確認した。画面の接続・実行情報は閉じたままのため、profile IDの表示値はこの試験では記録していない。Hostはprofile IDと作業ルートの照合に成功した場合だけターンを開始する。

OpenAI公式がUbuntu 24.04向けに示す限定的な対処は次のとおり。管理者権限が必要。ユーザーによる再試験時の具体的な実施内容は未記録なので、再現時の手順として残す。今後の確認では`接続・実行情報`の`settings.activePermissionProfile.id`が`focodex-project`であることも記録する。ホスト全体のuserns制限を無効化したり、Codexの隔離を外したりしない。

```bash
sudo apt update
sudo apt install apparmor-profiles apparmor-utils
sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
```

### Fedora（Surface実機）

SurfaceのFedoraではまだFocodexのpermission profileを試していない。Ubuntu用のAppArmor手順はそのまま適用しない。まず`command -v bwrap`で確認し、ない場合はOpenAI公式のFedora向け手順`sudo dnf install bubblewrap`で導入する。その後、Codex CLI単体、Focodex Host、Firefox経由の順に、ツールを使わない一往復とプロジェクト内外の読み書き・通信の境界を実機確認する。Fedoraのセキュリティ設定やFirefoxの導入形態による差は現時点で不明であり、Xubuntuでの成功・失敗をSurfaceへ当てはめない。

## 今回の実装範囲

- 固定IDのFirefox拡張 → ページ所有の`runtime.connectNative("focodex")` → Python Host → `codex app-server --listen stdio://`。
- `initialize` → `initialized` → `thread/start` → `turn/start` → `item/completed`／`turn/completed`。
- 指示ごとに一時スレッドを作成し、完了時にapp-serverを終了する。会話の継続・履歴保存はまだ実装しない。
- 登録済みプロジェクトのIDからHost側で作業ディレクトリを決める。拡張から任意のパス・シェルコマンドは受け付けない。
- モデル候補は`model/list`からページをたどって取得する。実行時はCLI起動引数で`focodex-project` permission profileを定義し、`thread/start.permissions`で選択する。`activePermissionProfile`、作業ルート、承認方針を照合できなければターンを始めない。profileはプロジェクト内の読み書き、`.git`の読み取りを許可し、プロジェクト外の読み取り・書き込みとネットワークを禁止する。Codexの設定ファイルには書き込まない。
- 完了・失敗・中断を区別する。一般的な承認要求は拒否し、追加質問はユーザーの明示操作で応答し、他の未対応要求は自動許可せず`unsupported_request`で終了する。
- 同じ設定ファイルを使うHost同士は実行ロックで排他する。
- 接続断・通常のHost終了・180秒の時間切れで、専用プロセス群へTERMを送り、1秒後も残る場合はKILLする。既存のファイル変更は戻さない。
- 停止ボタンは`turn/interrupt`を送り、5秒以内に中断結果が来なければ専用プロセス群を終了する。終了確認後にのみ停止済みと表示する。
- 追加質問への実応答、異常終了・スリープを含む停止管理、音声エディタ連携、Git専用操作は次段階。
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
