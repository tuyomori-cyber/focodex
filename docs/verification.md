# 接続検証記録

実施日：2026-10-20

## 実装・試験条件

- Linux、Python 3、Codex CLI 0.145.0。
- Native Messaging形式の長さ付きUTF-8 JSONをHostに渡し、app-serverとはstdioのJSONLで通信。
- 登録済みプロジェクト：`/home/tuyomori/project/focodex`。
- 初期化・スレッド開始・指示送信でモデル・サンドボックス・承認方針を上書きしない。
- 認証情報は既存のCodex側の仕組みを利用し、Focodexでは読み出し・保存しない。

## 自動試験

`python3 -m unittest discover -s tests -v`：8件成功。

- 長さ付きJSONによるプロジェクト一覧・指示送信・日本語応答・終了状態の往復、同じHostでの再実行。
- 既存設定を継承するリクエスト形式と作業ディレクトリ。
- failedをcompletedと扱わないこと。
- 承認要求を自動許可せず、未対応として終了すること。
- Codex接続断・RPCエラーを失敗として返すこと。
- 未登録プロジェクトをCodex起動前に拒否すること。
- 複数Hostの実行排他、接続断・SIGTERM時の模擬Codexプロセス回収。
- 一時ホームへのHost登録と、再登録時の既存設定保持。

JavaScriptの構文チェックも成功。模擬Codexによる試験であり、実モデルの応答成功を意味しない。

## 実Codexとの試験

`python3 scripts/probe_host.py --config /tmp/focodex-live-config.json`を実行。
送信文は「ツールを使わず、ファイルの読み書きやコマンド実行をせずに、『Focodex接続成功』とだけ返答してください。」。

1. 実行環境のサンドボックス内では、既存Codex状態DBの更新権限がなくapp-serverが起動失敗。Hostがfailedを返すことを確認。
2. 実行環境の制約を外した再試験では、初期化・スレッド作成・ターン開始・終了イベント受信まで到達。
3. 既存設定から継承されたモデル`gpt-6.1-sol`が、使用中のChatGPT認証では非対応としてサーバーに拒否された。

返却された主な情報：

- `status`: `failed`
- `text`: 空文字列
- `settings.model`: `gpt-6.1-sol`
- `settings.approvalPolicy`: `on-request`
- `settings.approvalsReviewer`: `user`
- `settings.sandbox`: `readOnly`、`networkAccess: false`
- `threadId`・`turnId`: 取得済み
- エラー：`The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.`

**Host〜実Codexの接続と失敗結果の往復は確認済み。実モデルからの正常応答は未確認。**
Codexの既存設定は変更していない。CLI側で利用可能なモデル設定を確認した上で同じ試験を再実行する。

## 未確認

- Firefoxで拡張を一時読み込みし、Hostへ接続して実モデルから正常応答を受ける全経路。
- Surface実機での動作。
- 承認・追加質問への回答と停止状態管理（次段階）。
- HostのSIGKILL・Firefoxの強制終了・スリープ復帰・子孫プロセスの残留を含む異常系。今回の通常接続断／SIGTERM試験の成功からは保証しない。

## 追加検証：Firefox内のモデル選択

同日、モデル候補の取得・選択・再取得と端末内の選択保存を追加。

- この追加検証時の実CLIは`0.163.0`。初回検証時の`0.145.0`とは異なる。
- `model/list`をNative Host経由で取得し、ページング・非表示候補の除外を確認。
- 選択モデルを`thread/start.model`へ渡す。サンドボックス・承認方針の指定とCodex設定ファイルの書き換えは行わない。
- Host自動試験は10件成功。追加2件で候補取得・ページング・明示選択・候補外モデルの拒否を確認。
- `node tests/test_probe.mjs`で、画面のモデル未選択時の送信抑止、失敗後の別モデルでの再送、指示文保持、保存選択の復元、一覧取得失敗後の再取得を確認。DOMとHostを模擬したロジック試験でありFirefox実機試験ではない。
- 実Hostで`--list-models`により候補一覧を取得。既に拒否された`gpt-6.1-sol`も一覧に含まれるため、一覧への掲載だけで利用可能とは判断しない。
- 実Hostで`python3 scripts/probe_host.py --model gpt-5.6-sol`を実行し、`status: completed`、`text: Focodex接続成功`を確認。
- この成功時の適用設定は`model: gpt-5.6-sol`、`approvalPolicy: on-request`、`approvalsReviewer: user`、`sandbox: readOnly`、`networkAccess: false`。
- 更新したHostはこのPCのFirefox向けに再登録済み。Firefoxで拡張と操作タブを再読み込みし、モデル選択から送信までを確認する作業は残っている。

これにより上記の「実モデルからの正常応答は未確認」は解消。この時点ではFirefox全経路とSurface実機は未実施。

## 追加検証：XubuntuのFirefox画面

2026-10-21、ユーザー提供のFirefox画面で、Focodex操作タブから`GPT-6-Luna (gpt-6-luna)`を選択し、入力済みの無変更確認文を送信した結果を確認。

