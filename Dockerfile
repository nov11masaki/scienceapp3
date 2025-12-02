# CloudRun用 ScienceBuddy Dockerfile
# Python 3.11ベースイメージ
FROM python:3.11-slim

# CloudRunでは8080番ポートを使用
EXPOSE 8080

# 作業ディレクトリを設定
WORKDIR /app

# システムパッケージのアップデート
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Pythonの依存関係をインストール
COPY requirements.txt .
# Upgrade pip/setuptools/wheel first to avoid resolver issues and print requirements for debugging
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip --version \
    && echo "--- requirements.txt ---" \
    && cat requirements.txt \
    && echo "------------------------" \
    && pip install --no-cache-dir -r requirements.txt

# アプリケーションコードをコピー
COPY . .

# ローカルストレージディレクトリを作成
RUN mkdir -p /app/uploads && \
    mkdir -p /app/logs && \
    mkdir -p /data

# 環境変数を設定
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV LEARNING_PROGRESS_FILE=/data/learning_progress.json
ENV SESSION_STORAGE_FILE=/data/session_storage.json

# gunicornでFlaskアプリを実行
VOLUME ["/data"]

# Use gunicorn to serve the Flask app. Use exec (JSON) form to ensure signals
# are delivered correctly. We bind to port 8080 (Cloud Run sets $PORT to 8080 by
# default). If you must honor a different $PORT, consider adding an entrypoint
# script that expands environment variables before exec.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
