from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, Response
import openai
import os
import sys
from dotenv import load_dotenv
import json
from datetime import datetime
import csv
import time
import hashlib
import ssl
import certifi
import urllib3
import re
import glob
import uuid
import zipfile
import tempfile
from pathlib import Path
from functools import lru_cache, wraps
from werkzeug.utils import secure_filename
import numpy as np
from sklearn.cluster import KMeans
import threading
import tempfile as _tempfile
import fcntl as _fcntl
import errno as _errno
import redis as _redis
import rq as _rq
from rq.job import Job as _RQJob


# 環境変数を読み込み
load_dotenv()

# 学習進行状況管理用のファイルパス（環境変数で上書き可能）
LEARNING_PROGRESS_FILE = os.environ.get('LEARNING_PROGRESS_FILE', 'learning_progress.json')
PROMPTS_DIR = Path('prompts')

# ストレージ設定：GCS（本番環境）またはローカルJSON（開発環境）
# 本番では FLASK_ENV=production のほか Cloud Run の環境変数 (K_SERVICE) や
# 明示的なフラグ `USE_GCS=1` によって GCS を有効化できます。
USE_GCS = (
    (os.getenv('FLASK_ENV') == 'production')
    or bool(os.getenv('K_SERVICE'))
    or os.getenv('USE_GCS') == '1'
) and bool(os.getenv('GCP_PROJECT_ID'))

if USE_GCS:
    try:
        from google.cloud import storage
        gcp_project = os.getenv('GCP_PROJECT_ID')
        storage_client = storage.Client(project=gcp_project)
        bucket_name = os.getenv('GCS_BUCKET_NAME', 'science-buddy-logs')
        bucket = storage_client.bucket(bucket_name)
        # バケット接続確認
        print(f"[INIT] GCS bucket '{bucket_name}' initialized successfully")
    except Exception as e:
        print(f"[INIT] Warning: GCS initialization failed: {e}")
        USE_GCS = False
        bucket = None
else:
    bucket = None

# Firestore optional runtime storage
USE_FIRESTORE = os.getenv('USE_FIRESTORE', '0').lower() in ('1', 'true', 'yes')
FIRESTORE_DATABASE = os.getenv('FIRESTORE_DATABASE')  # e.g. 'rika' for non-default DB
if USE_FIRESTORE:
    try:
        from storage import firestore_store
        FIRESTORE_PROJECT = os.getenv('GCP_PROJECT_ID') or os.getenv('GCP_PROJECT') or None
        # create a client (may raise if credentials/project/db invalid)
        firestore_client = firestore_store.get_client(project=FIRESTORE_PROJECT, database=FIRESTORE_DATABASE)
        print(f"[INIT] Firestore initialized project={firestore_client.project} database={FIRESTORE_DATABASE or '(default)'}")
    except Exception as e:
        print(f"[INIT] Firestore init failed: {e}")
        USE_FIRESTORE = False
        firestore_client = None
else:
    firestore_client = None

# SSL証明書の設定
ssl_context = ssl.create_default_context(cafile=certifi.where())

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # 本番環境では安全なキーに変更

# ============================================================================
# OpenAI API リクエスト同時実行数制限（2025年12月2日追加）
# 30 人同時接続でも OpenAI rate limit に引っかからないようにするため
# 
# 背景：
# - Free tier / Pay-as-you-go 低スピード: 3 requests/min が上限
# - 30 人が同時に「予想をまとめる」と 30 個のリクエストが一度に飛ぶ
# - OpenAI が 503 Service Unavailable を返す → Flask が 500 エラーになる
# 
# 対策：
# - Semaphore で同時実行数を制限（OPENAI_CONCURRENT_LIMIT）
# - 超過分はキュー待ち（自動的に順序付け）
# ============================================================================
from threading import Semaphore

OPENAI_CONCURRENT_LIMIT = int(os.environ.get('OPENAI_CONCURRENT_LIMIT', 3))
openai_request_semaphore = Semaphore(OPENAI_CONCURRENT_LIMIT)

print(f"[INIT] OpenAI concurrent request limit set to: {OPENAI_CONCURRENT_LIMIT}")

# File lock and atomic write utilities to avoid concurrent write corruption.
# Uses fcntl.flock on Unix-like systems. Also provides an in-process
# threading.Lock fallback for environments without fcntl.
_file_locks = {}
_file_locks_lock = threading.Lock()


def _get_lock_for_path(path):
    """Return a threading.Lock object for the given path (process-local).
    This is used as a fallback for platforms without fcntl, and to
    serialize atomic replace operations within the same process.
    """
    with _file_locks_lock:
        lock = _file_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _file_locks[path] = lock
        return lock


def _atomic_write_json(path, data):
    """Atomically write JSON-serializable `data` to `path`.

    Implementation details:
    - Write to a temporary file in the same directory
    - fsync the file and directory to ensure durability
    - os.replace to atomically move into place
    - Use an in-process lock to avoid races within the same process
      and also use POSIX fcntl locks when available to coordinate
      between processes on the same host.
    """
    import json
    import os

    dirpath = os.path.dirname(os.path.abspath(path)) or '.'
    basename = os.path.basename(path)
    tmp = None
    lock = _get_lock_for_path(path)
    with lock:
        # create temp file in same directory
        fd, tmp = _tempfile.mkstemp(prefix=basename, dir=dirpath)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                # If fcntl available, acquire exclusive lock on temp file
                try:
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
                except Exception:
                    pass
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            # ensure directory entry is flushed
            try:
                dirfd = os.open(dirpath, os.O_DIRECTORY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except Exception:
                pass

            # atomic replace
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass


def _read_json_file(path):
    """Read JSON from path safely; returns parsed JSON or None if file missing/invalid."""
    import json
    import os

    if not os.path.exists(path):
        return None

    lock = _get_lock_for_path(path)
    with lock:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    # Try to acquire shared lock where available
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_SH)
                except Exception:
                    pass
                data = json.load(f)
                return data
        except Exception:
            return None


# 開発用: 重い要約処理を模擬するエンドポイント（POST）。
# 本番で実行しないようにするため、簡易的に開発環境でのみ有効化する。
@app.route('/debug/mock_summary', methods=['POST'])
def debug_mock_summary():
    """模擬的に時間のかかる処理をシミュレートする。
    リクエストの JSON ボディは無視され、遅延後に簡易的なJSONを返す。
    """
    # 開発環境のみ有効化
    if os.environ.get('FLASK_ENV') == 'production':
        return jsonify({'error': 'not available in production'}), 403

    import random
    import time as _time

    # シミュレート遅延: 0.6～1.2秒程度
    delay = random.uniform(0.6, 1.2)
    _time.sleep(delay)

    # 簡易レスポンス
    return jsonify({
        'status': 'ok',
        'mock_delay': round(delay, 3),
        'summary': 'これはモックの要約です（開発用）'
    })


@app.route('/debug/save_session', methods=['POST'])
def debug_save_session():
    """開発用: セッション保存を模擬するエンドポイント。
    受け取った JSON を session_storage に保存します。パラメータがない場合は
    ランダムな student_id / unit / stage を生成します。
    """
    try:
        data = request.get_json(silent=True) or {}
        import random
        student_id = data.get('student_id') or f"{random.randint(1,5)}_{random.randint(1,30)}"
        unit = data.get('unit') or f"unit_{random.randint(1,10)}"
        stage = data.get('stage') or random.choice(['prediction', 'reflection'])
        conversation = data.get('conversation') or [{'role': 'user', 'content': 'テストメッセージ'}]

        session_entry = {
            'timestamp': datetime.now().isoformat(),
            'student_id': student_id,
            'unit': unit,
            'stage': stage,
            'conversation': conversation
        }

        # 既存の保存ヘルパーを利用
        save_session_to_db(student_id, unit, stage, conversation)

        return jsonify({'status': 'ok', 'student_id': student_id, 'unit': unit, 'stage': stage})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/debug/save_progress', methods=['POST'])
