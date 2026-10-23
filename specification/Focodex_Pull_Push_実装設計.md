# Focodex Pull・Push 実装設計

更新日: 2026-10-23
状態: 第3段階の安全契約を確定。Fetch／Pull／Pushは未実装・未実行。

## 1. 目的と範囲

Surface Pro 4 の Firefox/Cage 環境から、日常的な同期を限定的に行う。Focodex は汎用 Git クライアントではない。正常に設定済みの1リポジトリについて、現在チェックアウト中のブランチの Fast-forward Pull と通常 Push だけを、条件を満たす場合に明示操作で実行する。

**対象:** 現在ブランチの状態表示、Fast-forward Pull、単一追跡ブランチへの通常 Push、操作結果と安全な復旧案内。
**対象外:** ブランチ作成・切替、upstream 作成・変更、SSH 鍵生成・登録、認証設定、競合解決、merge、rebase、cherry-pick、force push、削除 push、タグ一括送信、任意 remote/refspec、Git 設定の修復、異常終了からの自動復旧。

複雑な操作は Focodex 外の Git CLI で行う。第3段階の完了後も、Git 機能を汎用操作へ拡張しないことを原則とする。

## 2. 責務と不変条件

- Stage と Commit は既存の `git_ops.py` の安全境界を維持する。Pull・Push のために既存の検査を緩めない。ステージ済み変更の解除は引き続き未提供である。
- Pull・Push は `git_remote.py` に分離し、Host が構築した固定引数配列だけを専用 Sandbox で実行する。UI には Git コマンド・URL・refspec の自由入力欄を置かない。
- Codex 実行、ローカル Git 操作、リモート Git 操作は同じ排他と永続実行記録を共有する。外部端末からの変更を完全には防げないため、検証は実行直前にも繰り返す。
- Git hooks、filter、external diff、fsmonitor、credential helper、署名、include、remote helper、環境変数など、Git が間接的に起動・参照できるものを信頼しない。許可規則で検証できない構成は拒否し、設定を自動修復しない。

## 3. 対象の確定と通信境界

操作ごとに現在のローカルブランチから単一 upstream を取得する。detached HEAD、upstream 未設定、複数値、許可外の remote 名・merge ref・URL、`pushurl` の設定は拒否する。事前表示、最終確認、実行直前の3時点で、ブランチ・upstream・remote URL を再検証し、いずれかが変化すれば中止する。

許可する URL は明示的な `ssh://user@host[:port]/path` のみとする。scp 形式、暗黙ユーザー、IP リテラル、query/fragment、複数 URL、別 remote、SSH 設定の継承、ProxyJump、ProxyCommand は拒否する。Host は検証済みの host・port・user・repository path だけへ接続する一操作限りの Unix ソケット中継を作る。Sandbox はホストのネットワーク名前空間を共有せず、この中継以外へ接続できない。

Sandbox 内 SSH は Host が生成する最小設定だけを使う。既知鍵は対象 host のものだけ、`StrictHostKeyChecking=yes`、対話認証なし、鍵ファイル探索なし、Agent forwarding なし、ControlMaster なしとする。認証には `FOCODEX_REMOTE_AGENT` で明示された、Host 実行ユーザー所有かつ group/other 非公開の専用 Agent ソケットだけを使い、通常の `SSH_AUTH_SOCK` は流用しない。

## 4. Pull の安全契約

Pull は次のすべてを満たす場合だけ開始する。

1. 対象リポジトリ、現在ブランチ、単一 upstream、固定通信先が一意に検証できる。
2. HEAD が attached である。
3. ステージ済み・未ステージ・未追跡の変更がない。初期実装では未追跡ファイルが1件でもあれば拒否する。
4. merge、rebase、cherry-pick、revert、bisect 等の進行状態、ロック、既存の実行未確認記録がない。
5. 設定・属性・管理ディレクトリ・worktree 構成が既存の許可規則を満たす。

実装は次の順で進める。

1. 上記の読み取り専用検証と、最終確認で表示する現在 HEAD・branch・upstream を取得する。
2. 固定通信境界を使う Fetch を単独機能として先に検証する。Fetch は remote-tracking ref を更新する変更操作であり、読み取り専用 SSH 試験の成功から安全とは推定しない。
3. Fetch 後、実行直前にローカル HEAD と upstream の取得先を再確認する。upstream の取得コミットが現在 HEAD の子孫である場合だけ進める。ローカル先行、分岐、判定不能の場合は作業ツリーを変更せず停止する。
4. 固定引数による `merge --ff-only` 相当を実行する。自動 merge、rebase、競合解決はしない。
5. 正常終了時は branch、HEAD、index、作業ツリーを再確認する。

