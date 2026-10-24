# foxbook 認証基盤調査記録：GNOME Keyring自動解除

- 記録日：2026-10-24
- 対象：Surface Pro 4 / Fedora 43 / GDM / カスタムCage（foxbook）/ Firefox / Focodex
- 結論：**OSログインパスワードとGNOME Keyringの「Login」鍵保管庫のパスワード不一致が原因。鍵保管庫のパスワードを現在のOSパスワードへ変更し、GDM→PAMによる自動解除を実機確認した。**
- 本文中の時刻はJST。PIDは調査時の一時的な値。

## 1. 調査の目的と要件

FocodexのGit pull/pushで使用する、パスフレーズ保護付きSSH秘密鍵（`~/.ssh/id_ed25519_github`）を、foxbookのGDMパスワードログイン後に、追加のキーボード入力なしで**Focodex専用ssh-agent**へ登録できるようにする。

前提・制約：

- foxbookはGDMからログインする独立Waylandセッション。実運用のセッション名は`firefox-kiosk-test`で、タッチ回転対応のカスタム`cage-touch-test`を使う。
- Cage内はFirefox中心のタッチ専用環境で、OSレベルの仮想キーボードを常用しない。Firefox拡張の仮想キーボードはOSの認証ダイアログに使えない。
- **OS自動ログインはしない**。GDMでパスワードを入力する運用を継続する。
- SSH秘密鍵のパスフレーズを無効化しない。FirefoxのJavaScriptや拡張にパスフレーズを渡さない。
- SSH AgentはFocodex専用に分離し、Git操作はNative Hostの限定機能として扱う。
- Fedora/GNOME固有処理は交換可能な小さな認証管理コンポーネントに閉じ込め、将来のOS変更に備える。

## 2. 環境と起動経路

```text
GDM（パスワード認証）
  └─ PAM（/etc/pam.d/gdm-password に pam_gnome_keyring.so）
      └─ firefox-kiosk-test.desktop
          └─ ~/.local/bin/firefox-kiosk-test
              └─ ~/cage-touch-test
                  └─ ~/.local/bin/firefox-kiosk-app
                      ├─ wlr-randr：画面スケール200%
                      ├─ wlr-randr：90°回転
                      └─ Firefox（Waylandネイティブ）
```

`firefox-kiosk-app`は`wlr-randr`による表示設定、`MOZ_ENABLE_WAYLAND=1`、`FOXBOOK_CAGE_SESSION=1`、`exec firefox --no-remote --new-instance about:debugging`等を実行する。Keyringを停止・再起動する処理は見当たらなかった。

確認した構成：

- `gnome-keyring-48.0-3.fc43.x86_64`導入済み。
- GDMのPAM設定にはKeyring用モジュールが含まれる。
- `gnome-keyring-daemon.service`は`gnome-keyring-daemon.socket`を要求。ソケットは`%t/keyring/control`を待ち受ける。
- XDG autostart由来の`app-gnome\x2dkeyring\x2dsecrets@autostart.service`にはデスクトップ環境の`ExecCondition`があり、foxbookではスキップされ得る。**スキップ自体は異常の証拠ではない**。

## 3. 調査経過と観測結果

### 3.1 最初の現象

- `busctl --user`からSecret Serviceを起動・参照すると、`/org/freedesktop/secrets/collection/login`の`Locked`が`b true`だった。
- PAMログには`gnome-keyring-daemon started properly and unlocked keyring`が出ていたため、当初はPAM起動デーモンとD-Bus起動デーモンの連携不良を疑った。
- 調査中には`gnome-keyring-daemon --daemonize --login`と`--start --foreground --components=secrets`の別プロセスが確認された。
- SSHセッションを残してCageをログアウトすると、systemdユーザーマネージャーとD-Bus起動のKeyringプロセスが残るケースがあった。これは**セッション寿命の設計上の注意点**だが、今回の鍵保管庫ロックの主因ではなかった。

### 3.2 10月24日のログの時系列

```text
08:27:56  gkr-pam: gnome-keyring-daemon started properly and unlocked keyring
08:30:01  GDM PAM session closed
08:34:16  gkr-pam: gnome-keyring-daemon started properly and unlocked keyring
08:50:22  GDM PAM session closed
08:50:53  gkr-pam: the password for the login keyring was invalid.
```

08:27のログインは、Wi-Fi/IP確認のためにPlasmaを使った時刻とみられる。08:34以降はfoxbookの利用を含む。08:50の`password ... was invalid`は、鍵保管庫のパスワード不一致を直接示す重要な手掛かりだった。

### 3.3 再起動してfoxbookへ直接ログインする試験

