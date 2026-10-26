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

専用SSH Agentの復元、固定鍵の登録、日常確認、更新時の注意は[専用Agentの復元と秘密鍵登録：導入・運用・保守](docs/dedicated-ssh-agent-operations.md)を参照。

初回に`~/.config/focodex/config.json`を作成する。既存の設定は上書きしない。
最初の登録対象はこの`focodex`リポジトリ。旧形式の設定では各プロジェクトが単独ワークスペースとして扱われ、権限範囲は広がらない。複数リポジトリを同じ権限で扱う場合だけ、`workspaces`へ明示的に登録する。プロジェクトの追加・移動やCodexのパス変更は、この設定を手動編集する。

```json
{
  "codex": "/absolute/path/to/codex",
  "projects": {
    "focodex": {"name": "Focodex", "path": "/absolute/path/to/focodex"}
  }
}
```

複数リポジトリの例：

```json
{
  "codex": "/absolute/path/to/codex",
  "projects": {
    "focodex": {"name": "Focodex", "path": "/home/user/project/focodex"},
    "voice": {"name": "Fox Voice Editor", "path": "/home/user/project/fox-voice-editor"}
  },
  "workspaces": {
    "foxbook": {"name": "Foxbook", "projectIds": ["focodex", "voice"], "defaultProjectId": "focodex"}
  }
}
```

ワークスペースには明示登録したリポジトリだけが含まれる。共通親ディレクトリや未登録の兄弟リポジトリには権限を与えない。画面ではワークスペース、開始プロジェクト、許可対象一覧を確認してから送信する。Gitパネルの「Git操作対象」は開始プロジェクトと独立に選ぶ。対象は選択ワークスペースに明示登録した1リポジトリ、または「単独リポジトリ」で明示選択した検出済みの1リポジトリに限る。単独リポジトリのGit操作は、ワークスペースを新設せずCodexの永続的な権限対象にも追加しない。

画面の「ワークスペースを管理」から検出ルートを追加し、その配下の配置済みGitリポジトリを発見・登録できる。検出ルートはリポジトリを探すためだけの場所であり、Codexのアクセス権限にはならない。通常画面の「単独リポジトリ」を選ぶと、検出済みのリポジトリ1件だけをその実行中の権限対象にできる。保存したワークスペースには選択したリポジトリだけが所属する。内部IDはFocodexが自動設定し、複数リポジトリをまとめる場合だけ名前を入力する。削除は最後の1件でも可能で、Focodex設定から除外するだけでフォルダやGit履歴は削除しない。

管理画面の「編集するワークスペース」では、「新規作成」と既存ワークスペースを選べる。既存を選ぶと所属リポジトリがチェック済みで表示され、チェックを変えて保存すると構成を更新する。新規作成では全て未選択から始める。

## Firefoxでの検証

1. `about:debugging#/runtime/this-firefox`を開き、「一時的なアドオンを読み込む」から`extension/manifest.json`を選ぶ。
2. ツールバーの「Focodex 接続検証」を押して画面を開く。
3. 「Hostへ接続」を押し、ワークスペース、開始プロジェクト、許可対象のリポジトリ一覧が表示されることを確認する。
4. 自動取得された一覧からモデルを選び、初期入力済みの、ツール使用・ファイル変更を求めない指示を送信する。このPCでは`gpt-5.6-sol`で正常応答を確認済み。
5. 「完了」と応答本文「Focodex接続成功」を確認する。「接続・実行情報」でstatus・threadId・turnId・適用モデル／権限を確認できる。
6. 再送しても1件ずつ実行されることを確認する。モデル拒否が実際に起きた場合は、指示文を保持したまま別モデルを選んで再送できる。一覧取得に失敗した場合は「モデル一覧を再取得」で再試行できる。拒否されるモデルが候補にない場合、失敗後の再送は模擬試験の確認で足りる。
7. 長く続く実行では「停止」を押す。「停止要求中…」の後、Codexの中断結果または専用プロセス群の終了確認を経て「停止済み」と表示する。自然に完了した場合は「完了」と表示する。

停止後は状態の直下に、Codexから受信した終了状態、中断要求への応答、5秒後の強制終了の有無、専用プロセス群の消滅確認を表示する。詳細な値は「接続・実行情報」の`stopDiagnostics`で確認できる。専用プロセス群の消滅を確認できない場合は失敗として表示し、同じHostからの再実行を拒否する。

従来の試験ではコマンド・ファイル変更承認を表示したが、v0.1.2の権限方針では汎用承認を禁止する。外部読み取りの承認UIはMVP対象外とし、Git pushは専用操作を実装するまで利用できない。ファイル変更の差分は対応する`item/started`から取得する。汎用要求では内容を表示しても承認ボタンは出さず、拒否か停止のみ可能。ネットワークや権限変更など、適切に表示できない要求も自動許可しない。追加質問では選択肢または自由文を入力して明示的に送信する。待機中も停止できる。

Native Hostは実行ID、Codex要求に紐付けた一時ID、項目ID、スレッドID、ターンIDを照合する。一般的な承認要求には`accept`を送らない。Codexが要求の解決・失効を通知したら承認画面を閉じ、古い要求への応答を拒否する。

