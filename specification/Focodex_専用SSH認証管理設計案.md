# Focodex専用SSH認証管理 設計案

作成日：2026-10-24  
状態：Surfaceでの鍵自動登録・ログイン後復元を確認済み

## 1. 目的と前提

Focodexの読み取り専用SSH接続試験、および将来の限定的なPull/Pushで使うSSH鍵を、通常のSSH Agentと分離して扱う。対象環境はSurfaceのFedora/GDM/foxbook（Cage）である。

- OSログインはGDMのパスワード入力を継続し、自動ログインは使わない。
- SSH秘密鍵はパスフレーズ保護を維持する。
- Firefox、拡張、Native Messaging、Codex、Gitの出力・ログへパスフレーズを渡さない。
- GDM/PAMによるGNOME Keyring Loginコレクションの自動解除と、鍵保管庫から専用Agentへの鍵登録を確認済みである。
- Native Hostが受理する専用Agentソケットは、`$XDG_RUNTIME_DIR/focodex/remote-agent.sock`に固定する。

## 2. 境界と責務

```text
GDM/PAM
  └─ GNOME Keyring Loginコレクションを解除
       └─ focodex-remote-agent（OS側・ユーザーサービス）
            ├─ Secret Serviceから固定属性の秘密値を取得
            ├─ 専用ssh-agentを固定ソケットで起動
            └─ 固定した秘密鍵だけをAgentへ登録
                 └─ Focodex Native Host
                      └─ 読み取り専用Sandbox内の固定Agentソケット
```

### OS側の認証管理コンポーネント

専用の実行ファイルとユーザーサービスで構成する。Secret Service、`ssh-agent`、`ssh-add`を扱うのはここだけとする。

- SSH秘密鍵の絶対パス、Secret Service検索属性、Agentソケットを固定設定から読み、拡張やHostから引数として受け取らない。
- 起動前にSecret ServiceとLoginコレクションの解除状態を確認する。ロック中・接続不能なら鍵登録を行わず失敗する。
- `$XDG_RUNTIME_DIR/focodex`を本人所有・`0700`で作成し、Agentソケットを`remote-agent.sock`として`0600`で作る。
- Agentへ登録する鍵は1本だけとし、鍵ファイルは通常ファイル、本人所有、group/other非公開であることを確認する。
- Agentの起動後に、秘密値を表示せず`ssh-add -l`の終了状態だけで登録可否を判定する。公開鍵指紋は通常の診断応答にも返さない。
- Agentを再生成する前に既存ソケットを無条件に削除しない。生存中の別Agent、非ソケット、所有者・権限不正は失敗として扱う。

### Native Host

Native HostはAgentの起動、停止、鍵保管庫へのアクセス、パスフレーズ取得を行わない。既存どおり`FOCODEX_REMOTE_AGENT`で示された本人所有・group/other非公開のUnixソケットだけを受理する。

Hostへ追加する診断は、認証管理コンポーネントが作成したソケットの可用性だけを示す。秘密値、鍵名、公開鍵、Secret Serviceの検索属性、リモートURLを返さない。認証準備ができていなければ、SSH接続試験を開始せず「認証準備未完了」と表示する。

## 3. 秘密値の扱い

標準の`ssh-add`はSecret Serviceを直接参照しない。鍵登録時だけ、固定の`SSH_ASKPASS`ヘルパーを使用する案を採用候補とする。

- ヘルパーは固定されたSecret Service属性で1件だけを問い合わせ、要求された場合にのみ標準出力へ返す。
- パスフレーズを環境変数、コマンドライン引数、設定ファイル、一時ファイル、標準エラー、journalへ出さない。
- `SSH_ASKPASS_REQUIRE=force`を使い、端末からの対話入力やFirefox経由の入力にフォールバックしない。
- ヘルパー、秘密鍵、Agentソケット以外の利用者入力・任意パス・任意属性を受け取らない。
- 初回のSecret Service項目登録は、Plasma等の保守用GUIで明示的に実施する。登録済みの値を確認表示・再出力する機能は作らない。

同一UIDの悪意あるプロセスからの完全隔離は提供しない。この方式は、FocodexのSandbox・拡張・Codexからパスフレーズと通常のSSH Agentを分離し、リモートGitに渡す鍵を限定するためのものである。

### 固定設定

`~/.config/focodex/remote-agent.json`を本人所有・`0600`の通常ファイルとして明示作成する。インストーラーはこのファイルを生成しない。秘密値そのものは入れず、鍵の絶対パスとSecret Service検索属性だけを固定する。

