@echo off
REM START_WORKERS.bat - RQ ワーカー複数起動（Windows低スペック最適化版）
chcp 65001 >nul

echo ============================================
echo 👷 RQ Workers Launcher (2個)
echo ============================================
echo.
echo Windows低スペックPC向け: 2個のワーカーを起動します
echo CPU負荷を抑えつつ、30人の同時処理に対応
echo.
echo 各ワーカーウィンドウを開いたままにしてください
echo ============================================
echo.

REM 仮想環境の存在確認
if not exist ".venv" (
    echo ❌ 仮想環境が見つかりません
    echo    quick_start.bat を先に実行してください
    pause
    exit /b 1
)

echo ✅ ワーカー1を起動中...
start "RQ Worker 1" cmd /k "cd /d "%CD%" && call .venv\Scripts\activate.bat && echo [Worker 1] 起動しました && python -m rq.cli worker default --url redis://localhost:6379/0"

REM 少し待機してから2つ目を起動（起動タイミングをずらす）
timeout /t 2 /nobreak >nul

echo ✅ ワーカー2を起動中...
start "RQ Worker 2" cmd /k "cd /d "%CD%" && call .venv\Scripts\activate.bat && echo [Worker 2] 起動しました && python -m rq.cli worker default --url redis://localhost:6379/0"

echo.
echo ============================================
echo ✅ 2個のRQワーカーを起動しました
echo ============================================
echo.
echo 📊 パフォーマンス予測（30人同時）:
echo   - ワーカー数: 2個
echo   - 並列度: 最大12 (OPENAI_CONCURRENT_LIMIT)
echo   - 処理時間: 約20-30秒
echo.
echo ⚠️  各ワーカーウィンドウは閉じないでください
echo 🛑  停止: 各ウィンドウで CTRL+C を押す
echo.
pause
