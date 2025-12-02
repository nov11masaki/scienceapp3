#!/bin/bash
# quick_start.sh - ScienceBuddy ローカル実行スクリプト

set -e

PROJECT_DIR="/Users/shimizumasaki/Sciencebuddy for windows"

echo "============================================"
echo "🚀 ScienceBuddy Local Launcher"
echo "============================================"
echo ""

# Step 1: 仮想環境作成
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "📦 仮想環境を作成中..."
    cd "$PROJECT_DIR"
    python -m venv .venv
    echo "✅ 仮想環境作成完了"
else
    echo "✅ 仮想環境は既に存在します"
fi

# Step 2: 依存パッケージインストール
echo ""
echo "📚 パッケージをインストール中..."
cd "$PROJECT_DIR"
source .venv/bin/activate
pip install -q -r requirements.txt 2>&1 || true
echo "✅ パッケージインストール完了"

# Step 3: 設定確認
echo ""
echo "⚙️  設定を確認中..."
echo "  - Port: 5014"
echo "  - Threads: 40"
echo "  - OpenAI Concurrent Limit: 10"
echo "  - ngrok URL: https://sciencebuddy.ngrok.dev"

# Step 4: アプリケーション起動
echo ""
echo "🎯 アプリケーション起動中..."
echo ""
echo "============================================"
echo "✅ アクセス方法:"
echo "  - ローカル: http://localhost:5014"
echo "  - LAN内: http://192.168.0.100:5014"
echo "  - 外部: https://sciencebuddy.ngrok.dev"
echo ""
echo "📖 詳細は LOCAL_EXECUTION.md を参照"
echo "🛑 停止: CTRL+C を押す"
echo "============================================"
echo ""

# アプリケーション起動
cd "$PROJECT_DIR"
source .venv/bin/activate
python app.py
