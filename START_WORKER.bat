@echo off
REM START_WORKER.bat - RQ ワーカー起動（並列処理用）
chcp 65001 >nul
setlocal

echo ============================================
echo 👷 RQ Worker Launcher (Single)
echo ============================================
echo.
echo このスクリプトは1つのワーカーを起動します
echo 複数起動する場合は START_WORKERS.bat を使用してください
echo.
echo このウィンドウは開いたままにしてください
echo ============================================
echo.

REM 仮想環境をアクティベート
if exist ".venv" (
    call .venv\Scripts\activate.bat
) else (
    echo ❌ 仮想環境が見つかりません
    echo    quick_start.bat を先に実行してください
    pause
    exit /b 1
)

REM RQ ワーカーを起動
echo ✅ RQ Worker を起動中...
echo.
python -m rq.cli worker default --url redis://localhost:6379/0

pause
