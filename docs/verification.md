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
