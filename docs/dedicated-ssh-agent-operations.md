# Focodex 専用Agentの復元と秘密鍵登録：導入・運用・保守

更新日：2026-10-26  
根拠：リポジトリ内の実装・自動試験・Surface検証記録  
対象：FocodexがリモートGit操作に使う、専用OpenSSH Agent

## 1. この文書の範囲

Focodexは通常の`SSH_AUTH_SOCK`をリモートGit認証に流用しない。Native Hostの起動時に
`FOCODEX_REMOTE_AGENT`へ設定された、本人所有かつ group/other 非公開のUnixソケットだけを受理する。
既定のソケットは`$XDG_RUNTIME_DIR/focodex/remote-agent.sock`である。

専用Agentは固定したSSH秘密鍵を1本だけ保持する。秘密鍵のパスと、パスフレーズを検索する
Secret Service属性は固定設定から読み取る。秘密鍵の内容、パスフレーズ、公開鍵、Secret Service属性、
リモートURLは通常の診断・画面・永続診断ログに出さない。

この文書は専用Agentの準備と復元を扱う。秘密鍵の生成、パスフレーズ変更、Secret Serviceの初期化・削除、
GitHub等のサービス側への公開鍵登録、リモートURLやupstreamの設定は対象外である。

## 2. 構成と責務

```text
GDM/PAM によるログイン
  └─ GNOME Keyring の Login コレクションが解除済みになる
       └─ FocodexのAgent準備経路
            ├─ 専用 ssh-agent を remote-agent.sock で起動
            ├─ Secret Serviceから固定属性の値を取得
            └─ ssh-add で固定秘密鍵を1本だけ登録
                 └─ Focodex Native Host
                      └─ FOCODEX_REMOTE_AGENT のソケットだけを検査・使用
```

`native_host/remote_agent.py`がAgent準備を担当し、`native_host/git_remote.py`がNative Host側の
ソケット検査と非秘密の可用性判定を担当する。Native HostはAgentの起動・停止、鍵保管庫へのアクセス、
パスフレーズの取得を行わない。

インストーラー`python3 scripts/install_host.py`は、Native Hostのコピー先
`~/.local/lib/focodex/`へ`remote_agent.py`を配置し、次のuser unitもコピーする。ただし有効化はしない。

- `~/.config/systemd/user/focodex-remote-agent.service`：専用Agentを`serve`で起動する。
- `~/.config/systemd/user/focodex-remote-key.service`：Agent起動後に`register`を1回実行する。

両unitには、確認済みのグラフィカルセッションへ結び付ける前に有効化しない、というコメントがある。
リポジトリ内には、セッション寿命へ結び付けるunit有効化手順はない。

## 3. 導入前の条件

導入前に、次の条件を満たす必要がある。

- GDM/PAMログイン後、GNOME KeyringのLoginコレクションが解除されること。
- `/usr/bin/ssh-agent`、`/usr/bin/ssh-add`、`/usr/bin/busctl`、`/usr/bin/secret-tool`が利用可能であること。
- 専用に使うSSH秘密鍵が通常ファイルであり、実行ユーザー所有かつ group/other 非公開であること。
- `$XDG_RUNTIME_DIR`が絶対パスのディレクトリで、実行ユーザー所有かつ group/other 非公開であること。

### 固定設定

`~/.config/focodex/remote-agent.json`はインストーラーが生成しない。運用者が通常ファイルとして作成し、
実行ユーザー所有・`0600`にする。秘密値そのものは書かない。

```json
{
  "version": 1,
  "privateKey": "/absolute/path/to/private-key",
  "secretAttributes": {
    "service": "focodex-remote-ssh",
    "key": "github"
  }
}
```

`privateKey`は絶対パス、`secretAttributes`は空でない文字列キーと文字列値だけを受理する。
設定ファイルまたは鍵ファイルがシンボリックリンク、通常ファイル以外、別所有者、または group/other
読み書き可能であれば、準備は失敗する。

Secret Serviceには、この設定の属性と一致する項目をあらかじめ明示的に登録する。その値は指定した
SSH秘密鍵のパスフレーズである。`secret-tool store`の入力時点では鍵との正誤は確認されず、後述の
`register`時に`ssh-add`が照合する。

