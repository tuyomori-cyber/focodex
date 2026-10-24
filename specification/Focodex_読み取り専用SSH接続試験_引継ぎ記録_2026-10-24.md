# Focodex 読み取り専用SSH接続試験 引継ぎ記録

更新日：2026-10-24  
開発移管先：Surface実機のCodex CLI  
状態：**未解決。Fetch / Pull / Pushは禁止を継続する。**

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

上記により、GitHub鍵、リポジトリアクセス、upstream、専用Agent、隔離Git環境、基本中継は原因ではないことを確認した。

## 未解決の実機結果

Focodex画面の「読み取り専用SSH接続試験」は失敗し、画面には安全な一般エラーとして「GitHub上のリポジトリアクセスを確認してください」と出る。これは実際のGitHub権限失敗を確定する文言ではない。

最新の安全な診断記録は次のとおりである。

```json
{"classification":"repository-access","event":"ssh-probe","outcome":"ssh-error","proxyStatus":"wrapper-command","relayAccepted":false,"returnCode":128,"targetConnected":false}
```

意味は以下である。

- `wrapper-command`: Gitが固定SSHラッパーを実接続として起動した。
- `relayAccepted=false` / `targetConnected=false`: `FixedTargetProxy`はSandboxから接続を受けておらず、固定宛先TCP接続も開始していない。
- `classification=repository-access`: Gitの一般的な終了文から得た分類であり、直接試験の成功と矛盾するため根本原因とは扱わない。

## 現在の実装と診断

主な実装は`native_host/git_remote.py`である。

- `readonly_ssh_probe()`が一時のknown_hosts、SSHラッパー、ProxyCommand、専用AgentソケットをSandboxへ限定公開する。
- Gitには`-c core.sshCommand=/run/focodex/ssh-wrapper`と`GIT_SSH_COMMAND=/run/focodex/ssh-wrapper`を固定して渡す。
- ラッパーは固定SSHオプションとProxyCommandを指定する。
- ProxyCommandの段階は`proxy-status`へ固定値だけを書き、Hostが`proxyStatus`として診断ログへ記録する。
- SSHの標準エラーは一時専用ファイルでHostが分類する。本文を画面・永続ログへ保存しない。
- 永続診断ログは`~/.local/state/focodex/remote-ssh-diagnostics.jsonl`。mode `0600`、親ディレクトリは本人所有`0700`。保存するのは終了コード・固定分類・中継段階だけである。

Xubuntu作業ツリーでは`python3 -m unittest tests/test_git_ops.py -v`が37件成功している。Surfaceで作業を始める前に差分を確認し、既存の未コミット作業を破棄・上書きしない。

## Surface Codex CLIでの最初の作業

1. この文書、`TODO.md`、`docs/verification.md`、`specification/Focodex_専用SSH認証管理設計案.md`を読む。
2. `git status --short`と`git diff --check`で引継ぎ時点の差分を確認する。reset・checkout・ログ削除はしない。
3. `python3 -m unittest tests/test_git_ops.py -v`を実行して、現在の37件の基準を確認する。
4. 実機再試験を繰り返す前に、`wrapper-command`なのにProxyCommandが開始されない経路を、Host内で安全に再現・追跡する。追加診断はホスト・URL・鍵・パスフレーズ・生のSSH出力を永続化しないこと。
5. 読み取り専用SSH試験が画面で成功するまで、Fetch / Pull / Pushを実装・実行しない。

## 関連文書

- [専用SSH認証管理設計案](Focodex_専用SSH認証管理設計案.md)
- [Pull / Push事前調査・検証事項](Focodex_Pull_Push_事前調査・検証事項.md)
- [Pull / Push実装設計](Focodex_Pull_Push_実装設計.md)
- [検証記録](../docs/verification.md)
