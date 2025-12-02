# 🔬 ScienceBuddy（サイエンスバディ）

小学4年生の理科学習で「変化」に着目させるAI対話型Webアプリケーション。子どもと一緒に実験の結果を観察し、「何が変わった？」という問いから科学的思考を育みます。

---

## 📖 プロダクト概要

### 何ができるアプリか
ScienceBuddy は、小学校理科の学習過程において、子ども達が**「変化」に気づき、その理由を考え、日常と結びつける**のをAIが支援します。

- 📝 **予想段階（AI対話）**: 子どもの経験から「どう変わると思う？」という根拠を引き出す → AIが自動で予想をまとめる
- 🔍 **実験段階（教員主導）**: 児童が実際に実験を実施して「何が変わったか」を観察
- 💭 **考察段階（AI対話）**: 実際の変化と予想を比較しながら、新しい理解を形成 → AIが考察をまとめる
- 📋 **記録**: 全ての対話と学習過程をJSON形式でログに記録

### 主な改善点（2025年12月1日版）

#### 🔥 最新アップデート（2025/12/01）
- **入力検証の大幅緩和** - 短い発言でも受け入れるように改善
  - `has_substantive_content` 関数を追加（2文字以上のトークン1つでOK）
  - `/summary` と `/final_summary` の判定を緩和
  - 「もっと、いっぱい入力してください」エラーをほぼ解消
- **最終まとめ（考察）表示の修正** - 教員用学生詳細ページで考察が正しく表示されるように修正
- **FORCE_SYNC_SUMMARY サポート** - 環境変数で同期処理モードを選択可能に
- **不要ファイルの削除** - 開発中の一時ファイル（PID、ログ、クッキー）を削除し、.gitignore を更新

#### ✅ 教員用ダッシュボードの強化
- **分析ダッシュボード** - 児童の予想・考察の傾向を可視化
- **クラスタリング分析** - 児童の回答をAIが分析し、似た考えを持つグループに分類
- **インサイト抽出** - クラス全体の傾向や指導のポイントをAIが提案
- **UI改善** - ダッシュボードのレイアウトを3列表示に変更し、一覧性を向上

#### ✅ システム改善
- **404エラー修正** - ルーティング定義順序の修正により、分析ページへのアクセス問題を解決
- **不要機能の削除** - 写真アップロード機能など、現在使用していない機能を削除しコードを軽量化
- **アクセシビリティ向上** - フォーム要素のラベル付けを改善し、アクセシビリティエラーを解消
- **Redis + RQ による非同期処理** - 要約生成を非同期ジョブ化（オプション）

#### ✅ バグ修正
- **`prediction_summary_created` 未定義エラー修正** - セッションから正しく取得するように修正
- **HTMLインラインスタイル削除** - CSS ルール準拠のため外部スタイルシートに統合
- **最終まとめ表示バグ修正** - `data.summary` を `data.final_summary` に修正

#### ✅ 会話履歴の改善
- **新規開始時に古い会話を引き継がない** - `resume` パラメータで明示的に制御
- **単元変更時のセッションクリア** - 異なる単元に移動時に会話を自動リセット
- **警告ダイアログ** - まとめる前に「単元選択に戻る」を押すと確認メッセージ表示

#### ✅ まとめ機能の改善
- **ユーザー操作時のみまとめを提示** - AI が勝手にまとめを出さない、「予想をまとめる」ボタン押下時のみ
- **内容検証機能の緩和** - 非常に緩い判定基準を採用
  - 2回以上のやりとりで無条件OK
  - 1回のみでも2文字以上あればOK
  - キーワードリスト拡充（「大きく」「小さく」「温」「冷」など）
- **親切なエラーメッセージ** - 不足している内容を具体的に促す

#### ✅ UI/UX改善
- **単元選択ボタンを常時表示** - ヘッダーの右上に「単元選択に戻る」ボタンを配置
- **まとめボタンの動作改善** - まとめ後は「単元選択に戻る」ボタンのみ表示
- **CSS スタイル規格に準拠** - すべてのインラインスタイルを外部スタイルシートに移動

#### ✅ プロンプト管理
- **外部ファイル化** - `prompts/` ディレクトリに単元別プロンプトを配置
- **JSON 設定ファイル** - `prompts/initial_messages.json` で初期メッセージを管理
- **AIの勝手なまとめ禁止** - プロンプト明示：「絶対に、自動的に・勝手に・先回りして要約を出さない」

---

## 🎯 主な機能

