# 🚀 ScienceBuddy セットアップガイド（Windows）

## 📋 30人同時接続対応 - 低スペックPC向け設定

### システム要件
- **CPU**: 2コア以上（4コア推奨）
- **RAM**: 4GB以上（8GB推奨）
- **OS**: Windows 10/11
- **Python**: 3.10以上
- **インターネット**: 安定した接続

---

## 🎯 起動手順（推奨）

### 1. Redis サーバー起動
```cmd
START_REDIS.bat
```
- **別ウィンドウ** で起動します
- このウィンドウは **開いたまま** にしてください

### 2. RQワーカー起動（2個 - CPU負荷最適化）
```cmd
START_WORKERS.bat
```
- **2つのウィンドウ** が自動で開きます
- 各ウィンドウは **開いたまま** にしてください
- CPU負荷を抑えるため2個のみ（30人対応可能）

### 3. アプリ本体起動
```cmd
quick_start.bat
```
- 自動で依存パッケージをインストール
- http://localhost:5014 で起動

### 4. ngrok起動（外部公開用）
別のコマンドプロンプトで:
```cmd
ngrok http 5014 --domain=sciencebuddy.ngrok.dev
```
- 外部URL: https://sciencebuddy.ngrok.dev

---

## ⚙️ 設定値の解説（低スペックPC最適化）

| 設定項目 | 値 | 理由 |
|---------|-----|------|
| **WAITRESS_THREADS** | 45 | 30人 + 予備（管理者、静的ファイル等） |
| **RQワーカー数** | 2個 | CPU負荷を抑えつつ並列処理 |
| **OPENAI_CONCURRENT_LIMIT** | 12 | API制限（500 RPM）内で最大化 |
| **REDIS_MAX_CONNECTIONS** | 40 | スレッド数に対応 |

### パフォーマンス予測（30人が同時に「まとめる」を押す場合）

```
処理時間: 約20-30秒
成功率: 95%以上
CPU使用率: 40-60%（低スペックPCでも安定）
```

---

## 🔧 トラブルシューティング

### Redis が起動しない
```cmd
REM Redis Windowsバージョンをインストール
REM https://github.com/tporadowski/redis/releases
REM redis-x64-xxx.zip をダウンロード・展開
```

### ポートが使用中（Port 5014 already in use）
```cmd
REM プロセスを特定
netstat -ano | findstr :5014

REM プロセスを終了（PIDを確認後）
taskkill /F /PID <PID番号>
```

### CPU負荷が高すぎる場合
`.env` を編集:
```env
# ワーカー数を1個に減らす（START_WORKER.bat を1回だけ実行）
OPENAI_CONCURRENT_LIMIT=8
WAITRESS_THREADS=35
```

### 処理が遅い場合
`.env` を編集:
```env
# ワーカー数を3個に増やす（START_WORKERS.bat を手動で3回実行）
OPENAI_CONCURRENT_LIMIT=15
```

---

## 📊 動作確認

### 基本確認
1. ブラウザで http://localhost:5014 を開く
2. クラス・出席番号を選択
3. 単元を選択
4. AIと会話
5. 「まとめる」ボタンで要約生成

### 負荷テスト（オプション）
複数のブラウザウィンドウ（またはシークレットウィンドウ）で:
1. 異なる生徒番号でログイン
2. 同時に「まとめる」ボタンを押す
3. 全員が20-30秒以内に要約を取得できればOK

---

## 🛑 停止手順

1. **ngrok**: ウィンドウで `CTRL+C`
2. **アプリ本体**: ウィンドウで `CTRL+C`
3. **RQワーカー**: 各ウィンドウで `CTRL+C`
4. **Redis**: ウィンドウで `CTRL+C`

---

## 💡 ヒント

### より高速にしたい場合（高スペックPC）
`.env` を編集:
```env
WAITRESS_THREADS=60
OPENAI_CONCURRENT_LIMIT=15
```
そして `START_WORKERS.bat` を2-3回実行（合計4-6ワーカー）

### よりCPU負荷を下げたい場合
`.env` を編集:
```env
WAITRESS_THREADS=35
OPENAI_CONCURRENT_LIMIT=8
```
そして `START_WORKER.bat` を1回だけ実行（1ワーカー）

---

## 📞 サポート

問題が解決しない場合:
1. `app.log` の内容を確認
2. `ngrok.log` の内容を確認
3. Windows イベントビューアでエラーを確認
