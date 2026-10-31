# Focodex 仕様書 v0.2.0

- 文書バージョン：0.2.0
- 作成日：2026-10-26
- 更新日：2026-10-31
- 状態：現行実装ベース
- 対象：Firefox拡張、Native Messaging Host、Codex app-server、限定Git操作、専用SSH Agent

## 1. 目的と境界

FocodexはFirefoxからNative Messaging経由でCodexへ指示を送り、応答・終了状態を表示する。登録済みワークスペース内のGitリポジトリに限り、状態確認、差分確認、選択ファイルのstage／commit、fetch／pull／pushを段階的な確認付きで扱う。

任意シェル、任意パス、任意Git引数、任意ネットワーク宛先は受け付けない。Codex実行とGit書込みは同時に行わず、停止・異常終了・不確定な後始末では安全側に停止する。

## 2. 構成

| 要素 | 責務 |
| --- | --- |
| `extension/` | Codex接続画面、ワークスペース／Git操作画面、fox-voice-editorからの受信バッファ。 |
| `native_host/focodex.py` | Native Messaging、Codex app-server、実行記録、確認状態、Git操作への入口。 |
| `native_host/git_ops.py` | ローカルGitの安全な読取り・stage・commit・状態照合。 |
| `native_host/git_remote.py` | 専用SSH Agent、固定SSH設定、隔離Sandbox、fetch／pull／pushの安全境界。 |
| `native_host/remote_agent.py` | 固定鍵1本だけを扱う専用ssh-agentの起動・登録・非秘密診断。 |

## 3. ワークスペースとCodex

設定は`~/.config/focodex/config.json`に置く。ワークスペースは明示登録したリポジトリだけを含み、検出ルートや親ディレクトリ自体にCodex権限を与えない。単独リポジトリのGit操作は、Codexの永続権限対象へ追加しない。

Codex実行ではモデル一覧を同一接続で取得し、選択モデル・許可対象・理由を画面で確認する。汎用のファイル変更・コマンド実行承認は許可せず、追加質問だけを明示回答する。停止後は専用プロセス群の終了確認まで次の実行を開始しない。

モデル一覧取得またはCodex実行が操作不可・途中停止となった場合、画面に原因調査用の`traceId`を表示する。Native Hostは要求受信、入力検証、Codex起動、app-server RPC、終了処理の段階を内容非保存の診断ログへ記録する。指示文、応答本文、モデル名、生カーソル、Codexの生エラー本文は記録しない。保存場所・保持上限・失敗時の扱いは[操作診断トレース設計](操作診断トレース設計.md)を正とする。

## 4. GitとSSH

Git書込みは確認した対象・スナップショット・選択ファイルだけに限定する。hook、署名、継承環境、進行中のGit操作、競合・sparse／split index、想定外の状態は拒否する。リモート操作は現在のブランチの単一upstream、明示的な`ssh://` URL、既知鍵、専用SSH Agentを検証してから行う。force push、任意リモート、通常の`SSH_AUTH_SOCK`の流用は行わない。

専用Agentは`FOCODEX_REMOTE_AGENT`で指定された所有者限定Unixソケットだけを受理する。固定設定・固定鍵・Secret Serviceの値を検証し、画面・診断・永続ログへ秘密鍵、パスフレーズ、公開鍵、リモートURLを出力しない。導入と復元の手順は[専用Agent運用文書](../docs/dedicated-ssh-agent-operations.md)を正本とする。

## 5. fox-voice-editor連携

`focodex.buffer.receive`を受け、文章を最大3件のFIFO受信バッファへ保存する。受信だけでは入力欄変更・Codex実行を行わない。同一request ID・同一本文は重複追加せず、同一ID・異なる本文は拒否する。横断契約の正本は[`IF-VE-FD-001`／`002`](../../foxbook/docs/interfaces/fox-voice-editor--focodex.md)である。

## 6. 検証と未完了

2026-10-26に受信バッファ、画面状態、ワークスペース権限隔離、Native Host、Git安全操作、専用SSH Agentの自動試験87件を確認した。Sandboxによる未登録リポジトリへの直接・リンク経由アクセス拒否も確認済みである。

SurfaceでのFirefox実操作、Gitの通信失敗・中断時、長期セッション、Split View、fox-voice-editorからの実配送、pull／pushの実運用は未完了である。詳細は[TODO](../TODO.md)、[検証記録](../docs/verification.md)で管理する。
