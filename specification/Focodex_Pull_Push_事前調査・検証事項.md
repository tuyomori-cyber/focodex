# Focodex Pull・Push 事前調査・検証事項

作成日: 2026-10-23  
状態: 調査中（Surfaceで読み取り専用の実upstream接続まで確認、Fetch/Pull/Pushは未実装・未実行）
関連文書: `Focodex_Pull_Push_実装設計.md`

## 1. 調査目的

Pull・PushをGit CLI＋専用Sandboxで安全に単純実装できるか、Surface Pro 4（Fedora/Cage/Firefox Native Messaging Host）の実環境で確認する。**SSH経由の対話シェルでGit操作が成功することは、FirefoxのNative HostやSandbox内で成功することを意味しない**ため、実行環境ごとに検証する。

## 2. 最優先：認証・通信の成立性

### A. Gitリモートの現状把握

- [ ] 対象リポジトリの`git remote -v`、`git branch -vv`、`git config --get-regexp '^(branch\.|remote\.|push\.)'`を確認する（機密情報を含むURLは記録時にマスク）。
- [x] SSH/HTTPSのどちらを使用しているかを確定する。対象は明示的な`ssh://`形式だけを許可する。
- [x] ローカルブランチのupstreamが設定され、Push先を一意に決定できるか確認する。読み取り専用試験では`main` / `origin/main`を確認した。
- [ ] SSH接続でSurface上のGit CLIから`git fetch`および通常Pushが成功することを確認する。

### B. Firefox Native Hostとの環境差

- [ ] SSHログインシェルとNative Hostの`HOME`、`PATH`、`SSH_AUTH_SOCK`、`GIT_*`等の差を比較する（秘密値をログに残さない）。
- [ ] Native Hostから同じGit CLIとSSH認証経路を利用できるか確認する。
- [ ] パスフレーズ付き鍵を使う場合、非対話実行で認証が成立するか確認する。
- [ ] `known_hosts`の確認済み状態がNative Hostでも使えるか確認する。
- [ ] 認証プロンプトが必要な場合は自動処理せず、明確な失敗として扱えるか確認する。

### C. Sandbox内の認証

- [ ] 秘密鍵を直接マウントせずに接続できる方法を比較する（例: 制限付きSSH Agent利用）。
- [ ] Agentソケット共有時、他の鍵や宛先への認証能力が露出しないか評価する。
- [ ] SSH実行ファイル・設定・known_hosts・Agentソケットの必要最小限の公開範囲を確認する。
- [ ] Sandboxからの外部通信の範囲をどう制御するか調査する。単純なbubblewrap実行だけで接続先制限が実現するとはみなさない。
- [ ] 認証情報・ソケットがCodex実行環境に流入しないことを確認する。

**判定条件:** Native Hostから起動したSandbox内で、対話操作なしに許可済みリモートへ安全にFetch/Pushできる方式が見つかること。成立しなければPull・Push実装を保留し、CLI運用を続ける。

2026-10-24に、この判定に先行する読み取り専用ゲートとして、Focodex画面から固定された実upstreamへの`git ls-remote --exit-code origin refs/heads/main`が成功した。安全な診断では`outcome=passed`、`returnCode=0`、`relayAccepted=true`、`targetConnected=true`、`wrapperStatus=exited`、`proxyStatus=connected`を確認した。この結果はFetch/Pushの成功を意味せず、以後は第4節のFetch試験を最初の変更操作として設計・実装する。

## 3. Sandbox・Git設定の安全性

- [ ] 利用可能な隔離手段（bubblewrap等）、Fedora側の制約、必要なユーザー名前空間機能を確認する。
- [ ] Git実行ファイル・共有ライブラリ・設定ファイルの読み取り範囲を列挙する。
- [ ] 対象リポジトリ以外のホーム・他プロジェクト・一時ディレクトリへのアクセスを遮断できるか確認する。
- [ ] Pullに必要な`.git`と作業ツリーの書込権限を最小化できるか確認する。
- [ ] hooks、Git aliases、filters、外部diff、credential helpers、`core.sshCommand`、`GIT_SSH_COMMAND`、`includeIf`等の実行・設定経路を洗い出す。
- [ ] 設定の無効化・固定とOS隔離を組み合わせ、意図しない補助プログラムが動いても権限を逸脱しないか検証する。
- [ ] シンボリックリンク、Git worktree、submodule、特殊なGit管理ディレクトリ等の扱いを決め、未対応構成は拒否する。
- [ ] Sandbox外でGitを起動してしまう例外経路がないか確認する。