選択モデルは端末内でプロジェクトごとに保存する。一覧は候補であり、アカウントでの利用可否を保証しない。選択肢は固定せずCodexから取得する。

Firefoxを再起動すると一時拡張は再読み込みが必要。拡張コードの変更後も`about:debugging`から再読み込みする。

画面下部の「Git・差分・Commit」では、選択中のプロジェクトについて「Git状態を取得」を押すと、変更ファイル名を一覧表示する。各ファイル名をタップすると、そのファイルの未ステージ・未追跡差分とステージ済み差分を開く。Codex App Serverを経由せず、Native HostがGit状態取得、選択的ステージ、最終確認、Commitを実行する。通常リポジトリとGit worktreeを扱い、Git管理ディレクトリの実体を確認する。Codex実行中は取得しない。外部ヘルパーを設定したリポジトリや表示上限を超える差分はエラーにする。未追跡ファイルのプレビューはプロジェクト内の通常ファイルかつUTF-8テキストに限り、1ファイル10万バイトまで表示する。更新Hostを再登録後、Firefoxで状態取得、Stage、Commit最終確認、Commit、再取得を確認済みである。リモート事前診断と、固定宛先・最小known_hosts・専用Agent・一接続中継を使う読み取り専用SSH接続試験を提供し、Surfaceで実upstreamへの`ls-remote`成功を確認済みである。Fetch、Fast-forward Pull、現在ブランチからupstreamへの非強制Pushを実装した。Surfaceで`git-test`に対し、Stage、最終確認、Commit、通常Pushを実行し、別端末で送信したコミット内容（`あ`）の反映を確認した。Pushは通常リポジトリの現在ブランチと明示upstreamに固定する。異常系など残る実機試験は未実施である。

## permission profileの適用状態と確認結果

確認日：2026-10-21

FocodexはCodex実行ごとに`focodex-project` permission profileをCLI起動引数で定義し、`thread/start.permissions`で選択する。選択したワークスペースの全リポジトリ内の読み書きと`.git`の読み取りを許可し、ワークスペース外の読み書き、`/tmp`と一時ディレクトリ、外部ネットワークは禁止する。汎用の権限拡大承認も許可しない。

Native Hostはターン開始前に、Codexの応答に含まれる`activePermissionProfile.id`が`focodex-project`であること、開始プロジェクトの`runtimeWorkspaceRoots`と追加ルートの`sandbox.writableRoots`の和集合が選択ワークスペースと一致すること、承認方針が`on-request`であることを照合する。ネットワークと一時ディレクトリの制限も確認する。いずれかが一致しなければ、Codexに指示を実行させない。permission profileと従来の`workspace-write` sandbox指定は併用しない。

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
- 同じ設定ファイルを使うHost同士は実行ロックで排他する。Codex起動前に`config.execution.json`へ実行記録を原子的に保存し、ファイルとディレクトリをfsyncする。ロックが解放されても、この記録が残る間はCodex・Git状態取得・設定変更を拒否する。将来のGit変更操作にも同じ確認を必須とする。
- 実行記録は専用プロセス群の消滅を確認した場合にだけ削除する。HostのSIGKILL・終了確認失敗・起動中の障害では記録を残す。FirefoxやHostの再起動、PIDが見当たらないことだけでは解除しない。正常な記録はOS再起動後、Linux boot IDの変化を照合して解除する。記録を手動削除して迂回しない。破損記録は再起動しても自動解除せず、個別調査が必要。
- 接続断・通常のHost終了・180秒の時間切れで、専用プロセス群へTERMを送り、1秒後も残る場合はKILLする。既存のファイル変更は戻さない。
- 停止ボタンは`turn/interrupt`を送り、5秒以内に中断結果が来なければ専用プロセス群を終了する。終了確認後にのみ停止済みと表示する。
- 追加質問への実応答、スリープを含む停止管理、音声エディタ連携の実機検証は残っている。第2段階のStatus・選択的ステージ・固定treeの確認・Commit・Commit欄への明示反映を実装・Firefoxで確認済み。設定規則と方式は[第2段階設計](specification/Git第2段階設計提案.md)を参照。第3段階のFetch、Fast-forward Pull、非強制Pushは実装済みで、`git-test`ではCommitからPush、別端末での反映確認までの正常系を1回確認した。更新拒否・通信失敗・中断など、残る実機試験は今後の課題である。
- 応答は完了したagentMessageを本文として保持する。合計16万文字超はエラーにする。逐次ログ・生のプロトコルイベントは通常表示しない。

## ブラウザを使わないHost試験