```json
{"version": 1, "privateKey": "/home/USER/.ssh/id_ed25519_github", "secretAttributes": {"service": "focodex-remote-ssh", "key": "github"}}
```

`remote_agent.py register`は設定を検証し、Keyringが解除済みで空の専用Agentが存在する場合だけ`ssh-add`を実行する。既存Agentが空でない、設定・鍵・ソケットの所有者や権限が不正、Secret Service項目がない、解除されていない場合は停止し、秘密値や公開鍵を出力しない。

Askpass子プロセスには、固定した`PATH`、専用Agentソケットに加え、検証済み`XDG_RUNTIME_DIR`とそこから導くD-Busアドレスだけを渡す。これによりSecret Serviceへ接続できる一方、任意の親環境を継承しない。

`config-status`は設定と鍵ファイルの安全性だけを、`secret-status`は固定属性に一致するSecret Service項目の有無だけを返す。どちらも鍵パス、属性、秘密値を出力しない。

`secret-tool store`が表示する`Password:`はSecret Service共通の固定的な文言であり、Focodex側から変更できない。この入力欄にはOSログイン用パスワードではなく、`privateKey`で指定したSSH秘密鍵のパスフレーズを入れる。`secret-tool store`の入力時点では、Secret ServiceはSSH鍵を知らないためパスフレーズの正誤を判定できない。後段の`remote_agent.py register`で`ssh-add`が照合し、不一致なら秘密値を表示せず「保存したパスフレーズとSSH秘密鍵の組合せを確認」と返す。

## 4. セッション寿命

`systemd --user`はSSHログインが残る場合にfoxbookログアウト後も生存し得る。そのため、ユーザーマネージャーの終了をAgent終了の根拠にしない。

1. foxbookのグラフィカルログイン後に認証管理サービスを開始する。
2. サービスはKeyring解除確認後にAgentを生成し、鍵登録に失敗した場合はAgentを利用可能として扱わない。
3. ログアウト、再ログイン、Plasma切替、SSH接続併存、再起動ごとに、旧ソケット・Agentの扱いと鍵登録状態を検証する。
4. 終了処理は自分が生成したAgentだけを対象とし、通常のSSH AgentやKeyringデーモンを停止しない。

具体的なsystemd unitの`PartOf=`、`BindsTo=`、グラフィカルセッションへの結び付けは、Surface実機のセッション名とuser managerの寿命を調べてから選ぶ。`linger`の有効化やユーザーマネージャー常駐を前提にしない。

## 5. 実装順序と受入条件

1. [x] 固定設定・権限検証・Secret Serviceのロック状態確認だけを行う、秘密値を出力しない検証コマンドを実装する。
2. [x] 専用Agent作成と、空Agentのソケット権限・再生成失敗を自動試験する。`remote_agent.py`とuser unitをインストーラーへ追加した。Surfaceで鍵登録前の`empty`状態を確認した。
3. [x] 固定属性のSecret Service項目を使う限定Askpassヘルパーと、固定鍵への`ssh-add`登録処理を追加した。SurfaceでKeyringの自動解除、設定・Secret Service項目の診断、`ready`状態まで確認した。
4. destination constraintを使う場合は、登録対象ホストの既知鍵と対象鍵を固定設定から決め、異なる宛先への署名を拒否することを確認する。
5. [x] foxbookの`firefox-kiosk-app`から専用Agentを再生成し、Firefox終了時に停止する処理を追加した。ログイン後、およびSurfaceのOS再起動後に`ready`を確認した。SSH接続併存とPlasma切替を含む長期的な再現性確認は継続課題とする。
6. Native Hostの非秘密診断を追加し、「認証準備未完了」時に読み取り専用SSH接続試験が開始されないことを確認する。
7. [x] 明示的な`ssh://`形式の実upstreamに対する`ls-remote`限定試験を実行する。Surfaceでは認証準備・直接試験に加え、Focodex固定中継経路の実upstream試験も成功した。安全な診断は`outcome=passed`、`wrapperStatus=exited`、`proxyStatus=connected`であり、中継と固定宛先接続を確認した。現在の記録は[読み取り専用SSH接続試験 引継ぎ記録](Focodex_読み取り専用SSH接続試験_引継ぎ記録_2026-10-24.md)を参照する。

Fetch、Pull、Push、秘密鍵生成、パスフレーズ変更、鍵保管庫の初期化・削除は、この設計の対象外とする。