**判定条件:** リポジトリ内の悪意ある設定・属性・フック等によって、Sandbox外のファイルや認証情報にアクセスできないことを確認できること。

## 4. Pullの仕様確認・試験

- [ ] detached HEAD、upstream未設定、複数候補、ブランチ切替直後を拒否または正しく判定できるか。
- [ ] 作業ツリー/インデックスが汚れている場合に開始しないか。
- [ ] merge/rebase/cherry-pick等の進行中に開始しないか。
- [ ] リモートが先行しFast-forward可能な場合だけ更新できるか。
- [ ] ローカルが先行している場合、不要な履歴操作をしないか。
- [ ] 履歴が分岐している場合、自動merge/rebaseせず拒否するか。
- [ ] Fetch直後にブランチや参照が外部変更された場合に安全に中止できるか。
- [ ] タイムアウト・ネットワーク断・ディスク容量不足・途中停止後の状態を判別できるか。
- [ ] Fetchによるリモート追跡参照の変更と、作業ツリー更新の違いをUIに正しく反映できるか。

## 5. Pushの仕様確認・試験

- [ ] Push先をupstreamのリモート・ブランチへ明示的に固定できるか（`push.default`等の影響を受けないか）。
- [ ] 通常Push、更新不要、非Fast-forward拒否を区別できるか。
- [ ] Force Push、削除refspec、タグ一括送信、別ブランチ指定が不可能か。
- [ ] Push中にリモートが更新された場合、Gitの拒否を正しく伝えられるか。
- [ ] 認証失敗、通信失敗、タイムアウト時に自動設定変更・再認証をしないか。
- [ ] 送信成功後にUI更新だけ失敗した場合、Push自体を「失敗」と誤表示しないか。

## 6. 既存Focodexとの統合

- [ ] Codex実行中、Stage/Unstage/Commit中、Pull/Push中の相互排他を確認する。
- [ ] プロセスグループ終了確認にSSH等の子孫プロセスが含まれるか。
- [ ] Host強制終了後に状態不明を安全に保持・回復できるか。
- [ ] 既存のGitローカル操作の検証条件を緩めずにリモート操作を追加できるか。
- [ ] SSHから`git switch`した後、Focodexが新しいブランチ/upstreamを操作時に再取得するか。
- [ ] エラー表示からSSH/CLI復旧に移れるだけの情報が得られるか（秘密情報は表示しない）。
- [ ] Surface実機のFirefox/CageからのNative Messaging動作を確認する。

## 7. 推奨する小規模検証順序

1. **読み取りだけ:** SurfaceのSSHシェルでGit設定とupstreamを確認する。
2. **Native Host単独:** Gitの状態取得と認証環境を比較する。まだリモートを書き換えない。
3. **Sandbox試作:** ネットワークなしでGit状態取得、不要なファイルアクセス拒否を確認する。
4. **Fetch試験:** 検証用リポジトリ・リモートで、Sandbox内の認証・通信・参照更新を確認する。
5. **Pull試験:** 検証用リポジトリでFast-forward、分岐、作業ツリー汚染、中断を試す。
6. **Push試験:** 検証用リモートで通常Push、非Fast-forward拒否、認証失敗を試す。
7. **統合試験:** 排他・異常終了・UI表示を確認してから日常用リポジトリへ適用する。

## 8. 調査完了の判断基準

- [ ] 認証情報をFocodexに保存・入力させずに、Sandbox内のGitが必要な認証を安全に使える。
- [ ] Git設定やリポジトリ内容による任意コマンド実行がSandboxの権限境界を越えない。
- [ ] Pullはクリーンな作業ツリーへのFast-forward以外を実行しない。
- [ ] Pushは現在のupstreamへの非強制送信に限られる。
- [ ] 失敗・中断・競合時に自動修復せず、状態を明示して停止できる。
- [ ] 既存Stage・Unstage・Commitの安全性と排他が維持される。

## 9. 未解決なら設計を見直す事項

1. Sandbox内でSSH認証を安全に利用できない。
2. Gitの補助プログラム実行やネットワーク通信を十分に制限できない。
3. Gitによるファイル更新を対象リポジトリに限定できない。
4. 操作中断時に不確定状態を安全に扱えない。

