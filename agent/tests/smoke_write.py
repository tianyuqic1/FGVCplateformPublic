"""Explicitly opted-in live write smoke: one tiny color-statistics training.
Requires an existing ready test dataset of <= 16 samples; never changes its data.
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
    parser.add_argument('--dataset-version', required=True)
    parser.add_argument('--confirm-test-training', action='store_true', required=True)
    args = parser.parse_args()
    credentials = json.loads(Path(args.credentials).read_text())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ''

    def request(path, body=None, method=None):
        headers = {'Content-Type': 'application/json'}
        if csrf:
            headers['X-CSRF-Token'] = csrf
        req = urllib.request.Request('http://127.0.0.1:8001' + path, None if body is None else json.dumps(body).encode(), headers, method=method)
        with opener.open(req, timeout=20) as response:
            return json.load(response)

    auth = request('/api/auth/login', {key: credentials[key] for key in ('email', 'password')})
    csrf = auth['csrf_token']
    detail = request('/api/agent-tools/invoke/datasets.get_version', {'arguments': {'dataset_version_id': args.dataset_version}})
    # Dataset-version metadata differs from its list envelope; read the authoritative sample count.
    metadata = detail['data']
    candidate = metadata.get('dataset_version', metadata.get('version', metadata))
    count = candidate.get('sample_count', 0)
    assert 1 <= count <= 16, 'live write smoke only permits a tiny test dataset'
    conv = request('/api/agent/conversations', {'title': 'Agent 写操作与跟踪验收'})
    command = {'conversation_id': conv['id'], 'request_key': str(uuid.uuid4()), 'input': f'这是平台端到端验收。请准备一个训练方案：dataset_version_id={args.dataset_version}，backbone_key=color_stats_v1，name=Agent 验收小样本，max_attempts=1，image_size=128，feature_batch_size=2。不要更换骨干，不使用 GPU 大模型，不指定 head_config。请调用 training.validate_plan，成功后提出 training.submit_plan，等待界面确认。'}
    run = request('/api/agent/runs', command)

    def wait_run(target):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = request('/api/agent/runs/' + run['id'])
            if current['status'] == target:
                return current
            assert current['status'] not in ('unknown', 'failed', 'cancelled', 'waiting_auth'), (current['status'], current.get('error'))
            time.sleep(1)
        raise AssertionError('run timeout')

    run = wait_run('waiting_confirmation')
    proposal = run['proposal']
    plan = proposal['arguments']
    assert proposal['call']['function']['name'] == 'training.submit_plan'
    assert plan['dataset_version_id'] == args.dataset_version
    assert plan['backbone_key'] == 'color_stats_v1' and plan['max_attempts'] == 1
    assert plan.get('name') == 'Agent 验收小样本'
    confirmation = {'operation_id': proposal['operation_id']}
    # Replay the browser confirmation: durable CAS + platform idempotency must produce one training.
    for _ in range(3):
        request('/api/agent/runs/' + run['id'] + '/confirm', confirmation)
    run = wait_run('succeeded')
    events = request('/api/agent/runs/' + run['id'] + '/events?format=json')['items']
    submitted = [event['data']['data']['training_run'] for event in events if event['kind'] == 'tool_result' and event['data'].get('tool') == 'training.submit_plan']
    assert len(submitted) == 1, 'duplicate training tool dispatch'
    training = submitted[0]
    watch = request('/api/agent/watches', {'conversation_id': conv['id'], 'run_id': training['id']})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        watches = request('/api/agent/watches?conversation_id=' + conv['id'])['items']
        current = next(item for item in watches if item['id'] == watch['id'])
        if current['status'] == 'completed':
            break
        time.sleep(2)
    result = {'conversation_id': conv['id'], 'agent_run': run, 'training': training, 'watch': current, 'confirmation_replayed': 3, 'events': events}
    output = '.finevision-agent-test/write-results.json'
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'agent_status': run['status'], 'training_status': current['observed_status'], 'watch_status': current['status'], 'training_id': training['id'], 'output': output}, ensure_ascii=False))
    assert current['status'] == 'completed' and current['observed_status'] == 'succeeded', 'training or watch did not finish successfully'


if __name__ == '__main__':
    main()