### 👨‍🎓 学習者用機能

#### 🔐 学習の流れ
1. **クラス選択** - 1組〜4組、研究室からの選択
2. **出席番号選択** - 各クラス1〜30番からの選択（カード形式UI）
3. **単元選択** - 小学4年生理科の熱と温度関連単元から選択
4. **予想段階** - AI と対話しながら予想を立てる → ボタン押下で要約
5. **実験段階** - 教室で実験を実施
6. **考察段階** - AI と対話しながら考察をまとめる → ボタン押下で要約

#### 📱 AI対話支援（チャット形式）

**予想段階:**
- 子どもの予想を受け止める
- 理由や経験を自然に引き出す
- 温める話と冷やす話の両方を引き出す
- 十分な対話の後、ユーザーが「予想をまとめる」ボタンを押してまとめ表示

**考察段階:**
- 実験結果を受け止める
- 予想と比べる（「当たった？」「どこが違う？」）
- なぜそうなったか考える
- 日常生活とつなげる

#### �� 対話結果の要約
- **ユーザーが「まとめるボタン」を押したときだけ** 対話内容から要約を提案
- テンプレート形式に固執しない **子ども本位の要約**
- 子どもの言葉や表現を活かす（「ボコボコ」「〜みたい」など）
- 話していない内容は加えない（バリデーション機能）

#### 入力方法の多様化
- **キーボード入力**（標準）
- **50音キーボード**（ひらがな50音配列、トグル ON/OFF）
  - 濁点・半濁点ボタンによる循環入力（例: は → ば → ぱ → は）
  - あ〜わ行すべての文字に対応
  - スマートフォン・タブレット最適化
- **音声入力**（リアルタイム認識、トグル ON/OFF）
  - Web Speech API使用
  - 話しながら随時テキストに変換
  - Chrome、Edge、Safari対応

### 👩‍🏫 教員用機能
- **認証システム** - 教員専用ログイン
- **学習ログ確認** - 学生の対話プロセス詳細表示
- **学習進捗管理** - 学生ごとの学習状況把握
- **ノート写真確認** - 生徒が撮影した実験ノート写真の管理

---

## 📚 対応学習単元（小学4年生 熱と温度）

| 単元 | 主な学習内容 |
|------|-----------|
| 🔥 **金属のあたたまり方** | 金属の熱伝導と温まり方 |
| 💧 **水のあたたまり方** | 液体の対流と温まり方 |
| 💨 **空気の温度と体積** | 気体の性質と温度変化 |
| 🌡️ **水を冷やし続けた時の温度と様子** | 冷却と温度の関係 |

---

## 💡 AI対話の設計哲学

### 基本方針：「変化」に着目

#### 学習の流れ
1. **予想段階**
   - 「どう変わると思う？」と子どもの予想を引き出す
   - 過去の経験から根拠を探る
   - **ユーザーが「予想をまとめる」ボタンを押すまで対話を続ける**
   - ボタン押下後、AIが予想文を自動生成
   
2. **実験段階**
   - 教室で実際に実験を実施
   - 子どもが「何が変わったか」を観察
   
3. **考察段階**
   - 実際の変化と予想を比較する
   - 「何が変わった？」「どこが違った？」という問いで理解を深める
   - 日常生活の中で似たような「変化」を見つけさせる
   - **ユーザーが「考察をまとめる」ボタンを押すまで対話を続ける**
   - ボタン押下後、AIが考察文を自動生成

#### AI対話の姿勢
- **子どもが言ったことを最優先** - テンプレートに固執しない
- **変化に焦点を当てる** - 「どう変わった？」という問いで思考を促す
- **自然な会話** - 短く、わかりやすい言葉で、子どもと一緒に観察する感覚で対話
- **経験を深く掘り下げる** - 1つの経験から、その背景にある「変化」の理由を考えさせる
- **子どもらしい表現を尊重** - 「ボコボコ」「パンパン」などの言葉もそのまま活かす

#### 要約作成の特徴
- **ユーザーがボタンを押したときだけ要約を提示**
- **話した内容だけをまとめる** - 話していない内容は加えない
- **子どもの言葉や順序を最優先** - テンプレート形式に固執しない
- **子どもらしい表現を残す** - 「気がする」「〜みたい」などの曖昧な表現もOK

**要約例（子どもの観察を中心に）:**
- 「あたためると体積が大きくなると思う。なぜなら、夏の日にタイヤがパンパンになったから。冷やすと小さくなると思う。」
- 「やかんでお湯をわかしたとき、ボコボコしてきて湯気が出た。」