## 4. 起動・復元の仕組み

`serve`は`$XDG_RUNTIME_DIR/focodex`を`0700`で作成し、そこに専用`ssh-agent`を
`remote-agent.sock`として起動する。既に同名のエントリがあれば無条件に削除せず失敗する。

`register`は次の順に確認し、すべて通った場合だけ鍵を登録する。

1. 固定設定と秘密鍵の所有者・権限・形式を検証する。
2. GNOME KeyringのLoginコレクションが解除済みであることを確認する。
3. 固定属性に一致するSecret Service項目があることを確認する。
4. 専用Agentが`empty`、すなわち有効な鍵をまだ持たない状態であることを確認する。
5. `SSH_ASKPASS`を`remote_agent.py`自身、`SSH_ASKPASS_REQUIRE=force`を指定して`ssh-add`を実行する。
6. `ssh-add -l`の終了状態で、Agentが`ready`になったことを確認する。

Askpass子プロセスへ渡す環境は固定の`PATH`、専用Agentソケット、検証済みの`XDG_RUNTIME_DIR`、
そこから導くD-Busアドレスだけである。任意の親環境、任意の鍵・属性・ソケット、端末対話への
フォールバックは使わない。

実装上、`register`は空でないAgentへ鍵を追加しない。失敗しても既存ソケット、通常のSSH Agent、
GNOME Keyringを停止・削除しない。

## 5. 導入手順

### 5.1 Focodex側のファイルを登録する

Focodexリポジトリのルートで実行する。

```sh
python3 scripts/install_host.py
```

この操作は既存の`~/.config/focodex/config.json`を上書きしない。一方、コピー済みNative Host、
`remote_agent.py`、および2つのunitはリポジトリの内容で更新される。

### 5.2 固定設定とSecret Service項目を用意する

前節の形式で`remote-agent.json`を作成し、権限を`0600`にする。設定に対応するSecret Service項目を
明示的に登録する。ここで入力する値はOSログイン用パスワードではなく、指定したSSH秘密鍵の
パスフレーズである。

このリポジトリは、秘密値を表示・再出力する操作を提供しない。パスフレーズが誤っていた場合は、
秘密値を表示せず、鍵と保存値の組合せを確認するエラーとして停止する。

### 5.3 セッション連携を確認してから自動化する

インストーラーだけではunitは有効化されない。Surfaceでは、GDM/PAMによるKeyring自動解除後、
foxbook起動時に専用Agentを再生成して鍵を登録する経路が使われ、OS再起動後も`ready`まで復元することを
確認済みである。

ただし、このリポジトリには`firefox-kiosk-app`や、その起動経路のソースは含まれない。したがって、
別環境へ導入する際は、対象環境のグラフィカルセッションの寿命を確認してから、その外部起動経路または
同等の管理方法を個別に検証する。unitを常時有効化する、`linger`を有効化する、といった方法は
この実装の導入手順として定義されていない。

## 6. 日常運用と確認

コピー済みの実行ファイルに対して、次の非秘密診断を実行できる。

```sh
python3 ~/.local/lib/focodex/remote_agent.py keyring-status
python3 ~/.local/lib/focodex/remote_agent.py config-status
python3 ~/.local/lib/focodex/remote_agent.py secret-status
python3 ~/.local/lib/focodex/remote_agent.py status
```

期待する状態は順に`unlocked`、`configured`、`present`、`ready`である。状態の意味は次のとおり。

| コマンド | 正常状態 | それ以外の場合 |
| --- | --- | --- |
| `keyring-status` | `unlocked` | Keyringがロック中、または状態確認不能 |
| `config-status` | `configured` | 設定・鍵の形式、所有者、権限が不正 |
| `secret-status` | `present` | Keyringが未解除、項目なし、または照会不能 |
| `status` | `ready` | `missing`、`invalid`、`unavailable`、または`empty` |

`empty`は有効な専用Agentが存在するが鍵を保持していない状態である。通常の復元経路では、空の状態でのみ
`register`を実行する。`ready`であれば再登録しない。

