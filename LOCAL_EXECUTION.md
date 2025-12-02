# ローカル実行ガイド

## 📌 現在の状態

✅ **アプリケーション起動完了**

```
[INIT] GCS bucket 'production' initialized successfully
[INIT] OpenAI concurrent request limit set to: 10
[INIT] OpenAI client initialized with model: gpt-4o-mini
[INIT] Starting ScienceBuddy with:
  - Port: 5014
  - Flask ENV: production
  - Threads: 40
  - Channel Timeout: 120s
  - ngrok URL: https://sciencebuddy.ngrok.dev
```

---

## 🌐 アクセス方法

### ローカル（このマシンから）
```
http://localhost:5014
または
http://127.0.0.1:5014
```

### ネットワーク内（同じ WiFi 接続のデバイスから）
```
http://192.168.0.100:5014
```

### 外部からアクセス（ngrok 経由）
```
https://sciencebuddy.ngrok.dev
```

---

## 📱 テスト方法

### 1. ブラウザで直接アクセス
- **URL**: http://localhost:5014
- **表示**: クラス選択画面が表示される

### 2. コマンドラインでテスト
```bash
# ヘルスチェック
curl http://localhost:5014

# API エンドポイント確認
curl http://localhost:5014/api/health
```

### 3. 複数ユーザーシミュレーション
別のターミナルで：
```bash
# 30 人同時接続テスト
python tools/load_test.py --url http://localhost:5014/ --concurrency 30 --requests-per-worker 5
```

---

## 🔧 設定確認

### 1. 環境変数の確認
```bash
cat .env | grep -E "OPENAI|WAITRESS|NGROK"
```

**出力例**:
```
OPENAI_API_TIMEOUT=60
OPENAI_MAX_RETRIES=5
OPENAI_CONCURRENT_LIMIT=10
WAITRESS_THREADS=40
WAITRESS_CHANNEL_TIMEOUT=120
NGROK_URL=https://sciencebuddy.ngrok.dev
```

### 2. ポート使用確認
```bash
lsof -i :5014
```

**出力例**:
```
COMMAND   PID      USER   FD   TYPE DEVICE SIZE NODE NAME
python 45102 shimizu   16u  IPv4  xxxx 0t0  TCP 0.0.0.0:5014 (LISTEN)
```

---

## 📊 ログ確認

### リアルタイムログ（app.py が実行中）
ターミナルに出力される以下の情報を確認：

```
[OPENAI_QUEUE] Request waiting in queue... (limit: 10)
[OPENAI_QUEUE] Request acquired, calling OpenAI API...
[OPENAI_USAGE] Model: gpt-4o-mini, Prompt tokens: 1234, ...
```

### ローカルログファイル
```bash
ls -la logs/
tail -f logs/learning_log_*.json
```

---

## 🛑 アプリケーション停止

ターミナルで `CTRL+C` を押す

```bash
^C
```

---

## ⚠️ トラブルシューティング

### エラー 1: ポート 5014 が既に使用中
```bash
# 別のプロセスがポートを使用している
lsof -i :5014

# 前回のプロセスを強制終了（必要な場合）
kill -9 <PID>
```

### エラー 2: OpenAI API キーが無効
```
[OPENAI_ERROR] attempt 1/5: Invalid API Key
```

**対策**: `.env` ファイルの `OPENAI_API_KEY` を確認

```bash
echo $OPENAI_API_KEY
# または
grep OPENAI_API_KEY .env
```

### エラー 3: Redis が起動していない
```
[INIT] Redis/RQ not available: Connection refused
```

**対策**: Redis を起動（オプション）
```bash
# Docker で起動
docker run -d -p 6379:6379 redis:7
```

### エラー 4: scikit-learn インポートエラー
```
ModuleNotFoundError: No module named 'sklearn'
```

**対策**: 依存パッケージを再インストール
```bash
pip install -r requirements.txt
```

---

## 📈 パフォーマンス測定

### 1. 単一ユーザーテスト
```bash
curl -w "\nTotal time: %{time_total}s\n" http://localhost:5014
```

### 2. 同時接続テスト（30人シミュレーション）
```bash
cd "/Users/shimizumasaki/Sciencebuddy for windows"
source .venv/bin/activate
python tools/load_test.py --url http://localhost:5014/ --concurrency 30 --requests-per-worker 10
```

### 3. レスポンスタイム計測
```bash
# API エンドポイントのレスポンスタイム
time curl http://localhost:5014/api/health
```

---

## 🚀 次のステップ

1. **ローカルテスト完了後**
   - 教室で 30 人が同時にアクセス

2. **ngrok トンネル起動**（外部アクセス用）
   ```bash
   ngrok http 5014
   ```
   表示される URL を生徒に共有

3. **ログ確認**
   - 各生徒の会話ログ: `logs/learning_log_*.json`
   - エラーログ: `logs/error_log_*.json`

4. **本番デプロイ**
   - Google Cloud Run への デプロイ
   - Cloud Storage / Firestore での永続化

---

## 📞 サポート

問題が発生した場合は以下の情報を確認してください：

1. **ポート状態**: `lsof -i :5014`
2. **環境変数**: `grep -E "OPENAI|WAITRESS" .env`
3. **Python バージョン**: `python --version`
4. **ログ出力**: app.py の全出力をコピー

---

**最終更新**: 2025-12-02  
**アプリケーション**: ScienceBuddy v3  
**環境**: macOS, Python 3.12, Flask 3.1.1