結果は「更新成功」「更新不要」「開始前拒否」「Git が明確に失敗」「結果未確認」を区別する。明確な失敗でも状態を再取得するが、状態が確認できなければ結果未確認として扱う。

## 5. Push の安全契約

Push は現在ブランチの検証済み upstream にだけ送る。`push.default`、`remote.pushDefault`、暗黙の push 先決定は使わない。送信コマンドは、直前に再検証した値から構築する明示 refspec（概念例: `refs/heads/main:refs/heads/main`）だけとする。force、`--force-with-lease`、削除 refspec、タグ一括送信、任意 refspec は許可しない。

実行前に固定通信境界で対象 remote branch の OID を確認し、その OID がローカル HEAD の祖先であることを確認する。これは開始前の安全診断であり、確認後の別端末による更新との競合を排除する保証ではない。実際の送信先との一致は、同一の検証済み upstream スナップショットから Host が refspec と固定中継先を生成し、実行直前に再検証することで担保する。

通常 Push の non-fast-forward 拒否を最終的な競合防止に使う。確認後に remote が進んだ場合、Git の通常 Push が拒否したら停止し、自動 Pull・rebase・再 Push を行わない。`--force-with-lease` は採用しない。

Push 前にも Pull と同じリポジトリ状態・進行状態・設定検証を行う。正常終了後は、固定経路で対象 remote branch を再確認し、期待した送信先と commit OID を確認する。ローカル HEAD が不変でも remote 側の更新は成立し得るため、再確認できなければ「Push結果未確認」であり「Push失敗」ではない。

## 6. 正常・失敗・結果未確認

| 状態 | Pull | Push | Focodex の動作 |
| --- | --- | --- | --- |
| 正常終了 | HEAD・index・作業ツリーを再確認 | 対象 remote branch を再確認 | 成功または更新不要を表示 |
| 明確な失敗 | ローカル状態を再確認 | remote 状態を再確認 | 失敗理由を固定分類で表示。自動再実行なし |
| タイムアウト・中断・終了未確認 | 成否を推定しない | remote 更新済みの可能性を含め成否を推定しない | 結果未確認として変更操作を停止 |
| 復旧 | Focodex 外の CLI で状態確認 | Focodex 外の CLI で remote を確認 | 確認後にのみ運用を再開 |

Host の子プロセス・SSH・中継の終了確認ができない場合、永続実行記録を残す。Firefox・Host の再起動、PID 不在、通信切断だけでは解除しない。結果未確認時の自動再 Pull／再 Push、設定変更、別認証方式の試行はしない。

## 7. Focodex が保証する範囲

Focodex は、許可された固定操作だけを実行し、操作対象を明示的に確定し、危険・未検証の状態では開始せず、結果不明時に勝手に再実行しないことを保証目標とする。

競合解決、外部からの同時変更の完全防止、不正・特殊なリポジトリ設定の自動修復、異常終了後の自動復旧は保証しない。

## 8. 検証状況と実装順序

| 検証項目 | 状態 |
| --- | --- |
| 固定宛先 SSH | 成功 |
| 専用 SSH Agent | 成功 |
| 最小 known_hosts | 成功 |
| 中継経由の接続 | 成功 |
| 実 upstream への `ls-remote` | 成功 |
| Fetch | 未確認 |
| Fast-forward Pull | 未確認 |
| Push | 未確認 |
| 異常終了・復旧 | 未確認 |

読み取り専用 SSH 経路は実機で成立している。ただし、これは Fetch・Pull・Push、または変更操作の後処理の安全性を実証するものではない。

実装は、(1) 現在 branch/upstream と専用 Agent の読み取り専用診断表示の改善、(2) 固定通信境界内の Fetch 単独検証、(3) Fast-forward Pull、(4) 通常 Push、(5) Surface 実機で成功・拒否・中断・結果未確認・CLI 復旧を確認、の順とする。各段階で自動試験と実機確認を完了するまで次へ進まない。