- プロジェクト欄には`Focodex (/home/tuyomori/project/focodex)`が表示された。
- 状態は「完了」、応答全文は「Focodex接続成功」。
- これによりFirefoxのHost接続、プロジェクト・モデル選択、指示送信、正常応答の画面経路を確認済みとする。
- 現在のFirefox候補には以前拒否された`gpt-6.1-sol`が表示されていない。モデル拒否後の再選択・再送は画面ロジック試験で確認済みとし、拒否される候補を探して実機試験することは必須条件にしない。
- Surface実機、承認・追加質問・停止は、この時点では未確認。

## 停止操作の実装と模擬試験

同日、停止ボタンとHostの`turn/interrupt`経路を追加。停止要求中は再実行・連続停止を受け付けず、中断結果を受信して専用プロセス群を回収した後に「停止済み」とする。5秒以内に中断結果が届かない場合は、専用プロセス群へTERMを送り、最大1秒後にKILLする。

- 模擬Codexが中断に応じる場合と、応答せず強制終了が必要な場合を試験。後者は子プロセス消滅と次回実行可能まで確認。
- Host自動試験12件、画面ロジック試験、JavaScript構文チェックが成功。
- この時点ではFirefox実画面からの停止、Codex実モデルの中断、Host異常終了時の子孫プロセス回収は未確認。

## 追加検証：XubuntuのFirefoxから停止

2026-10-21、ユーザー提供のFirefox画面で、`GPT-6-Luna`に「5秒待機後に返答」と指示した実行を停止。画面に「停止済み」と「（応答本文なし）」が表示された。Firefoxの停止ボタンから結果表示までの経路は確認済み。

画面の「接続・実行情報」は閉じた状態のため、Codexが返した終了状態、通常の`turn/interrupt`応答か5秒後の強制終了か、プロセス群の消滅はこの画像だけでは判定しない。これらと異常終了時の回収は別途確認する。

## 停止診断表示の追加

同日、試験画面の状態直下に停止診断を追加。Hostの結果に`stopDiagnostics`を含め、Codexの`turn/completed`で受信した状態、中断RPCへの応答の有無、5秒後の強制終了の有無、専用プロセス群の消滅確認を区別する。強制終了時にCodex終了通知を受けていなければ「未受信」と表示する。プロセス群の消滅を確認できない場合は失敗とし、同じHostでの再実行を拒否する。

Host自動試験12件、画面ロジック試験、JavaScript構文チェックが成功。Firefoxの新診断表示は拡張再読み込み後に確認する。

## 追加検証：Firefoxの停止診断

2026-10-21、ユーザーがFirefox画面で次の診断表示を確認した。

> Codex終了=interrupted、中断応答=受信、5秒後の強制終了=なし、専用プロセス群=消滅確認

この実行ではCodexの中断通知を受信し、強制終了を使わずに専用プロセス群の消滅までHostが確認した。タブ閉鎖・再読み込み、Firefox終了、Host異常終了時の回収は別途確認する。

## 承認・追加質問の模擬試験

同日、app-serverのサーバー要求に対して、コマンド承認の承認／拒否、選択肢と自由文の追加質問への回答をFirefox画面から明示送信する経路を実装。質問待ち・承認待ち中の停止を許し、停止開始後の回答は拒否する。要求IDと実行IDを照合し、別の要求や期限切れの要求には回答しない。

- Codex CLI 0.163.0の`generate-json-schema`で要求・応答形式を確認。コマンド承認ではその実行だけの`accept`／`decline`を使い、セッション全体の承認や権限変更は行わない。
- この時点ではファイル変更の承認要求そのものに変更差分が含まれないため、拒否か停止のみ可能とした。後述の更新で対応する`item/started`から差分を取得するよう変更した。権限変更等のその他要求は自動許可しない。
- 模擬CodexでHost自動試験16件、画面ロジック試験、JavaScript構文チェックが成功。実CodexとFirefoxを通した承認・質問の往復は未確認。

## 追加検証：Firefoxでコマンド承認を拒否

2026-10-21、`readOnly`設定の実Codexへ、`/tmp/focodex-approval-probe-20261021.txt`へ書き込む`printf`コマンドを指示。Firefoxの承認要求でユーザーが「拒否する」を選択した。

- 画面上のターン状態は「完了」。Codexは承認が拒否されたためコマンドを実行できず、ファイルを作成していないと応答した。
- 対象ファイルが存在しないことを端末側でも確認。ターンの「完了」はコマンドの実行成功を意味しない。
- 承認して実行する経路と、追加質問への回答は実Codexでは未確認。

## 追加検証：Firefoxでコマンドを承認

同じ`printf`コマンドを再送し、ユーザーがFirefoxの承認要求で「承認する」を選択。`/tmp/focodex-approval-probe-20261021.txt`が作られ、Mousepad画面と端末の両方で内容`Focodex承認試験\n`を確認した。対象が通常ファイルであり、内容が期待値と一致することを検査してから削除し、ファイルがなくなったことも確認した。