def debug_save_progress():
    """開発用: 学習進捗ファイルに対して頻繁に更新を行うエンドポイント。
    リクエストボディがある場合はそれを使い、なければランダムな更新を行う。
    """
    try:
        data = request.get_json(silent=True) or {}
        import random
        class_number = data.get('class_number') or str(random.randint(1,5))
        student_number = data.get('student_number') or str(random.randint(1,30))
        unit = data.get('unit') or f"unit_{random.randint(1,10)}"

        # 進捗データを読み込み・更新（簡易）
        progress = load_learning_progress()
        student_id = f"{class_number}_{student_number}"
        if student_id not in progress:
            progress[student_id] = {}
        if unit not in progress[student_id]:
            progress[student_id][unit] = get_student_progress(class_number, student_number, unit)

        # マーク予想/考察の作成フラグをトグルする（模擬）
        current = progress[student_id][unit]
        current['stage_progress']['prediction']['summary_created'] = True
        current['stage_progress']['prediction']['last_message'] = '並行テストによる更新'

        save_learning_progress(progress)

        return jsonify({'status': 'ok', 'student_id': student_id, 'unit': unit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'md', 'txt'}

# 教員認証情報（実際の運用では環境変数やデータベースに保存）
TEACHER_CREDENTIALS = {
    "teacher": "science",  # 全クラス管理者
    "4100": "science",  # 1組担任
    "4200": "science",  # 2組担任
    "4300": "science",  # 3組担任
    "4400": "science",  # 4組担任
    "5000": "science",  # 研究室管理者
}

# 教員IDとクラスの対応
TEACHER_CLASS_MAPPING = {
    "teacher": ["class1", "class2", "class3", "class4", "lab"],  # 全クラス管理可能
    "4100": ["class1"],  # 1組のみ
    "4200": ["class2"],  # 2組のみ
    "4300": ["class3"],  # 3組のみ
    "4400": ["class4"],  # 4組のみ
    "5000": ["lab"],  # 研究室のみ
}

# 生徒IDとクラスの対応
STUDENT_CLASS_MAPPING = {
    "class1": list(range(4101, 4131)),  # 4101-4130 (1組1-30番)
    "class2": list(range(4201, 4231)),  # 4201-4230 (2組1-30番)
    "class3": list(range(4301, 4331)),  # 4301-4330 (3組1-30番)
    "class4": list(range(4401, 4431)),  # 4401-4430 (4組1-30番)
    "lab": list(range(5001, 5031)),     # 5001-5030 (研究室1-30番)
}

# ログ削除用パスワード
LOG_DELETE_PASSWORD = "RIKA"  # ログを消す際のパスワード

# 同時セッション管理用（同じアカウントの同時ログインを防止）
active_sessions = {}  # {student_id: session_id}
session_devices = {}  # {session_id: device_info}

def get_device_fingerprint():
    """デバイスフィンガープリントを生成"""
    import hashlib
    ua = request.headers.get('User-Agent', 'unknown')
    ip = request.remote_addr
    device_info = f"{ua}:{ip}"
    fingerprint = hashlib.md5(device_info.encode()).hexdigest()
    return fingerprint

def check_session_conflict(student_id):
    """同一児童IDの他セッションを検出"""
    current_device = get_device_fingerprint()
    
    if student_id in active_sessions:
        previous_session_id = active_sessions[student_id]
        previous_device = session_devices.get(previous_session_id)
        
        # 異なるデバイスからのアクセス
        if previous_device and previous_device != current_device:
            return True, previous_session_id, previous_device
    
    return False, None, None

def register_session(student_id, session_id):
    """セッションを登録"""
    device_fingerprint = get_device_fingerprint()
    active_sessions[student_id] = session_id
    session_devices[session_id] = device_fingerprint

def clear_session(session_id):
    """セッションをクリア"""
    # student_idを逆引きして削除
    for student_id, sid in list(active_sessions.items()):
        if sid == session_id:
            del active_sessions[student_id]
            break
    
    if session_id in session_devices:
        del session_devices[session_id]

def normalize_class_value(class_value):
    """クラス指定の表記ゆれを統一（lab -> '5' など）"""
    if class_value is None:
        return None
    value_str = str(class_value).strip()
    if not value_str:
        return None
    if value_str.lower() == 'lab':
        return '5'
    return value_str

def normalize_class_value_int(class_value):
    """クラス指定を整数に変換（lab も 5 として扱う）"""
    normalized = normalize_class_value(class_value)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None

# 認証チェック用デコレータ
def require_teacher_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('teacher_authenticated'):
            return redirect(url_for('teacher_login'))
        return f(*args, **kwargs)
    return decorated_function

# セッション管理機能（ブラウザ閉鎖後の復帰対応）
# デフォルトはローカルファイルだが、コンテナ環境ではボリュームにマウントした
# パスを環境変数 `SESSION_STORAGE_FILE` で指定して永続化できる。
SESSION_STORAGE_FILE = os.environ.get('SESSION_STORAGE_FILE', 'session_storage.json')

def save_session_to_db(student_id, unit, stage, conversation_data):
    """セッションデータをデータベースに保存（GCS優先、ローカルはフォールバック）"""
    session_entry = {
        'timestamp': datetime.now().isoformat(),
        'student_id': student_id,
        'unit': unit,
        'stage': stage,  # 'prediction' or 'reflection'
        'conversation': conversation_data
    }
    # Firestore 優先（環境変数で有効化されている場合）
    if USE_FIRESTORE and firestore_client:
        try:
            key = f"{student_id}_{unit}_{stage}"
            firestore_client.collection('sb_session_storage').document(key).set(session_entry)
            print(f"[SESSION_SAVE] Firestore - {key}")
            return
        except Exception as e:
            print(f"[SESSION_SAVE] Firestore failed: {e}, falling back to next storage")

    # 次に GCS を試行
    if USE_GCS and bucket:
        try:
            _save_session_gcs(session_entry)
            print(f"[SESSION_SAVE] GCS - {student_id}_{unit}_{stage}")
            return  # GCS保存成功したらローカル保存は不要
        except Exception as e:
            print(f"[SESSION_SAVE] GCS failed: {e}, falling back to local")

    # 開発環境または全失敗時: ローカル保存
    _save_session_local(session_entry)

def _save_session_local(session_entry):
    """セッションをローカルファイルに保存"""
    try:
        sessions = _read_json_file(SESSION_STORAGE_FILE) or {}
        student_id = session_entry['student_id']
        unit = session_entry['unit']
        stage = session_entry['stage']
        key = f"{student_id}_{unit}_{stage}"
        sessions[key] = session_entry
        _atomic_write_json(SESSION_STORAGE_FILE, sessions)
        print(f"[SESSION_SAVE] Local - {key}")
    except Exception as e:
        print(f"[SESSION_SAVE] Local Error: {e}")

def _save_session_gcs(session_entry):
    """セッションをGCSに保存"""
    try:
        from google.cloud import storage
        student_id = session_entry['student_id']
        unit = session_entry['unit']
        stage = session_entry['stage']
        key = f"{student_id}_{unit}_{stage}"
        
        # GCSのパス: sessions/{student_id}/{unit}/{stage}.json
        gcs_path = f"sessions/{student_id}/{unit}/{stage}.json"
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(
            json.dumps(session_entry, ensure_ascii=False, indent=2),
            content_type='application/json'
        )
        print(f"[SESSION_SAVE] GCS - {gcs_path}")
    except Exception as e:
        print(f"[SESSION_SAVE] GCS Error: {e}")

def load_session_from_db(student_id, unit, stage):
    """セッションデータをデータベースから復元（GCS優先）"""
    # 本番環境: GCS優先
    if USE_GCS and bucket:
        try:
            conversation = _load_session_gcs(student_id, unit, stage)
            if conversation is not None:
                print(f"[SESSION_LOAD] GCS - {student_id}_{unit}_{stage}")
                return conversation
        except Exception as e:
            print(f"[SESSION_LOAD] GCS failed: {e}, trying local")
    
    # 開発環境またはGCS失敗時: ローカルから読み込み
    conversation = _load_session_local(student_id, unit, stage)
    if conversation:
        print(f"[SESSION_LOAD] Local - {student_id}_{unit}_{stage}")
    return conversation

def _load_session_local(student_id, unit, stage):
    """セッションをローカルファイルから復元"""
    try:
        sessions = _read_json_file(SESSION_STORAGE_FILE)
        if not sessions:
            return []
        key = f"{student_id}_{unit}_{stage}"
        if key in sessions:
            print(f"[SESSION_LOAD] Local - {key}")
            return sessions[key].get('conversation', [])
    except Exception as e:
        print(f"[SESSION_LOAD] Local Error: {e}")
    
    return []


# -----------------------------
# Background job queue (RQ + Redis) setup
# -----------------------------
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
try:
    redis_conn = _redis.from_url(REDIS_URL)
    # Test the connection before creating the queue
    redis_conn.ping()
    rq_queue = _rq.Queue('default', connection=redis_conn)
    print(f"[INIT] Redis/RQ initialized successfully at {REDIS_URL}")
except Exception as e:
    print(f"[INIT] Redis/RQ not available: {e}. Will use synchronous processing.")
    redis_conn = None
    rq_queue = None


def perform_summary_job(conversation, unit, student_id, class_number, student_number, stage='prediction', model_override='gpt-4o-mini'):
    """Background job function: given a conversation and metadata, call OpenAI,
    extract summary, save to storage (GCS or local), update progress and logs,
    and return the summary text. This function is importable by RQ workers.
    """
    try:
        # Build messages similarly to the synchronous handler
        unit_prompt = load_unit_prompt(unit, stage='prediction')
        summary_instruction = (
            "以下の会話内容のみをもとに、児童の話した言葉や順序を活かして予想をまとめてください。"
            "児童が自分のノートにそのまま写せる、短い1〜2文にしてください。"
            "「〜と思う。なぜなら〜。」の形で、むずかしい言い回しや第三者目線は使わないでください。"
            "会話に含まれていない内容や新しい事実は追加しないでください。"
        )

        messages = [{"role": "system", "content": f"{unit_prompt}\n\n【重要】{summary_instruction}"}]
        for msg in conversation:
            messages.append({"role": msg['role'], "content": msg['content']})
        messages.append({"role": "user", "content": "これまでの話をもとに、予想をまとめてください。"})

        # Call OpenAI (existing helper)
        summary_response = call_openai_with_retry(messages, model_override=model_override, enable_cache=True, stage=stage)
        summary_text = extract_message_from_json_response(summary_response)

        # Persist summary
        _save_summary_to_db(student_id, unit, stage, summary_text)

        # Update progress and logs
        try:
            update_student_progress(class_number=class_number, student_number=student_number, unit=unit, prediction_summary_created=True)
        except Exception:
            pass

        try:
            save_learning_log(student_number=student_number, unit=unit, log_type='prediction_summary', data={'summary': summary_text, 'conversation': conversation}, class_number=class_number)
        except Exception:
            pass

        return summary_text
    except Exception as e:
        print(f"[JOB_SUMMARY] Error: {e}")
        raise

def _load_session_gcs(student_id, unit, stage):
    """セッションをGCSから復元"""
    try:
        from google.cloud import storage
        
        # GCSのパス: sessions/{student_id}/{unit}/{stage}.json
        gcs_path = f"sessions/{student_id}/{unit}/{stage}.json"
        blob = bucket.blob(gcs_path)
        
        if blob.exists():
            content = blob.download_as_string().decode('utf-8')
            data = json.loads(content)
            print(f"[SESSION_LOAD] GCS - {gcs_path}")
            return data.get('conversation', [])
    except Exception as e:
        print(f"[SESSION_LOAD] GCS Error: {e}")
    
    return None

# OpenAI APIの設定
api_key = os.getenv('OPENAI_API_KEY')
# デフォルトモデル（環境変数で変更可能）
# gpt-4o-mini: 安定した軽量モデル + プロンプトキャッシング対応
DEFAULT_OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
try:
    client = openai.OpenAI(api_key=api_key)
    print(f"[INIT] OpenAI client initialized with model: {DEFAULT_OPENAI_MODEL}")
except Exception as e:
    client = None
    print(f"[INIT] OpenAI client initialization failed: {e}")

# マークダウン記法を除去する関数
def remove_markdown_formatting(text):
    """AIの応答からマークダウン記法を除去する"""
    import re
    
    # 太字 **text** や __text__ を通常のテキストに
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    
    # 斜体 *text* や _text_ を通常のテキストに
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    
    # 箇条書きの記号を除去
    text = re.sub(r'^\s*\*\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\+\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    
    # 見出し記号 ### text を除去
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # コードブロック ```text``` を除去
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`(.*?)`', r'\1', text)
    
    # 引用記号 > を除去
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)
    
    # その他の記号の重複を整理
    text = re.sub(r'\s+', ' ', text)  # 複数の空白を1つに
    text = re.sub(r'\n\s*\n', '\n', text)  # 複数の改行を1つに
    
    return text.strip()

# 学習進行状況管理機能
def load_learning_progress():
    """学習進行状況を読み込み（ローカル JSON のみ）"""
    # ローカルファイルから読み込み
    data = _read_json_file(LEARNING_PROGRESS_FILE)
    if not data:
        return {}
    return data

def save_learning_progress(progress_data):
    """学習進行状況を保存（ローカル JSON のみ）"""
    # まず Firestore に保存（環境変数で有効化されていれば）
    if USE_FIRESTORE and firestore_client:
        try:
            # progress_data は {student_id: {unit: {...}}}
            batch = firestore_client.batch()
            count = 0
            for student_id, student_obj in progress_data.items():
                doc_ref = firestore_client.collection('sb_learning_progress').document(str(student_id))
                batch.set(doc_ref, student_obj)
                count += 1
                if count >= 500:
                    batch.commit()
                    batch = firestore_client.batch()
                    count = 0
            if count > 0:
                batch.commit()
            print(f"[PROGRESS_SAVE] Firestore: imported {len(progress_data)} student progress entries")
            return
        except Exception as e:
            print(f"[PROGRESS_SAVE] Firestore failed: {e}, falling back to local file")

    # ローカルファイルに保存（フォールバック）
    try:
        _atomic_write_json(LEARNING_PROGRESS_FILE, progress_data)
        print(f"[PROGRESS_SAVE] Local file saved successfully")
    except Exception as e:
        print(f"[PROGRESS_SAVE] Error: {e}")

def get_student_progress(class_number, student_number, unit):
    """特定の学習者の単元進行状況を取得"""
    normalized_class = normalize_class_value(class_number)
    class_number = normalized_class if normalized_class is not None else class_number
    legacy_ids = []
    if class_number == '5':
        legacy_ids.append(f"lab_{student_number}")
    student_id = f"{class_number}_{student_number}"
    progress_data = load_learning_progress()
    for legacy_id in legacy_ids:
        if legacy_id in progress_data and student_id not in progress_data:
            progress_data[student_id] = progress_data.pop(legacy_id)
            save_learning_progress(progress_data)
            break
    
    if student_id not in progress_data:
        progress_data[student_id] = {}
    
    if unit not in progress_data[student_id]:
        progress_data[student_id][unit] = {
            "current_stage": "prediction",
            "last_access": datetime.now().isoformat(),
            "stage_progress": {
                "prediction": {
                    "started": False,
                    "conversation_count": 0,
                    "summary_created": False,
                    "last_message": ""
                },
                "experiment": {
                    "started": False,
                    "completed": False
                },
                "reflection": {
                    "started": False,
                    "conversation_count": 0,
                    "summary_created": False
                }
            },
            "conversation_history": [],
            "reflection_conversation_history": []
        }
    
    return progress_data[student_id][unit]

def update_student_progress(class_number, student_number, unit, prediction_summary_created=False, reflection_summary_created=False):
    """学習者の進行状況を更新（フラグのみ保存）"""
    normalized_class = normalize_class_value(class_number)
    class_number = normalized_class if normalized_class is not None else class_number
    progress_data = load_learning_progress()
    student_id = f"{class_number}_{student_number}"
    
    # 現在の進行状況を取得
    current_progress = get_student_progress(class_number, student_number, unit)
    
    # 予想・考察の完了フラグのみ更新
    if prediction_summary_created:
        current_progress["stage_progress"]["prediction"]["summary_created"] = True
    if reflection_summary_created:
        current_progress["stage_progress"]["reflection"]["summary_created"] = True
    
    # 進行状況を保存
    if student_id not in progress_data:
        progress_data[student_id] = {}
    progress_data[student_id][unit] = current_progress
    
    save_learning_progress(progress_data)
    return current_progress


def check_resumption_needed(class_number, student_number, unit):
    """復帰が必要かチェック（現在は常にFalse。セッションリセット方針のため）"""
    # ページリロード時はセッションがリセットされるため、復帰は不要
    return False

def get_progress_summary(progress):
    """進行状況の要約を生成"""
    stage_progress = progress.get('stage_progress', {})
    
    # 考察完了が最優先
    if stage_progress.get('reflection', {}).get('summary_created', False):
        return "考察完了"
    
    # 予想完了
    if stage_progress.get('prediction', {}).get('summary_created', False):
        return "予想完了"
    
    return "未開始"

def extract_message_from_json_response(response):
    """JSON形式のレスポンスから純粋なメッセージを抽出する"""
    try:
        # JSON形式かどうか確認
        if response.strip().startswith('{') and response.strip().endswith('}'):
            import json
            parsed = json.loads(response)
            
            # よくあるフィールド名から順番に確認
            common_fields = ['response', 'message', 'question', 'summary', 'text', 'content', 'answer']
            
            for field in common_fields:
                if field in parsed and isinstance(parsed[field], str):
                    return parsed[field]
            
            # その他のフィールドから文字列値を探す
            for key, value in parsed.items():
                if isinstance(value, str) and len(value.strip()) > 0:
                    return value
                    
            # JSONだが適切なフィールドがない場合はそのまま返す
            return response
                
        # リスト形式の場合の処理
        elif response.strip().startswith('[') and response.strip().endswith(']'):
            import json
            parsed = json.loads(response)
            if isinstance(parsed, list) and len(parsed) > 0:
                # リストの各要素を処理
                results = []
                for item in parsed:
                    if isinstance(item, dict):
                        # よくあるフィールド名から順番に確認
                        common_fields = ['予想', 'response', 'message', 'question', 'summary', 'text', 'content']
                        found = False
                        for field in common_fields:
                            if field in item and isinstance(item[field], str):
                                results.append(item[field])
                                found = True
                                break
                        
                        # よくあるフィールドが見つからない場合は最初の文字列値を使用
                        if not found:
                            for key, value in item.items():
                                if isinstance(value, str) and len(value.strip()) > 0:
                                    results.append(value)
                                    break
                    elif isinstance(item, str):
                        results.append(item)
                
                # 複数の予想を改行で結合
                if results:
                    return '\n'.join(results)
            return response
            
        # JSON形式でない場合はそのまま返す
        else:
            return response
            
    except (json.JSONDecodeError, Exception) as e:
        return response


def has_substantive_content(text):
    """短い文字数判定ではなく、意味のある発言かを判定する軽量ヘルパー。
    
    非常に緩い基準を採用：
    - 長さ2文字以上のトークンが1つでもあればOK
    - キーワード（観察・経験・理由を示す語）を含む場合も有意と判断
    - 空文字や意味のない1文字の羅列のみを除外
    """
    try:
        import re
        if not text or len(text.strip()) == 0:
            return False

        # 代表的な日本語の経験/理由を示す語句（簡易）
        keywords = [
            'あった', 'あります', '見た', '見ました', '思う', '思います',
            'なった', 'になった', 'だから', 'ため', 'ことが', '見', 'できた', 
            '変わ', '気づ', '観察', '理由', '大きく', '小さく', '温', '冷',
            'なる', 'ます', 'です', 'から', 'ので'
        ]
        for k in keywords:
            if k in text:
                return True

        # 記号等をスペースに置換してトークン化
        cleaned = re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff]", ' ', text)
        tokens = [t for t in re.split(r'\s+', cleaned) if t and len(t) >= 2]
        # 長さ2以上のトークンが1つでもあればOK
        if len(tokens) >= 1:
            return True

        return False
    except Exception:
        return False

# APIコール用のリトライ関数
def call_openai_with_retry(prompt, max_retries=5, delay=3, unit=None, stage=None, model_override=None, enable_cache=False, temperature=None):
    """OpenAI APIを呼び出し、エラー時はリトライする
    
    Args:
        prompt: 文字列またはメッセージリスト
        max_retries: リトライ回数 (デフォルト 5: デザリング環境向けに増加)
        delay: リトライ間隔（秒、デフォルト 3: より長い待機時間）
        unit: 単元名
        stage: 学習段階
        model_override: モデルオーバーライド
        enable_cache: プロンプトキャッシング有効化（システムメッセージに対して有効）
        temperature: 生成の多様性パラメータ (指定がない場合はstageから自動決定)
    
    改善点:
    - Windows/デザリング環境での通信エラーに対応するため、timeout を 60秒に延長
    - リトライ回数を 5 回に増加し、指数バックオフで待機
    - Semaphore で同時実行数を制限（OpenAI rate limit 回避）
    - 500番台エラーをより詳細に記録・診断
    """
    if client is None:
        return "AI システムの初期化に問題があります。管理者に連絡してください。"
    
    # ========================================================================
    # Semaphore: OpenAI API への同時リクエスト数を制限
    # （30 人同時接続でも rate limit に引っかからないようにするため）
    # ========================================================================
    print(f"[OPENAI_QUEUE] Request waiting in queue... (limit: {OPENAI_CONCURRENT_LIMIT})")
    with openai_request_semaphore:
        print(f"[OPENAI_QUEUE] Request acquired, calling OpenAI API...")
        return _call_openai_impl(prompt, max_retries, delay, unit, stage, model_override, enable_cache, temperature)


