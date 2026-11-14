#!/usr/bin/env python3
"""Opt-in real Docker acceptance test; only its own project/volumes are removed.

Run from the checkout with prebuilt application images:
    python3 deploy/tests/smoke_live.py --no-build
Omit --no-build to exercise image builds too. No jobs or LLM requests are sent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--models', default='dinov3-vits')
    args = parser.parse_args()
    project = 'finevision-test-' + secrets.token_hex(5)
    folder = ROOT / '.deploy' / project
    folder.mkdir(parents=True, mode=0o700)
    config = folder / 'test.env'
    held = []
    ports = {}
    try:
        for key in ('FRONTEND', 'API', 'POSTGRES', 'RABBITMQ', 'RABBITMQ_CONSOLE', 'MINIO', 'MINIO_CONSOLE'):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            held.append(sock)
            ports[key] = sock.getsockname()[1]
        config.write_text((ROOT / '.env.example').read_text() + '\n' + ''.join(
            f'FINEVISION_{key}_PORT={port}\n' for key, port in ports.items()))
        config.chmod(0o600)
    finally:
        for sock in held:
            sock.close()
    base = [str(ROOT / 'deploy.sh'), '--project-name', project, '--env-file', str(config)]
    # deploy.sh takes the operation before flags.
    def deploy(operation='up', *flags):
        return [base[0], operation, *base[1:], *flags]

    compose = ['docker', 'compose', '--project-directory', str(ROOT), '--project-name', project,
               '--env-file', str(config), '-f', str(ROOT / 'docker-compose.yml'), '--profile', 'local-worker']
    log_path = folder / 'acceptance.log'
    print(f'Isolated project: {project}\nLog: {log_path}', flush=True)
    with log_path.open('w') as log:
        log_path.chmod(0o600)

        def run(label, command):
            print(label, flush=True)
            log.write('\n' + label + '\n')
            log.flush()
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)

        def state():
            return json.loads((folder / 'state.json').read_text())

        def endpoint(key, path):
            with urlopen(f'http://127.0.0.1:{ports[key]}{path}', timeout=15) as response:
                assert response.status == 200

        build = ['--no-build'] if args.no_build else []
        try:
            run('Start default platform', deploy('up', *build))
            assert state()['options']['worker'] is False
            running = subprocess.check_output(compose + ['ps', '--services', '--status', 'running'], text=True).split()
            assert not set(running).intersection(['python-training-worker', 'python-inference-runtime', 'hardware-collector'])
            endpoint('API', '/api/health')
            endpoint('FRONTEND', '/')
            run('Diagnose default platform', deploy('doctor'))
            run('Enable local CPU workers and verified models', deploy('up', '--models', args.models, *build))
            selected = state()['options']
            assert selected['worker'] and selected['with_models'] and selected['device'] == 'cpu'
            run('Repeat up without feature flags', deploy('up', '--no-build'))
            assert state()['options'] == selected
            run('Restart saved selection', deploy('restart'))
            run('Diagnose worker platform', deploy('doctor'))
            endpoint('API', '/api/health')
            endpoint('FRONTEND', '/')
            run('Stop saved selection', deploy('stop'))
            running = subprocess.check_output(compose + ['ps', '--services', '--status', 'running'], text=True).split()
            assert not running, running
            volumes = subprocess.check_output(['docker', 'volume', 'ls', '--filter',
                f'label=com.docker.compose.project={project}', '--quiet'], text=True).splitlines()
            assert volumes, 'stop must preserve data volumes'
            result = {'project': project, 'models': args.models, 'builds': not args.no_build,
                      'checks': ['default-up', 'http', 'doctor', 'worker-models-up', 'saved-up', 'restart', 'stop-preserves-volumes']}
            (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            print('PASS: deployment lifecycle and HTTP endpoints', flush=True)
        finally:
            # Include the last immutable overlay for resources added by with-models.
            overlay = [ '-f', state()['override_file']] if (folder / 'state.json').exists() else []
            run('Remove only acceptance-test containers and volumes', compose + overlay + ['down', '--volumes', '--remove-orphans'])
            # Temporary test credentials have no purpose after cleanup.
            config.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