コマンド承認の拒否・承認の両経路は実Codexで確認済み。追加質問への回答は未確認。

## 承認内容と要求IDの表示・照合

ユーザーの指摘を受け、承認前に判断できる内容を画面へ明示するよう更新。使用中のCodex CLI `0.156.1`の`generate-json-schema`と[OpenAI公式app-server仕様](https://learn.chatgpt.com/docs/app-server)で、コマンド承認要求の項目と単発／セッション承認の違い、ファイル変更の`item/started`通知を確認した。

- 画面にコマンドまたは変更対象・差分、Codexが示した理由、作業場所、承認範囲「今回の操作のみ」を表示。「拒否」と「今回だけ承認」を区別。
- 理由・操作内容・必要な作業場所または差分を取得できなければ、画面とHostの両方で承認を拒否する。ネットワーク承認や形式が異なる要求も自動許可しない。
- Hostは実行ID、ランダムな要求トークン、項目ID、スレッドID、ターンIDを照合してから元のCodex JSON-RPC要求IDへ応答。`serverRequest/resolved`で失効した要求への回答を拒否する。
- Hostは`accept`／`decline`のみを送信。`acceptForSession`や権限の継続的な追加は送信しない。
- 模擬CodexでHost試験20件、画面ロジック試験、JavaScript構文チェックが成功。更新した承認画面のFirefox実機確認は残る。

## 権限方針の改訂（2026-10-21）

仕様書v0.1.2で、プロジェクト内の読み書きとGit操作を通常許可、外部読み取りとGit pushを専用承認、外部書き込み・権限昇格・一般の外部通信を禁止する方針に改訂した。上記の`/tmp`書き込み承認は旧方針下の試験であり、新方針では認めない。Codex CLI 0.156.1の生成スキーマでは`workspaceWrite`に読み取り先の制限がなく、外部読み取りの承認制には別のOS隔離が必要。実装・実機検証までは完全達成とみなさない。

## 新権限方針に合わせたHost変更

`thread/start`で`workspace-write`と`on-request`を指定し、`turn/start`で追加の書き込み先なし、ネットワーク禁止、`/tmp`と`TMPDIR`の書き込み除外を指定。有効なスレッド設定が異なればターンを開始しない。汎用のコマンド・ファイル変更要求は内容を表示しても承認不可とし、拒否のみ可能。Host模擬試験20件と画面ロジック試験は成功。実Codexへのツール不使用の一往復では`gpt-6-luna`で正常応答し、スレッド設定が`workspaceWrite`、`networkAccess: false`、`on-request`であることを確認した。プロジェクト内書き込みと外部アクセス境界は実Codexで未検証。

さらに一時的な登録プロジェクトを作り、`gpt-6-luna`にその中の`check.txt`だけの作成を指示した。承認要求なしでターンが完了し、内容`FocodexPolicyOK\n`を確認した。一時プロジェクトと設定は終了時に削除した。これはプロジェクト内書き込みの確認であり、外部読み取りを遮断できる証拠ではない。

## 次の権限検証方針

ユーザー方針を「プロジェクトの中では自由に開発できる。それ以外には原則触れない」に整理。外部読み取りの承認UIはMVPで実装しない。OpenAI公式のベータ版permission profileには、`":root" = "deny"`、`":minimal" = "read"`、workspace rootの書き込みを組み合わせる例がある。従来のsandbox設定とは併用できないため、まず使用中のCLI 0.156.1とapp-serverで動作を検証する。成功を確認するまで現行の外部読み取り境界は未達のままとする。参照：https://learn.chatgpt.com/docs/permissions

## Codex CLI 0.163.0 permission profileの実動作

2026-10-21、Focodexが参照する`/home/tuyomori/.local/bin/codex`が`0.163.0`であることを確認。既存のCodex設定とFocodex Hostは変更せず、app-server起動時の一時`-c`指定で検証した。`initialize.capabilities.experimentalApi=true`、`permissionProfile/list`で一時profileが`allowed: true`、`thread/start.permissions`で選択後の`activePermissionProfile.id`が一致することを確認した。

一時profileは`":root"="deny"`、`":minimal"="read"`、`":workspace_roots"."="write"`、`":tmpdir"="deny"`、`":slash_tmp"="deny"`、`network.enabled=false`を基本とした。Codex CLI本体がホーム配下に置かれているため、その実体パスを読み取り例外に追加した。追加しない場合、bubblewrap内でCodex本体を実行できず、全コマンドが開始前に失敗した。

| 試験 | 結果 |
| --- | --- |
| 一時プロジェクト内の読み取り・書き込み | 成功 |
| プロジェクト外の既知の一時ファイル読み取り | `FileNotFoundError`で失敗 |
| プロジェクト内のシンボリックリンク経由で外部ファイルを読み取り | `FileNotFoundError`で失敗 |
| プロジェクト外の既知の一時ファイルへ書き込み | コマンドは終了コード0だが、ホスト側の実ファイルは不変。隔離内の一時的な書き込みとみられるため、結果表示の設計には注意が必要 |
| 外部へのTCP接続 | `PermissionError: Operation not permitted` |
| 一時Gitリポジトリ内の`git status --short` | 成功 |
| 同じリポジトリ内の`git add` | `.git/index.lock`が読み取り専用で失敗 |
| `gpt-6-luna`の実ターンから外部ファイルを読むコマンド | コマンド失敗。モデルも`FileNotFoundError`と報告 |

一時プロジェクトと外部ファイルは試験後に削除した。Focodex Hostは引き続き従来の`workspaceWrite`を指定しており、通常のFirefox実行にこの隔離はまだ適用されていない。次はHostのprofile選択・有効設定照合とGit操作の扱いを実装する。参照：[OpenAI公式 permission profile](https://learn.chatgpt.com/docs/permissions)、[App Server](https://learn.chatgpt.com/docs/app-server)。

## 子プロセス・シンボリックリンクルート・Git worktreeの境界検証

2026-10-21、Codex CLI 0.163.0の一時permission profile（前節と同じ基本設定）を使い、Codex App Serverの`command/exec.permissionProfile`と`thread/start.permissions`を検証した。本番Hostは変更していない。全試験はリポジトリ内に作った一時プロジェクトと既知の一時外部ファイルだけを使い、終了後に削除した。初回のPythonスクリプト生成には引用符の誤りがあったため修正し、以下は修正後の結果である。

| 経路 | 結果 |
| --- | --- |
| プロジェクト内`child.py` → `subprocess.run()` → 子Python | プロジェクト内書き込み成功。外部読み取りとTCP接続は失敗。外部書き込みの子プロセスは終了コード0だが実ファイルは不変 |
| プロジェクト内`child.sh` → `cat` | 外部読み取り失敗。プロジェクト内書き込み成功 |
| プロジェクト内`Makefile` → shell → `child.py` → 子Python | 直接実行と同じ隔離結果 |
| `gpt-6-luna`の実ターン → `python3 child.py` | コマンドは終了コード0で、子プロセスの外部読み取り・通信が失敗し、外部実ファイルは不変 |
| プロジェクトルートをシンボリックリンクに指定 | `activePermissionProfile`が適用され、リンク先プロジェクト内の書き込み成功。外部読み取りとプロジェクト内リンク経由の外部読み取りは失敗 |
| Git worktreeをプロジェクトルートに指定 | ソース内の書き込み成功、外部読み取り失敗。ただし`git status`と`git add`は、worktree外にあるGit管理情報が見えず失敗 |
| 通常Gitリポジトリで`git restore --worktree` | `.git/index.lock`を作れず失敗。試験用の変更ファイルは元に戻らなかった |

これらの試験は、観測した子プロセス経路でもCodex標準profileの境界が維持されたことを示す。全ての開発ツールやGitコマンドを網羅した保証ではない。`rg`はこの環境ではホーム配下のVS Code拡張に置かれており、profile内の実モデルターンでは見つからなかった。通常のGitリポジトリで`.git`を読み取り専用にすると`git status`は使えるが、worktreeでは管理情報が外部にあるためCodex側のGit読み取りも利用できない。Git操作をHostに分離する設計と整合する一方、Hostはworktreeの管理ディレクトリの実体を検証して扱う必要がある。

## Native Hostへの標準適用

2026-10-21、Hostの`run`経路をCLI起動引数で定義した`focodex-project` permission profileへ切り替えた。`initialize.capabilities.experimentalApi=true`を送り、`thread/start.permissions`でprofileを選択する。従来の`thread/start.sandbox`と`turn/start.sandboxPolicy`は送らない。`activePermissionProfile.id`、`runtimeWorkspaceRoots`、`approvalPolicy`が期待値と異なる場合は`turn/start`前に終了する。Codex実行ファイルの実体パスは読み取り例外に含める。

模擬Host試験21件と画面ロジック試験が成功。実Codex CLI 0.163.0と`gpt-6-luna`によるHost一往復で「Focodex接続成功」を受信し、`activePermissionProfile.id=focodex-project`、作業ルートが選択プロジェクトのみ、`approvalPolicy=on-request`、専用プロセス群の消滅を確認した。この一往復はツールを使わないため、ファイル境界そのものの検証結果は前節の実験による。

## 複数ワークスペースルートの検証

2026-10-21、`scripts/verify_workspace_permissions.py`でCodex CLI 0.163.0を一時profileで起動した。既存のHost設定・Codex設定・既存リポジトリは変更していない。試験用のA・B・未登録CとCへ向くA内のシンボリックリンクは一時ディレクトリ内に作成し、終了時に削除した。

profileの`workspace_roots`にはAとBを設定し、`thread/start.cwd`にはAを指定した。`permissionProfile/list`でprofileが`allowed: true`であることを確認し、`thread/start`でprofile IDと`approvalPolicy=on-request`を確認した。応答は`runtimeWorkspaceRoots=[A]`、`sandbox.type=workspaceWrite`、`sandbox.writableRoots=[B]`、`networkAccess=false`、一時ディレクトリ除外を返した。したがって、実行時の許可ルートは両配列の和集合として照合できる。

| 試験 | 結果 |
| --- | --- |
| Aへの書き込み | 成功 |
| Bへの書き込み・読み取り | 成功 |
| 未登録Cの直接読み取り | 拒否（`No such file or directory`） |
| A内のリンク経由でのC読み取り | 拒否（`No such file or directory`） |
| 未登録Cへの書き込み | 拒否。ホスト側のCは不変 |

`command/exec`の`command`は文字列ではなく引数配列を要求した。最初に送った`initialized` RPCはこのCLIのapp-serverで未対応だったため、検証スクリプトでは送らない。ワークスペース実装時は、Hostが使用するプロトコルの現行性を別途確認し、この結果だけを既存Hostの互換性証明とは扱わない。

ワークスペース対応の実装後、`focodex`と`foxbook-control`を含む一時設定で実Hostを起動した。`focodex`を開始プロジェクトにして`gpt-6-luna`へツール未使用の一往復を送り、「Focodex接続成功」を受信した。Hostが返した設定は`runtimeWorkspaceRoots=[focodex]`、`sandbox.writableRoots=[foxbook-control]`、`workspaceWrite`、ネットワーク禁止、一時ディレクトリ除外、`activePermissionProfile.id=focodex-project`、`approvalPolicy=on-request`であり、実装した和集合照合を通過した。既存の`~/.config/focodex/config.json`は変更していない。Firefox経由の複数リポジトリ編集は未確認である。

## リポジトリ検出とワークスペース管理

Native Hostに検出ルート追加、深さ3・100件上限のリポジトリ検出、ワークスペース保存・削除の固定操作を追加した。検出では通常リポジトリの`.git`ディレクトリとworktreeの`.git`ファイルを識別するだけで、Git設定・フック・フィルターを実行しない。検出ルートはCodexのpermission profileへ渡さず、保存時に選択した作業ツリーだけを`projects`と`workspaces`へ登録する。設定ファイルはロック、一時ファイル、`fsync`、原子的置換で更新する。

Host試験では、旧形式設定に検出ルートを追加し、通常リポジトリとworktreeを検出して新規ワークスペースへ保存する経路を確認した。画面ロジック試験では管理画面を開き、設定の取得と検出結果の表示を確認した。Firefox実機での管理画面操作は未確認である。

## Firefox Native Hostのbwrap起動失敗

2026-10-21、Firefox経由の送信では`thread/start`時に`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`で停止した。Codexターンとコマンドは開始していない。端末のHost試験は`vscode (unconfined)` AppArmorプロファイルで成功したが、Firefox SnapのNative Messagingポータルが起動したHostは`unconfined`だった。`aa-exec -p unconfined`で同じ`bwrap`失敗を再現した。Ubuntu 24.04の`kernel.apparmor_restrict_unprivileged_userns=1`、`/usr/bin/bwrap`は存在するが`/etc/apparmor.d/bwrap-userns-restrict`は未導入。したがって、Firefoxと端末のAppArmor状態の差が直接の原因と判断した。

OpenAI公式の[Sandbox](https://learn.chatgpt.com/docs/sandboxing)はUbuntu 24.04向けに`apparmor-profiles`と`apparmor-utils`を入れ、配布される`bwrap-userns-restrict`を`/etc/apparmor.d/`へ配置・読み込みする方法を示している。管理者権限が必要で、このセッションでは`sudo -n`がパスワードを要求したため未適用。ホスト全体のuserns制限を無効化する変更や、Codexの隔離を外す変更は行っていない。Native HostはこのエラーをLinux隔離の起動失敗として表示し、実行を終了する。

その後ユーザーがXubuntuのFirefox画面から同じツール不使用の指示を再送し、「完了」と応答本文「Focodex接続成功」を確認した。修復時に実施したコマンドと`接続・実行情報`のprofile ID表示値は提供されていない。Hostは`activePermissionProfile.id=focodex-project`などを照合した後にのみ`turn/start`を送るため、画面の正常完了はHostの照合が通ったことを示す。ファイル境界・通信境界のFirefox経由の再検証ではない。

## Codexから独立したGit状態・差分表示

2026-10-21、Native Hostに`git_snapshot`を追加した。登録済みプロジェクトだけを対象に`/usr/bin/git`を引数配列で実行し、Codex App Serverは通さない。プロジェクトのリポジトリルートとGit管理ディレクトリを確認し、通常リポジトリと相互リンクのあるworktreeに対応する。Codex実行との排他、任意パス・任意コマンドの拒否、10秒・25万バイトの表示上限を設けた。環境の`GIT_*`値を除去し、グローバル・システム設定、fsmonitor、フック、外部diff、textconv、任意のGit pagerを使わない。フィルターとinclude設定を検出したリポジトリは表示を拒否する。

模擬Host試験24件と画面ロジック試験が成功。実FocodexリポジトリでもNative Messaging形式のHost応答として状態307バイト、未ステージ差分23038バイト、ステージ済み差分0バイトを取得した。Firefox画面はまだ再読み込み・実機確認していない。ステージング・コミット・pushは未実装であり、Git変更操作の外部ヘルパーや認証経路は別途設計する。

その後、ユーザーがXubuntuのFirefoxでCodexにプロジェクトルートの`test.txt`作成を指示し、「完了」を確認した。実ファイルの内容は`test`。Gitパネルの「取得完了」と未追跡`test.txt`の表示も確認した。未追跡ファイルはGitの通常差分に含まれないため、両差分欄は「差分なし」だった。日本語ファイル名がGitの八進数エスケープで表示されていたため、`core.quotePath=false`を読み取りコマンドに追加し、画面に未追跡ファイルの説明を追記した。表示改善後のFirefox実機確認はこれから。

ユーザーの指摘を受け、Hostが`git ls-files --others --exclude-standard -z`で列挙した未追跡ファイルを追加表示するよう変更した。プロジェクト内を`O_NOFOLLOW`でたどり、通常ファイルのUTF-8テキストだけを1ファイル10万バイトまで読み取る。通常の未ステージ差分に新規ファイルのプレビューを連結する。シンボリックリンク、バイナリ、大きなファイルは内容を表示せず理由を示す。Firefox画面での再確認はこれから。

その後、Git状態をファイル単位で返すよう変更した。画面は変更ファイル名だけを通常表示し、各項目のタップでそのファイルの未ステージ・未追跡差分とステージ済み差分を開く。未追跡`test.txt`を含む実FocodexリポジトリのHost応答で、全8項目がファイル単位となり、`test.txt`の差分に`+test`が入ることを確認した。模擬Host試験と画面ロジック試験も成功。Firefox画面での確認はこれから。

## 2026-10-22：受信バッファ連携API v1

実装契約：`specification/draft-api-v1.md`。Focodex 0.1.11、fox-voice-editor 0.1.1。

- `node tests/test_draft.mjs`：未許可ID/URLの副作用ゼロ、不正入力、4件目でのFIFO押し出し、同一ID・同一本文の重複応答、同一ID・異本文の拒否、保存失敗、旧1件保留データの移行、再起動後の重複検出を確認。
- `node tests/test_probe.mjs`：既存のモデル選択・停止診断・承認・追加質問の画面ロジックが成功。
- `node tests/test_focodex_send.mjs`：fox-voice-editorの`accepted`／`duplicate`／`rejected`送信契約と自動再送なしを確認。
- `git diff --check`を両拡張で確認。

これらは模擬API・DOMでの検証であり、Firefoxでの実配送・描画・権限反映を保証するものではない。実機では両拡張と操作タブを手動再読み込みし、以下を確認する。

1. 4件を送信し、受信バッファが最新3件だけを表示すること、反映後も項目が残ることを確認する。
2. 「指示文へ」が確認なしで反映し、「元に戻す」と「クリア」から直前の指示文へ復元できることを確認する。
3. Codex実行中は受信項目の反映・削除が無効になり、終了後に再有効化されることを確認する。
4. 応答を失った送信を同じID・本文で再送し、重複追加されないことを確認する。別IDの試験拡張と同一ID・異本文は拒否され、保存されないことも確認する。
5. XubuntuとSurfaceのSplit Viewで、折りたたみ、タッチによる反映・Undo・削除・再送を確認する。

実機通信・Split View・Surfaceタッチは未確認。Commitメッセージ欄への反映は第2段階の対象である。

## 第2段階の先行修正：永続的な終了未確認状態（2026-10-22）

Git変更機能に先立ち、既存Codex経路でロック解放と安全な待機状態を分離した。起動前に`config.execution.json`を原子的に保存・fsyncし、所有トークンの一致と専用プロセス群の消滅を確認したときだけ削除する。終了未確認・Host強制終了後は同じ設定の別HostでもCodex・Git状態取得・設定変更を拒否する。モデル取得も同じ管理とする。

- `python3 -m unittest discover -s tests -v`：30件成功。
- 実HostをSIGKILLしてflockが解放された後も、別Hostのrun・models・git_snapshot・設定変更が拒否されることを確認。模擬Codexを後から終了させても禁止は解除されない。
- cleanupでのシグナル送信失敗とプロセス群消滅未確認を模擬し、ロックが解放されても永続記録と禁止が残ること、cleanup再呼び出しで記録を誤削除しないことを確認。
- 破損・形式不正・起動ID不正の記録を拒否することを確認。正常記録について、boot ID変化を模擬してOS再起動後の解除経路を確認。
- 既存の正常終了、SIGTERM、接続断、停止後の再実行も成功。実際のOS再起動とFirefoxでの表示確認は未実施。

確認範囲は既存の専用プロセス群。同じ設定ファイルを使うHost同士での保護であり、異なる設定による同一リポジトリ利用や別セッションへ離脱した子孫の包含を新たに保証するものではない。

## Git第2段階の方式比較（2026-10-22）

この先行検証時点では[設計提案](../specification/Git第2段階設計提案.md)だけを追加し、Git変更機能は未実装だった。その後の実装・検証は次節に記録する。Pull・Pushは引き続き対象外。

ローカルGit 2.43.0の使い捨てリポジトリだけで、次を事前確認した。

- 確認後に実インデックスを変更しても、確認treeを指定したcommit-treeの内容は変わらない。
- index.lock保持中も、コピーした一時インデックスに対するwrite-treeで確認treeとの不一致を検出でき、通常のgit addは拒否される。
- 旧OIDを指定したupdate-refは、参照が変わると失敗し、別の更新を上書きしない。
- HostがHEAD.lockを自前保持するとupdate-ref自身と競合するため、この組み合わせは採用しない。参照ロックはGitに任せ、更新先を固定したブランチrefとする。

これらはHost/UIのCommit実装や設定許可規則の検証成功を意味しない。外部ヘルパーの不実行、worktree、初回Commit、クラッシュ時の結果照合等は方式確定後の実装試験に残す。


## 第2段階の実装と検証（2026-10-22）

②設定許可規則・③固定tree方式の承認を受け、Status・選択的ステージ・固定したステージ済み全差分の確認・Commit・Commit欄への明示反映を実装した。Git対象は開始プロジェクトから独立して、選択ワークスペースの登録済み1リポジトリを指定する。Pull・Push・fetch、任意コマンド・任意引数は提供しない。

Git用の起動前永続記録と共有ロックを追加。設定・属性・環境の許可リストを適用し、フック・署名を無効化する。ステージは一時インデックスに限定パスでaddし、選択パスだけをrenormalizeで再計算し、選択外不変と作業状態を再検査してから標準のindex.lock・rename・fsyncで反映する。確認画面は固定treeから生成し、Commitはcommit-treeと旧OID指定update-refを使う。実インデックスの自動resetは行わない。

自動試験：

- `python3 -m unittest discover -s tests -v`：47件成功（既存Host 30件、Git 17件）。
- `node tests/test_probe.mjs`：既存画面と、Git対象分離・ファイル選択・最終確認・メッセージ変更での失効・二重Commit送信抑止が成功。
- `node tests/test_draft.mjs`：受信バッファの既存試験が成功。
- `node tests/test_draft_client.mjs`：Commit欄への反映、欄ごとの独立Undo・クリア、実行中の操作抑止、反映によるGit/Host呼び出しゼロを確認。
- Python/JavaScript構文検査と`git diff --check`が成功。

実Gitの使い捨てリポジトリで検証した内容：

- 選択外と既存ステージ済みの保持、初回Commit、名前変更・削除、literal pathspec、日本語の作者・メッセージ、SHA-1/SHA-256、通常リポジトリとworktree。
- 確認treeと実Commitのtree・親・メッセージの一致。バイナリ・非対応属性・競合・merge中・detached HEADの拒否。
- 確認後の作業ファイル・インデックス・属性・HEAD変更による拒否と、旧OID照合による競合Commitの保護。
- 一時インデックスのstatキャッシュが同サイズ編集を見落とす場合を模擬し、選択パスの強制再計算で内容がステージされることを確認。
- 外部フック・フィルター・署名・include・diff・SSH・認証ヘルパー・未知設定の拒否または無効化。継承されたGIT_CONFIG_COUNT・GIT_INDEX_FILE・GIT_SSH_COMMANDによる注入の無効化。
- HostをCommit作成後・ref更新前でSIGKILLし、後続Hostが操作を拒否することを確認。残ったindex.lockは保持し、履歴の自動更新・再送をしない。
- ref更新後の結果検証失敗でも成功を返さず、永続記録を残すことを確認。
- Gitの時間切れ・出力超過・シグナル終了・プロセス群の終了確認例外を未確認として扱うこと、CodexとGitの共通排他を確認。

この時点ではHostインストーラーを`git_ops.py`もコピーするよう更新し、一時ホームでモジュールが配置されることを検査した。現在は`git_remote.py`も同様にコピーする。更新後のFirefox画面での第2段階の通常操作は後続の記録で確認済みである。実拡張間配送・Split View・Surfaceタッチ・実際のOS再起動による復旧は未確認。試験用リポジトリだけを変更し、実Focodexのインデックス・Commit履歴・リモートは変更していない。

## Git第2段階のFirefox正常系確認と表示再取得失敗の復旧（2026-10-23）

更新Hostを再登録し、Firefox拡張と操作タブを再読み込み後、Git状態取得、選択的Stage、Commit最終確認、Commit、Git状態再取得の通常操作を確認した。実機での表示再取得失敗を無理に発生させるために`.git`やネットワークを破壊する試験は行わない。

表示再取得失敗の分岐は、Host境界でStageまたはCommitの成功後に`git_snapshot`だけを失敗させる自動試験で確認した。操作結果は成功として返り、永続実行記録を残さず、後続の`git_snapshot`が実行できる。Git indexの短縮・終端欠落も検証エラーとして拒否する。

- `python3 -m unittest tests/test_git_ops.py`：20件成功。
- `python3 -m unittest tests/test_host.py`：31件成功。
- `python3 -m unittest discover -s tests -v`：52件成功（上記の回帰試験を含む）。
- `python3 -m py_compile native_host/focodex.py native_host/git_ops.py`、`node --check extension/probe.js`、`git diff --check`：成功。

これはPull・Pushの認証・通信経路を検証したものではない。次段階は[Pull・Push事前調査・検証事項](../specification/Focodex_Pull_Push_事前調査・検証事項.md)に従い、まずSurfaceの実行環境と認証経路を非破壊に確認する。

## Pull・Pushの通信なしSandbox事前試験（2026-10-23）

`git_remote_sandbox_probe`を追加し、SurfaceのFirefox/Cageから実行した。SELinuxは`Enforcing`のままで試験は成功した。Sandboxはネットワーク名前空間を共有せず、対象リポジトリだけを読取り専用で公開し、ホスト`HOME`を公開せず、SSH Agentソケットだけを固定したSandbox内パスにbindする。Sandbox内でAgentへの接続、対象リポジトリへの書込み拒否、`/home`の非公開を確認した。

この結果は、限定したファイル境界とAgent受渡しが当該環境で機能したことを示す。SSH鍵の宛先制限、SSH接続、DNS、Fetch、Push、リモート参照・作業ツリーの変更は試験していない。ネットワークをホストと共有する設定は追加しておらず、Pull・Pushは未実装・未許可である。次は、宛先を限定した通信経路を別途設計・検証する。

固定宛先・一接続のUnixソケット中継`FixedTargetProxy`を追加した。Sandbox側は中継先host/portを指定できず、Hostが固定した宛先にだけTCP接続する。中継ソケットはmode `0700`の一時ディレクトリ内にmode `0600`で作る。まだNative Messaging要求やGit操作からは呼び出さない。自動試験はソケット生成・固定宛先・一接続・不正入力拒否をモックで確認した。開発環境がAF_UNIXソケット作成を禁止するため、実ソケット通信の確認はSurfaceに留保した。

その後、Surfaceで「中継事前試験」が成功した。Sandboxはネットワーク非共有・Agent非公開のまま、Host固定のループバックエコー宛先とのUnixソケット通信を確認した。外部通信、SSH、Git、Fetch、Pull、Push、作業ツリー変更は行っていない。専用AgentのNative Host継承、Agent内鍵、対象ホストの既知鍵存在も確認済み。既知鍵の最小抽出と固定SSH設定を追加し、自動試験58件が成功した。実upstreamへのSSH接続は未実装・未実行である。

## 読み取り専用SSH接続試験の実装とSurface安全停止（2026-10-23）

固定宛先・一接続中継、最小known_hosts、明示設定された専用Agent、固定SSH設定を一操作へ統合し、`git ls-remote --exit-code`だけを実行する`git_remote_ssh_probe`を追加した。Fetch、Pull、Push、作業ツリー・参照の変更は行わない。Agentは共有の`SSH_AUTH_SOCK`を流用せず、`FOCODEX_REMOTE_AGENT`で指定された本人所有・group/other非公開のUnixソケットだけを受理する。Hostインストーラーはこの既定ソケットをNative Host起動時に設定する。

Surfaceでは専用AgentへGitHub用鍵をdestination constraint付きで登録し、ソケットの本人所有・`0600`を確認した。更新Hostと拡張の再読み込み後、画面に「読み取り専用SSH接続試験」ボタンが表示された。実行前のリモート事前診断は、scp形式のremote URLを許可しないため拒否した。

その後、Host再登録・拡張再読み込みの過程で前回Codexの専用プロセス群の終了を確認できない永続記録が残った。Focodexは意図どおりCodex・Git操作・設定変更を拒否し、画面にOS再起動後の再接続と実行記録を削除しない旨を表示した。

OS再起動後の再試験では、専用Agentのソケット確認、中継終了待機、Sandbox内のUID解決、Agent鍵の選択を順に修正した。`git-test` の`origin`を実在する明示的な`ssh://` URLへ設定後、Firefoxの「読み取り専用SSH接続試験」で「upstreamを確認しました」と表示された。固定宛先・専用Agent・最小known_hosts・中継を通る`git ls-remote --exit-code`だけが実行され、Fetch、Pull、Push、作業ツリー・参照の変更は行っていない。自動試験は64件成功した。