def _call_openai_impl(prompt, max_retries=5, delay=3, unit=None, stage=None, model_override=None, enable_cache=False, temperature=None):
    """Internal OpenAI API caller (called within Semaphore context)"""
    if client is None:
        return "AI システムの初期化に問題があります。管理者に連絡してください。"
    
    # promptがリストの場合（メッセージフォーマット）
    if isinstance(prompt, list):
        messages = prompt.copy()  # 元のリストを変更しないようにコピー
    else:
        # promptが文字列の場合（従来フォーマット）
        messages = [{"role": "user", "content": prompt}]
    
    # キャッシング有効時、システムメッセージにキャッシュ制御を追加
    # OpenAI Prompt Cachingはシステムメッセージの再利用でInput tokensを50%削減
    if enable_cache:
        for i, msg in enumerate(messages):
            if msg.get('role') == 'system' and 'cache_control' not in msg:
                # 元のメッセージを変更せず、新しい辞書を作成
                messages[i] = {
                    **msg,
                    'cache_control': {'type': 'ephemeral'}
                }
    
    for attempt in range(max_retries):
        try:
            import time
            start_time = time.time()
            
            # temperatureが指定されていない場合、stage（学習段階）に応じて設定
            if temperature is None:
                # 予想段階: より創造的で多様な回答 (1.0)
                # 考察段階: より創造的で多様な回答 (1.0) - 実験後の新しい気づきを促す
                if stage == 'prediction':
                    temperature = 1.0
                elif stage == 'reflection':
                    temperature = 1.0  # 実験結果との比較から新しい視点を引き出すため
                else:
                    temperature = 0.5  # デフォルト
            
            # モデル選択: model_override > DEFAULT_OPENAI_MODEL > gpt-4o-mini
            model_name = model_override if model_override else DEFAULT_OPENAI_MODEL
            
            # プロンプトキャッシングの状態をログ出力
            cache_enabled = any(msg.get('cache_control') for msg in messages)
            if cache_enabled:
                print(f"[OPENAI_CACHE] Prompt caching enabled for model: {model_name}")

            # モデルによってトークン制限パラメータを切り替え
            # gpt-4o-2024-08-06以降のモデルはmax_completion_tokensを使用
            token_param = {}
            if 'o1' in model_name or '2024-08' in model_name or '2025' in model_name:
                token_param['max_completion_tokens'] = 2000
            else:
                token_param['max_tokens'] = 2000

            # タイムアウトをデザリング環境向けに拡張（60秒）
            openai_timeout = int(os.environ.get('OPENAI_API_TIMEOUT', 60))
            
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                timeout=openai_timeout,
                **token_param
            )
            
            # トークン使用状況とキャッシュヒット率をログ出力
            if hasattr(response, 'usage'):
                usage = response.usage
                # キャッシュトークン数を取得（prompt_tokens_detailsはオブジェクトまたは辞書）
                cached_tokens = 0
                if hasattr(usage, 'prompt_tokens_details'):
                    details = usage.prompt_tokens_details
                    if hasattr(details, 'cached_tokens'):
                        cached_tokens = details.cached_tokens
                    elif isinstance(details, dict):
                        cached_tokens = details.get('cached_tokens', 0)
                
                print(f"[OPENAI_USAGE] Model: {model_name}, "
                      f"Prompt tokens: {getattr(usage, 'prompt_tokens', 'N/A')}, "
                      f"Completion tokens: {getattr(usage, 'completion_tokens', 'N/A')}, "
                      f"Total: {getattr(usage, 'total_tokens', 'N/A')}, "
                      f"Cached tokens: {cached_tokens}")
            
            if response.choices and response.choices[0].message.content:
                content = response.choices[0].message.content
                # マークダウン除去を削除（MDファイルのプロンプトに従う）
                return content
            else:
                raise Exception("空の応答が返されました")
                
        except Exception as e:
            error_msg = str(e)
            
            print(f"[OPENAI_ERROR] attempt {attempt + 1}/{max_retries}: {error_msg}")
            print(f"[OPENAI_ERROR] Full exception type: {type(e).__name__}")
            import traceback
            print(f"[OPENAI_ERROR] Traceback: {traceback.format_exc()}")
            
            if "API_KEY" in error_msg.upper() or "invalid_api_key" in error_msg.lower():
                return "APIキーの設定に問題があります。管理者に連絡してください。"
            elif "QUOTA" in error_msg.upper() or "LIMIT" in error_msg.upper() or "rate_limit_exceeded" in error_msg.lower():
                return "API利用制限に達しました。しばらく待ってから再度お試しください。"
            elif "TIMEOUT" in error_msg.upper() or "DNS" in error_msg.upper() or "503" in error_msg:
                if attempt < max_retries - 1:
                    wait_time = delay * (attempt + 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return "ネットワーク接続に問題があります。インターネット接続を確認してください。"
            elif "400" in error_msg or "INVALID" in error_msg.upper():
                print(f"[OPENAI_ERROR] 400/INVALID error detected, raw error: {e}")
                return "リクエストの形式に問題があります。管理者に連絡してください。"
            elif "403" in error_msg or "PERMISSION" in error_msg.upper():
                return "APIの利用権限に問題があります。管理者に連絡してください。"
            else:
                if attempt < max_retries - 1:
                    wait_time = delay * (attempt + 1)
                    time.sleep(wait_time)
                    continue
                else:
                    return f"予期しないエラーが発生しました: {error_msg[:100]}..."
                    
    return "複数回の試行後もAPIに接続できませんでした。しばらく待ってから再度お試しください。"

# 学習単元のデータ
UNITS = [
    "金属のあたたまり方",
    "水のあたたまり方",
    "空気の温度と体積",
    "水を冷やし続けた時の温度と様子"
]

# 課題文を読み込む関数
def load_task_content(unit_name):
    try:
        with open(f'tasks/{unit_name}.txt', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return f"{unit_name}について実験を行います。どのような結果になると予想しますか？"

INITIAL_MESSAGES_FILE = PROMPTS_DIR / 'initial_messages.json'

@lru_cache(maxsize=1)
def _load_initial_messages():
    try:
        with open(INITIAL_MESSAGES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[INIT_MSG] Warning: {INITIAL_MESSAGES_FILE} not found.")
        return {}
    except json.JSONDecodeError as e:
        print(f"[INIT_MSG] JSON decode error: {e}")
        return {}


def get_initial_ai_message(unit_name, stage='prediction'):
    """初期メッセージを取得する"""
    messages = _load_initial_messages()
    stage_messages = messages.get(stage, {})
    message = stage_messages.get(unit_name)
    
    if not message:
        default_template = stage_messages.get('_default')
        if default_template:
            message = default_template.replace('{{unit}}', unit_name)
    
    if not message:
        if stage == 'prediction':
            message = f"{unit_name}について、どう思う？"
        elif stage == 'reflection':
            message = "実験でどんな結果になった？"
        else:
            message = "あなたの考えを聞かせてください。"
    
    return message

# 単元ごとのプロンプトを読み込む関数
def load_unit_prompt(unit_name, stage=None):
    """単元専用のプロンプトファイルを読み込む
    
    Args:
        unit_name: 単元名
        stage: 学習段階 ('prediction' または 'reflection')
    """
    try:
        # stageが指定されている場合、段階別プロンプトを読み込む
        if stage:
            stage_suffix = "_prediction" if stage == "prediction" else "_reflection"
            prompt_path = PROMPTS_DIR / f"{unit_name}{stage_suffix}.md"
        else:
            # 従来の単一プロンプトにフォールバック
            prompt_path = PROMPTS_DIR / f"{unit_name}.md"
        
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return "児童の発言をよく聞いて、適切な質問で考えを引き出してください。"

def load_prompt_template(filename):
    """汎用テンプレートを読み込み"""
    try:
        template_path = PROMPTS_DIR / filename
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print(f"[PROMPTS] Warning: template '{filename}' not found")
        return ""

def render_prompt_template(template: str, **placeholders):
    """テンプレート内の{{KEY}}を置換"""
    rendered = template
    for key, value in placeholders.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value) if value is not None else "")
    return rendered


# 学習ログを保存する関数
def save_learning_log(student_number, unit, log_type, data, class_number=None):
    """学習ログをGCSまたはローカルJSONに保存
    
    Args:
        student_number: 生徒番号 (例: "4103"=1組3番, "5015"=研究室15番) または出席番号
        unit: 単元名
        log_type: ログタイプ
        data: ログデータ
        class_number: クラス番号 (例: "1", "2") - 省略時は student_number から自動解析
    """
    class_number = normalize_class_value(class_number) or class_number
    # parse_student_info を使って正しくパースする
    parsed_info = parse_student_info(student_number)
    
    if parsed_info:
        # 生徒番号から自動解析できた場合
        class_num = parsed_info['class_num']
        seat_num = parsed_info['seat_num']
        class_display = parsed_info['display']
    else:
        # 従来の方法（class_numberから）
        try:
            class_num = int(class_number) if class_number else None
            seat_num = int(student_number) if student_number else None
            if class_num and seat_num:
                class_display = f'{class_num}組{seat_num}番'
            else:
                class_display = str(student_number)
        except (ValueError, TypeError):
            class_num = None
            seat_num = None
            class_display = str(student_number)
    
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'student_number': student_number,
        'class_num': class_num,
        'seat_num': seat_num,
        'class_display': class_display,
        'unit': unit,
        'log_type': log_type,
        'data': data
    }
    
    if USE_GCS and bucket:
        # 本番環境: GCSにも保存する（ローカル保存は必ず実施）
        try:
            log_date = datetime.now().strftime('%Y%m%d')
            log_filename = f"logs/learning_log_{log_date}.json"
            
            print(f"[LOG_SAVE] GCS START - path: {log_filename}, class: {class_display}, unit: {unit}, type: {log_type}")
            
            blob = bucket.blob(log_filename)
            logs = []
            try:
                content = blob.download_as_string()
                logs = json.loads(content.decode('utf-8'))
            except Exception:
                logs = []
            
            logs.append(log_entry)
            
            blob.upload_from_string(
                json.dumps(logs, ensure_ascii=False, indent=2).encode('utf-8'),
                content_type='application/json'
            )
            print(f"[LOG_SAVE] GCS SUCCESS - saved to GCS (local copy will also be written)")
        except Exception as e:
            print(f"[LOG_SAVE] GCS ERROR - {type(e).__name__}: {str(e)}, continue with local save")
            import traceback
            traceback.print_exc()
    
    # ローカルファイルにも必ず保存
    log_filename = f"learning_log_{datetime.now().strftime('%Y%m%d')}.json"
    os.makedirs('logs', exist_ok=True)
    log_file = f"logs/{log_filename}"
    
    logs = []
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logs = []
    
    logs.append(log_entry)
    
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

