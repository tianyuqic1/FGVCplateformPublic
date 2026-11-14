"""Deployment regressions; all mutations use disposable directories / fake Docker."""
from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('deployment_manager', ROOT / 'deploy/manage.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)
DOCKER = shutil.which('docker')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='finevision-deploy-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['deploy.sh', 'deploy/manage.py', 'scripts/demo-up.sh', 'weights/manifest.json',
                     '.env.example', 'docker-compose.yml', 'compose.observability.yml']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.log = self.root / 'docker.jsonl'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        code = '''#!/usr/bin/env python3
import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['DOCKER_TEST_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
joined=' '.join(args)
if os.environ.get('DOCKER_TEST_FAIL') and os.environ['DOCKER_TEST_FAIL'] in joined: sys.exit(17)
if args[:2]==['compose','version']: print(os.environ.get('DOCKER_TEST_VERSION','2.35.0')); sys.exit(0)
if args[0]=='run' and 'python:3.12-alpine' in args:
 index=args.index('python:3.12-alpine')
 sys.exit(subprocess.call([os.environ['DOCKER_TEST_PYTHON'],*args[index+2:]]))
if 'ps' in args and '--format' in args:
 rows=[{'Service':s,'State':'running','Health':'healthy'} for s in json.loads(os.environ['DOCKER_TEST_SERVICES'])]
 if os.environ.get('DOCKER_TEST_UNHEALTHY'): rows[0]['Health']='unhealthy'
 print(json.dumps(rows))
if 'port' in args: print('0.0.0.0:5173')
'''
        (self.bin / 'docker').write_text(code)
        (self.bin / 'docker').chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        DOCKER_TEST_LOG=str(self.log),
                        DOCKER_TEST_PYTHON=sys.executable,
                        DOCKER_TEST_SERVICES=json.dumps(manager.CORE + manager.WORKERS + manager.OBSERVABILITY))
        self.env.pop('FINEVISION_DEPLOY_BOOTSTRAP', None)

    def cli(self, *args, ok=True, env=None, script='deploy.sh'):
        result = subprocess.run(['bash', str(self.root / script), *args], cwd=self.root,
                                env=env or self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def state(self):
        return json.loads((self.root / '.deploy/finevision/state.json').read_text())

    def tiny_models(self):
        doc = json.loads((self.root / 'weights/manifest.json').read_text())
        for i, item in enumerate(doc['weights'] + doc['external_weights']):
            raw = ('weight fixture ' + str(i)).encode()
            item.update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            path = manager.cache_path(self.root, item)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            if item.get('lfs_path'):
                path = self.root / item['lfs_path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        (self.root / 'weights/manifest.json').write_text(json.dumps(doc))
        return doc


class CommandTests(Fixture):
    def test_bootstrap_reuses_saved_external_env_and_mounts_it(self):
        with tempfile.TemporaryDirectory(prefix='finevision-external-config-') as folder:
            config = Path(folder) / 'custom.env'
            shutil.copy2(self.root / '.env.example', config)
            env = dict(self.env, FINEVISION_DEPLOY_BOOTSTRAP='container')
            self.cli('--env-file', str(config), '--with-worker', '--no-build', env=env)
            self.log.unlink()
            self.cli('status', env=env)
            helpers = [c for c in self.calls() if c[0] == 'run']
            self.assertTrue(helpers)
            # First call only discovers the saved path; subsequent helpers can read it.
            self.assertTrue(any(str(config) + ':' + str(config) in c for c in helpers))
            compose = next(c for c in self.calls() if 'ps' in c)
            self.assertEqual(compose[compose.index('--env-file') + 1], str(config))
            self.assertIn('python-training-worker', compose)

    def test_old_host_python_uses_bootstrap_container(self):
        wrapper = self.bin / 'python3'
        wrapper.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 1; fi\nexec '
                           + str(sys.executable) + ' "$@"\n')
        wrapper.chmod(0o755)
        result = self.cli('--dry-run')
        self.assertIn('Local workers: disabled', result.stdout)
        self.assertTrue(any(c[0] == 'run' and 'python:3.12-alpine' in c for c in self.calls()))

    def test_old_compose_rejected_before_start(self):
        self.cli('--no-build', env=dict(self.env, DOCKER_TEST_VERSION='2.29.0'), ok=False)
        self.assertFalse(any('up' in c for c in self.calls()))
        self.assertFalse((self.root / '.deploy').exists())

    def test_default_preview_is_readonly_and_excludes_local_workers(self):
        result = self.cli('--dry-run')
        self.assertIn('Local workers: disabled', result.stdout)
        self.assertNotIn('python-training-worker', result.stdout)
        self.assertIn('python-artifact-runtime', result.stdout)
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse((self.root / '.deploy').exists())
        self.assertEqual(self.calls(), [])

    def test_models_preview_enables_workers_and_unknown_names_fail(self):
        result = self.cli('--models', 'dinov3-vits,inat-mobilenetv3', '--device', 'cuda', '--dry-run')
        self.assertIn('Local workers: enabled', result.stdout)
        self.assertIn('Device: cuda', result.stdout)
        self.assertIn('98,377,816 bytes', result.stdout)
        self.cli('--models', 'unknown-model', '--dry-run', ok=False)
        self.assertEqual(self.calls(), [])

    def test_invalid_arguments_fail_before_docker(self):
        for args in [('--device', 'auto'), ('--device', 'cuda'), ('--models', ''), ('--with-annotation',),
                     ('--env-file', 'absent.env'), ('--project-name', '../bad'), ('--timeout', '-1'),
                     ('--models',), ('--follow',), ('restart', '--device', 'cuda')]:
            with self.subTest(args=args):
                self.cli(*args, '--dry-run', ok=False)
        self.assertEqual(self.calls(), [])

    def test_first_up_has_no_worker_no_host_npm_and_private_state(self):
        self.cli('--no-build')
        state = self.state()
        self.assertFalse(state['options']['worker'])
        calls = self.calls()
        up = next(c for c in calls if 'up' in c)
        self.assertNotIn('python-training-worker', up)
        self.assertNotIn('hardware-collector', up)
        self.assertIn('--wait', up)
        self.assertNotIn('down', sum(calls, []))
        self.assertEqual(stat.S_IMODE((self.root / '.env').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / '.deploy/finevision/state.json').stat().st_mode), 0o600)
        self.assertNotIn('SIGNING_KEY', json.dumps(state))
        self.assertFalse((self.root / '.deploy/finevision/lock').exists())

    def test_compute_image_built_first_even_without_local_workers(self):
        self.cli()
        builds = [c for c in self.calls() if 'build' in c]
        self.assertEqual(builds[0][-2:], ['build', 'python-training-worker'])
        self.assertIn('migrate', builds[1])
        self.assertNotIn('python-inference-runtime', builds[1])

    def test_pull_only_fetches_third_party_images(self):
        self.cli('--pull', '--with-worker', '--with-observability', '--no-build')
        pull = next(c for c in self.calls() if 'pull' in c)
        targets = pull[pull.index('pull') + 1:]
        for remote in ['postgres', 'pretrained-weight-init', 'grafana']:
            self.assertIn(remote, targets)
        for local in ['python-artifact-runtime', 'go-llm-gateway', 'outbox-relay',
                      'deployment-relay', 'observability-console', 'frontend', 'agent-service']:
            self.assertNotIn(local, targets)

    def test_saved_cuda_worker_composition_survives_restart_stop_and_omission(self):
        self.cli('--with-worker', '--device', 'cuda', '--with-observability', '--no-build')
        state = self.state()
        self.log.unlink()
        self.cli('--no-build')
        self.assertEqual(self.state()['options'], state['options'])
        self.cli('restart')
        self.cli('stop')
        for call in self.calls():
            if 'restart' in call or 'stop' in call:
                self.assertIn('python-training-worker', call)
                self.assertIn('observability-console', call)
        self.assertFalse(any('down' in call or '-v' in call for call in self.calls()))
        self.assertEqual(self.state()['options'], state['options'])

    def test_config_priority_and_no_shell_execution(self):
        cfg = self.root / '.env.server'
        cfg.write_text('FINEVISION_DEPLOY_WORKER=true\nFINEVISION_DEPLOY_DEVICE=cuda\nFINEVISION_DEPLOY_MODEL_LIST=dinov3-vits\n')
        result = self.cli('--env-file', str(cfg), '--device', 'cpu', '--dry-run')
        self.assertIn('Device: cpu', result.stdout)
        self.assertIn('Local workers: enabled', result.stdout)
        cfg.write_text('FINEVISION_DEPLOY_DEVICE=$(touch pwned)\n')
        self.cli('--env-file', str(cfg), '--dry-run', ok=False)
        self.assertFalse((self.root / 'pwned').exists())

    def test_existing_secrets_preserved_and_duplicates_rejected(self):
        cfg = self.root / '.env'
        cfg.write_text('FINEVISION_AGENT_DB_PASSWORD=known-private-password\nFINEVISION_AGENT_DELEGATION_KEY=\n')
        result = self.cli('--no-build')
        self.assertIn('FINEVISION_AGENT_DB_PASSWORD=known-private-password', cfg.read_text())
        self.assertNotIn('known-private-password', result.stdout + result.stderr)
        cfg.write_text('FINEVISION_AGENT_DB_PASSWORD=a\nFINEVISION_AGENT_DB_PASSWORD=b\n')
        self.cli('--no-build', ok=False)

    def test_failed_start_or_failed_health_does_not_overwrite_saved_selection(self):
        self.cli('--no-build')
        before = self.state()
        self.cli('--with-worker', '--no-build', ok=False, env=dict(self.env, DOCKER_TEST_FAIL='up -d'))
        self.assertEqual(self.state(), before)
        self.cli('--with-worker', '--no-build', ok=False, env=dict(self.env, DOCKER_TEST_UNHEALTHY='1'))
        self.assertEqual(self.state(), before)
        self.assertFalse((self.root / '.deploy/finevision/lock').exists())

    def test_gpu_failure_does_not_start_services_or_save(self):
        self.cli('--with-worker', '--device', 'cuda', '--no-build', ok=False,
                 env=dict(self.env, DOCKER_TEST_FAIL='nvidia-smi'))
        self.assertFalse(any('up' in c for c in self.calls()))
        self.assertFalse((self.root / '.deploy/finevision/state.json').exists())

    def test_live_project_lock_is_not_removed_by_contender(self):
        lock = self.root / '.deploy/finevision/lock'
        lock.mkdir(parents=True)
        (lock / 'pid').write_text(str(os.getpid()))
        self.cli('--no-build', ok=False)
        self.assertTrue(lock.exists())
        self.assertFalse(any('up' in c for c in self.calls()))

    def test_legacy_wrapper_keeps_old_worker_gpu_semantics(self):
        result = self.cli('--gpu', '--skip-smoke', '--dry-run', script='scripts/demo-up.sh')
        self.assertIn('Local workers: enabled', result.stdout)
        self.assertIn('Device: cuda', result.stdout)
        self.assertIn('deprecated', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_model_commands_do_not_start_services(self):
        self.tiny_models()
        self.cli('models', 'pull')
        self.cli('models', 'check')
        self.assertFalse((self.root / '.env').exists())
        self.assertEqual(self.calls(), [])

    def test_download_failure_prevents_start_and_preserves_state(self):
        doc = self.tiny_models()
        self.cli('--no-build')
        before = self.state()
        external = doc['external_weights'][0]
        manager.cache_path(self.root, external).unlink()
        external['source_url'] = 'https://127.0.0.1:1/not-a-weight'
        (self.root / 'weights/manifest.json').write_text(json.dumps(doc))
        self.log.unlink()
        self.cli('--with-models', '--no-build', ok=False)
        self.assertFalse(any('up' in call for call in self.calls()))
        self.assertEqual(self.state(), before)

    def test_explicit_models_mounts_verified_cache_and_uses_strict_promotion(self):
        self.tiny_models()
        self.cli('--models', 'dinov3-vits,inat-mobilenetv3', '--no-build')
        state = self.state()
        self.assertTrue(state['options']['worker'])
        overlay = json.loads(Path(state['override_file']).read_text())['services']
        command = overlay['pretrained-weight-init']['entrypoint'][-1]
        self.assertIn('test "$${actual%% *}"', command)
        self.assertNotIn('exit 0', command)
        self.assertNotIn('imagenet', command)
        mount = overlay['python-training-worker']['volumes'][0]
        self.assertTrue(mount['read_only'])
        self.assertNotIn('.env', mount['source'])
        self.assertIn('FINEVISION_INAT_MOBILENETV3_WEIGHT', overlay['python-inference-runtime']['environment'])

    def test_logs_service_must_belong_to_selection(self):
        self.cli('--no-build')
        self.cli('logs', 'agent-service', '--tail', '20')
        self.cli('logs', 'not-a-service', ok=False)
        self.assertFalse(any('not-a-service' in call for call in self.calls()))

    def test_separate_project_has_separate_saved_state(self):
        self.cli('--project-name', 'isolated-test', '--no-build')
        self.assertTrue((self.root / '.deploy/isolated-test/state.json').is_file())
        self.assertFalse((self.root / '.deploy/finevision/state.json').exists())


class WeightTests(Fixture):
    def test_reuses_valid_lfs_file_without_network_and_rejects_corruption(self):
        item = self.tiny_models()['weights'][0]
        manager.cache_path(self.root, item).unlink()
        with patch.object(manager, 'urlopen', side_effect=AssertionError('network should not be used')):
            with contextlib.redirect_stdout(io.StringIO()):
                manager.prepare_weight(self.root, item)
        self.assertTrue(manager.verified(manager.cache_path(self.root, item), item))
        manager.cache_path(self.root, item).write_bytes(b'tampered')
        self.assertFalse(manager.verified(manager.cache_path(self.root, item), item))

    def test_bad_download_never_replaces_cache_and_cleans_temporary_files(self):
        item = self.tiny_models()['external_weights'][0]
        target = manager.cache_path(self.root, item)
        target.write_bytes(b'old corrupt cache')
        for raw in (b'short', b'x' * (item['size_bytes'] + 1), b'x' * item['size_bytes']):
            with self.subTest(raw=raw), patch.object(manager, 'urlopen', return_value=io.BytesIO(raw)):
                with self.assertRaises(ValueError):
                    manager.prepare_weight(self.root, item)
            self.assertEqual(target.read_bytes(), b'old corrupt cache')
            self.assertEqual(list(target.parent.glob('.' + item['sha256'] + '*')), [])

    def test_concurrent_cache_preparation_downloads_once_and_is_readable_by_worker(self):
        item = self.tiny_models()['external_weights'][0]
        target = manager.cache_path(self.root, item)
        raw = target.read_bytes()
        target.unlink()
        with patch.object(manager, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(raw)) as download:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: manager.prepare_weight(self.root, item), range(4)))
            self.assertEqual(download.call_count, 1)
        self.assertTrue(manager.verified(target, item))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o755)


