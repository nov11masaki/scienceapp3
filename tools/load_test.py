#!/usr/bin/env python3
"""Simple load tester using threads + requests.

Usage:
  python tools/load_test.py --url http://localhost:5014/ --concurrency 30 --requests-per-worker 10

This will start `concurrency` worker threads; each worker will perform
`requests_per_worker` sequential requests to the target URL. The script
measures per-request latency and prints a summary.

NOTE: This script uses the `requests` library which is already in
`requirements.txt` for the project. It does NOT call heavy endpoints
like the OpenAI-backed summary endpoint by default; point `--url`
to whichever route you want to test.
"""
import argparse
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests


def worker_task(url, n_requests, pause_between=0.0, timeout=30):
    results = []
    session = requests.Session()
    for i in range(n_requests):
        start = time.time()
        try:
            if worker_task.method == 'GET':
                r = session.get(url, timeout=timeout)
            else:
                # POST with small JSON payload
                r = session.post(url, json={'dummy': 'data'}, timeout=timeout)
            latency = time.time() - start
            results.append((r.status_code, latency, None))
        except Exception as e:
            latency = time.time() - start
            results.append((None, latency, str(e)))
        if pause_between:
            time.sleep(pause_between)
    return results


def run_load_test(url, concurrency, requests_per_worker, pause_between=0.0):
    all_latencies = []
    statuses = {}
    start_all = time.time()
    futures = []
    exceptions = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for _ in range(concurrency):
            futures.append(ex.submit(worker_task, url, requests_per_worker, pause_between))

        for fut in as_completed(futures):
            res = fut.result()
            for status, latency, exc in res:
                all_latencies.append(latency)
                statuses[status] = statuses.get(status, 0) + 1
                if exc:
                    exceptions[exc] = exceptions.get(exc, 0) + 1

    total_time = time.time() - start_all
    total_requests = len(all_latencies)
    success = sum(v for k, v in statuses.items() if k and 200 <= k < 400)
    failures = total_requests - success

    print("\n=== Load Test Summary ===")
    print(f"Target URL: {url}")
    print(f"Concurrency workers: {concurrency}")
    print(f"Requests per worker: {requests_per_worker}")
    print(f"Total requests: {total_requests}")
    print(f"Elapsed time: {total_time:.2f}s")
    if total_requests:
        print(f"Requests/sec: {total_requests / total_time:.2f}")
        print(f"Success: {success}, Failures: {failures}")
        print(f"Latency p50: {statistics.median(all_latencies):.3f}s")
        print(f"Latency p90: {statistics.quantiles(all_latencies, n=10)[8]:.3f}s")
        print(f"Latency mean: {statistics.mean(all_latencies):.3f}s")
        print(f"Latency max: {max(all_latencies):.3f}s")
    print("Status codes distribution:")
    for k, v in sorted(statuses.items(), key=lambda x: (x[0] is None, x[0])):
        print(f"  {k}: {v}")
    if exceptions:
        print("\nTop exception messages:")
        for exc, cnt in sorted(exceptions.items(), key=lambda x: -x[1])[:10]:
            print(f"  {cnt}x: {exc}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help='Target URL (include http://)')
    parser.add_argument('--concurrency', type=int, default=30, help='Number of concurrent worker threads')
    parser.add_argument('--requests-per-worker', type=int, default=10, help='Requests each worker performs')
    parser.add_argument('--pause', type=float, default=0.0, help='Pause (seconds) between requests in each worker')
    parser.add_argument('--method', choices=['GET', 'POST'], default='GET', help='HTTP method to use')
    return parser.parse_args()


def main():
    args = parse_args()
    # attach method to worker_task for simple access
    worker_task.method = args.method

    run_load_test(args.url, args.concurrency, args.requests_per_worker, args.pause)


if __name__ == '__main__':
    main()
