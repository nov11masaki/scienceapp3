@echo off
REM START_REDIS.bat - Redis サーバー起動（並列処理用）
chcp 65001 >nul

echo ============================================
echo 🔴 Redis Server Launcher
echo ============================================
echo.
echo Redis は並列処理（複数生徒の同時まとめ生成）に必要です
echo.
echo ⚠️  Redis がインストールされていない場合:
echo    Windows: https://github.com/tporadowski/redis/releases
echo    （redis-x64-xxx.zip をダウンロードして展開）
echo.
echo このウィンドウは開いたままにしてください
echo ============================================
echo.

REM Redis が PATH にある場合
where redis-server >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo ✅ Redis を起動中...
    redis-server
) else (
    echo ❌ redis-server が見つかりません
    echo.
    echo 📥 Redis のインストール方法:
    echo    1. https://github.com/tporadowski/redis/releases
    echo    2. redis-x64-xxx.zip をダウンロード
    echo    3. 展開して redis-server.exe を実行
    echo.
    pause
)
