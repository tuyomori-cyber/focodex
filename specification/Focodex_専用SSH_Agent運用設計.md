# Focodex 専用SSH Agent運用設計

更新日: 2026-10-23
状態: 方針確定、実装前

## 1. 目的

Surfaceのタッチ専用運用で、OS再起動・ログアウト後に端末やターミナルを使ってSSH鍵を再登録する負担をなくす。FocodexのリモートGit操作は、同期用の鍵だけを持つ専用OpenSSH Agentを使い続ける。

## 2. 必須構成

- 鍵保管庫: Freedesktop Secret Service APIを実装する、ログイン連動の鍵保管庫を使う。当面の実装・検証先はGNOME Keyringとするが、Focodexの中核はGNOME固有のコマンド・パス・設定を持たない。
- 自動解除: ディスプレイマネージャのPAM設定がログイン時に鍵保管庫を解除する。自動ログインは対象外とする。
- Agent管理: ユーザーsystemdサービスが、固定パス %t/focodex/remote-agent.sock の専用OpenSSH Agentを開始・停止する。
- 鍵投入: ログイン保管庫が解除できる場合だけ、Agent起動用の限定コンポーネントが同期用の鍵を専用Agentへ登録する。鍵・パスフレーズ・秘密の出力をFirefox、Native Messaging、Codex、ログへ渡さない。

GNOME Keyringが提供する共有SSH Agentを、Focodexの専用Agentとして直接使わない。共有Agentにある他の鍵をFocodexのSandboxへ公開しないためである。

## 3. 起動と利用の流れ

1. ユーザーが通常のログイン画面で認証する。
2. PAMとSecret Service互換鍵保管庫がログイン保管庫を解除する。
3. ユーザーsystemdがFocodex専用Agentを起動し、所有者専用の固定ソケットを作る。
4. 鍵投入コンポーネントが同期用の鍵だけをAgentへ登録する。
5. Native Hostは同じ固定ソケットを FOCODEX_REMOTE_AGENT として受け取り、事前診断で「設定済み・鍵あり」を表示する。
6. ログアウト時にAgentとruntimeディレクトリのソケットは消える。次のログインで1から再開する。

鍵保管庫が未解除、鍵投入に失敗、Agentソケットの所有者・権限が不正な場合は、Focodexはリモート操作を開始しない。画面には秘密を含まない状態だけを示す。

## 4. セキュリティ境界

- Codex SandboxはSecret ServiceのD-Bus、鍵保管庫、専用Agentソケットを読取・接続できない。
- Native Hostの通常のCodex実行経路にも、Secret Serviceや専用Agentの環境変数を渡さない。
- リモートGit専用Sandboxには、実行ごとに確認済みの専用Agentソケットだけを固定パスへbindする。共有Agent、HOME、D-Bus、runtimeディレクトリ全体は公開しない。
- Agent準備はFetch／Pull／Pushと別の限定操作とし、任意コマンド・任意鍵・任意ソケット・任意Secret Service項目を受け付けない。

## 5. 移行性

Fedora、Debian、Ubuntuの通常構成ではPAM・systemdユーザーサービス・Secret Service APIを組み合わせられる。ただし、PAMのモジュール名、鍵保管庫の実装、Cageのセッション起動方法はOSごとに異なるため、インストーラーは必ず事前診断する。

OS移行時に引き継ぐのはFocodexの固定ソケット契約、専用Agent、SSH中継、Git安全規則である。鍵保管庫の実装は再選択・再登録する。Secret Serviceを提供しない環境で自動解除を要求する場合は、別の鍵解除プロバイダを追加するまでリモート操作を無効にする。

## 6. 実装前確認結果

2026-10-23のSurfaceセッションで、次を確認した。

| 項目 | 結果 |
| --- | --- |
| LightDMのPAM設定 | pam_gnome_keyring.so と auto_start を設定済み |
| 現在のログイン保管庫 | Secret Serviceの応答、login alias、未ロック状態を確認 |
| Cage/Firefox用セッション | ユーザーD-Bus、runtimeディレクトリ、Keyring制御先を確認 |
| 実Codex Sandbox | runtime内の一時Unixソケットを検出できないことを確認 |

この結果は現在のSurface環境での前提確認であり、他OSでの自動構成を保証するものではない。

## 7. 次の実装

1. systemdユーザーunitと、固定ソケットの専用Agent起動・停止を実装する。
2. Secret Serviceから同期用鍵を取得して専用Agentへ限定投入するコンポーネントを実装する。
3. インストーラーへPAM・Secret Service・systemd・Agentの診断を追加する。
4. ログイン、ログアウト、再起動、鍵保管庫未解除、鍵なし、Codex実行中の各状態を自動・Surface実機で確認する。
