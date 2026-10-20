# 接続検証記録

実施日：2026-10-20

## 実装・試験条件

- Linux、Python 3、Codex CLI 0.145.0。
- Native Messaging形式の長さ付きUTF-8 JSONをHostに渡し、app-serverとはstdioのJSONLで通信。
- 登録済みプロジェクト：`/home/tuyomori/project/focodex`。
- 初期化・スレッド開始・指示送信でモデル・サンドボックス・承認方針を上書きしない。
- 認証情報は既存のCodex側の仕組みを利用し、Focodexでは読み出し・保存しない。

## 自動試験

`python3 -m unittest discover -s tests -v`：8件成功。

- 長さ付きJSONによるプロジェクト一覧・指示送信・日本語応答・終了状態の往復、同じHostでの再実行。
- 既存設定を継承するリクエスト形式と作業ディレクトリ。
- failedをcompletedと扱わないこと。
- 承認要求を自動許可せず、未対応として終了すること。
- Codex接続断・RPCエラーを失敗として返すこと。
- 未登録プロジェクトをCodex起動前に拒否すること。
- 複数Hostの実行排他、接続断・SIGTERM時の模擬Codexプロセス回収。
- 一時ホームへのHost登録と、再登録時の既存設定保持。

JavaScriptの構文チェックも成功。模擬Codexによる試験であり、実モデルの応答成功を意味しない。

## 実Codexとの試験

`python3 scripts/probe_host.py --config /tmp/focodex-live-config.json`を実行。
送信文は「ツールを使わず、ファイルの読み書きやコマンド実行をせずに、『Focodex接続成功』とだけ返答してください。」。

1. 実行環境のサンドボックス内では、既存Codex状態DBの更新権限がなくapp-serverが起動失敗。Hostがfailedを返すことを確認。
2. 実行環境の制約を外した再試験では、初期化・スレッド作成・ターン開始・終了イベント受信まで到達。
3. 既存設定から継承されたモデル`gpt-6.1-sol`が、使用中のChatGPT認証では非対応としてサーバーに拒否された。

返却された主な情報：

- `status`: `failed`
- `text`: 空文字列
- `settings.model`: `gpt-6.1-sol`
- `settings.approvalPolicy`: `on-request`
- `settings.approvalsReviewer`: `user`
- `settings.sandbox`: `readOnly`、`networkAccess: false`
- `threadId`・`turnId`: 取得済み
- エラー：`The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.`

**Host〜実Codexの接続と失敗結果の往復は確認済み。実モデルからの正常応答は未確認。**
Codexの既存設定は変更していない。CLI側で利用可能なモデル設定を確認した上で同じ試験を再実行する。

## 未確認

- Firefoxで拡張を一時読み込みし、Hostへ接続して実モデルから正常応答を受ける全経路。
- Surface実機での動作。
- 承認・追加質問への回答と停止状態管理（次段階）。
- HostのSIGKILL・Firefoxの強制終了・スリープ復帰・子孫プロセスの残留を含む異常系。今回の通常接続断／SIGTERM試験の成功からは保証しない。

## 追加検証：Firefox内のモデル選択

同日、モデル候補の取得・選択・再取得と端末内の選択保存を追加。

- この追加検証時の実CLIは`0.163.0`。初回検証時の`0.145.0`とは異なる。
- `model/list`をNative Host経由で取得し、ページング・非表示候補の除外を確認。
- 選択モデルを`thread/start.model`へ渡す。サンドボックス・承認方針の指定とCodex設定ファイルの書き換えは行わない。
- Host自動試験は10件成功。追加2件で候補取得・ページング・明示選択・候補外モデルの拒否を確認。
- `node tests/test_probe.mjs`で、画面のモデル未選択時の送信抑止、失敗後の別モデルでの再送、指示文保持、保存選択の復元、一覧取得失敗後の再取得を確認。DOMとHostを模擬したロジック試験でありFirefox実機試験ではない。
- 実Hostで`--list-models`により候補一覧を取得。既に拒否された`gpt-6.1-sol`も一覧に含まれるため、一覧への掲載だけで利用可能とは判断しない。
- 実Hostで`python3 scripts/probe_host.py --model gpt-5.6-sol`を実行し、`status: completed`、`text: Focodex接続成功`を確認。
- この成功時の適用設定は`model: gpt-5.6-sol`、`approvalPolicy: on-request`、`approvalsReviewer: user`、`sandbox: readOnly`、`networkAccess: false`。
- 更新したHostはこのPCのFirefox向けに再登録済み。Firefoxで拡張と操作タブを再読み込みし、モデル選択から送信までを確認する作業は残っている。

これにより上記の「実モデルからの正常応答は未確認」は解消。Firefox全経路とSurface実機の確認は引き続き未実施。