これらが解消しない場合は、Sandbox制限を緩めて強行せず、Pull・PushをSSH経由のGit CLI運用に留める。

## 10. 実施記録

### 2026-10-23：開発機での非破壊な初期確認

これはSurface Pro 4の結果ではない。リモートへの通信、Fetch、Push、設定変更、認証試行は行っていない。

| 確認項目 | 結果 | 判断 |
| --- | --- | --- |
| 現在のブランチとupstream | `main`、`origin/main` | Push先候補は一意。ただし実行直前の再検証が必要。 |
| リモートの方式 | SSH（URL自体は記録しない） | Agent/known_hosts/非対話認証の調査対象。 |
| SSH Agent | 有効なソケットが存在 | 鍵・宛先を制限できるかは未確認。Agent共有を採用したことを意味しない。 |
| 基盤 | Linux、`/usr/bin/bwrap`、`/usr/bin/git`、`/usr/bin/ssh` が存在 | SurfaceのFedora/Cage/Firefoxと同一視しない。 |
| 現行ローカルGit経路 | `git_ops.environment()` は最小環境を構築し、`SSH_AUTH_SOCK`を継承しない | Stage/Commit用の経路をリモート操作へ転用しない。専用Sandboxと明示的な認証受渡しが必要。 |

`tests/test_git_ops.py`で、ローカルGit経路が`SSH_AUTH_SOCK`および呼出元の`GIT_SSH_COMMAND`を継承しないことを回帰試験化した。

次はSurfaceで、SSHログインシェルとFirefox Native Hostの環境を**値を表示せずに**比較する。確認対象は、SSH Agentソケットの有無・ソケット種別、`HOME`/`PATH`の実効値、`bwrap`/Git/SSHの存在、SELinux状態、対象リポジトリのcurrent branch/upstreamである。この段階でもFetch/Pushは実行しない。

SurfaceのSSHログインシェルでは、対象リポジトリ内で次だけを実行して記録する。リモートURL、鍵名、Agentソケットのパス、`~/.ssh`の内容は表示・転記しない。

```bash
printf 'branch='; git branch --show-current
printf 'upstream='; git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
printf 'agent_socket='; if [ -n "${SSH_AUTH_SOCK:-}" ] && [ -S "$SSH_AUTH_SOCK" ]; then echo available; else echo unavailable; fi
printf 'bwrap='; command -v bwrap >/dev/null && echo available || echo unavailable
printf 'git='; command -v git >/dev/null && echo available || echo unavailable
printf 'ssh='; command -v ssh >/dev/null && echo available || echo unavailable
printf 'selinux='; command -v getenforce >/dev/null && getenforce || echo unavailable
```

Firefox Native Host側では、同等の情報を安全に返す読み取り専用診断がまだない。このため、Firefox実環境との差分を推測で埋めず、次の調査作業として「秘密値・パス・URLを返さず、Agentの有無と実行ファイルの可用性だけを返すHost診断」の必要性を評価する。診断を追加する場合もネットワーク、SSH、Gitの書込みは実行しない。

### 2026-10-23：SurfaceのSSHログインシェル（非破壊確認）

| 確認項目 | 結果 | 判断 |
| --- | --- | --- |
| 現在のブランチとupstream | `main`、`origin/main` | Push先候補は一意。実行直前の再検証は必要。 |
| SSH Agent | `SSH_AUTH_SOCK`なし | Agent共有方式は現時点で利用できない。鍵をSandboxへ直接渡す方針へは進まない。 |
| ユーザーsystemd環境のSSH Agent | `SSH_AUTH_SOCK`なし | Firefoxを含むグラフィカルセッションへ受け渡せるAgentも未確認。Agent連携方式の調査が必要。 |
| Agent関連unit | `ssh-agent.socket`、`gnome-keyring-daemon.socket` ともに disabled/inactive | 利用可能な実装はあるが、グラフィカルセッションでAgentは起動していない。unit定義を確認後に、`ssh-agent`だけを最小構成で試験する。 |
| 基盤 | `bwrap`、Git、SSHが利用可能 | Sandbox試作の前提はある。実行可否は別途検証する。 |
| SELinux | Enforcing | Firefox Native HostとSandboxの組合せで、拒否ログを含む実機検証が必要。 |
| 非対話SSH認証 | `GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' git ls-remote --exit-code origin HEAD` が成功 | SSHログインシェルでは、既存の鍵とknown_hostsによりプロンプトなしで`origin`を読み取り可能。ただしこの経路は鍵を直接読む可能性があり、Sandboxの認証方式としては未承認。 |

