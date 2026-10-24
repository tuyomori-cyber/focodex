# Focodex Pull・Push 実装設計案

作成日: 2026-10-23  
状態: 設計案（通信境界の方式を調査中。実装・実機検証前）

## 1. 目的と範囲

Surface Pro 4のFirefox/Cage環境で、タッチ操作から日常的なGit同期を実行する。Focodexを汎用Gitクライアントにはせず、正常に設定済みのローカルリポジトリについて、現在チェックアウト中のブランチをPull・Pushする機能だけを提供する。

**対象:** 現在のブランチのPull、Push、状態表示、エラー表示。  
**対象外:** ブランチ作成・切替、upstream設定、SSH鍵生成・登録、認証設定、競合解決、rebase、force push、任意のリモート/ブランチ指定、Git設定修復。対象外の操作はSSH経由のGit CLI等で行う。

## 2. 既存機能との責務分離

- **ローカルGit操作:** Stage・Unstage・Commitは既存の厳格な検証方式を維持する。既存の`git_ops.py`の安全境界をPull・Push導入のために緩めない。
- **リモートGit操作:** Pull・Pushは別モジュール（仮称`git_remote.py`）に分離し、固定されたGit CLIコマンドを専用Sandboxで実行する。
- **Native Host:** 登録済みプロジェクトの特定、要求の検証、Codex/Git操作との排他、実行状態の管理、結果の返却を担う。
- **UI:** 現在のブランチ・追跡先・同期結果を表示し、Pull/Pushボタンを提供する。Gitコマンドや引数の自由入力欄は設けない。

## 3. 追跡先の扱い

現在の**ローカルブランチ**に設定されたupstreamを、操作時にローカルGit設定から取得する。Focodex固有の「固定ブランチ」設定は持たない。ブランチをCLIで切り替えた場合、次回操作では切替後のブランチとupstreamを使用する。

- upstream未設定・detached HEAD・対象が曖昧な場合は拒否。
- Push先はupstreamと同じリモート・ブランチを明示して実行する。`push.default`等の暗黙のPush先決定には依存しない。
- 操作直前にブランチ名・upstream・リモートURLを再検証し、事前確認後に変化した場合は中止する。
- リモートやブランチの追加・変更はCLIで行う。

## 4. Pullの動作（Fast-forward限定）

1. Native Hostが対象リポジトリの登録状態と排他状態を確認する。
2. 現在のブランチ、upstream、リモートを確認する。
3. 作業ツリーとインデックスがクリーンであることを確認する。進行中のmerge/rebase等があれば拒否する。
4. 専用Sandbox内で、指定されたupstreamの取得に必要な`git fetch`を実行する。
5. 取得したコミットが現在のHEADの子孫か検証する。ローカルが先行・分岐している場合の扱いは「更新不要」または「拒否」とし、自動マージ・rebaseは行わない。
6. Fast-forward可能な場合だけ`git merge --ff-only`相当で更新する。
7. 結果のブランチ、HEAD、作業ツリー状態を再確認し、成功・更新不要・拒否・失敗を区別して表示する。

**重要:** Fetchはリモート追跡参照を更新し、Fast-forwardは作業ツリーを書き換える。Sandboxだけで更新の妥当性は保証されない。実行前後の状態検証と、操作中の排他が必要。

## 5. Pushの動作（非強制・単純同期限定）

1. Native Hostが対象リポジトリ、排他、現在のブランチ、upstreamを検証する。
2. Push先のリモート・ブランチをupstreamから明示的に確定する。
3. Force、削除、タグ一括送信、任意refspecを許可しない。
4. Sandbox内で固定コマンドにより通常Pushを実行する。リモート側が進んでいて非Fast-forwardになる場合はGitに拒否させる。
5. 成功・更新不要・拒否・認証失敗・通信失敗を区別して表示する。

**注意:** 非強制Pushでも、送信前の状態とリモートの状態には競合があり得る。成功判定はGitの実行結果に基づく。認証の再設定や競合解決は自動で行わない。

## 6. Sandboxの基本要件

- LinuxのOSレベルの隔離（候補: bubblewrap）を検討する。既存のCodex用制限設定をそのまま転用できるとは仮定しない。
- 登録済みの対象リポジトリ以外のファイルシステムアクセスを原則禁止する。必要なGit実行ファイル・ライブラリ等は読み取り専用で提供する。
- Git hooks、外部filter、credential helper、SSHコマンド、Git config、環境変数等を含め、**Gitが間接的に起動するプログラム**を脅威モデルに含める。
- Pull時のファイル書き換え、`.git`の参照・オブジェクト更新に必要な書込範囲を定義する。Gitが想定外の場所に書き込めないことを検証する。
- Pushは通信が必要。認証と通信のために開ける境界を最小化する。ネットワーク宛先の制御をSandbox単体で実現できるとは仮定しない。
- SSH秘密鍵をSandboxに直接渡すことを既定にしない。SSH Agentソケットを渡す場合も鍵の利用範囲は自動的には制限されない。
- 失敗時は権限を広げず中止する（fail closed）。

## 7. 排他・異常終了・結果表示

- Codex実行中、ローカルGit変更操作中、他のリモートGit操作中はPull/Pushを開始しない。
- Native Hostが既存の実行状態記録と終了確認の仕組みを再利用する。Gitの子プロセス・SSH等の子孫プロセスも終了確認の対象にする。
- タイムアウト・中断・終了未確認時は、処理が成功したと推測しない。状態を「不明」として追加変更操作を止め、CLIによる確認・復旧へ誘導する。
- 操作失敗後に自動で別の認証方法を試したり、設定を書き換えたりしない。
- UIには現在のローカルブランチ、upstream、Pull/Push結果、必要なCLI復旧案内を表示する。