---

## 🛠️ 技術仕様

| カテゴリ | 技術スタック |
|--------|-----------|
| **バックエンド** | Flask (Python 3.12) |
| **AI** | OpenAI API (gpt-4o-mini) |
| **フロントエンド** | HTML5, CSS3, JavaScript, Bootstrap 5 |
| **ローカルストレージ** | JSON形式ログ（`logs/`、`session_storage.json`） |
| **本番環境** | Google Cloud Run, Cloud Storage, Cloud Build |
| **セッション管理** | Flask Session（ローカルでも永続化） |
| **プロンプト管理** | Markdown形式（`prompts/`ディレクトリ） |

---

## 🚀 セットアップ・起動方法

### ローカル開発環境

#### 1. リポジトリのクローン
```bash
git clone https://github.com/nov11masaki/ScienceBuddy.git
cd "ScienceBuddy(壊していい)"
```

#### 2. 仮想環境の作成（推奨）
```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# または
.venv\Scripts\activate  # Windows
```

#### 3. パッケージのインストール
```bash
pip install -r requirements.txt
```

#### 4. 環境変数の設定
`.env` ファイルを作成：
```env
OPENAI_API_KEY=your_actual_openai_api_key_here
FLASK_ENV=development
```

#### 5. アプリケーション起動
```bash
python app.py
```

#### 6. アクセス
- **学習者用**: http://localhost:5014
- **教員用**: http://localhost:5014/teacher

### 本番環境（Google Cloud Run デプロイ）

#### 前提条件
- Google Cloud Project
- Cloud Build有効化（GitHub連携）
- Cloud Storage バケット作成

#### デプロイ手順
```bash
# 1. Google Cloud CLIでプロジェクト認証
gcloud auth login
gcloud config set project PROJECT_ID

# 2. Cloud Storageバケット作成（ログ・進捗保存用）
gsutil mb gs://YOUR_BUCKET_NAME/

# 3. Cloud Runにデプロイ
gcloud run deploy science-buddy \
  --source . \
  --platform managed \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars OPENAI_API_KEY=your_api_key,BUCKET_NAME=YOUR_BUCKET_NAME,FLASK_ENV=production
```

## Docker イメージ（Waitress）

このリポジトリの `Dockerfile` は `waitress` を使って WSGI アプリを実行します。
ローカルやクラウドで Docker コンテナを起動する際に、学習進捗やセッションを永続化したい場合は
ホストのディレクトリをコンテナの `/data` にマウントしてください（`learning_progress.json` と
`session_storage.json` がそこに保存されます）。

例: ローカルでビルドしてデータを永続化して実行する

```bash
# イメージをビルド
docker build -t sciencebuddy:latest .

# ./data をコンテナの /data にマウントして起動
docker run -it --rm -p 8080:8080 -v $(pwd)/data:/data \
   -e OPENAI_API_KEY=your_openai_api_key_here \
   -e FLASK_ENV=production \
   -e PORT=8080 \
   sciencebuddy:latest
```

上の例ではコンテナ内の `/data/learning_progress.json` と `/data/session_storage.json` が
ホストの `./data` に永続化されます。デプロイ前に「予想を完了」している状態はこのファイルに
記録されるため、コンテナを再起動・再デプロイしても進捗状態は維持されます。

### 注意点
- Cloud Run のようなステートレスな環境では、コンテナローカルに書き込んでも再起動やスケールで
   データが失われます。永続化が必要な場合は Cloud Storage や Firestore など外部ストレージを利用
   してください（`FLASK_ENV=production` のときは GCS を優先する設定になっています）。

## 🔁 非同期ジョブ（要約の非同期化）

このリポジトリは RQ（Redis Queue）を使ったジョブキューのプロトタイプを含みます。要約のような
外部 API 呼び出しを伴う重い処理は Web プロセスからジョブとしてキューへ入れ、別プロセス（worker）で
処理することを推奨します。主なコンポーネント：

- `requirements.txt`: `redis`, `rq` を追加
- `tools/worker.py`: RQ worker 起動スクリプト（開発用）
- `tools/migrate_to_gcs.py`: 既存のローカル JSON を GCS に移行するためのスクリプト

### 同期処理モード（推奨：本番環境）

即時レスポンスが必要な場合は、環境変数 `FORCE_SYNC_SUMMARY=true` を設定して同期処理モードで実行できます：

