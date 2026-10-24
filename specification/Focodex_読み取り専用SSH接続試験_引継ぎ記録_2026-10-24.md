# Focodex 読み取り専用SSH接続試験 引継ぎ記録

更新日：2026-10-24  
開発移管先：第3段階のFetch設計・実装
状態：**解決済み。読み取り専用SSH接続試験は成功。Fetch / Pull / Pushは未実装・未実行のまま。**

## 目的と不変条件

対象はSurface上の`git-test`で、明示的な`ssh://`形式のupstreamに対して、`git ls-remote --exit-code origin refs/heads/main`だけを実行する試験である。

- リモートURL、ホスト名、鍵パス、パスフレーズ、生のSSH標準エラーを拡張画面・永続診断ログへ出さない。
- Sandboxはネットワーク名前空間を共有しない。Host側の`FixedTargetProxy`が、検証済みの固定宛先へ一接続だけTCP接続する。
- 通常のSSH AgentとGNOME Keyringを停止・変更しない。
- Fetch、Pull、Push、参照・作業ツリーの変更を追加しない。
- 終了未確認状態、時間切れ、子プロセス終了確認不能は成功として扱わない。

## Surfaceで確認済みの事実

| 項目 | 結果 |
| --- | --- |
| `origin` | `ssh://git@github.com/tuyomori-cyber/git-test.git` |
| 現在ブランチ / upstream | `main` / `origin/main` |
| GNOME Keyring | GDM/PAMログイン後にLoginコレクションが自動解除される |
| 専用Agent | SurfaceのOS再起動後、foxbook起動により`$XDG_RUNTIME_DIR/focodex/remote-agent.sock`が再生成され、Keyring解除・設定・Secret Service項目・`ssh-add -l`を含む`remote_agent.py`の全診断が`ready`まで成功 |
| GitHub鍵認証 | 同専用Agentで`ssh -T git@github.com`が想定アカウントとして成功 |
| リポジトリ / ブランチ | 同Agent・隔離Git環境で直接実行した`git ls-remote --exit-code origin refs/heads/main`が成功 |
| Focodex事前診断 | branch=`main`、upstream=`origin/main`、専用Agent・bwrap/Git/SSHは利用可能 |
| 中継事前試験 | ネットワーク非共有SandboxからHost固定ループバック宛先へのUnixソケット中継が成功 |
| Focodex実upstream試験 | 画面の読み取り専用SSH接続試験が成功。安全な診断は`outcome=passed`、`returnCode=0`、`relayAccepted=true`、`targetConnected=true`、`wrapperStatus=exited`、`proxyStatus=connected` |

上記により、GitHub鍵、リポジトリアクセス、upstream、専用Agent、隔離Git環境、固定中継、Focodex経由の実接続がすべて成立することを確認した。

## 実upstream試験の成功

Focodex画面の「読み取り専用SSH接続試験」は成功した。最新の安全な診断記録は次のとおりである。

```json
{"event":"ssh-probe","outcome":"passed","proxyStatus":"connected","relayAccepted":true,"returnCode":0,"targetConnected":true,"wrapperStatus":"exited"}
```

- `wrapperStatus=exited`: 固定SSHラッパーがSSHを起動し、終了状態を確認した。
- `proxyStatus=connected`: ProxyCommandが固定Unixソケットへ接続した。
- `relayAccepted=true` / `targetConnected=true`: Host中継がSandboxからの接続を受理し、固定宛先へのTCP接続を開始した。
- `returnCode=0`: `git ls-remote --exit-code origin refs/heads/main`が成功した。

この試験は読み取り専用であり、Fetch、Pull、Push、参照・作業ツリーの変更は実行していない。以後の作業は、固定済み経路を再利用するFetch単独の安全設計・実装とする。

## 現在の実装と診断

主な実装は`native_host/git_remote.py`である。

- `readonly_ssh_probe()`が一時のknown_hosts、SSHラッパー、ProxyCommand、専用AgentソケットをSandboxへ限定公開する。
- Gitには`-c core.sshCommand=/run/focodex/ssh-wrapper`と`GIT_SSH_COMMAND=/run/focodex/ssh-wrapper`を固定して渡す。
- ラッパーは固定SSHオプションとProxyCommandを指定し、ラッパー自身とProxyCommandの段階を別々の固定値で記録する。
- SSHの標準エラーは一時専用ファイルでHostが分類する。本文を画面・永続ログへ保存しない。
- 永続診断ログは`~/.local/state/focodex/remote-ssh-diagnostics.jsonl`。mode `0600`、親ディレクトリは本人所有`0700`。保存するのは終了コード・固定分類・中継段階だけである。

Xubuntu作業ツリーでは、追加診断に関する6件の回帰試験とPython構文確認が成功している。Surfaceで作業を始める前に差分を確認し、既存の未コミット作業を破棄・上書きしない。

## Surface Codex CLIでの最初の作業

1. この文書、`TODO.md`、`docs/verification.md`、`specification/Focodex_専用SSH認証管理設計案.md`を読む。
2. `git status --short`と`git diff --check`で引継ぎ時点の差分を確認する。reset・checkout・ログ削除はしない。
3. `python3 -m unittest tests/test_git_ops.py -v`を実行して、Git回帰試験全体の基準を確認する。
4. 読み取り専用SSH接続が成功した固定経路を前提に、Fetch時に許可するGit設定・書込範囲・参照更新の原子性を設計する。追加診断はホスト・URL・鍵・パスフレーズ・生のSSH出力を永続化しないこと。
5. Fetch単独の安全設計・実装・実機検証が済むまで、Pull / Pushを実装・実行しない。

## 関連文書

- [専用SSH認証管理設計案](Focodex_専用SSH認証管理設計案.md)
- [Pull / Push事前調査・検証事項](Focodex_Pull_Push_事前調査・検証事項.md)
- [Pull / Push実装設計](Focodex_Pull_Push_実装設計.md)
- [検証記録](../docs/verification.md)