Plasmaを経由せずにfoxbookへ直接ログインしても、以下が再現した。

```text
pgrep -af '[g]nome-keyring-daemon'
2163 /usr/bin/gnome-keyring-daemon --daemonize --login
```

```text
sudo journalctl -b --no-pager | grep 'gkr-pam'
... gkr-pam: gnome-keyring-daemon started properly and unlocked keyring
```

```text
busctl --user get-property \
  org.freedesktop.secrets \
  /org/freedesktop/secrets/collection/login \
  org.freedesktop.Secret.Collection Locked
b true
```

このとき、D-Bus名`org.freedesktop.secrets`の所有者がPAM起動のPID 2163であることも確認した。**「別デーモンが所有権を奪ったことが主因」という仮説は支持されなかった**。

さらに`/run/user/1000/keyring/control`ソケットが存在し、デーモンの環境に`XDG_RUNTIME_DIR=/run/user/1000`と`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`があることを確認した。`gnome-keyring-daemon --start --components=secrets`を手動実行しても`b true`のままだった。**単純な`--start`不足とも断定できなかった**。

### 3.4 原因特定

ユーザーはFedora導入後、OSログインパスワードを変更していた。`~/.local/share/keyrings/login.keyring`は10月5日作成のファイルで、パスワード変更以前の状態が残っていた可能性があった。

Seahorseは当初未導入だったため、`sudo dnf install seahorse`で導入し、Plasma上で「Login」鍵保管庫を確認した。

**古いOSログインパスワードで「Login」鍵保管庫の解除に成功**。これにより、現在のOSログインパスワードと鍵保管庫のパスワードが一致していないことを確認した。

Seahorseから「Login」鍵保管庫のパスワードを現在のOSログインパスワードへ変更し、新パスワードで解除できることを確認した。

### 3.5 修正後の最終検証：成功

Surfaceを再起動し、GDMからfoxbookへログイン。その後、SSHから確認した結果：

```text
busctl --user get-property \
  org.freedesktop.secrets \
  /org/freedesktop/secrets/collection/login \
  org.freedesktop.Secret.Collection Locked
b false
```

```text
10月 24 09:39:11 ... gkr-pam: unable to locate daemon control file
10月 24 09:39:11 ... gkr-pam: stashed password to try later in open session
10月 24 09:39:12 ... gkr-pam: gnome-keyring-daemon started properly and unlocked keyring
```

**最終判定：GDM→PAM→GNOME Keyringの自動解除はfoxbook上で成功。**

`unable to locate daemon control file`と`stashed password`はログに残るが、後続の解除成功と実測`b false`が確認できているため、今回の自動解除を妨げていない。

## 4. 結論と棄却・保留した仮説

| 仮説 | 判定 |
|---|---|
| OSパスワード変更後、Login鍵保管庫だけ古いパスワードを保持 | **原因として確認。修正後に自動解除成功** |
| Cageの起動スクリプトがKeyringを終了させる | 該当処理は見つからず |
| XDG autostartがスキップされること自体が故障 | 根拠なし。条件付きスキップは仕様上あり得る |
| PAMデーモンが終了し、別デーモンが乗っ取るのが主因 | 再起動試験でPAMデーモン自身がSecret Serviceを提供しており、支持されず |
| `gnome-keyring-daemon --start`不足が唯一の原因 | 手動実行でもロック解除せず、支持されず |
| SSH接続が残ることでロックされる | 再起動直後の単独foxbookログインでも再現し、主因ではない |

**教訓：PAMの「解除成功」ログだけで実際の鍵保管庫状態を断定せず、`Locked`プロパティで検証する。OSパスワード変更歴がある場合は、鍵保管庫パスワードの不一致を早い段階で確認する。**

## 5. 今後の実装方針：Focodex専用SSH認証

### 5.1 目標の処理経路

```text
GDMでOSパスワード入力（自動ログインなし）
  ↓
PAMがGNOME KeyringのLoginコレクションを自動解除【実機確認済み】
  ↓
OS側の認証管理コンポーネントがSecret ServiceからSSH鍵パスフレーズを取得【未実装】
  ↓
Focodex専用ssh-agentへパスフレーズ保護付き秘密鍵を登録【未実装】
  ↓
Focodex Native Hostが専用SSH_AUTH_SOCKを使用【設計・実装要確認】
  ↓
固定追跡先へのGit pull/push【今後実装】
```

**自動解除が成功したのは鍵保管庫まで。SSH鍵のパスフレーズ保存、取得、ssh-agent登録は未完了。**

### 5.2 設計上の確定事項・推奨事項

