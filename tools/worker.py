#!/usr/bin/env python3
"""Simple RQ worker launcher for development.

Run this in the project virtualenv to start a worker that processes
summary jobs. Requires Redis reachable at REDIS_URL.

Usage:
  source .venv/bin/activate
  python tools/worker.py

"""
import os
from redis import from_url
from rq import Worker, Queue, Connection

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

if __name__ == '__main__':
    conn = from_url(REDIS_URL)
    with Connection(conn):
        q = Queue('default')
        worker = Worker([q], name='summary-worker')
        print(f"Starting RQ worker listening on {REDIS_URL} (queue 'default')")
        worker.work()
