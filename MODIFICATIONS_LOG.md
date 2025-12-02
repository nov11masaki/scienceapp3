# 2025年12月2日 修正内容サマリー

## 実施内容

### 1. リポジトリクローン ✅
- GitHub `nov11masaki/scienceapp2` をクローン
- `/Users/shimizumasaki/Sciencebuddy for windows/` に展開

### 2. ngrok エンドポイント更新 ✅
- 設定ファイル: `.env`
- `NGROK_URL=https://sciencebuddy.ngrok.dev` を追加

### 3. スレッド数最適化（30 人同時接続対応） ✅

#### 修正ファイル
- `app.py`: Waitress スレッド設定を追加
- `.env`: `WAITRESS_THREADS=40` を設定

**変更内容**:
```python
# app.py（main 実行部分）
serve(
    app, 
    host='0.0.0.0', 
    port=port,
    _thread_count=threads,          # ← 新規追加
    _channel_timeout=channel_timeout,  # ← 新規追加
)
```

### 4. 500 エラー軽減対策 ✅

#### 主要な問題: OpenAI API レート制限

30 人が同時に「予想をまとめる」を押す → 30 個の OpenAI リクエストが一度に飛ぶ → OpenAI が 503 を返す → Flask が 500 に変換

#### 解決策: Semaphore によるリクエストキュー化

**修正ファイル**: `app.py`

```python
# グローバル設定
from threading import Semaphore
OPENAI_CONCURRENT_LIMIT = int(os.environ.get('OPENAI_CONCURRENT_LIMIT', 3))
openai_request_semaphore = Semaphore(OPENAI_CONCURRENT_LIMIT)

# call_openai_with_retry() の修正
def call_openai_with_retry(prompt, ...):
    # Semaphore で同時実行数を制限
    with openai_request_semaphore:
        return _call_openai_impl(prompt, ...)
```

**効果**:
- 30 人が同時に操作 → OpenAI には最大 3 個のリクエスト
- 残りはキューで待機
- 500 エラーがほぼ消える

#### その他の改善

| 項目 | 旧設定 | 新設定 | 効果 |
|------|-------|-------|------|
| タイムアウト | 30秒 | 60秒 | 遅い通信にも対応 |
| リトライ回数 | 3回 | 5回 | エラー復帰率向上 |
| コネクションプール | 未設定 | 30 | 同時接続の安定化 |
| TCP keepalive | 未設定 | 有効 | ネットワーク切断時の自動復帰 |

### 5. .env 環境変数設定 ✅

**新規追加項目**（全 40 行以上）:

```env
# スレッド設定
WAITRESS_THREADS=40
WAITRESS_CHANNEL_TIMEOUT=120

# OpenAI API
OPENAI_API_TIMEOUT=60
OPENAI_MAX_RETRIES=5
OPENAI_RETRY_DELAY_BASE=3
OPENAI_CONCURRENT_LIMIT=3

# 接続プール
URLLIB3_POOL_SIZE=30
URLLIB3_MAX_RETRIES=5

# ソケット
SOCKET_KEEPALIVE=1
SOCKET_KEEPALIVE_INTERVAL=60

# ngrok
NGROK_URL=https://sciencebuddy.ngrok.dev
```

### 6. ドキュメント作成 ✅

#### `WINDOWS_DEPLOYMENT.md`
- 30 人同時接続ガイド
- スレッド数の説明と計算方法
- 500 エラーの原因と対策（詳細図解）
- セットアップ手順
- トラブルシューティング
- パフォーマンス目安表

#### `app_windows_config.py`
- 設定値を集約したリファレンスファイル
- 本番環境デプロイ時の参考資料

---

## スレッド数についてのよくある質問

### Q: スレッド数が多いとまずいのか？

**A**: 必ずしも悪いわけではありませんが、以下の点に注意：

1. **OpenAI API のレート制限** ← **最重要**
   - スレッド数が多い = 同時 OpenAI リクエスト数が多い
   - OpenAI の 1 分間制限（3-500 requests/min）に達しやすくなる
   - **対策**: Semaphore で同時実行数を制限（修正済み）

2. **メモリ使用量増加**
   - スレッド数が増える = メモリ使用量も増える
   - 40 スレッドならほぼ無視できる

3. **接続プール不足**
   - 対策: `URLLIB3_POOL_SIZE=30` に設定（修正済み）

### Q: 30 人対応には何スレッド必要？

**A**: 
- **最小**: 30（30 人が同時に操作した場合）
- **推奨**: 40（予備・余裕を持つ）

### Q: スレッド数を 15 にしたら 500 エラーが減るのか？

**A**: 
- **直接的な効果**: ほぼなし（OpenAI 制限が根本原因）
- **副作用**: 15 人以上同時接続時に「サーバーが忙しい」エラー
- **推奨**: スレッド数は 30-40 にして、Semaphore で OpenAI 制限を設定

---

## テスト方法

### 動作確認（ローカルテスト）

```bash
# 1. app.py 起動
python app.py

# 2. ログに以下が表示されることを確認
# [INIT] OpenAI concurrent request limit set to: 3
# [INIT] Starting ScienceBuddy with:
#   - Threads: 40
#   - ...

# 3. ブラウザで http://localhost:5014 にアクセス

# 4. 複数ウィンドウで同時に「予想をまとめる」を試す
#    （30 個のリクエストが同時に飛ばない）
```

### 負荷テスト（30 人シミュレーション）

```bash
# tools/load_test.py を使用
python tools/load_test.py --url http://localhost:5014/ --concurrency 30 --requests-per-worker 10

# または k6 を使用
TARGET_URL=https://sciencebuddy.ngrok.dev k6 run tests/k6_load_test.js
```

---

## 注意事項

1. **API キーの保護**
   - `.env` ファイルは Git リポジトリに追加しないこと
   - `git add .env` を絶対にしない
   - `.gitignore` に `.env` が含まれていることを確認

2. **本番環境での設定**
   - `FLASK_ENV=production` に設定
   - ngrok URL は本番環境での アクセス先に合わせる

3. **OpenAI API の料金**
   - API 呼び出し数に応じて課金される
   - 同時実行数を 3 に制限しても、全体のリクエスト数は変わらない

---

## 次のステップ

1. **実際のテスト**
   - 教室で 30 人が実際に操作してみる
   - 500 エラーが発生するか、応答時間を計測

2. **パフォーマンス調整**
   - 必要に応じて `WAITRESS_THREADS`、`OPENAI_CONCURRENT_LIMIT` を調整

3. **エラーログ確認**
   - `ENABLE_DEBUG_LOGGING=true` で詳細ログを出力
   - 問題が発生した場合は原因を特定

---

**作成日**: 2025-12-02  
**対応者**: GitHub Copilot  
**環境**: macOS, Python 3.12, Flask 3.1.1