# 学習ログを読み込む関数
def load_learning_logs(date=None):
    """指定日の学習ログを読み込み（GCS優先）"""
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    
    # 本番環境: GCS優先
    if USE_GCS and bucket:
        # GCS から読み込み
        try:
            log_filename = f"logs/learning_log_{date}.json"
            print(f"[LOG_LOAD] GCS START - loading logs from: {log_filename}")
            
            blob = bucket.blob(log_filename)
            try:
                content = blob.download_as_string()
                logs = json.loads(content.decode('utf-8'))
                log_count = len(logs)
                print(f"[LOG_LOAD] GCS SUCCESS - loaded {log_count} logs from {date}")
                return logs
            except Exception as e:
                print(f"[LOG_LOAD] GCS file not found: {log_filename}")
        except Exception as e:
            print(f"[LOG_LOAD] GCS ERROR - {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
    
    # 開発環境またはGCS失敗時: ローカルファイルから読み込み
    log_filename = f"learning_log_{date}.json"
    log_file = f"logs/{log_filename}"
    
    if not os.path.exists(log_file):
        return []
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def get_available_log_dates():
    """利用可能な全ログの日付リストを取得"""
    import glob
    import os
    
    # ローカルファイル
    dates = []
    log_files = glob.glob("logs/learning_log_*.json")
    for file in log_files:
        filename = os.path.basename(file)
        if filename.startswith('learning_log_') and filename.endswith('.json'):
            date_str = filename[13:-5]
            if len(date_str) == 8 and date_str.isdigit():
                dates.append(date_str)
    
    dates.sort(reverse=True)  # 新しい順
    print(f"[DATES] Found {len(dates)} log dates: {dates[:5]}")
    
    return dates

# エラーログ管理機能
def save_error_log(student_number, class_number, error_message, error_type, stage, unit, additional_info=None):
    """児童のエラーをログに記録
    
    Args:
        student_number: 出席番号
        class_number: クラス番号
        error_message: エラーメッセージ
        error_type: エラータイプ ('api_error', 'network_error', 'validation_error', etc)
        stage: 学習段階 ('prediction', 'reflection', etc)
        unit: 単元名
        additional_info: 追加情報 (dict)
    """
    try:
        normalized_class = normalize_class_value(class_number) or class_number
        class_num = int(normalized_class) if normalized_class else None
        seat_num = int(student_number) if student_number else None
        class_display = f'{class_num}組{seat_num}番' if class_num and seat_num else str(student_number)
    except (ValueError, TypeError):
        class_display = str(student_number)
    
    error_entry = {
        'timestamp': datetime.now().isoformat(),
        'student_number': student_number,
        'class_number': class_number,
        'class_display': class_display,
        'error_message': error_message,
        'error_type': error_type,
        'stage': stage,
        'unit': unit,
        'additional_info': additional_info or {}
    }
    
    # GCSに保存
    if USE_GCS and bucket:
        try:
            _save_error_log_gcs(error_entry)
            print(f"[ERROR_LOG] GCS saved - {class_display}")
        except Exception as e:
            print(f"[ERROR_LOG] GCS save failed: {e}")
    
    # ローカルにも保存
    try:
        _save_error_log_local(error_entry)
        print(f"[ERROR_LOG] Local saved - {class_display}")
    except Exception as e:
        print(f"[ERROR_LOG] Local save failed: {e}")

def _save_error_log_local(error_entry):
    """エラーログをローカルファイルに保存"""
    os.makedirs('logs', exist_ok=True)
    error_log_file = f"logs/error_log_{datetime.now().strftime('%Y%m%d')}.json"
    
    logs = []
    if os.path.exists(error_log_file):
        try:
            with open(error_log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logs = []
    
    logs.append(error_entry)
    
    with open(error_log_file, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

def _save_error_log_gcs(error_entry):
    """エラーログをGCSに保存"""
    try:
        from google.cloud import storage
        
        date = datetime.now().strftime('%Y%m%d')
        gcs_path = f"error_logs/error_log_{date}.json"
        blob = bucket.blob(gcs_path)
        
        # 既存のエラーログを読み込み
        logs = []
        if blob.exists():
            try:
                content = blob.download_as_string().decode('utf-8')
                logs = json.loads(content)
            except:
                logs = []
        
        # 新しいエラーを追加
        logs.append(error_entry)
        
        # GCSに保存
        blob.upload_from_string(
            json.dumps(logs, ensure_ascii=False, indent=2),
            content_type='application/json'
        )
        
        print(f"[ERROR_LOG_GCS] {gcs_path} saved")
    except Exception as e:
        print(f"[ERROR_LOG_GCS] Error: {e}")

def load_error_logs(date=None):
    """エラーログを読み込み（GCS優先）"""
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    
    # 本番環境: GCS優先
    if USE_GCS and bucket:
        try:
            gcs_path = f"error_logs/error_log_{date}.json"
            blob = bucket.blob(gcs_path)
            
            if blob.exists():
                content = blob.download_as_string().decode('utf-8')
                logs = json.loads(content)
                print(f"[ERROR_LOAD] GCS - {gcs_path} loaded ({len(logs)} entries)")
                return logs
            else:
                print(f"[ERROR_LOAD] GCS - {gcs_path} not found, trying local")
        except Exception as e:
            print(f"[ERROR_LOAD] GCS Error: {e}, trying local")
    
    # 開発環境またはGCS失敗時: ローカルから読み込み
    error_log_file = f"logs/error_log_{date}.json"
    if not os.path.exists(error_log_file):
        return []
    
    try:
        with open(error_log_file, 'r', encoding='utf-8') as f:
            logs = json.load(f)
            print(f"[ERROR_LOAD] Local - {error_log_file} loaded ({len(logs)} entries)")
            return logs
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def perform_clustering_analysis(unit_logs, unit_name, class_num):
    """学生の対話をエンベディング＆クラスタリング分析
    
    Args:
        unit_logs: 単元のログ一覧
        unit_name: 単元名
        class_num: クラス番号
    
    Returns:
        dict: クラスタリング結果
    """
    try:
        print(f"[CLUSTERING] Starting analysis for {class_num}_{unit_name}")
        
        # 予想と考察を分離
        prediction_logs = [l for l in unit_logs if l.get('log_type') == 'prediction_chat']
        reflection_logs = [l for l in unit_logs if l.get('log_type') == 'reflection_chat']
        
        clustering_results = {}
        
        for phase_name, phase_logs in [('予想段階', prediction_logs), ('考察段階', reflection_logs)]:
            if not phase_logs:
                clustering_results[phase_name] = {'clusters': [], 'message': f'{phase_name}のデータがありません'}
                continue
            
            # 学生ごとに対話をグループ化
            student_messages = {}
            for log in phase_logs:
                student_id = log.get('student_number', '不明')
                msg = log.get('data', {}).get('user_message', '')
                if msg:
                    if student_id not in student_messages:
                        student_messages[student_id] = []
                    student_messages[student_id].append(msg)
            
            if not student_messages:
                clustering_results[phase_name] = {'clusters': [], 'message': 'テキストデータがありません'}
                continue
            
            # 各学生のテキストをまとめる
            student_ids = list(student_messages.keys())
            student_texts = [' '.join(student_messages[sid]) for sid in student_ids]
            
            print(f"[CLUSTERING] Getting embeddings for {len(student_ids)} students...")
            
            # OpenAI Embedding API を使用
            client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
            embeddings_response = client.embeddings.create(
                input=student_texts,
                model="text-embedding-3-small"
            )
            
            embeddings = np.array([e.embedding for e in embeddings_response.data])
            
            # クラスタ数を決定（学生数に基づいて、最大5クラスタ）
            n_clusters = min(max(2, len(student_ids) // 3), 5)
            
            print(f"[CLUSTERING] Performing KMeans clustering with {n_clusters} clusters...")
            
            # クラスタリング実行
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            cluster_labels = kmeans.fit_predict(embeddings)
            
            # クラスタごとに学生をグループ化
            clusters = {}
            for i, (student_id, label) in enumerate(zip(student_ids, cluster_labels)):
                if label not in clusters:
                    clusters[label] = {'students': [], 'sample_texts': []}
                clusters[label]['students'].append(student_id)
                clusters[label]['sample_texts'].append(student_texts[i][:200])
            
            clustering_results[phase_name] = {
                'clusters': [
                    {
                        'cluster_id': cid,
                        'students': clusters[cid]['students'],
                        'student_count': len(clusters[cid]['students']),
                        'sample_text': clusters[cid]['sample_texts'][0] if clusters[cid]['sample_texts'] else ''
                    }
                    for cid in sorted(clusters.keys())
                ]
            }
            
            print(f"[CLUSTERING] {phase_name}: {len(clusters)} clusters created")
        
        return clustering_results
    
    except Exception as e:
        print(f"[CLUSTERING] Error: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            '予想段階': {'clusters': [], 'error': str(e)},
            '考察段階': {'clusters': [], 'error': str(e)}
        }

def parse_student_info(student_number):
    """生徒番号からクラスと出席番号を取得
    
    Args:
        student_number: 生徒番号 (str) 例: "4103" = 4年1組3番, "5015" = 研究室5組15番
    
    Returns:
        dict: {'class_num': 1, 'seat_num': 3, 'display': '1組3番'} または None
    """
    try:
        if student_number == '1111':
            return {'class_num': 0, 'seat_num': 0, 'display': 'テスト'}
        
        student_str = str(student_number)
        if len(student_str) == 4:
            prefix = student_str[0]
            
            # 4年生（1-4組）
            if prefix == '4':
                class_num = int(student_str[1])  # 2桁目がクラス番号
                seat_num = int(student_str[2:])  # 3-4桁目が出席番号
                return {
                    'class_num': class_num,
                    'seat_num': seat_num,
                    'display': f'{class_num}組{seat_num}番'
                }
            
            # 研究室（5組）
            elif prefix == '5':
                class_num = 5  # 研究室は5組（ログ表示は通常クラスと同様）
                seat_num = int(student_str[1:])  # 後ろ3桁が出席番号
                return {
                    'class_num': class_num,
                    'seat_num': seat_num,
                    'display': f'{class_num}組{seat_num}番'
                }
        
        return None
    except (ValueError, TypeError):
        return None

def get_teacher_classes(teacher_id):
    """教員IDから管理可能なクラス一覧を取得
    
    Args:
        teacher_id: 教員ID
    
    Returns:
        クラス名のリスト ["class1", "class2", ...]
    """
    return TEACHER_CLASS_MAPPING.get(teacher_id, [])

@app.route('/api/test')
def api_test():
    """API接続テスト"""
    try:
        test_prompt = "こんにちは。短い挨拶をお願いします。"
        response = call_openai_with_retry(test_prompt, max_retries=1)
        return jsonify({
            'status': 'success',
            'message': 'API接続テスト成功',
            'response': response
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'API接続テスト失敗: {str(e)}'
        }), 500

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/select_class')
def select_class():
    return render_template('select_class.html')

@app.route('/select_number')
def select_number():
    class_number = request.args.get('class', '1')
    class_number = normalize_class_value(class_number) or '1'
    # 5組（研究室）はパスワード必須
    if class_number == '5':
        provided = request.args.get('pass')
        if provided != 'RIKA':
            flash('5組（研究室）に入るにはパスワードが必要です。', 'danger')
            return redirect(url_for('select_class'))
    return render_template('select_number.html', class_number=class_number)

@app.route('/select_unit')
def select_unit():
    class_number = request.args.get('class', '1')
    class_number = normalize_class_value(class_number) or '1'
    student_number = request.args.get('number')
    session['class_number'] = class_number
    session['student_number'] = student_number
    
    # 同時セッション競合チェック
    student_id = f"{class_number}_{student_number}"
    has_conflict, previous_session_id, previous_device = check_session_conflict(student_id)
    
    if has_conflict:
        # 前のセッションをクリア
        clear_session(previous_session_id)
        flash(f'別の端末でこのアカウントがアクセスされたため、前のセッションを終了しました。', 'warning')
    
    # 現在のセッションを登録（セッションIDを生成）
    session_id = str(uuid.uuid4())
    session['_session_id'] = session_id
    register_session(student_id, session_id)
    
    # 各単元の進行状況をチェック
    unit_progress = {}
    for unit in UNITS:
        progress = get_student_progress(class_number, student_number, unit)
        needs_resumption = check_resumption_needed(class_number, student_number, unit)
        stage_progress = progress.get('stage_progress', {})
        
        # 各段階の状態を取得
        prediction_started = stage_progress.get('prediction', {}).get('started', False)
        prediction_summary_created = stage_progress.get('prediction', {}).get('summary_created', False)
        experiment_started = stage_progress.get('experiment', {}).get('started', False)
        reflection_started = stage_progress.get('reflection', {}).get('started', False)
        reflection_summary_created = stage_progress.get('reflection', {}).get('summary_created', False)
        reflection_needs_resumption = reflection_started and stage_progress.get('reflection', {}).get('conversation_count', 0) > 0 and not reflection_summary_created
        
        unit_progress[unit] = {
            'current_stage': progress['current_stage'],
            'needs_resumption': needs_resumption,
            'last_access': progress.get('last_access', ''),
            'progress_summary': get_progress_summary(progress),
            # 各段階の状態フラグを追加
            'prediction_started': prediction_started,
            'prediction_summary_created': prediction_summary_created,
            'experiment_started': experiment_started,
            'reflection_started': reflection_started,
            'reflection_summary_created': reflection_summary_created,
            'reflection_needs_resumption': reflection_needs_resumption
        }
    
    return render_template('select_unit.html', units=UNITS, unit_progress=unit_progress)

@app.route('/prediction')
def prediction():
    class_number = request.args.get('class', session.get('class_number', '1'))
    class_number = normalize_class_value(class_number) or normalize_class_value(session.get('class_number')) or '1'
    student_number = request.args.get('number', session.get('student_number', '1'))
    unit = request.args.get('unit')
    
    # 異なる単元に移動した場合、セッションをクリア
    current_unit = session.get('unit')
    if current_unit and current_unit != unit:
        print(f"[PREDICTION] 単元変更: {current_unit} → {unit}")
        session.pop('conversation', None)
        session.pop('prediction_summary', None)
        session.pop('reflection_conversation', None)
        session.pop('reflection_summary', None)
    
    session['class_number'] = class_number
    session['student_number'] = student_number
    session['unit'] = unit
    # 明示的にセッションの状態を初期化して、前の段階のプロンプトや会話が残らないようにする
    task_content = load_task_content(unit) if unit else ''
    session['task_content'] = task_content
    session['current_stage'] = 'reflection'
    # 予想段階の対話履歴と混在しないように会話履歴もクリアしておく
    session['conversation'] = []
    session.modified = True
    
    task_content = load_task_content(unit)
    session['task_content'] = task_content
    
    # 進行状況をチェック
    progress = get_student_progress(class_number, student_number, unit)
    
    # 常に新規開始 - セッションを完全にリセット
    # (中断・リロード時に会話履歴は復元しない)
    session.clear()
    session['class_number'] = class_number
    session['student_number'] = student_number
    session['unit'] = unit
    session['task_content'] = task_content
    session['current_stage'] = 'prediction'
    session['conversation'] = []
    session['prediction_summary'] = ''
    session['prediction_summary_created'] = False
    
    print(f"[PREDICTION] 新規開始モード")
    
    # 予想段階開始を記録
    update_student_progress(class_number, student_number, unit)
    
    # 単元に応じた最初のAIメッセージを取得
    initial_ai_message = get_initial_ai_message(unit, stage='prediction')
    
    # 初期メッセージを会話履歴に追加
    conversation_history = session.get('conversation', [])
    if not conversation_history:
        # 新規セッション時のみ、初期メッセージを会話履歴に追加
        conversation_history = [{'role': 'assistant', 'content': initial_ai_message}]
        session['conversation'] = conversation_history
    
    return render_template('prediction.html', unit=unit, task_content=task_content, 
                         prediction_summary_created=session.get('prediction_summary_created', False), 
                         initial_ai_message=initial_ai_message,
                         conversation_history=conversation_history)

@app.route('/chat', methods=['POST'])
def chat():
    try:
        # リクエストが JSON か確認
        if request.content_type and 'application/json' not in request.content_type:
            print(f"[ERROR] Content-Type error: {request.content_type}")
            return jsonify({'error': 'Content-Type が application/json である必要があります'}), 400
        
        if not request.json:
            print(f"[ERROR] No JSON in request body")
            return jsonify({'error': 'リクエストボディが空です'}), 400
        
        user_message = request.json.get('message')
        if not user_message:
            print(f"[ERROR] No message in request")
            return jsonify({'error': 'メッセージが指定されていません'}), 400
            
        input_metadata = request.json.get('metadata', {})
        
        conversation = session.get('conversation', [])
        unit = session.get('unit')
        task_content = session.get('task_content')
        student_number = session.get('student_number')
        
        print(f"[CHAT] message: {user_message[:50]}...")
        print(f"[CHAT] unit: {unit}, student: {student_number}")
        print(f"[CHAT] conversation length: {len(conversation)}")
    except Exception as e:
        print(f"[ERROR] Request parsing error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'リクエスト解析エラー: {str(e)}'}), 400
    
    # 対話履歴に追加
    conversation.append({'role': 'user', 'content': user_message})
    
    # 単元ごとのプロンプトを読み込み（stage指定で段階別プロンプト）
    unit_prompt = load_unit_prompt(unit, stage='prediction')
    
    # 対話履歴を含めてプロンプト作成
    # OpenAI APIに送信するためにメッセージ形式で構築
    messages = [
        {"role": "system", "content": unit_prompt}
    ]
    
    # 対話履歴をメッセージフォーマットで追加
    # 初期メッセージは既に conversation に含まれているので、そのまま追加
    for msg in conversation:
        messages.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    try:
        ai_response = call_openai_with_retry(messages, unit=unit, stage='prediction', enable_cache=True)
        
        # JSON形式のレスポンスの場合は解析して純粋なメッセージを抽出
        ai_message = extract_message_from_json_response(ai_response)
        
        # 予想・考察段階ではマークダウン除去をスキップ（MDファイルのプロンプトに従う）
        # ai_message = remove_markdown_formatting(ai_message)
        
        conversation.append({'role': 'assistant', 'content': ai_message})
        session['conversation'] = conversation
        
        # セッションをDBに保存（ブラウザ閉鎖後の復帰対応）
        student_id = f"{session.get('class_number')}_{session.get('student_number')}"
        save_session_to_db(student_id, unit, 'prediction', conversation)
        
        # 学習ログを保存
        save_learning_log(
            student_number=session.get('student_number'),
            unit=unit,
            log_type='prediction_chat',
            data={
                'user_message': user_message,
                'ai_response': ai_message
            },
            class_number=session.get('class_number')
        )
        
        # 対話が2回以上あれば、予想のまとめを作成可能
        # user + AI で最低2セット（2往復）= 4メッセージ以上必要
        # ただし、実際のユーザーとの往復回数をカウント(AIの初期メッセージは除外)
        user_messages_count = sum(1 for msg in conversation if msg['role'] == 'user')
        suggest_summary = user_messages_count >= 2  # ユーザーメッセージが2回以上
        
        response_data = {
            'response': ai_message,
            'suggest_summary': suggest_summary
        }
        
        print(f"[CHAT] AI response success, user_messages: {user_messages_count}")
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Chat error: {e}")
        print(f"[ERROR] Traceback:\n{error_trace}")
        return jsonify({'error': f'AI接続エラーが発生しました。しばらく待ってから再度お試しください。'}), 500

@app.route('/report_error', methods=['POST'])
def report_error():
    """児童からのエラー報告を受け取る"""
    try:
        data = request.json
        student_number = session.get('student_number')
        class_number = session.get('class_number')
        
        error_message = data.get('error_message', '不明なエラー')
        error_type = data.get('error_type', 'unknown')
        stage = data.get('stage', session.get('current_stage', 'unknown'))
        unit = data.get('unit', session.get('unit', ''))
        additional_info = data.get('additional_info', {})
        
        print(f"[ERROR_REPORT] {class_number}_{student_number}: {error_type} - {error_message}")
        
        # エラーログを保存
        save_error_log(
            student_number=student_number,
            class_number=class_number,
            error_message=error_message,
            error_type=error_type,
            stage=stage,
            unit=unit,
            additional_info=additional_info
        )
        
        return jsonify({'status': 'success', 'message': 'エラー報告を受け取りました'}), 200
    
    except Exception as e:
        print(f"[ERROR_REPORT] Error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/summary', methods=['POST'])
def summary():
    conversation = session.get('conversation', [])
    unit = session.get('unit')

    # すでに要約が作成されている場合はスキップ
    if session.get('prediction_summary'):
        print(f"[SUMMARY] Already created: {session.get('prediction_summary')[:50]}...")
        return jsonify({'summary': session.get('prediction_summary')})
    
    # ユーザーの発言をチェック（初期メッセージを除く）
    user_messages = [msg for msg in conversation if msg['role'] == 'user']
    
    # ユーザー発言が不足している場合
    if len(user_messages) == 0:
        return jsonify({
            'error': 'まだ何も話していないようです。あなたの予想や考えを教えてください。',
            'is_insufficient': True
        }), 400
    
    # ユーザー発言の内容をチェック
    user_content = ' '.join([msg['content'] for msg in user_messages])
    
    # 文字数による判定はやめ、意味的に有意な発言かを判定する
    exchange_count = len(user_messages)
    
    # 非常に緩い判定：2回以上のやりとりがあれば無条件でOK
    # 1回のみの場合も、少しでも内容があればOK
    if exchange_count < 2:
        # 1回のみの場合、内容が極端に空でなければOK
        if len(user_content.strip()) < 2:
            return jsonify({
                'error': 'あなたの考えが伝わりきっていないようです。どういうわけでそう思ったの？何か見たことや経験があれば教えてね。',
                'is_insufficient': True
            }), 400
    
    # 単元のプロンプトを読み込み（予想段階の指示を必ず参照）
    unit_prompt = load_unit_prompt(unit, stage='prediction')
    
    summary_instruction = (
        "以下の会話内容のみをもとに、児童の話した言葉や順序を活かして予想をまとめてください。"
        "児童が自分のノートにそのまま写せる、短い1〜2文にしてください。"
        "「〜と思う。なぜなら〜。」の形で、むずかしい言い回しや第三者目線（例:「児童は〜」）は使わないでください。"
        "理由は児童が話した経験や具体的な様子のみを書き、結論を言い換えただけの理由（例:「体積が大きくなるのは体積がふくらむから」）は書かないでください。"
        "会話に含まれていない内容や新しい事実は追加しないでください。"
    )
    
    # メッセージフォーマットで構築
    messages = [
        {"role": "system", "content": f"{unit_prompt}\n\n【重要】{summary_instruction}"}
    ]
    
    # 対話履歴をメッセージフォーマットで追加
    for msg in conversation:
        messages.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    # 最後に要約を促すメッセージを追加
    messages.append({
        "role": "user",
        "content": "これまでの話をもとに、予想をまとめてください。児童の話した順序と言葉を活かし、口語を自然な書き言葉に整えてください。会話に含まれていない内容は追加しないでください。"
    })
    
    try:
        # Debug: log whether FORCE_SYNC_SUMMARY is set and PID
        try:
            force_sync = os.environ.get('FORCE_SYNC_SUMMARY', 'false').lower() in ('1', 'true', 'yes')
            print(f"[SUMMARY] PID:{os.getpid()} FORCE_SYNC_SUMMARY={force_sync} rq_queue_present={rq_queue is not None}")
        except Exception as env_err:
            print(f"[SUMMARY] Warning: Could not read FORCE_SYNC_SUMMARY: {env_err}")
            force_sync = False

        # Enqueue background job to generate and persist summary
        class_number = session.get('class_number')
        student_number = session.get('student_number')
        student_id = f"{class_number}_{student_number}"
        
        print(f"[SUMMARY] Starting summary for {student_id}_{unit}, force_sync={force_sync}")
        
        # If FORCE_SYNC_SUMMARY is enabled, perform synchronous generation here
        if force_sync:
            try:
                summary_response = call_openai_with_retry(messages, model_override="gpt-4o-mini", enable_cache=True, stage='prediction')
                summary_text = extract_message_from_json_response(summary_response)
                session['prediction_summary'] = summary_text
                session['prediction_summary_created'] = True
                session.modified = True
                _save_summary_to_db(student_id, unit, 'prediction', summary_text)
                update_student_progress(class_number=class_number, student_number=student_number, unit=unit, prediction_summary_created=True)
                save_learning_log(student_number=student_number, unit=unit, log_type='prediction_summary', data={'summary': summary_text, 'conversation': conversation}, class_number=class_number)
                print(f"[SUMMARY] Synchronous summary generated for {student_id}_{unit}")
                return jsonify({'summary': summary_text})
            except Exception as e:
                print(f"[SUMMARY_ERROR] Synchronous summary generation failed: {e}")
                import traceback
                traceback.print_exc()
                # Fall through to enqueue path if sync failed

        if rq_queue is None:
            # Fallback to synchronous processing if Redis/RQ not configured
            print(f"[SUMMARY] RQ queue not available, using synchronous processing")
            try:
                print(f"[SUMMARY] Step 1: Calling OpenAI API...")
                summary_response = call_openai_with_retry(messages, model_override="gpt-4o-mini", enable_cache=True, stage='prediction')
                print(f"[SUMMARY] Step 2: Extracting message from response...")
                summary_text = extract_message_from_json_response(summary_response)
                print(f"[SUMMARY] Step 3: Saving to session... (length: {len(summary_text)})")
                session['prediction_summary'] = summary_text
                session['prediction_summary_created'] = True
                session.modified = True
                print(f"[SUMMARY] Step 4: Saving to database...")
                _save_summary_to_db(student_id, unit, 'prediction', summary_text)
                print(f"[SUMMARY] Step 5: Updating progress...")
                update_student_progress(class_number=class_number, student_number=student_number, unit=unit, prediction_summary_created=True)
                print(f"[SUMMARY] Step 6: Saving learning log...")
                save_learning_log(student_number=student_number, unit=unit, log_type='prediction_summary', data={'summary': summary_text, 'conversation': conversation}, class_number=class_number)
                print(f"[SUMMARY] Synchronous summary completed for {student_id}_{unit}")
                return jsonify({'summary': summary_text})
            except Exception as sync_err:
                print(f"[SUMMARY_ERROR] Synchronous processing failed at step: {sync_err}")
                import traceback
                traceback.print_exc()
                raise

        # Enqueue job
        job = rq_queue.enqueue(perform_summary_job, args=(conversation, unit, student_id, class_number, student_number, 'prediction'), job_timeout=600)
        print(f"[SUMMARY] Enqueued job: {job.id} for {student_id}_{unit}")
        # Return job id so client can poll status
        return jsonify({'job_id': job.id, 'status': 'queued'})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        # Print detailed traceback to server logs for debugging
        print(f"[SUMMARY_ERROR] {type(e).__name__}: {e}")
        print(f"[SUMMARY_ERROR] Traceback:\n{tb}")
        # Also persist an error log entry for later inspection
        try:
            save_error_log(
                student_number=session.get('student_number'),
                class_number=session.get('class_number'),
                error_message=str(e),
                error_type='summary_exception',
                stage='prediction',
                unit=unit,
                additional_info={'traceback': tb[:1000]}
            )
        except Exception:
            pass
        return jsonify({'error': f'まとめ生成中にエラーが発生しました。'}), 500

@app.route('/job_status/<job_id>', methods=['GET'])
def job_status(job_id):
    """RQジョブのステータスを取得"""
    try:
        if rq_queue is None:
            return jsonify({'error': 'Job queue not available'}), 503
        
        from rq.job import Job
        job = Job.fetch(job_id, connection=rq_queue.connection)
        
        if job.is_finished:
            return jsonify({
                'status': 'finished',
                'result': job.result
            })
        elif job.is_failed:
            return jsonify({
                'status': 'failed',
                'error': str(job.exc_info) if job.exc_info else 'Unknown error'
            })
        elif job.is_started:
            return jsonify({'status': 'started'})
        elif job.is_queued:
            return jsonify({'status': 'queued'})
        else:
            return jsonify({'status': 'unknown'})
    
    except Exception as e:
        print(f"[JOB_STATUS] Error fetching job {job_id}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync-session', methods=['POST'])
def sync_session():
    """クライアント側のlocalStorageデータをサーバーに同期（GCS/ローカル保存）"""
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        unit = data.get('unit')
        stage = data.get('stage')  # 'prediction' or 'reflection'
        chat_messages = data.get('chat_messages', [])
        summary_content = data.get('summary_content', '')
        
        if not all([student_id, unit, stage]):
            return jsonify({'error': '必須パラメータが不足しています'}), 400
        
        # セッションデータを構成
        conversation_data = chat_messages
        
        # サーバー側にセッションを保存（GCS/ローカル）
        save_session_to_db(student_id, unit, stage, conversation_data)
        
        # サマリーも保存したい場合は別途保存
        if summary_content:
            # Save the summary using the standard helper (student_id, unit, stage, summary_text)
            _save_summary_to_db(student_id, unit, stage, summary_content)
        
        print(f"[SYNC] Session synced - {student_id}_{unit}_{stage}")
        return jsonify({
            'success': True,
            'message': 'セッションをサーバーに同期しました'
        })
    
    except Exception as e:
        print(f"[SYNC] Error: {e}")
        return jsonify({
            'error': 'セッションの同期に失敗しました',
            'details': str(e)
        }), 500

def _save_summary_to_db(student_id, unit, stage, summary_text):
    """サマリーを永続ストレージに保存（GCS優先、ローカルはフォールバック）"""
    # Firestore 優先
    if USE_FIRESTORE and firestore_client:
        try:
            key = f"{student_id}_{unit}_{stage}"
            firestore_client.collection('sb_summary_storage').document(key).set({
                'summary': summary_text,
                'saved_at': datetime.now().isoformat(),
                'student_id': student_id,
                'unit': unit,
                'stage': stage
            })
            print(f"[SUMMARY_SAVE] Firestore - {key}")
            return
        except Exception as e:
            print(f"[SUMMARY_SAVE] Firestore failed: {e}, falling back to next storage")

    # 本番環境: GCS優先
    if USE_GCS and bucket:
        try:
            _save_summary_gcs(student_id, unit, stage, summary_text)
            print(f"[SUMMARY_SAVE] GCS - {student_id}_{unit}_{stage}")
            return  # GCS保存成功したらローカル保存は不要
        except Exception as e:
            print(f"[SUMMARY_SAVE] GCS failed: {e}, falling back to local")
    
    # 開発環境またはGCS失敗時: ローカル保存
    try:
        _save_summary_local(student_id, unit, stage, summary_text)
        print(f"[SUMMARY_SAVE] Local - {student_id}_{unit}_{stage}")
    except Exception as e:
        print(f"[SUMMARY_SAVE] Local failed: {e}")

def _save_summary_local(student_id, unit, stage, summary_text):
    """サマリーをローカルファイルに保存"""
    try:
        summary_file = 'summary_storage.json'
        
        # 既存のファイルを読み込む
        if os.path.exists(summary_file):
            with open(summary_file, 'r', encoding='utf-8') as f:
                summaries = json.load(f)
        else:
            summaries = {}
        
        # キーを作成
        key = f"{student_id}_{unit}_{stage}"
        
        # 新しいサマリーを追加
        summaries[key] = {
            'summary': summary_text,
            'saved_at': datetime.now().isoformat(),
            'student_id': student_id,
            'unit': unit,
            'stage': stage
        }
        
        # ファイルに保存
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summaries, f, ensure_ascii=False, indent=2)
        
        print(f"[SUMMARY_SAVE_LOCAL] {key} saved to {summary_file}")
    except Exception as e:
        print(f"[SUMMARY_SAVE_LOCAL] Error: {e}")


@app.route('/summary/status/<job_id>', methods=['GET'])
def summary_status(job_id):
    """Return job status and result (if finished)."""
    try:
        if redis_conn is None:
            return jsonify({'error': 'Redis not configured', 'status': 'unavailable'}), 503

        job = _RQJob.fetch(job_id, connection=redis_conn)
        status = job.get_status()
        if job.is_finished:
            return jsonify({'status': status, 'summary': job.result})
        if job.is_failed:
            return jsonify({'status': 'failed', 'error': str(job.exc_info)}), 500
        return jsonify({'status': status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _save_summary_gcs(student_id, unit, stage, summary_text):
    """サマリーをGCSに保存"""
    try:
        from google.cloud import storage
        
        # GCSのパス: summaries/{student_id}/{unit}/{stage}_summary.json
        gcs_path = f"summaries/{student_id}/{unit}/{stage}_summary.json"
        blob = bucket.blob(gcs_path)
        
        data = {
            'summary': summary_text,
            'saved_at': datetime.now().isoformat(),
            'student_id': student_id,
            'unit': unit,
            'stage': stage
        }
        
        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type='application/json'
        )
        
        print(f"[SUMMARY_SAVE_GCS] {gcs_path} saved")
    except Exception as e:
        print(f"[SUMMARY_SAVE_GCS] Error: {e}")

def _load_summary_from_db(student_id, unit, stage):
    """サマリーをデータベースから取得（GCS優先）"""
    # Firestore 優先
    if USE_FIRESTORE and firestore_client:
        try:
            key = f"{student_id}_{unit}_{stage}"
            doc = firestore_client.collection('sb_summary_storage').document(key).get()
            if doc.exists:
                data = doc.to_dict()
                print(f"[SUMMARY_LOAD] Firestore - {key}")
                return data.get('summary', '')
        except Exception as e:
            print(f"[SUMMARY_LOAD] Firestore failed: {e}, trying next storage")

    # 本番環境: GCS優先
    if USE_GCS and bucket:
        try:
            summary = _load_summary_gcs(student_id, unit, stage)
            if summary is not None:
                print(f"[SUMMARY_LOAD] GCS - {student_id}_{unit}_{stage}")
                return summary
        except Exception as e:
            print(f"[SUMMARY_LOAD] GCS failed: {e}, trying local")

    # 開発環境またはGCS失敗時: ローカルから取得
    summary = _load_summary_local(student_id, unit, stage)
    if summary:
        print(f"[SUMMARY_LOAD] Local - {student_id}_{unit}_{stage}")
    return summary

def _load_summary_local(student_id, unit, stage):
    """サマリーをローカルファイルから取得"""
    try:
        summary_file = 'summary_storage.json'
        if not os.path.exists(summary_file):
            return ''
        
        with open(summary_file, 'r', encoding='utf-8') as f:
            summaries = json.load(f)
        
        key = f"{student_id}_{unit}_{stage}"
        if key in summaries:
            print(f"[SUMMARY_LOAD] Local - {key}")
            return summaries[key].get('summary', '')
    except Exception as e:
        print(f"[SUMMARY_LOAD] Local Error: {e}")
    
    return ''

def _load_summary_gcs(student_id, unit, stage):
    """サマリーをGCSから取得"""
    try:
        from google.cloud import storage
        
        # GCSのパス: summaries/{student_id}/{unit}/{stage}_summary.json
        gcs_path = f"summaries/{student_id}/{unit}/{stage}_summary.json"
        blob = bucket.blob(gcs_path)
        
        if blob.exists():
            content = blob.download_as_string().decode('utf-8')
            data = json.loads(content)
            print(f"[SUMMARY_LOAD] GCS - {gcs_path}")
            return data.get('summary', '')
    except Exception as e:
        print(f"[SUMMARY_LOAD] GCS Error: {e}")
    
    return None

@app.route('/reflection')
def reflection():
    unit = request.args.get('unit', session.get('unit'))
    class_number = normalize_class_value(session.get('class_number', '1')) or '1'
    student_number = session.get('student_number')
    session['class_number'] = class_number
    prediction_summary = session.get('prediction_summary')
    
    print(f"[REFLECTION] アクセス: unit={unit}, student={class_number}_{student_number}")
    
    # 進行状況をチェック
    progress = get_student_progress(class_number, student_number, unit)
    stage_progress = progress.get('stage_progress', {})
    prediction_stage = stage_progress.get('prediction', {})
    prediction_summary_created = prediction_stage.get('summary_created', False)
    
    # ℹ️ 予想完了なしでも考察へアクセス可能（新仕様）
    print(f"[REFLECTION] 考察へアクセス: unit={unit}, student={class_number}_{student_number}, prediction_completed={prediction_summary_created}")
    
    # 異なる単元に移動した場合、セッションをクリア（単元混在防止）
    current_unit = session.get('unit')
    if current_unit and current_unit != unit:
        print(f"[REFLECTION] 単元変更: {current_unit} → {unit}")
        session.pop('reflection_conversation', None)
        session.pop('reflection_summary', None)
        session.pop('conversation', None)
        session.pop('prediction_summary', None)
    
    session['unit'] = unit
    
    # 常に新規開始 - セッションを完全にリセット
    # (中断・リロード時に会話履歴は復元しない)
    session.pop('reflection_conversation', None)
    session.pop('reflection_summary', None)
    session.pop('reflection_summary_created', None)
    session['reflection_conversation'] = []

    # 明示的にセッションの状態を初期化して、前の段階のプロンプトや会話が残らないようにする
    task_content = load_task_content(unit) if unit else ''
    session['task_content'] = task_content
    session['current_stage'] = 'reflection'
    # 予想段階の対話履歴と混在しないように会話履歴もクリアしておく
    session['conversation'] = []
    session.modified = True
    
    # 予想まとめがセッションに存在しない場合はストレージから復元
    student_id = f"{class_number}_{student_number}"
    if (not prediction_summary) and unit and student_number:
        restored_prediction_summary = _load_summary_from_db(student_id, unit, 'prediction')
        if restored_prediction_summary:
            prediction_summary = restored_prediction_summary
            session['prediction_summary'] = restored_prediction_summary
            print(f"[REFLECTION] 予想まとめをストレージから復元: {len(restored_prediction_summary)} 文字")
    
    print(f"[REFLECTION] 新規開始モード")
    
    # 常に新規開始のため、resumption_infoは常にFalse
    reflection_summary_created = stage_progress.get('reflection', {}).get('summary_created', False)
    resumption_info = {
        'is_resumption': False,
        'reflection_summary_created': reflection_summary_created
    }
    
    if unit and student_number:
        # 考察段階開始を記録（フラグは修正しない）
        update_student_progress(
            class_number,
            student_number,
            unit
        )
    
    # 単元に応じた最初のAIメッセージを取得
    initial_ai_message = get_initial_ai_message(unit, stage='reflection')
    
    # セッションデータをテンプレートに明示的に渡す
    reflection_conversation_history = session.get('reflection_conversation', [])
    
    return render_template('reflection.html', 
                         unit=unit,
                         prediction_summary=prediction_summary,
                         reflection_summary_created=reflection_summary_created,
                         initial_ai_message=initial_ai_message,
                         reflection_conversation_history=reflection_conversation_history,
                         reflection_resumption_info=resumption_info)

@app.route('/reflect_chat', methods=['POST'])
def reflect_chat():
    user_message = request.json.get('message')
    reflection_conversation = session.get('reflection_conversation', [])
    unit = session.get('unit')
    prediction_summary = session.get('prediction_summary', '')
    
    # 反省対話履歴に追加
    reflection_conversation.append({'role': 'user', 'content': user_message})
    
    # プロンプトファイルからベースプロンプトを取得（考察段階用）
    unit_prompt = load_unit_prompt(unit, stage='reflection')
    
    # 考察段階のシステムプロンプトを構築
    reflection_system_prompt = f"""
あなたは小学4年生の理科学習を支援するAIアシスタントです。現在、児童が実験後の「考察段階」に入っています。

## 重要な役割
児童は実験を終え、その結果と自分の予想を比較しながら、「なぜそうなったのか」，日常生活や既習事項との関連を自分の言葉で考える段階です。

## あなたが守ること（絶対ルール）
1. **子どもの発言を最優先する**
   - 子どもの話した内容をそのまま受け止める
   - 「〜なんだね」「〜だったんだね」と整理する
   - 子どもの表現を活かす

2. **自然で短い対話を心がける**
   - 1往復ごとに1つの応答を返す
   - 一度に3つ以上の質問をしない
   - やさしく、短く、日常的な言葉を使う

3. **無理に続けない**
   - 児童が短い応答をした場合でも、それを受け止めて終わることもある
   - 「もっと話して」と促し続けない
   - 児童が充分に答えたと感じたら、その内容を認める
   - 児童がまとめボタンを押すのを待つ

4. **絶対にしてはいけないこと**
   - ❌ 長文のまとめを途中で出さない（児童が「まとめボタン」を押すまで対話を続ける）
   - ❌ 難しい専門用語を使わない
   - ❌ 子どもの考えを否定しない
   - ❌ 科学的な正確性よりも子どもの気づきを優先する
   - ❌ 児童の応答が完璧でなくても、無理に続けさせる

## 対話の進め方（ただしムリは禁物）
1. 実験結果を聞く：「じっけんではどんなけっかになった？」
2. 予想との簡単な確認：「さいしょの予そうと同じだった？」
3. 子どもの考え・気づきを軽く引き出す：「それってなぜだと思う？」
4. 児童の返答を受け止めて、必要に応じて次の質問へ
5. 児童が「もう話す事がない」という雰囲気なら、そこで終了でOK

## 単元の指導内容
{unit_prompt}

## 児童の予想
{prediction_summary or '予想がまだ記録されていません。'}

## 大事なこと
- 子どもが何を考えたか、気づいたかを最優先に引き出す
- 膜の変化（ふくらむ / 凹む）から体積の変化（大きくなる / 小さくなる）を自然に導く
- 予想との比較は簡単な確認程度
- **充分な対話ができたら、児童がまとめボタンを押すのを待つ（促し続けない）**
"""
    
    # メッセージフォーマットで対話履歴を構築
    messages = [
        {"role": "system", "content": reflection_system_prompt}
    ]
    
    # 対話履歴をメッセージフォーマットで追加
    for msg in reflection_conversation:
        messages.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    try:
        ai_response = call_openai_with_retry(messages, unit=unit, stage='reflection', enable_cache=True)
        
        # JSON形式のレスポンスの場合は解析して純粋なメッセージを抽出
        ai_message = extract_message_from_json_response(ai_response)
        
        # 予想・考察段階ではマークダウン除去をスキップ（MDファイルのプロンプトに従う）
        # ai_message = remove_markdown_formatting(ai_message)
        
        reflection_conversation.append({'role': 'assistant', 'content': ai_message})
        session['reflection_conversation'] = reflection_conversation
        
        # セッションをDBに保存（ブラウザ閉鎖後の復帰対応）
        student_id = f"{session.get('class_number')}_{session.get('student_number')}"
        save_session_to_db(student_id, unit, 'reflection', reflection_conversation)
        
        # 考察チャットのログを保存
        save_learning_log(
            student_number=session.get('student_number'),
            unit=unit,
            log_type='reflection_chat',
            data={
                'user_message': user_message,
                'ai_response': ai_message
            },
            class_number=session.get('class_number')
        )
        
        # 対話が2往復以上あれば、考察のまとめを作成可能
        # ユーザーメッセージが2回以上必要
        user_messages_count = sum(1 for msg in reflection_conversation if msg['role'] == 'user')
        suggest_final_summary = user_messages_count >= 2
        
        return jsonify({
            'response': ai_message,
            'suggest_final_summary': suggest_final_summary
        })
        
    except Exception as e:
        import traceback
        error_msg = f"[REFLECT_CHAT_ERROR] {str(e)}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        return jsonify({'error': f'AI接続エラーが発生しました。しばらく待ってから再度お試しください。\nDebug: {str(e)}'}), 500

@app.route('/final_summary', methods=['POST'])
def final_summary():
    reflection_conversation = session.get('reflection_conversation', [])
    prediction_summary = session.get('prediction_summary', '')
    unit = session.get('unit')
    
    # ユーザーの発言をチェック（初期メッセージを除く）
    user_messages = [msg for msg in reflection_conversation if msg['role'] == 'user']
    
    # ユーザー発言が不足している場合
    if len(user_messages) == 0:
        return jsonify({
            'error': 'まだ何も話していないようです。実験の結果や気づきを教えてください。',
            'is_insufficient': True
        }), 400
    
    # ユーザー発言の内容をチェック
    user_content = ' '.join([msg['content'] for msg in user_messages])
    
    # 文字数による判定は廃止し、意味的に有意かで判定する
    exchange_count = len(user_messages)
    
    # 非常に緩い判定：2回以上のやりとりがあれば無条件でOK
    # 1回のみの場合も、少しでも内容があればOK
    if exchange_count < 2:
        # 1回のみの場合、内容が極端に空でなければOK
        if len(user_content.strip()) < 2:
            return jsonify({
                'error': 'あなたの考えが伝わりきっていないようです。どんな結果になった？予想と同じだった？ちがった？',
                'is_insufficient': True
            }), 400
    
    # 単元のプロンプトを読み込み（考察段階用）
    unit_prompt = load_unit_prompt(unit, stage='reflection')
    
    # メッセージフォーマットで構築
    messages = [
        {"role": "system", "content": unit_prompt + "\n\n【重要】以下の会話内容のみをもとに、児童の話した言葉や考えを活かして、考察をまとめてください。会話に含まれていない内容は追加しないでください。"}
    ]
    
    # 対話履歴をメッセージフォーマットで追加
    for msg in reflection_conversation:
        messages.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    # 最後に考察作成を促すメッセージを追加
    messages.append({
        "role": "user",
        "content": "児童が「考察をまとめる」ボタンを押しました。これまでの対話内容から、児童自身の言葉や気づきを活かして考察をまとめてください。"
    })
    
    try:
        final_summary_response = call_openai_with_retry(messages, model_override="gpt-4o-mini", enable_cache=True)
        
        # JSON形式のレスポンスの場合は解析して純粋なメッセージを抽出
        final_summary_text = extract_message_from_json_response(final_summary_response)
        
        # 要約段階ではマークダウン除去をスキップ（MDファイルのプロンプトに従う）
        # final_summary_text = remove_markdown_formatting(final_summary_text)
        
        # セッションに保存（フロントの復元用）
        session['reflection_summary'] = final_summary_text
        session['reflection_summary_created'] = True
        session.modified = True
        
        # 考察完了フラグを設定
        update_student_progress(
            class_number=session.get('class_number'),
            student_number=session.get('student_number'),
            unit=session.get('unit'),
            reflection_summary_created=True
        )
        
        # 永続ストレージに保存（ローカル/GCS）
        student_id = f"{session.get('class_number')}_{session.get('student_number')}"
        _save_summary_to_db(student_id, unit, 'reflection', final_summary_text)
        
        # 最終考察のログを保存
        save_learning_log(
            student_number=session.get('student_number'),
            unit=session.get('unit'),
            log_type='final_summary',
            data={
                'final_summary': final_summary_text,
                'prediction_summary': prediction_summary,
                'reflection_conversation': reflection_conversation
            },
            class_number=session.get('class_number')
        )
        
        return jsonify({'summary': final_summary_text})
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"【ERROR】/final_summary エラー: {str(e)}")
        print(error_detail)
        save_learning_log(
            student_number=session.get('student_number'),
            unit=session.get('unit'),
            log_type='final_summary_error',
            data={
                'error': str(e),
                'traceback': error_detail
            },
            class_number=session.get('class_number')
        )
        return jsonify({'error': f'最終まとめ生成中にエラーが発生しました: {str(e)}'}), 500

@app.route('/get_prediction_summary', methods=['GET'])
def get_prediction_summary():
    """復帰時に予想のまとめを取得するエンドポイント"""
    unit = session.get('unit')
    student_number = session.get('student_number')
    
    if not unit or not student_number:
        return jsonify({'summary': None}), 400
    
    # セッションに保存されている予想のまとめを返す
    summary = session.get('prediction_summary')
    if summary:
        return jsonify({'summary': summary})
    
    # セッションにない場合は学習ログから取得を試みる
    logs = load_learning_logs(datetime.now().strftime('%Y%m%d'))
    for log in logs:
        if (log.get('student_number') == student_number and 
            log.get('unit') == unit and 
            log.get('log_type') == 'prediction_summary'):
            session['prediction_summary'] = log.get('data', {}).get('summary', '')
            return jsonify({'summary': log.get('data', {}).get('summary', '')})
    
    return jsonify({'summary': None})

# 教員用ルート
@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    """教員ログインページ"""
    if request.method == 'POST':
        teacher_id = request.form.get('teacher_id')
        password = request.form.get('password')
        
        # 認証チェック
        if teacher_id in TEACHER_CREDENTIALS and TEACHER_CREDENTIALS[teacher_id] == password:
            session['teacher_authenticated'] = True
            session['teacher_id'] = teacher_id
            return redirect(url_for('teacher'))
        else:
            flash('IDまたはパスワードが正しくありません', 'error')
    
    return render_template('teacher/login.html')

@app.route('/teacher/logout')
def teacher_logout():
    """教員ログアウト"""
    session.pop('teacher_authenticated', None)
    session.pop('teacher_id', None)
    return redirect(url_for('index'))

@app.route('/teacher')
@require_teacher_auth
def teacher():
    """教員用ダッシュボード"""
    teacher_id = session.get('teacher_id')
    
    return render_template('teacher/dashboard.html', 
                         units=UNITS, 
                         teacher_id=teacher_id)

@app.route('/teacher/dashboard')
@require_teacher_auth
def teacher_dashboard():
    """教員用ダッシュボード（別ルート）"""
    teacher_id = session.get('teacher_id')
    
    return render_template('teacher/dashboard.html', 
                         units=UNITS, 
                         teacher_id=teacher_id)

@app.route('/teacher/logs')
@require_teacher_auth
def teacher_logs():
    """学習ログ一覧"""
    # デフォルト日付を現在の日付に設定
    try:
        available_dates_raw = get_available_log_dates()
        default_date = available_dates_raw[0] if available_dates_raw else datetime.now().strftime('%Y%m%d')
        # フロントエンド用に辞書形式に変換
        available_dates = [
            {'raw': d, 'formatted': f"{d[:4]}/{d[4:6]}/{d[6:8]}"}
            for d in available_dates_raw
        ]
    except Exception as e:
        print(f"[LOGS] Error getting available dates: {str(e)}")
        default_date = datetime.now().strftime('%Y%m%d')
        available_dates = []
    
    date = request.args.get('date', default_date)
    unit = request.args.get('unit', '')
    raw_class_filter = request.args.get('class', '')
    class_filter = normalize_class_value(raw_class_filter) or ''
    class_filter_int = None
    if class_filter:
        try:
            class_filter_int = int(class_filter)
        except ValueError:
            class_filter_int = None
    student = request.args.get('student', '')
    
    logs = load_learning_logs(date)
    
    # フィルタリング
    if unit:
        logs = [log for log in logs if log.get('unit') == unit]
    
    # クラスと出席番号でフィルター（両方を組み合わせる）
    if class_filter_int is not None and student:
        # クラスと出席番号の両方が指定された場合
        logs = [log for log in logs 
                if log.get('class_num') == class_filter_int 
                and log.get('seat_num') == int(student)]
    elif class_filter_int is not None:
        # クラスのみ指定された場合
        logs = [log for log in logs 
                if log.get('class_num') == class_filter_int]
    elif student:
        # 出席番号のみ指定された場合（全クラスから該当番号を検索）
        logs = [log for log in logs 
                if log.get('seat_num') == int(student)]
    
    # 児童ごとにグループ化（クラスと出席番号の組み合わせで識別）
    students_data = {}
    for log in logs:
        class_num = log.get('class_num')
        seat_num = log.get('seat_num')
        student_num = log.get('student_number')
        
        # クラスと出席番号の組み合わせで一意のキーを生成
        student_key = f"{class_num}_{seat_num}" if class_num and seat_num else student_num
        
        if student_key not in students_data:
            # ログから直接クラスと出席番号の情報を取得
            if class_num is not None and seat_num is not None:
                display_label = f'{class_num}組{seat_num}番'
            else:
                display_label = log.get('class_display', str(student_num))
            student_info = {
                'class_num': class_num,
                'seat_num': seat_num,
                'display': display_label
            }
            students_data[student_key] = {
                'student_number': student_num,
                'student_info': student_info,
                'units': {}
            }
        
        unit_name = log.get('unit')
        if unit_name not in students_data[student_key]['units']:
            students_data[student_key]['units'][unit_name] = {
                'prediction_chats': [],
                'prediction_summary': None,
                'reflection_chats': [],
                'final_summary': None
            }
        
        log_type = log.get('log_type')
        if log_type == 'prediction_chat':
            students_data[student_key]['units'][unit_name]['prediction_chats'].append(log)
        elif log_type == 'prediction_summary':
            students_data[student_key]['units'][unit_name]['prediction_summary'] = log
        elif log_type == 'reflection_chat':
            students_data[student_key]['units'][unit_name]['reflection_chats'].append(log)
        elif log_type == 'final_summary':
            students_data[student_key]['units'][unit_name]['final_summary'] = log
    
    # クラスと番号でソート
    students_data = dict(sorted(students_data.items(), 
                                key=lambda x: (x[1]['student_info']['class_num'] if x[1]['student_info'] else 999, 
                                             x[1]['student_info']['seat_num'] if x[1]['student_info'] else 999)))
    
    return render_template('teacher/logs.html', 
                         students_data=students_data, 
                         units=UNITS,
                         current_date=date,
                         current_unit=unit,
                         current_class=class_filter,
                         current_student=student,
                         available_dates=available_dates,
                         teacher_id=session.get('teacher_id'))

@app.route('/teacher/export')
@require_teacher_auth
def teacher_export():
    """ログをCSVでエクスポート - ダウンロード日までのすべてのログ"""
    from io import StringIO, BytesIO
    import csv
    
    download_date_str = request.args.get('date', datetime.now().strftime('%Y%m%d'))
    
    # ダウンロード日までのすべてのログを取得
    all_logs = []
    available_dates = get_available_log_dates()
    
    print(f"[EXPORT] START - exporting logs up to date: {download_date_str}")
    print(f"[EXPORT] Available dates: {available_dates}")
    
    for date_str in available_dates:
        # date_str は文字列 (YYYYMMDD format)
        current_date_raw = date_str if isinstance(date_str, str) else date_str.get('raw', '')
        # ダウンロード日以下の日付のみを対象
        if current_date_raw <= download_date_str:
            try:
                logs = load_learning_logs(current_date_raw)
                all_logs.extend(logs)
                print(f"[EXPORT] Loaded {len(logs)} logs from {current_date_raw}")
            except Exception as e:
                print(f"[EXPORT] ERROR loading logs from {current_date_raw}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

    # フロントのフィルタ（現在の表示）に合わせて絞り込み可能にする
    unit_filter = request.args.get('unit', '')
    class_filter = request.args.get('class', '')
    student_filter = request.args.get('student', '')

    def matches_filters(log):
        if unit_filter and log.get('unit') != unit_filter:
            return False
        if class_filter:
            try:
                cf_int = normalize_class_value_int(class_filter)
                if cf_int is not None and log.get('class_num') != cf_int:
                    return False
            except Exception:
                pass
        if student_filter:
            try:
                if int(student_filter) != int(log.get('seat_num') or -1):
                    return False
            except Exception:
                if str(student_filter) != str(log.get('student_number')):
                    return False
        return True

    filtered_logs = [log for log in all_logs if matches_filters(log)]
    
    # CSVをメモリに作成（UTF-8 BOM付き）
    output = StringIO()
    fieldnames = ['timestamp', 'class_display', 'student_number', 'unit', 'log_type', 'content']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for log in filtered_logs:
        content = ""
        if log.get('log_type') == 'prediction_chat':
            content = f"Q: {log['data'].get('user_message', '')}\nA: {log['data'].get('ai_response', '')}"
        elif log.get('log_type') == 'prediction_summary':
            content = log['data'].get('summary', '')
        elif log.get('log_type') == 'reflection_chat':
            content = f"Q: {log['data'].get('user_message', '')}\nA: {log['data'].get('ai_response', '')}"
        elif log.get('log_type') == 'final_summary':
            content = log['data'].get('final_summary', '')
        
        writer.writerow({
            'timestamp': log.get('timestamp', ''),
            'class_display': log.get('class_display', ''),
            'student_number': log.get('student_number', ''),
            'unit': log.get('unit', ''),
            'log_type': log.get('log_type', ''),
            'content': content
        })
    
    # StringIOをUTF-8 BOM付きバイナリにエンコード
    csv_string = output.getvalue()
    csv_bytes = '\ufeff'.encode('utf-8') + csv_string.encode('utf-8')  # UTF-8 BOM追加
    
    filename = f"all_learning_logs_up_to_{download_date_str}.csv"
    
    print(f"[EXPORT] SUCCESS - exported {len(filtered_logs)} total logs, size: {len(csv_bytes)} bytes")
    
    return Response(
        csv_bytes,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )

@app.route('/teacher/export_json')
@require_teacher_auth
def teacher_export_json():
    """対話内容をJSONでエクスポート - 単元ごとのディレクトリ構造でzip出力"""
    from io import BytesIO
    
    download_date_str = request.args.get('date', datetime.now().strftime('%Y%m%d'))
    
    # ダウンロード日までのすべてのログを取得
    all_logs = []
    available_dates = get_available_log_dates()
    
    print(f"[EXPORT_JSON] START - exporting logs up to date: {download_date_str}")
    
    for date_str in available_dates:
        # date_str は文字列 (YYYYMMDD format)
        current_date_raw = date_str if isinstance(date_str, str) else date_str.get('raw', '')
        if current_date_raw <= download_date_str:
            try:
                logs = load_learning_logs(current_date_raw)
                all_logs.extend(logs)
                print(f"[EXPORT_JSON] Loaded {len(logs)} logs from {current_date_raw}")
            except Exception as e:
                print(f"[EXPORT_JSON] ERROR loading logs from {current_date_raw}: {str(e)}")
                continue

    # フィルタリング（テンプレートの現在の表示に合わせる）
    unit_filter = request.args.get('unit', '')
    class_filter = request.args.get('class', '')
    student_filter = request.args.get('student', '')

    def matches_filters(log):
        if unit_filter and log.get('unit') != unit_filter:
            return False
        if class_filter:
            try:
                cf_int = normalize_class_value_int(class_filter)
                if cf_int is not None and log.get('class_num') != cf_int:
                    return False
            except Exception:
                pass
        if student_filter:
            try:
                if int(student_filter) != int(log.get('seat_num') or -1):
                    return False
            except Exception:
                if str(student_filter) != str(log.get('student_number')):
                    return False
        return True

    filtered_logs = [log for log in all_logs if matches_filters(log)]
    
    # 児童ごと・単元ごとにグループ化
    # 構造: {unit: {student_id: [logs]}}
    structured_logs = {}
    
    for log in filtered_logs:
        unit = log.get('unit', 'unknown')
        student_id = log.get('student_number', 'unknown')
        
        if unit not in structured_logs:
            structured_logs[unit] = {}
        if student_id not in structured_logs[unit]:
            structured_logs[unit][student_id] = []
        
        structured_logs[unit][student_id].append(log)
    
    # Zipファイルをメモリに作成
    zip_buffer = BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for unit in sorted(structured_logs.keys()):
            for student_id in sorted(structured_logs[unit].keys()):
                logs_for_student = structured_logs[unit][student_id]
                
                # JSON データの作成
                json_data = {
                    'unit': unit,
                    'student_id': student_id,
                    'class_display': logs_for_student[0].get('class_display', '') if logs_for_student else '',
                    'export_date': datetime.now().isoformat(),
                    'logs': logs_for_student
                }
                
                # ファイルパス: talk/{unit}/student_{student_id}.json
                file_path = f"talk/{unit}/student_{student_id}.json"
                
                # JSONファイルをzipに追加
                json_string = json.dumps(json_data, ensure_ascii=False, indent=2)
                zip_file.writestr(file_path, json_string.encode('utf-8'))
    
    zip_buffer.seek(0)
    filename = f"dialogue_logs_up_to_{download_date_str}.zip"
    
    print(f"[EXPORT_JSON] SUCCESS - exported JSON with {len(filtered_logs)} total logs")
    
    return Response(
        zip_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )

@app.route('/teacher/student_detail')
@require_teacher_auth
def student_detail():
    """児童の詳細ログページ"""
    # クラスと出席番号をクエリパラメータから取得
    class_param = request.args.get('class')
    class_num = normalize_class_value_int(class_param)
    seat_num = request.args.get('seat', type=int)
    student_id = request.args.get('student')
    unit = request.args.get('unit', '')
    
    # デフォルト日付を最新のログがある日付に設定
    try:
        available_dates_raw = get_available_log_dates()
        default_date = available_dates_raw[0] if available_dates_raw else datetime.now().strftime('%Y%m%d')
        # フロントエンド用に辞書形式に変換
        available_dates = [
            {'raw': d, 'formatted': f"{d[:4]}/{d[4:6]}/{d[6:8]}"}
            for d in available_dates_raw
        ]
    except Exception as e:
        print(f"[DETAIL] Error getting available dates: {str(e)}")
        default_date = datetime.now().strftime('%Y%m%d')
        available_dates = []
    
    selected_date = request.args.get('date', default_date)
    
    # 学習ログを読み込み
    logs = load_learning_logs(selected_date)
    
    # 該当する児童のログを抽出（クラスと出席番号で絞り込み）
    student_logs = []
    if class_num and seat_num:
        student_logs = [log for log in logs if 
                        log.get('class_num') == class_num and 
                        log.get('seat_num') == seat_num and 
                        (not unit or log.get('unit') == unit)]
    elif student_id:
        student_logs = [log for log in logs if 
                        str(log.get('student_number')) == str(student_id) and 
                        (not unit or log.get('unit') == unit)]
        if student_logs:
            class_num = student_logs[0].get('class_num') or class_num
            seat_num = student_logs[0].get('seat_num') or seat_num
    else:
        flash('クラスと出席番号が指定されていません。', 'error')
        return redirect(url_for('teacher_logs'))
    
    # 児童表示名
    if class_num and seat_num:
        student_display = f"{class_num}組{seat_num}番"
    elif student_id:
        student_display = f"ID: {student_id}"
    else:
        student_display = "対象の児童"
    
    if not student_logs:
        flash(f'{student_display}のログがありません。日付や単元を変更してお試しください。', 'warning')
    
    # 単元一覧を取得（フィルター用）
    all_units = list(set([log.get('unit') for log in logs if log.get('unit')]))
    
    return render_template('teacher/student_detail.html',
                         class_num=class_num,
                         seat_num=seat_num,
                         student_display=student_display,
                         unit=unit,
                         current_unit=unit,
                         current_date=selected_date,
                         logs=student_logs,
                         available_dates=available_dates,
                         units_data={unit_name: {} for unit_name in all_units},
                         teacher_id=session.get('teacher_id', 'teacher'))


# ===== 教師用ノート写真管理エンドポイント =====

@app.route('/api/teacher/students-by-class')
@require_teacher_auth
def api_students_by_class():
    """クラスごとの児童情報をJSON形式で返す"""
    students_by_class = {}
    
    # learning_progress.jsonから児童情報を取得
    if os.path.exists(LEARNING_PROGRESS_FILE):
        try:
            with open(LEARNING_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
            
            for class_num in ['1', '2', '3', '4', '5', '6']:
                students_by_class[class_num] = []
                
                if f'class_{class_num}' in progress_data:
                    class_data = progress_data[f'class_{class_num}']
                    for student_id in sorted(class_data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                        student_info = class_data[student_id]
                        students_by_class[class_num].append({
                            'number': student_id,
                            'name': student_info.get('name', f'学生{student_id}')
                        })
        except Exception as e:
            print(f"Error loading students: {e}")
    
    return jsonify(students_by_class)


# ===== 分析機能 =====

@app.route('/teacher/analysis_dashboard')
def analysis_dashboard():
    """教員用分析ダッシュボード"""
    return render_template('teacher/analysis_dashboard.html', units=UNITS)


@app.route('/teacher/analysis')
@require_teacher_auth
def teacher_analysis():
    """教員用分析ダッシュボード"""
    unit = request.args.get('unit', '')
    date = request.args.get('date', datetime.now().strftime('%Y%m%d'))
    
    try:
        # ログを読み込み
        logs = load_learning_logs(date)
        
        if unit:
            logs = [log for log in logs if log.get('unit') == unit]
        
        # 分析を実行
        analysis_result = analyze_predictions_and_reflections(logs)
        
        return jsonify({
            'success': True,
            'date': date,
            'unit': unit,
            'analysis': analysis_result,
            'log_count': len(logs)
        })
    except Exception as e:
        print(f"[ANALYSIS] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def analyze_predictions_and_reflections(logs):
    """予想と考察のテキスト分析 + 埋め込み + クラスタリング"""
    try:
        prediction_logs = [log for log in logs if log.get('log_type') == 'prediction_chat']
        reflection_logs = [log for log in logs if log.get('log_type') == 'reflection_chat']
        
        result = {
            'total_logs': len(logs),
            'prediction_chats': len(prediction_logs),
            'reflection_chats': len(reflection_logs),
            'predictions_by_unit': {},
            'reflections_by_unit': {},
            'text_analysis': {},
            'embeddings_analysis': {},
            'insights': {},
            'prompt_recommendations': {}
        }
        
        # 単元ごとに分類
        for log in prediction_logs:
            unit = log.get('unit', '不明')
            if unit not in result['predictions_by_unit']:
                result['predictions_by_unit'][unit] = []
            
            data = log.get('data', {})
            result['predictions_by_unit'][unit].append({
                'student': f"{log.get('class_num', 0)}_{log.get('seat_num', 0)}",
                'user_message': data.get('user_message', ''),
                'ai_response': data.get('ai_response', '')
            })
        
        for log in reflection_logs:
            unit = log.get('unit', '不明')
            if unit not in result['reflections_by_unit']:
                result['reflections_by_unit'][unit] = []
            
            data = log.get('data', {})
            result['reflections_by_unit'][unit].append({
                'student': f"{log.get('class_num', 0)}_{log.get('seat_num', 0)}",
                'user_message': data.get('user_message', ''),
                'ai_response': data.get('ai_response', '')
            })
        
        # テキスト分析
        for unit in result['predictions_by_unit']:
            prediction_messages = [
                p['user_message'] for p in result['predictions_by_unit'][unit] if p['user_message']
            ]
            reflection_messages = [
                r['user_message'] for r in result.get('reflections_by_unit', {}).get(unit, [])
                if r['user_message']
            ]
            
            result['text_analysis'][unit] = {
                'prediction': analyze_text(prediction_messages),
                'reflection': analyze_text(reflection_messages)
            }
            
            # 埋め込み + クラスタリング分析
            result['embeddings_analysis'][unit] = analyze_with_embeddings(
                prediction_messages, 
                reflection_messages, 
                unit
            )
            
            # インサイト生成
            result['insights'][unit] = generate_insights(
                prediction_messages,
                reflection_messages,
                result['text_analysis'][unit],
                unit
            )
            
            # プロンプト改善提案
            result['prompt_recommendations'][unit] = recommend_prompt_improvements(
                prediction_messages,
                reflection_messages,
                result['insights'][unit],
                unit
            )
        
        return result
    
    except Exception as e:
        print(f"[ANALYSIS] Analysis error: {e}")
        import traceback
        traceback.print_exc()
        return {'error': str(e)}


def analyze_with_embeddings(prediction_messages, reflection_messages, unit):
    """埋め込み + クラスタリング分析"""
    try:
        all_messages = prediction_messages + reflection_messages
        if len(all_messages) < 2:
            return {'clusters': [], 'cluster_count': 0}
        
        # テキスト埋め込みを取得
        embeddings = []
        for msg in all_messages:
            if msg.strip():
                embedding = get_text_embedding(msg)
                if embedding:
                    embeddings.append({
                        'text': msg,
                        'embedding': embedding,
                        'stage': 'prediction' if msg in prediction_messages else 'reflection'
                    })
        
        if len(embeddings) < 2:
            return {'clusters': [], 'cluster_count': 0}
        
        # クラスタリング（K-means）
        embedding_vectors = np.array([e['embedding'] for e in embeddings])
        
        # 適切なクラスタ数を決定（3～5）
        n_clusters = min(max(3, len(embeddings) // 3), 5)
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        clusters = kmeans.fit_predict(embedding_vectors)
        
        # クラスタを分類
        cluster_groups = {}
        for idx, (embedding, cluster_id) in enumerate(zip(embeddings, clusters)):
            if cluster_id not in cluster_groups:
                cluster_groups[cluster_id] = []
            cluster_groups[cluster_id].append(embedding)
        
        # クラスタの特徴を抽出
        cluster_summaries = []
        for cluster_id, items in cluster_groups.items():
            representative = items[0]['text']  # 代表テキスト
            stage_ratio = sum(1 for item in items if item['stage'] == 'prediction') / len(items)
            
            cluster_summaries.append({
                'cluster_id': int(cluster_id),
                'size': len(items),
                'representative_text': representative,
                'prediction_ratio': round(stage_ratio * 100, 1),
                'reflection_ratio': round((1 - stage_ratio) * 100, 1),
                'sample_texts': [item['text'] for item in items[:3]]
            })
        
        return {
            'clusters': cluster_summaries,
            'cluster_count': len(cluster_summaries),
            'total_messages': len(embeddings)
        }
    
    except Exception as e:
        print(f"[EMBEDDINGS] Error: {e}")
        return {'clusters': [], 'cluster_count': 0, 'error': str(e)}


def get_text_embedding(text):
    """テキストの埋め込みを取得（OpenAI Embeddings API）"""
    try:
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"[EMBEDDING_ERROR] {e}")
        return None


def generate_insights(prediction_messages, reflection_messages, text_analysis, unit):
    """ログから洞察を生成"""
    try:
        insights = []
        
        # 予想の多様性
        pred_analysis = text_analysis.get('prediction', {})
        pred_keywords = pred_analysis.get('keywords', [])
        if pred_keywords:
            insights.append(
                f"【{unit}の予想の特徴】\n"
                f"児童の予想には、以下のキーワードが頻出しています: "
                f"{', '.join([kw['word'] for kw in pred_keywords[:5]])}。\n"
                f"これは、児童が単元の核心的な概念に気づいていることを示唆しています。"
            )
        
        # 予想と考察の比較
        if prediction_messages and reflection_messages:
            insights.append(
                f"【予想から考察への学習過程】\n"
                f"予想段階で{len(prediction_messages)}件、考察段階で{len(reflection_messages)}件の発言がありました。\n"
                f"実験を通じて、児童の理解がどの程度深まったかを検証する機会があります。"
            )
        
        # 表現パターンから読み取る理解度
        pred_patterns = pred_analysis.get('patterns', {})
        refl_patterns = text_analysis.get('reflection', {}).get('patterns', {})
        
        causal_growth = (refl_patterns.get('causal_expressions', 0) - 
                        pred_patterns.get('causal_expressions', 0))
        if causal_growth > 0:
            insights.append(
                f"【因果関係の理解深化】\n"
                f"考察段階で因果表現（「だから」「なぜなら」）が"
                f"{causal_growth}回増加しました。\n"
                f"児童が実験結果を基に因果関係を構築しようとしていることが示唆されます。"
            )
        
        # 経験参照の活用
        exp_refs = pred_patterns.get('experience_references', 0)
        if exp_refs > 0:
            insights.append(
                f"【日常経験との結びつき】\n"
                f"予想段階で{exp_refs}回、児童の過去の経験が参照されました。\n"
                f"児童が既有知識と新しい学習を結びつけようとしていることが分かります。"
            )
        
        # 不確実性表現
        uncertainty = pred_patterns.get('uncertainty_expressions', 0)
        if uncertainty > 2:
            insights.append(
                f"【暫定的な理解の段階】\n"
                f"予想段階で不確実性表現（「たぶん」「かもしれない」）が{uncertainty}回ありました。\n"
                f"児童がまだ確実でない知識について探索的に考えていることが示唆されます。"
            )
        
        return insights
    
    except Exception as e:
        print(f"[INSIGHTS] Error: {e}")
        return [f"インサイト生成エラー: {str(e)}"]


def recommend_prompt_improvements(prediction_messages, reflection_messages, insights, unit):
    """プロンプト改善の提案"""
    try:
        recommendations = []
        
        # メッセージの平均長から判定
        avg_pred_len = np.mean([len(m) for m in prediction_messages if m]) if prediction_messages else 0
        avg_refl_len = np.mean([len(m) for m in reflection_messages if m]) if reflection_messages else 0
        
        # 短い回答が多い場合
        if avg_pred_len < 30:
            recommendations.append({
                'issue': '予想段階の回答が短い',
                'suggestion': '児童がより詳しく理由を述べるよう促すプロンプトを追加してください。\n'
                             'プロンプト例: 「どうしてそう思いますか？理由を詳しく教えてください。」',
                'priority': 'high'
            })
        
        if avg_refl_len < 40 and reflection_messages:
            recommendations.append({
                'issue': '考察段階の回答が短い',
                'suggestion': '実験結果と予想の違いをより深く考察するよう促してください。\n'
                             'プロンプト例: 「実験結果はどうでしたか？予想と同じでしたか？違うとしたら、'
                             'なぜだと思いますか？」',
                'priority': 'high'
            })
        
        # 回答数が少ない場合
        total_messages = len(prediction_messages) + len(reflection_messages)
        if total_messages < 6:
            recommendations.append({
                'issue': '対話の回数が少ない',
                'suggestion': 'AIの質問がより開かれた質問になるよう調整してください。\n'
                             'プロンプト例: 「はい/いいえで答えずに、児童の考えを引き出す質問を心がけてください」',
                'priority': 'medium'
            })
        
        # 経験参照が少ない場合
        if len(prediction_messages) > 0:
            has_experience_ref = any('前' in m or '経験' in m or 'やったことある' in m 
                                    for m in prediction_messages)
            if not has_experience_ref:
                recommendations.append({
                    'issue': '児童の経験を活かせていない',
                    'suggestion': '児童の過去の経験や日常生活との関連を引き出すよう促してください。\n'
                                 'プロンプト例: 「このような現象、今までに見たことがありますか？'
                                 '日常生活の中で、似たようなことを経験したことはありませんか？」',
                    'priority': 'medium'
                })
        
        # 予想と考察の大きなギャップ
        if prediction_messages and reflection_messages:
            if len(prediction_messages) > len(reflection_messages) * 2:
                recommendations.append({
                    'issue': '予想段階に比べて考察段階の対話が少ない',
                    'suggestion': 'AI が実験結果との比較をより丁寧に促すよう改善してください。\n'
                                 'プロンプト例: 「予想と実験結果を比べて、何が同じで何が違いましたか？」',
                    'priority': 'medium'
                })
        
        # インサイトから提案
        if any('不確実性' in i for i in insights):
            recommendations.append({
                'issue': '児童の予想に不確実性が残っている',
                'suggestion': 'より確実な理解を引き出すため、段階的な質問を用意してください。\n'
                             'プロンプト例: 段階的に理由を深掘りし、最終的に確実な理解に到達させる',
                'priority': 'low'
            })
        
        return recommendations
    
    except Exception as e:
        print(f"[RECOMMENDATIONS] Error: {e}")
        return [{'issue': 'エラー', 'suggestion': str(e), 'priority': 'low'}]


def analyze_text(messages):
    """テキスト分析（キーワード、頻度、文字数など）"""
    if not messages:
        return {
            'total_messages': 0,
            'average_length': 0,
            'keywords': [],
            'common_patterns': []
        }
    
    try:
        # 基本統計
        message_lengths = [len(msg) for msg in messages]
        
        # キーワード抽出（簡易版：名詞と重要な表現）
        keywords = extract_keywords(messages)
        
        # 一般的なパターン検出
        patterns = detect_patterns(messages)
        
        return {
            'total_messages': len(messages),
            'average_length': sum(message_lengths) / len(message_lengths) if message_lengths else 0,
            'max_length': max(message_lengths) if message_lengths else 0,
            'min_length': min(message_lengths) if message_lengths else 0,
            'keywords': keywords[:10],  # トップ10
            'patterns': patterns
        }
    
    except Exception as e:
        print(f"[TEXT_ANALYSIS] Error: {e}")
        return {'error': str(e)}


def extract_keywords(messages):
    """キーワード抽出（日本語対応）"""
    try:
        import re
        from collections import Counter
        
        # 複合メッセージを結合
        combined_text = ' '.join(messages)
        
        # ひらがなとカタカナの単語を抽出
        # 3文字以上の連続した仮名を抽出
        hiragana_pattern = r'[ぁ-ん]{3,}'
        katakana_pattern = r'[ァ-ヴー]{3,}'
        kanji_pattern = r'[\u4e00-\u9fff]{2,}'
        
        words = []
        words.extend(re.findall(hiragana_pattern, combined_text))
        words.extend(re.findall(katakana_pattern, combined_text))
        words.extend(re.findall(kanji_pattern, combined_text))
        
        # ストップワード（一般的な助詞などを除外）
        stopwords = {'思う', 'ます', 'です', 'ある', 'する', 'なる', 'いる', 'できる', 'みたい', 'ような', 'いっぱい', 'すごく'}
        words = [w for w in words if w not in stopwords]
        
        # 頻度を計算
        word_freq = Counter(words)
        
        # 頻度順に返す
        return [{'word': word, 'count': count} for word, count in word_freq.most_common()]
    
    except Exception as e:
        print(f"[KEYWORD_EXTRACTION] Error: {e}")
        return []


def detect_patterns(messages):
    """パターン検出（予想の表現、因果関係など）"""
    try:
        patterns = {
            'prediction_expressions': 0,  # 「〜だと思う」「〜と思う」など
            'causal_expressions': 0,      # 「〜だから」「なぜなら」など
            'comparison_expressions': 0,   # 「〜より」「〜ほうが」など
            'experience_references': 0,    # 「前に」「この前」など
            'uncertainty_expressions': 0  # 「たぶん」「かもしれない」など
        }
        
        prediction_keywords = ['思う', 'と思う', 'だと思う', 'と予想']
        causal_keywords = ['だから', 'なぜなら', 'ので', 'わけ']
        comparison_keywords = ['より', 'ほうが', '比べて', 'より大きい']
        experience_keywords = ['前に', 'この前', '経験', 'やったことある']
        uncertainty_keywords = ['たぶん', 'かもしれない', 'わからない', 'かな']
        
        for message in messages:
            for keyword in prediction_keywords:
                if keyword in message:
                    patterns['prediction_expressions'] += 1
            for keyword in causal_keywords:
                if keyword in message:
                    patterns['causal_expressions'] += 1
            for keyword in comparison_keywords:
                if keyword in message:
                    patterns['comparison_expressions'] += 1
            for keyword in experience_keywords:
                if keyword in message:
                    patterns['experience_references'] += 1
            for keyword in uncertainty_keywords:
                if keyword in message:
                    patterns['uncertainty_expressions'] += 1
        
        return patterns
    
    except Exception as e:
        print(f"[PATTERN_DETECTION] Error: {e}")
        return {}


if __name__ == '__main__':
    # 環境変数からポート番号を取得（CloudRun用）
    port = int(os.environ.get('PORT', 5014))
    # 本番環境ではdebug=False
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    
    # ============================================================================
    # Windows / デザリング環境向けスレッド・タイムアウト設定
    # ============================================================================
    # デザリング環境での 500 エラー軽減: スレッド数を 15 程度に設定
    # (参考: デフォルトは 4 threads。高さすぎるとレート制限に達する)
    threads = int(os.environ.get('WAITRESS_THREADS', 15))
    channel_timeout = int(os.environ.get('WAITRESS_CHANNEL_TIMEOUT', 120))
    
    print(f"[INIT] Starting ScienceBuddy with:")
    print(f"  - Port: {port}")
    print(f"  - Flask ENV: {os.environ.get('FLASK_ENV', 'development')}")
    print(f"  - Threads: {threads}")
    print(f"  - Channel Timeout: {channel_timeout}s")
    print(f"  - ngrok URL: {os.environ.get('NGROK_URL', 'Not set (using local)')}")
    
    # Try to use Waitress if available (recommended for production on Windows/macOS)
    try:
        from waitress import serve
        # When using Waitress, disable Flask's debugger
        # スレッド数とタイムアウトを設定して、デザリング環境での接続リセットを軽減
        serve(
            app, 
            host='0.0.0.0', 
            port=port,
            _thread_count=threads,
            _channel_timeout=channel_timeout,
            _http10_logger=None,
            _quiet=False
        )
    except Exception as e:
        print(f"[INIT] Waitress failed ({e}), falling back to Flask development server")
        # Fallback to Flask's built-in server for development
        app.run(debug=debug_mode, host='0.0.0.0', port=port)