```bash
# ローカルでの起動例
export FORCE_SYNC_SUMMARY=true
export PYTHONUNBUFFERED=1
gunicorn -b 0.0.0.0:8080 app:app --workers 2 --timeout 120
```

この設定により、`/summary` エンドポイントは即座に要約を返します（job_id ではなく summary を直接返す）。

### 非同期処理モード（開発・負荷分散）

ローカルでの試験運用例:

```bash
# 1) Redis を起動（Docker で簡単に立てられます）
docker run -d --name redis -p 6379:6379 redis:7

# 2) 仮想環境をアクティベートして依存をインストール
source .venv/bin/activate
pip install -r requirements.txt

# 3) Web サーバを起動（FORCE_SYNC_SUMMARY を設定しない）
export PORT=5014
python app.py

# 4) 別ターミナルで worker を起動
python tools/worker.py

# 5) Web アプリから /summary を呼ぶとジョブをキューに投入します。
#    クライアントは /summary のレスポンスで返される job_id を使って
#    /summary/status/<job_id> をポーリングして結果を取得できます。
```

### GCS への既存ファイル移行
ローカルに残っている `learning_progress.json` / `session_storage.json` / `summary_storage.json` を
GCS に移行するためのスクリプトを `tools/migrate_to_gcs.py` として用意しました。環境変数
`GCP_PROJECT_ID` と `GCS_BUCKET_NAME` を設定した上で実行してください。

```bash
export GCP_PROJECT_ID=your-project-id
export GCS_BUCKET_NAME=your-bucket
python tools/migrate_to_gcs.py
```

注意: Cloud Run 等のステートレス環境へデプロイする場合は、GCS/Firestore 等の外部ストレージを利用して
データの永続化を行ってください。



---

## 📁 ディレクトリ構造

```
ScienceBuddy/
├── app.py                           # Flaskアプリケーション本体
├── requirements.txt                 # Python依存パッケージ
├── .env                            # 環境変数（OpenAI APIキー等）
├── learning_progress.json          # 学習進捗管理ファイル
├── session_storage.json            # セッションデータ（ローカル）
├── logs/                           # 学習ログ（日付別自動生成）
│   └── learning_log_YYYYMMDD.json
├── prompts/                        # 単元別AIプロンプト
│   ├── 金属のあたたまり方.md
│   ├── 水のあたたまり方.md
│   ├── 空気の温度と体積.md
│   ├── 水を冷やし続けた時の温度と様子.md
│   └── initial_messages.json       # 初期メッセージ設定
├── tasks/                          # 単元別課題文
│   ├── 金属のあたたまり方.txt
│   ├── 水のあたたまり方.txt
│   ├── 空気の温度と体積.txt
│   └── 水を冷やし続けた時の温度と様子.txt
├── templates/                      # HTMLテンプレート
│   ├── base.html
│   ├── index.html
│   ├── select_class.html
│   ├── select_number.html
│   ├── select_unit.html
│   ├── prediction.html             # 予想段階
│   ├── experiment.html
│   ├── reflection.html             # 考察段階
│   └── teacher/                    # 教員用ページ
│       ├── login.html
│       ├── dashboard.html
│       ├── logs.html
│       └── student_detail.html
├── static/
│   └── css/style.css
└── README.md
```

---

## 🔄 セッション管理・会話保存

### ローカル環境
- **セッションデータ**: `session_storage.json` に保存
- **学習ログ**: `logs/learning_log_YYYYMMDD.json` に自動保存
- **進捗管理**: `learning_progress.json` で各学生の学習段階を記録

### 会話の復帰機能
- **新規開始時**: `resume=false` → セッション完全リセット、古い会話を引き継がない
- **ページリロード**: `resume=true` → セッション復帰、同じ単元で対話を継続
- **単元変更時**: 新しい単元に移動時に自動的にセッションをリセット

### 会話検証機能
- ユーザーが何も話していない場合 → まとめられない
- ユーザー発言が 10 文字未満 → エラーメッセージ表示
- 有意な内容（経験や理由キーワード）がない → 親切な促し

---

## �� UI/UX特徴

### デザイン
- **クリーンデザイン**: 白ベース・ライトブルーアクセントの小学生向けUI
- **直感的操作**: カード形式の選択とチャット形式の対話
- **レスポンシブデザイン**: スマートフォン・タブレット対応

### 入力エリアの最適化

#### デフォルト設定
- **50音表**: OFF（チャット画面を最大化）
- **音声入力**: OFF（キーボード操作を優先）
- **入力エリア**: コンパクト表示

