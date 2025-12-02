# ScienceBuddy ローカル実行 - クイックガイド

## 🚀 起動方法（3通り）

### 方法 1: シェルスクリプトで起動（推奨）
```bash
cd "/Users/shimizumasaki/Sciencebuddy for windows"
./quick_start.sh
```

### 方法 2: 手動実行
```bash
cd "/Users/shimizumasaki/Sciencebuddy for windows"
source .venv/bin/activate
python app.py
```

### 方法 3: Python コマンドで起動
```bash
cd "/Users/shimizumasaki/Sciencebuddy for windows"
source .venv/bin/activate
FLASK_ENV=production WAITRESS_THREADS=40 OPENAI_CONCURRENT_LIMIT=10 python app.py
```

---

## 📌 起動ログの確認

以下のログが表示されたら成功です：

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
 * Running on http://127.0.0.1:5014
```

---

## 🌐 アクセス URL

| 場所 | URL |
|------|-----|
| このマシン | http://localhost:5014 |
| LAN 内デバイス | http://192.168.0.100:5014 |
| 外部（ngrok） | https://sciencebuddy.ngrok.dev |

---

## 📊 動作確認

### ブラウザで確認
- **URL**: http://localhost:5014 を開く
- **表示内容**: クラス選択画面

### コマンドラインで確認
```bash
# API 健全性チェック
curl http://localhost:5014

# レスポンスタイム測定
curl -w "Total: %{time_total}s\n" http://localhost:5014
```

---

## ⚙️ 設定値

| 設定項目 | 値 | 説明 |
|--------|-----|------|
| Port | 5014 | Flask アプリケーションポート |
| Threads | 40 | 同時接続数（30人対応） |
| OpenAI Limit | 10 | 同時 API リクエスト数（500 RPM対応） |
| Timeout | 60秒 | OpenAI API タイムアウト |
| Retries | 5 | リトライ回数 |

---

## 📖 詳細ドキュメント

より詳しい情報は以下を参照：

- `LOCAL_EXECUTION.md` - 詳細ガイド
- `WINDOWS_DEPLOYMENT.md` - Windows デプロイメント
- `MODIFICATIONS_LOG.md` - 修正内容
- `PUSH_COMPLETE.md` - GitHub プッシュ記録

---

## 🛑 停止方法

ターミナルで **`CTRL+C`** を押す

```
^C
Shutting down
```

---

**最終確認**: 2025-12-02