この確認ではFetch、Push、参照更新、作業ツリー変更は行っていない。次は、鍵を直接渡さずに済むSSH AgentをSurfaceのFirefox Native Hostまで安全に到達させられるかを調査する。Agentが利用できないままなら、Sandbox内のPull/Pushは実装せず保留する。

`ssh-agent.socket`のunit定義を確認した。ソケットは`%t/ssh-agent.socket`（ユーザーruntimeディレクトリ）に作られ、modeは`0600`である。`ssh-agent.service`自身は`SSH_AUTH_SOCK`を設定せず、グラフィカルセッション環境側で同変数を設定することを要求している。よって、試験では恒久的なenableはせず、socketの一時起動、ユーザーsystemd環境への`SSH_AUTH_SOCK`設定、鍵の明示的なAgent登録、Firefox/Cage再起動を順に行う。

### 2026-10-23：Native Host向け読み取り専用診断を追加

`git_remote_preflight`を追加した。この操作はネットワーク通信、Gitの書込み、SSH接続を行わず、現在のローカルブランチ、upstream（リモートURLは返さない）、Native Hostプロセスから見たSSH Agentソケットの有無、`bwrap`/Git/SSHの可用性だけを返す。診断後は、既存のGit状態表示トークンを無効化し、明示的な状態再取得を要求する。

Host・Git自動試験53件（Git 22件、Host 31件）と画面ロジック試験が成功した。次はSurfaceへ更新Hostを登録し、Firefox/Cageを再起動して「リモート事前診断」で`SSH Agent=利用可能`となるか確認する。この表示はAgentソケットの継承だけを示し、Sandboxへの受渡しやリモート操作の許可を意味しない。

### 2026-10-23：SurfaceのFirefox Native Host事前診断

更新Hostの登録後、Firefox/Cageで`git-test`を対象に「リモート事前診断」を実行した。画面では`ブランチ=main`、`upstream=origin/main`、`SSH Agent=利用可能`、`bwrap/Git/SSH=利用可能`と表示された。診断実装はネットワーク通信・Git変更を行わない。

これにより、Firefox Native HostプロセスがAgentソケットを継承していることを確認できた。ただし、Sandbox内へ同じAgentを最小権限で受け渡せること、SELinux Enforcing下でbubblewrapが動作すること、リモートGit操作を許可することは、まだ確認していない。次段階では通信なしのSandbox試作で、この3点のうちファイル境界とAgentソケット接続だけを検証する。

### 2026-10-23：Sandbox事前試験を実装・Surfaceで確認

`git_remote.py`に`git_remote_sandbox_probe`を実装し、画面へ「Sandbox事前試験」を追加した。試験は`--unshare-all`によりネットワークを分離し、ホストの`HOME`、`/etc`、`/run`、対象外のプロジェクトをマウントしない。可視化するホスト側オブジェクトは、読み取り専用の対象リポジトリと、Sandbox内の固定パスへbindするSSH Agentソケットだけである。

Sandbox内では、対象リポジトリの`.git/HEAD`が読めること、`/home`が存在しないこと、対象リポジトリへの書込みが失敗すること、`ssh-add -l`がAgentへ接続できることを確認する。ネットワーク、Fetch、Push、SSH接続、Git変更は行わない。10秒でタイムアウトし、終了確認不能時は既存の未確認状態処理へ接続する。

自動試験54件（Git 23件、Host 31件）、画面ロジック試験、Python/JavaScript構文検査、差分チェックが成功した。

SurfaceのFirefox/Cageから、SELinux `Enforcing` のままこの試験を実行し、画面で成功結果を確認した。したがって、現行のNative Hostプロセスからは、対象リポジトリを読取り専用で渡し、ホスト`HOME`を非公開に保ち、固定パスへbindしたSSH Agentソケットへ接続できる。一方で、この確認はAgentの鍵・宛先制限、SSH接続、DNS、Fetch、Push、リモート参照・作業ツリーの更新を確認したものではない。Sandboxは引き続きネットワーク非共有であり、リモート操作を許可していない。

次は、このファイル境界とAgent受渡しを維持したまま、通信を承認済みの宛先に限定できる経路を設計・検証する。`--share-net`を追加してホストネットワーク全体を共有する案は、この段階では採用しない。