1. **鍵の保護**：`~/.ssh/id_ed25519_github`のパスフレーズは維持。Firefox拡張・Webページ・Native MessagingのJSON応答・ログへ秘密情報を渡さない。
2. **Agent分離**：Focodex専用ソケットは既存Native Hostの既定値である`$XDG_RUNTIME_DIR/focodex/remote-agent.sock`に固定し、通常のSSH Agentと区別する。ソケットと鍵登録状態の診断機能を用意する。
3. **認証バックエンド分離**：Secret Service/GNOME Keyring依存はOS側の小さな認証管理コンポーネントに閉じ込める。将来、他の鍵保管庫やOSに切り替えられるインターフェースにする。
4. **セッション寿命**：`systemd --user`はSSHログインが残るとCageログアウト後も存続し得る。単純にユーザーマネージャーの終了をAgent終了とみなさない。foxbookのログイン・ログアウト境界、再ログイン時のAgent再生成、ソケット残骸、SSH接続併存を設計・検証する。
5. **最小権限**：Git操作はFocodex専用Native Hostに限定し、CodexからのGit書込みは禁止する既存方針を維持。リモート追跡先固定、競合時停止など既存のGit安全方針を維持する。
6. **秘密情報の限界**：Secret Serviceは同一ユーザー権限のアプリからアクセスされ得る。専用コンポーネントを設けても同一UIDの悪意あるプロセスから完全隔離できるわけではない。保存項目の属性・取得方法・実行権限をレビューする。
7. **起動時の順序**：Secret Serviceの利用可能性と`Login`コレクションの解除を確認してからAgentへ鍵登録。失敗時は無理に続行せず、Focodexに「認証準備未完了」と表示する。

### 5.3 実装前に決めること

- Secret ServiceへのSSH鍵パスフレーズの安全な登録方法（初回のみ、OS GUIを使えるPlasmaで設定可能）。
- 鍵登録の実装方法。**標準の`ssh-add`はSecret Serviceからパスフレーズを自動取得しない**ため、パスフレーズを安全に渡す仕組み（例：限定された`SSH_ASKPASS`ヘルパー）を別途設計・検証する。環境変数・コマンドライン引数・一時ファイル・ログへの平文露出を避ける。
- Agentの起動・停止をGDMのグラフィカルセッションに結びつける具体的方法。SSHセッションからの保守やPlasma利用と干渉しないこと。
- Secret Serviceがロック中・未起動・接続不可の場合のエラー処理。鍵保管庫の自動解除を無理に回避しないこと。
- 秘密鍵の登録対象を固定・検証し、Agentソケットのアクセス権限とNative Hostの実行環境を確認すること。

### 5.4 推奨する検証順序

1. 現在の自動解除成功状態をベースラインとして保持する。Cage/GDM/PAMの設定は当面変更しない。
2. PlasmaでSecret ServiceへFocodex用のSSH鍵パスフレーズを登録する手順を設計・検証する。
3. OS側の小さな検証プログラムから秘密情報を取得できることを、秘密値を表示せずに確認する。
4. Focodex専用Agentを作成し、鍵を登録。`SSH_AUTH_SOCK=... ssh-add -l`等で登録状態を確認する（秘密値をログ出力しない）。
5. foxbookへの新規ログイン、ログアウト→再ログイン、SSH接続併存、Plasma切替、再起動でAgent寿命と自動登録を試験する。
6. Focodex Native Hostへ統合し、固定リモートへのGit接続テストを実施する。失敗時は操作を停止し、ユーザーに状態を表示する。

## 6. 再確認に使うコマンド

```bash
# GDM/PAMのKeyring関連ログ
sudo journalctl -b --no-pager | grep 'gkr-pam'

# Login鍵保管庫のロック状態（b falseなら解除済み）
busctl --user get-property \
  org.freedesktop.secrets \
  /org/freedesktop/secrets/collection/login \
  org.freedesktop.Secret.Collection Locked

# Keyringプロセス
pgrep -af '[g]nome-keyring-daemon'

# Secret Serviceの所有プロセス
busctl --user status org.freedesktop.secrets

# 制御ソケット
ls -la "$XDG_RUNTIME_DIR/keyring/"

# セッション一覧
loginctl list-sessions
```

## 7. 作業時の注意

- 鍵保管庫ファイル`~/.local/share/keyrings/login.keyring`を安易に削除・初期化しない。保存済みの秘密情報を失う可能性がある。
- 旧・新パスワード、SSH鍵パスフレーズの実際の文字列は、この記録に保存しない。
- Seahorseは保守時のみPlasmaで使用すればよく、foxbookへの常駐は不要。
- 本調査の最終状態は**GNOME Keyringの自動解除成功**。Focodex専用AgentへのSSH鍵自動登録まで成功したという意味ではない。