```bash
python3 -m unittest discover -s tests -v
node tests/test_probe.mjs
node tests/test_draft.mjs
node tests/test_draft_client.mjs
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

## fox-voice-editorから文章を受け取る

両拡張を同じFirefoxプロファイルに読み込み、fox-voice-editorのプレビュー横の「Focodexへ貼り付け」を押します。初期送信先は`focodex@foxbook.example`です。受信はHostへ未接続でも利用できます。

- Focodexは文章を最大3件のFIFO受信バッファへ保存し、受信時に指示文を変更しません。バッファ項目の「指示文へ」または「Commit欄へ」で、選んだ入力欄へ全文を反映します。
- 反映前の文章は「元に戻す」で復元できます。「クリア」もUndoできます。受信・反映だけでCodexは実行されません。
- 実行中は受信項目の反映・削除を止め、完了後に再び操作できます。受信文は折りたたみ可能な「fox-voice-editorからの受信バッファ」に表示します。
- 同一ID・同一本文の明示再送は重複追加せず受理済みとして扱います。同一ID・異本文は拒否します。状態照会APIはありません。
- 更新後は`about:debugging`で両拡張と既存の操作タブを手動で再読み込みしてください。

外部APIは許可したfox-voice-editor編集ページからの文章追加だけです。送信元検証と公開操作は[API契約](specification/draft-api-v1.md)、実機で未確認の項目は[検証記録](docs/verification.md)を参照してください。

```bash
node tests/test_draft.mjs
```


## Git第2段階の操作

更新したHostには`focodex.py`、`git_ops.py`、`git_remote.py`のすべてが必要。`python3 scripts/install_host.py`で既存設定を保持して再登録し、拡張と操作タブを再読み込みする。リポジトリ内のソース変更だけでは、コピー済みのNative Hostは更新されない。

1. 「Git・差分・Commit」を開き、「Git操作対象」を選んで「Git状態を取得」を押す。
2. 各ファイルを開いて差分を読み、「このファイルをステージ」を選択して「選択ファイルをステージ」を押す。以前からステージ済みの変更も保持する。未ステージの名前変更が削除・未追跡の2件で表示された場合は、移動前後の両方を選ぶ。
3. メッセージを入力するか、受信バッファの「Commit欄へ」で全文を反映する。指示文とCommit欄には独立した「元に戻す」「クリア」があり、反映だけでGitは実行されない。
4. 「ステージ済み全体を最終確認」を押し、対象ブランチ・作者・メッセージ・全差分を確認して「確認した内容をCommit」を押す。確認の有効期限は15分で、メッセージ変更後は再確認が必要。
5. 完了後は次の操作前にGit状態を再取得する。確認後にファイル・属性・インデックス・HEADが変わった場合も、再取得・再確認からやり直す。

確認したtreeだけをCommitし、作業ツリーの追加編集を混入させない。実インデックスを自動resetせず、ブランチを切り替えたり、競合を自動解消したりしない。リモート操作は現在ブランチの単一upstreamに限定する。

リモートGitは、固定宛先・最小known_hosts・専用Agent・一接続中継を使う読み取り専用SSH接続試験まで実装済みで、SurfaceのFocodex画面から実upstreamへの`ls-remote`成功を確認した。GDM/PAMによるGNOME Keyringの自動解除、再起動後の専用Agent復元、固定鍵の自動登録もSurfaceで確認済みである。Fetch、Fast-forward Pull、非強制Pushを実装し、SurfaceのFocodex画面で`git-test`のStage、Commit、Pushを実行後、別端末で送信内容の反映を確認した。これは通常経路1回分の実機確認であり、異常系を含む網羅試験は未実施である。詳細と既存の制約は[Pull・Push事前調査・検証事項](specification/Focodex_Pull_Push_事前調査・検証事項.md)および[読み取り専用SSH接続試験 引継ぎ記録](specification/Focodex_読み取り専用SSH接続試験_引継ぎ記録_2026-10-24.md)を参照する。

## Git コミット識別情報（検証環境）

`git-test`でのCommit最終確認は、Git の `user.name` と `user.email` が未設定の状態では実行できなかった。`include` を使わない通常のグローバル設定として、検証環境では次を直接登録して正常系を確認した。

```text
user.name  tuyomori
user.email tuyomori@gmail.com
```

```bash
git config --global user.name "tuyomori"
git config --global user.email "tuyomori@gmail.com"
```

初版は表示可能なUTF-8テキスト、通常ファイル、index v2/v3が対象。バイナリCommit、リンク・submodule・sparse/split index、進行中のmerge/rebase、detached HEADなどは拒否する。外部フィルター・include等の設定や非対応の属性があれば、理由を表示して操作を止める。フックと署名は無効化し、認証ヘルパーや通信操作は呼び出さない。設定は自動修正しない。

CodexとGitは同じ設定単位の排他と永続実行記録を共有する。Host設定と実行記録は、Codexが編集できるワークスペースとGit操作対象の外へ置く。Gitの時間切れ・シグナル終了・結果検証失敗でも禁止記録を残す。Host異常終了のタイミングによってはCommitが既に記録されている可能性があるため、自動再送しない。再起動後も残ったGitのindex.lockを自動削除しない。必要な状態確認はFocodex外で行う。

自動試験では実Gitの一時リポジトリと模擬画面を検証した。2026-10-23には、更新Hostの再登録後、FirefoxでGit状態取得、Stage、Commit最終確認、Commit、再取得の通常操作を確認した。表示再取得失敗からの復旧分岐は自動試験で確認している。拡張間の実配送とSurfaceのタッチ操作は未確認。