次の非破壊調査では、対象リモートのホスト鍵を専用known_hostsへ限定して渡せるか、専用一時Agentに対象鍵だけを投入しdestination constraintを付けられるか、固定宛先の一接続Unixソケット中継をSandbox内SSHが使えるかを順に確認する。これらが揃うまで、Fetch試験へ進まない。

固定宛先の一接続Unixソケット中継`FixedTargetProxy`を追加した。中継先host/portはHost側だけが生成・検証し、Sandbox側クライアントは指定できない。中継ソケットはmode `0700`の一時ディレクトリ内にmode `0600`で作り、1接続だけを受け入れる。現段階ではNative Messaging要求・Git操作から呼び出さない。

自動試験では、ソケット生成、固定先への接続、1回だけの受入、入力不正の拒否をモックで確認した。この開発環境はAF_INETだけでなくAF_UNIXソケットの作成も拒否するため、実ストリームの接続試験はSurfaceで行う必要がある。試験目的はSandboxから中継ソケットへ接続できることと、Hostが指定済みの一宛先以外へ接続しないことの確認であり、Git・SSH・Fetch・Pushは実行しない。

### 2026-10-23：中継Sandbox・認証前提の現状

SurfaceのFirefox/Cageで「中継事前試験」が成功した。これはSandboxがネットワークを共有せず、SSH Agentも公開せずに、Hostが固定した`127.0.0.1`の一時エコー宛先とUnixソケット経由で往復できたことを示す。外部通信、SSH、Git、Fetch、Pull、Push、作業ツリー変更は行っていない。

専用一時Agentについては、Firefox Native Hostの「リモート事前診断」で利用可能を確認した。端末側でも専用ソケットとAgent内鍵が利用可能であることを確認した。対象ホストの既知鍵は`ssh-keygen -F`で存在を確認したが、ホスト名・鍵・URLは記録していない。

Host側には対象ホストの一致行だけを抽出する`known_host_entries()`、および専用known_hosts・厳格なホスト鍵照合・非対話認証・鍵ファイル探索禁止を指定する固定SSH設定を追加した。さらにupstreamのリモートURLは、明示的な`ssh://user@host[:port]/path`形式だけを受理する解析器を追加した。scp形式、暗黙のユーザー名、IPリテラル、query/fragment、複数URL、`pushurl`は拒否する。解析結果のURL・host・user・pathは拡張へ返さない。固定構成を統合した`ls-remote`限定の読み取り専用SSH接続試験を実装し、Host・画面回帰試験は成功した。この時点では、終了未確認の安全停止中のため実upstreamへの試験を保留していた。

### 2026-10-24：GNOME Keyring自動解除と認証経路の現状

GDM/PAMによりGNOME KeyringのLoginコレクションが自動解除されることを、Surface再起動後に`Locked=false`で実機確認した。OSログインパスワード変更後も鍵保管庫が旧パスワードを保持していたことが原因であり、鍵保管庫のパスワードを現行OSパスワードへ変更して解消した。詳細は[GNOME Keyring調査記録](foxbook_GNOME_Keyring_調査記録_2026-10-24.md)を参照する。

この成功は鍵保管庫までであり、SSH鍵のパスフレーズ取得、専用Agentの起動、鍵の登録は未実装である。Hostの既定ソケットは`$XDG_RUNTIME_DIR/focodex/remote-agent.sock`であり、認証管理コンポーネントもこのパスへ専用Agentを作成しなければならない。OS再起動後は、当該コンポーネントの再起動試験と終了未確認記録の解除、`origin`の明示SSH URL化を確認した後にのみ実upstreamへの読み取り専用試験を再開する。

### 2026-10-24：読み取り専用実upstream接続の成功

終了未確認状態を解消し、明示SSH URL・専用Agent・最小known_hosts・固定Unixソケット中継を用いて、Focodex画面から実upstreamへの`git ls-remote --exit-code origin refs/heads/main`が成功した。安全な診断は`outcome=passed`、`returnCode=0`、`relayAccepted=true`、`targetConnected=true`、`wrapperStatus=exited`、`proxyStatus=connected`である。Fetch、Pull、Push、参照・作業ツリーの変更は行っておらず、次は第4節のFetch単独試験を設計・実装する。

※本書はPull・Push実装前のチェックリストであり、未チェック項目は検証済みの結果ではない。
