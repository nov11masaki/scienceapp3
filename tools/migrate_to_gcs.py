#!/usr/bin/env python3
"""Migrate local JSON persistence files to GCS.

This script uploads `learning_progress.json`, `session_storage.json`, and
`summary_storage.json` to the configured GCS bucket under sensible paths.
Requires GCP credentials to be available to the environment.

Usage:
  export GCP_PROJECT_ID=your-project
  export GCS_BUCKET_NAME=your-bucket
  python tools/migrate_to_gcs.py
"""
import os
import json
from google.cloud import storage

FILES = [
    ('learning_progress.json', 'learning_progress.json'),
    ('session_storage.json', 'session_storage.json'),
    ('summary_storage.json', 'summary_storage.json'),
]

BUCKET = os.environ.get('GCS_BUCKET_NAME')
PROJECT = os.environ.get('GCP_PROJECT_ID')

if not BUCKET or not PROJECT:
    print('Please set GCS_BUCKET_NAME and GCP_PROJECT_ID environment variables')
    raise SystemExit(1)

client = storage.Client(project=PROJECT)
bucket = client.bucket(BUCKET)

for local, remote in FILES:
    if not os.path.exists(local):
        print(f"Skipping {local} (not found)")
        continue
    with open(local, 'r', encoding='utf-8') as f:
        try:
            content = f.read()
        except Exception as e:
            print(f"Failed to read {local}: {e}")
            continue
    blob = bucket.blob(f"migrated/{remote}")
    blob.upload_from_string(content, content_type='application/json')
    print(f"Uploaded {local} -> gs://{BUCKET}/migrated/{remote}")