## 8. 初期対応の制限

- リモート分岐の自動解消なし。
- 複数リモートの選択UIなし。
- upstreamの作成・変更UIなし。
- 認証情報の入力・保存UIなし。
- ブランチ切替UIなし。SSHで`git switch`した後にFocodexを使用する。
- Gitの特殊な構成や未検証の認証方式はサポート対象外とし、検証できない場合は拒否する。

## 9. 実装順序（案）

1. 別紙「事前調査・検証事項」を実施し、Sandboxと認証経路の成立性を確認する。
2. 既存のローカルGit操作の安全性・排他を維持できるインターフェースを定義する。
3. 読み取り専用の現在ブランチ・upstream表示を実装する。
4. Sandbox内のFetchを単独で検証する。
5. Fast-forward限定Pullを実装する。
6. 通常Pushを実装する。
7. Surface実機でSSH/Firefox Native Host経由の動作、失敗・中断・復旧を確認する。

## 10. 未確定事項

- Sandbox実装方式と起動権限。
- SSH鍵/Agent/known_hostsの安全な受け渡し方法。
- Git設定、hooks、filters、補助プログラムの具体的な遮断方法。
- リモートURL・認証方式の許容範囲。
- Fetch後のupstream参照更新とFast-forward適用の原子的な扱い。
- 実装時に使用するGitコマンドと固定環境変数の詳細。

## 11. 通信境界の調査結果と候補方式（2026-10-23）

SurfaceのSELinux `Enforcing` 環境では、ネットワークを共有しないbubblewrap Sandboxに、対象リポジトリを読取り専用、SSH Agentソケットだけを固定パスへbindして、Firefox Native Hostから起動できることを実機確認した。この時点でSSH接続・Fetch・Pushは実行していない。

`bwrap --share-net`は、Sandboxにホストのネットワーク名前空間を戻す指定であり、宛先単位の制限ではない。そのため、本機能の通信許可に追加しない。bubblewrap公式資料でも、`--unshare-all`はネットワーク名前空間を分離し、`--share-net`はそれを保持する指定とされている。[bubblewrap manual](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)

現時点の候補方式は次のとおりである。

1. Hostが操作直前に現在branchの単一upstreamを再検証し、明示的な`ssh://user@host[:port]/path`だけを許可してhost・port・ユーザー名・refを固定する。scp形式、暗黙ユーザー、IPリテラル、query/fragment、複数URL、`pushurl`、任意URL、別remote、ProxyJump、ProxyCommand、include、SSH設定の継承は拒否する。
2. Hostが固定済みのhost/portだけへ接続する、一操作限り・一接続だけのUnixソケット中継を作る。Sandboxはネットワーク名前空間を共有せず、この固定ソケットへしか接続できない。
3. Sandbox内のSSHは、Hostが生成した最小の設定だけを使う。ホスト鍵は対象hostの明示的な既知鍵だけを読取り専用で与え、`StrictHostKeyChecking=yes`、既知鍵の自動追加・更新なし、対話認証なし、鍵ファイル探索なしとする。OpenSSHでは`UserKnownHostsFile`と`StrictHostKeyChecking=yes`で専用の既知鍵ファイルと未知・変更鍵の拒否を指定できる。[ssh_config(5)](https://man.openbsd.org/ssh_config)
4. Agentは共有状態のまま渡さない。Hostは`SSH_AUTH_SOCK`をリモート操作へ流用せず、`FOCODEX_REMOTE_AGENT`で明示された、Host実行ユーザー所有かつgroup/other非公開の専用Unixソケットだけを候補にする。専用Agentには対象鍵だけを読み込み、利用可能なら対象hostのホスト鍵に紐付くdestination constraintを設定する。OpenSSHの`ssh-add -h`は鍵を特定宛先だけに制限できるが、対応するAgent・SSHクライアントと既知のホスト鍵が必要である。[ssh-add(1)](https://man7.org/linux/man-pages/man1/ssh-add.1.html)

この候補はまだ採用決定ではない。特に、Host側中継の接続先固定、DNSとSSHホスト鍵の扱い、専用Agentを作る運用、Sandbox内SSHへの固定ソケット接続の渡し方を、小さな読み取り専用試験で確認する必要がある。既存の共有Agentを直接Sandboxへ渡しただけで、鍵や宛先が制限されたとは扱わない。

実施済みの段階的確認として、通信なしSandbox、Host固定ループバック中継、専用AgentのNative Host継承、対象ホストの既知鍵存在、SSH URLの許可形式への限定を確認した。再起動後の専用Agent復元と固定鍵の自動登録も実装・Surface確認済みである。さらに、Focodex画面から厳格に解析した実upstreamへの`ls-remote`が成功し、安全な診断で`outcome=passed`、`wrapperStatus=exited`、`proxyStatus=connected`、中継・固定宛先接続の成功を確認した。Fetch、Pull、Pushは引き続き未実装・未実行であり、次はこの通信経路を再利用するFetch単独の設計・検証とする。最新の実機事実と制約は[読み取り専用SSH接続試験 引継ぎ記録](Focodex_読み取り専用SSH接続試験_引継ぎ記録_2026-10-24.md)を参照する。

※本書は会話で合意した運用方針を整理した設計案であり、Sandboxの安全性や実装可能性を検証済みとするものではない。
