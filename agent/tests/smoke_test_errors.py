"""Opt-in real training/report/Agent smoke using a <=16-sample test version."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--dataset-version', required=True)
    parser.add_argument('--legacy-run')
    parser.add_argument('--confirm-test-training', required=True, action='store_true')
    args = parser.parse_args()
    credentials = json.loads(Path(args.credentials).read_text())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ''

    def request(path, body=None):
        headers = {'Content-Type': 'application/json'}
        if csrf:
            headers['X-CSRF-Token'] = csrf
        req = urllib.request.Request('http://127.0.0.1:8001' + path, None if body is None else json.dumps(body).encode(), headers)
        with opener.open(req, timeout=30) as response:
            return json.load(response)

    auth = request('/api/auth/login', {k: credentials[k] for k in ('email', 'password')})
    csrf = auth['csrf_token']
    version = request('/api/agent-tools/invoke/datasets.get_version', {'arguments': {'dataset_version_id': args.dataset_version}})['data']
    assert 1 <= version['sample_count'] <= 16, 'only tiny test fixtures are permitted'
    test_count = sum(version['split_counts'].get('test', {}).values())
    assert test_count > 0, 'independent test split required'
    plan = {'dataset_version_id': args.dataset_version, 'backbone_key': 'color_stats_v1',
            'name': 'Agent 测试集错分工具验收', 'max_attempts': 1, 'image_size': 128, 'feature_batch_size': 2,
            'head_config': {'epochs': 1, 'batch_size': 4, 'learning_rate': 0.001}}
    validated = request('/api/agent-tools/invoke/training.validate_plan', {'arguments': plan})['data']['normalized_arguments']
    operation = str(uuid.uuid4())
    receipt = request('/api/agent-tools/approvals/training.submit_plan', {'arguments': validated, 'operation_id': operation})
    training = request('/api/agent-tools/invoke/training.submit_plan', {'arguments': receipt['arguments'], 'approval': receipt['approval'], 'operation_id': operation})['data']['training_run']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        current = request('/api/training-runs/' + training['id'])['training_run']
        if current['status'] == 'succeeded':
            break
        assert current['status'] not in ('failed', 'cancelled'), current.get('error')
        time.sleep(1)
    assert current['status'] == 'succeeded', 'training did not finish'
    analysis = request('/api/agent-tools/invoke/training.analyze_test_errors', {'arguments': {'run_id': training['id'], 'max_examples': 2, 'max_confusion_pairs': 2}})['data']
    assert analysis['availability'] == 'available', analysis
    assert analysis['test_evidence']['sample_count'] == test_count
    assert analysis['test_evidence']['evaluation_split'] == 'test'
    assert len(analysis['test_evidence']['examples']) <= 2
    legacy = None
    if args.legacy_run:
        legacy = request('/api/agent-tools/invoke/training.analyze_test_errors', {'arguments': {'run_id': args.legacy_run}})['data']
        assert legacy['availability'] == 'unavailable', 'historical mixed metrics were incorrectly treated as test'
    conv = request('/api/agent/conversations', {'title': '测试集错分分析验收'})
    run = request('/api/agent/runs', {'conversation_id': conv['id'], 'request_key': str(uuid.uuid4()),
                                    'input': f'请使用 training.analyze_test_errors 分析训练 {training["id"]} 最终测试集上分类错误的可能原因。区分证据与推测，不要重训或推理，也不要把验证集混合指标当测试结果。'})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = request('/api/agent/runs/' + run['id'])
        if run['status'] in ('succeeded', 'failed', 'unknown', 'waiting_confirmation', 'waiting_auth'):
            break
        time.sleep(1)
    events = request('/api/agent/runs/' + run['id'] + '/events?format=json')['items']
    used = [item['result']['tool'] for event in events if event['kind'] == 'tool_results'
            for item in event['data'] if item.get('result')]
    output = '.finevision-agent-test/test-errors-results.json'
    Path(output).write_text(json.dumps({'training': current, 'analysis': analysis, 'legacy': legacy, 'agent_run': run, 'events': events}, ensure_ascii=False, indent=2))
    print(json.dumps({'training_status': current['status'], 'test_samples': test_count, 'errors': analysis['test_evidence']['error_count'],
                      'availability': analysis['availability'], 'agent_status': run['status'], 'tools': used, 'output': output}, ensure_ascii=False))
    assert run['status'] == 'succeeded' and 'training.analyze_test_errors' in used


if __name__ == '__main__':
    main()