#### トグル動作
- **50音表 ON**: チャット領域を縮小、キーボード表示
- **50音表 OFF**: チャット領域を拡大、コンパクトな入力エリア
- **アニメーション**: スムーズな transition（0.4s ease-out）

---

## 📊 プロンプト設計

### プロンプト管理
- すべての単元のプロンプトは `prompts/` ディレクトリに配置
- **CIIO形式**で統一：Context/Instruction/Input/Output
- AIの対話方針やまとめルールはプロンプトファイルで管理
- `app.py`では余計な指示を追加しない

### 初期メッセージ
- `prompts/initial_messages.json` で単元別の初期メッセージを管理
- JSON形式での集約化で、プロンプト変更が容易

### プロンプトの特徴
- **自動的なまとめ禁止**: 「絶対に、自動的に・勝手に・先回りして要約を出さない」
- **話した内容のみ**: 「会話に含まれていない内容は追加しないでください」
- **子どもの言葉尊重**: オノマトペや口語も活かす

---

## 🆕 最新アップデート（2025年12月1日）

### 主な改善内容
- ✅ **入力検証の大幅緩和** - 短い発言（「大きくなる」「温めた」など）も受け入れ
- ✅ **最終まとめ表示の修正** - 教員用ページで考察が正しく表示されるように修正
- ✅ **同期処理モードのサポート** - `FORCE_SYNC_SUMMARY` 環境変数で即時レスポンス
- ✅ **デバッグログ強化** - PID、環境変数の状態を出力して問題診断が容易に
- ✅ **開発ファイルの整理** - 不要な一時ファイルを削除、.gitignore を更新

---

## 🚀 クイックスタート

### ローカル環境での起動

```bash
# リポジトリのクローン
git clone https://github.com/nov11masaki/ScienceBuddy.git
cd ScienceBuddy

# 仮想環境の作成
python -m venv .venv
source .venv/bin/activate  # Windowsの場合: .venv\Scripts\activate

# 依存関係のインストール
pip install -r requirements.txt

# 環境変数の設定（.envファイルを作成）
cat > .env << EOF
OPENAI_API_KEY=your_openai_api_key_here
FLASK_ENV=development
EOF

# アプリケーションの起動
python app.py
```

その後、ブラウザで http://localhost:5014 にアクセスしてください。

### 本番環境（Cloud Run）でのセットアップ

#### 必要な環境変数
Cloud Run のシークレット管理またはサービス環境変数で以下を設定してください：

```bash
# 必須
OPENAI_API_KEY=your_openai_api_key_here
FLASK_ENV=production
GCP_PROJECT_ID=your-gcp-project-id
GCS_BUCKET_NAME=science-buddy-logs

# オプション
PORT=8080  # デフォルト: 8080 (Cloud Run推奨)
```

#### GCS バケットの準備
```bash
# バケットの作成（存在しない場合）
gsutil mb gs://science-buddy-logs/

# 保存先フォルダの準備（Cloud Run 起動時に自動作成されます）
# - gs://science-buddy-logs/logs/
# - gs://science-buddy-logs/sessions/
# - gs://science-buddy-logs/summaries/
# - gs://science-buddy-logs/error_logs/
```

#### データの永続化戦略
本番環境では以下の優先度でデータを保存します：

1. **GCS（推奨）** - コンテナ再起動時も保持
   - セッション: `gs://science-buddy-logs/sessions/{student_id}/{unit}/{stage}.json`
   - サマリー: `gs://science-buddy-logs/summaries/{student_id}/{unit}/{stage}_summary.json`
   - 学習ログ: `gs://science-buddy-logs/logs/learning_log_YYYYMMDD.json`
   - エラーログ: `gs://science-buddy-logs/error_logs/error_log_YYYYMMDD.json`

2. **ローカルストレージ** - 開発環境のみ
   - GCS が利用不可の場合のフォールバック

#### GCS 内のデータ確認

```bash
# バケット内の全ファイル一覧
gsutil ls -r gs://science-buddy-logs/

# 特定日付のログを確認
gsutil cat gs://science-buddy-logs/logs/learning_log_20251120.json

# データをローカルにダウンロード
gsutil cp -r gs://science-buddy-logs/logs/* ./logs_backup/
```

---

## 🔗 参考資料

- [Flask ドキュメント](https://flask.palletsprojects.com/)
- [OpenAI API リファレンス](https://platform.openai.com/docs/api-reference)
- [Bootstrap 5 ドキュメント](https://getbootstrap.com/docs/5.0/)
