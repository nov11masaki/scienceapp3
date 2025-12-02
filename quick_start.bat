@echo off
REM quick_start.bat - ScienceBuddy Windows起動スクリプト
chcp 65001 >nul
setlocal

echo ============================================
echo 🚀 ScienceBuddy Windows Launcher
echo ============================================
echo.

REM Step 1: 仮想環境作成
if not exist ".venv" (
    echo 📦 仮想環境を作成中...
    python -m venv .venv
    echo ✅ 仮想環境作成完了
) else (
    echo ✅ 仮想環境は既に存在します
)

REM Step 2: 仮想環境をアクティベート
echo.
echo 🔧 仮想環境をアクティベート中...
call .venv\Scripts\activate.bat

REM Step 3: 依存パッケージインストール
echo.
echo 📚 パッケージをインストール中...
python -m pip install --upgrade pip >nul 2>&1
pip install -q -r requirements.txt 2>&1
echo ✅ パッケージインストール完了

REM Step 4: 設定確認
echo.
echo ⚙️  設定を確認中...
echo   - Port: 5014
echo   - Threads: 40
echo   - OpenAI Concurrent Limit: 10
echo   - ngrok URL: https://sciencebuddy.ngrok.dev

REM Step 5: アプリケーション起動
echo.
echo 🎯 アプリケーション起動中...
echo.
echo ============================================
echo ✅ アクセス方法:
echo   - ローカル: http://localhost:5014
echo   - 外部: https://sciencebuddy.ngrok.dev
echo.
echo 📖 詳細は README.md を参照
echo 🛑 停止: CTRL+C を押す
echo ============================================
echo.

REM アプリケーション起動
python app.py

pause