@unittest.skipUnless(DOCKER, 'Docker CLI required for real Compose configuration parsing')
class ComposeTests(Fixture):
    def render(self, *flags):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            manager.main(['--root', str(self.root), 'resolve', '--write', *flags])
        records = dict(line.split('\t', 1) for line in output.getvalue().splitlines() if not line.startswith('service\t'))
        command = [DOCKER, 'compose', '--project-directory', str(self.root), '--env-file', str(self.root / '.env.example'),
                   '-f', str(self.root / 'docker-compose.yml')]
        if records['observability'] == '1':
            command += ['-f', str(self.root / 'compose.observability.yml'), '--profile', 'observability']
        if records['worker'] == '1':
            command += ['--profile', 'local-worker']
        command += ['-f', records['override_file'], 'config', '--format', 'json']
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_compose_default_has_no_worker_dependency(self):
        rendered = self.render()
        self.assertNotIn('python-training-worker', rendered['services'])
        self.assertNotIn('python-inference-runtime', rendered['services'])
        self.assertIn('python-artifact-runtime', rendered['services'])
        self.assertIn('healthcheck', rendered['services']['go-control-plane'])

    def test_real_compose_models_cuda_and_observability(self):
        rendered = self.render('--with-models', '--device', 'cuda', '--observability')
        services = rendered['services']
        self.assertIn('grafana', services)
        self.assertIn('gpus', services['python-training-worker'])
        self.assertEqual(services['python-inference-runtime']['environment']['FINEVISION_COMPUTE_DEVICE'], 'cuda')
        self.assertIn('set -eu', services['pretrained-weight-init']['entrypoint'][-1])
        self.assertEqual(services['agent-service']['environment']['FINEVISION_OBSERVABILITY_ENABLED'], 'true')


if __name__ == '__main__':
    unittest.main()