Focodex画面のリモートGit事前診断は、専用Agentについて`notConfigured`、`unavailable`、`noKeys`、
`ready`だけを返す。`ready`以外ではリモートGit操作を開始できない。

## 7. 障害時の扱い

- **Keyringが`locked`または確認不能**：Agent登録を行わない。ログイン連動の解除状態を確認する。
- **`config-status`が失敗**：`remote-agent.json`と秘密鍵の所有者、通常ファイル性、シンボリックリンクでないこと、group/other非公開を確認する。鍵パスや属性を画面・ログへ貼り付けない。
- **`secret-status`が`missing`**：固定属性と一致するSecret Service項目を確認する。値を読み出して表示する手順は採用しない。
- **`status`が`missing`**：専用Agentが存在しない。復元を担当するセッション連携の起動状況を確認する。
- **`status`が`invalid`または`unavailable`**：ソケットを手動削除・置換しない。所有者・権限・実体を調査し、復元経路を安全に停止してから原因を解消する。
- **登録でパスフレーズ不一致**：Secret Serviceに保存した値と指定鍵の組合せを確認する。エラー出力にはパスフレーズを含めない。

HostまたはGit操作が終了未確認状態になった場合は、実行記録を手動削除して迂回しない。Focodexの実行記録は、
専用プロセス群の消滅を確認した場合だけ削除され、正常な記録はOS再起動後のboot ID変化で解除できる。
破損記録は自動解除されず、個別調査が必要である。

## 8. 更新・保守

FocodexのHost関連ソースを更新したら、リポジトリの変更だけでは済まない。コピー先を更新するため、
再度`python3 scripts/install_host.py`を実行する。更新後はFocodex拡張と操作タブも手動で再読み込みする。

保守時は、少なくとも次を確認する。

1. `python3 -m unittest tests/test_remote_agent.py -v`でAgent準備の回帰試験を実行する。
2. `keyring-status`、`config-status`、`secret-status`、`status`が期待状態になることを確認する。
3. Focodexのリモート事前診断が専用Agentを利用可能と判断することを確認する。
4. OSログアウト・再ログイン・再起動後に、対象のグラフィカルセッション経路で`ready`へ復元することを確認する。

専用Agentの分離は、FocodexのSandbox、Firefox拡張、Codex、通常のSSH Agentからパスフレーズと認証鍵を
分離するためのものである。同一UIDで動く悪意あるプロセスからの完全隔離を提供するものではない。

## 9. 確認済みの範囲と未確認事項

Surfaceでは、GDM/PAMによるGNOME Keyring Loginコレクションの自動解除、foxbook起動後の専用Agent再生成、
固定鍵の自動登録、OS再起動後の`ready`復元を確認済みである。同じ専用Agentを使ったGitHub鍵認証、
隔離Git環境での`git ls-remote --exit-code origin refs/heads/main`、Focodex画面からの読み取り専用SSH接続試験も
成功している。

一方、SSH接続併存、Plasma切替を含む長期的な再現性、リモートGit操作の通信失敗・中断・終了未確認などの
異常系は、網羅的には実機確認されていない。別OSや別のFirefox導入形態へ、Surfaceでの結果をそのまま
適用してはならない。

## 10. 根拠ファイル

- 実装：[`native_host/remote_agent.py`](../native_host/remote_agent.py)、[`native_host/git_remote.py`](../native_host/git_remote.py)
- 配置とunit：[`scripts/install_host.py`](../scripts/install_host.py)、[`systemd/focodex-remote-agent.service`](../systemd/focodex-remote-agent.service)、[`systemd/focodex-remote-key.service`](../systemd/focodex-remote-key.service)
- 自動試験：[`tests/test_remote_agent.py`](../tests/test_remote_agent.py)
- Surface検証記録：[`docs/verification.md`](verification.md)、[`specification/Focodex_読み取り専用SSH接続試験_引継ぎ記録_2026-10-24.md`](../specification/Focodex_読み取り専用SSH接続試験_引継ぎ記録_2026-10-24.md)
- 設計上の制約：[`specification/Focodex_専用SSH認証管理設計案.md`](../specification/Focodex_専用SSH認証管理設計案.md)
