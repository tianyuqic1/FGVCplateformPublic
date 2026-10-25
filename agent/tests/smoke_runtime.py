"""Opt-in smoke against a running stack and its configured model.

Uses a separate test account file; never prints or persists cookies/tokens in results.
Run: python3 agent/tests/smoke_runtime.py --credentials .finevision-agent-test/session.json
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--base', default='http://127.0.0.1:8001')
    parser.add_argument('--output', default='.finevision-agent-test/results.json')
    args = parser.parse_args()
    credentials = json.loads(Path(args.credentials).read_text())
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = ''

    def request(path, body=None, method=None):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if csrf:
            headers['X-CSRF-Token'] = csrf
        req = urllib.request.Request(args.base + path, None if body is None else json.dumps(body).encode(), headers, method=method)
        with opener.open(req, timeout=20) as response:
            return json.load(response)

    auth = request('/api/auth/login', {key: credentials[key] for key in ('email', 'password')})
    csrf = auth['csrf_token']
    conv = request('/api/agent/conversations', {'title': 'Agent 真实模型验收'})
    command = {'conversation_id': conv['id'], 'request_key': str(uuid.uuid4()), 'input': '请查询当前平台的数据集列表和 Worker 计算容量，必须使用 datasets.list 和 workers.get_capacity 工具。用中文简要说明真实查询结果，不要创建训练任务。'}
    run = request('/api/agent/runs', command)
    replay = request('/api/agent/runs', command)
    assert replay['id'] == run['id'], 'duplicate submission created another Run'
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = request('/api/agent/runs/' + run['id'])
        if run['status'] in ('succeeded', 'failed', 'unknown', 'waiting_auth', 'waiting_confirmation', 'cancelled'):
            break
        time.sleep(2)
    trace = request('/api/agent/runs/' + run['id'] + '/events?format=json')
    results = {'conversation_id': conv['id'], 'run': run, 'events': trace['items'], 'idempotency_passed': True}
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2))
    kinds = [item['kind'] for item in trace['items']]
    print(json.dumps({'status': run['status'], 'rounds': run['rounds'], 'tools': run['tools'], 'events': kinds, 'error': run.get('error', ''), 'output': args.output}, ensure_ascii=False))
    assert run['status'] == 'succeeded', 'live model round did not succeed; inspect sanitized run result'
    assert run['tools'] >= 2 and 'tool_results' in kinds, 'model did not exercise platform tools'
    assert run['output'], 'empty assistant answer'


if __name__ == '__main__':
    main()
