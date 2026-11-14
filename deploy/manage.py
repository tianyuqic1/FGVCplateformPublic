#!/usr/bin/env python3
"""Dependency-free deployment configuration, immutable plans and verified weights.

Docker orchestration stays in deploy.sh. This helper can run with host Python or
in the bootstrap container; it never sources .env as executable shell code.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
from urllib.request import Request, urlopen

CORE = ['postgres', 'rabbitmq', 'object-store', 'agent-postgres',
        'python-artifact-runtime', 'go-llm-gateway', 'go-control-plane',
        'outbox-relay', 'deployment-relay', 'agent-service', 'frontend']
WORKERS = ['python-training-worker', 'python-inference-runtime', 'hardware-collector']
OBSERVABILITY = ['alloy', 'loki', 'tempo', 'prometheus', 'postgres-exporter',
                 'grafana', 'observability-console']
SETTINGS = {'worker': 'FINEVISION_DEPLOY_WORKER', 'with_models': 'FINEVISION_DEPLOY_MODELS',
            'models': 'FINEVISION_DEPLOY_MODEL_LIST', 'device': 'FINEVISION_DEPLOY_DEVICE',
            'observability': 'FINEVISION_DEPLOY_OBSERVABILITY'}


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def env_values(path):
    """Only parse our literal deployment switches, leaving full dotenv to Compose."""
    result = {}
    for raw in Path(path).read_text().splitlines() if Path(path).exists() else []:
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', raw)
        if not match:
            continue
        key, value = match.groups()
        if key not in SETTINGS.values():
            continue
        if key in result:
            raise ValueError(f'{key}: duplicate configuration')
        if value.startswith(('"', "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end == -1 or value[end+1:].strip().split('#', 1)[0].strip():
                raise ValueError(f'{key}: invalid quoted value')
            value = value[1:end]
        else:
            value = value.split(' #', 1)[0].strip()
        if '$' in value or '`' in value or '\n' in value or '\t' in value:
            raise ValueError(f'{key}: use a literal value')
        result[key] = value
    return result


def boolean(value):
    if isinstance(value, bool):
        return value
    if str(value).lower() in ('true', '1', 'yes'):
        return True
    if str(value).lower() in ('false', '0', 'no'):
        return False
    raise ValueError('deployment booleans must be true or false')


def catalog(root):
    doc = json.loads((root / 'weights/manifest.json').read_text())
    items = doc['weights'] + doc.get('external_weights', [])
    for item in items:
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*', item['alias']):
            raise ValueError('invalid model alias')
        if not re.fullmatch('[a-f0-9]{64}', item['sha256']) or item['size_bytes'] <= 0:
            raise ValueError('invalid weight identity')
        if not re.fullmatch('[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*', item['category']):
            raise ValueError('invalid weight category')
        if not re.fullmatch('FINEVISION_[A-Z0-9_]+', item['environment_key']):
            raise ValueError('invalid weight environment key')
        if item.get('lfs_path') and not (root / item['lfs_path']).resolve().is_relative_to(root):
            raise ValueError('weight path escapes repository')
    if len({i['alias'] for i in items}) != len(items):
        raise ValueError('duplicate model alias')
    return items


def selection(root, names):
    items = catalog(root)
    if names == 'all':
        return items
    requested = names.split(',') if isinstance(names, str) else names
    if not requested or any(not name for name in requested):
        raise ValueError('model list cannot be empty')
    result = []
    for name in requested:
        item = next((item for item in items if name in (item['alias'], item['preset'])), None)
        if item is None:
            raise ValueError(f'unknown model: {name}; use ./deploy.sh models list')
        if item not in result:
            result.append(item)
    return result


def verified(path, item):
    if not path.is_file() or path.stat().st_size != item['size_bytes']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == item['sha256']


def cache_path(root, item):
    return root / '.deploy/models/sha256' / item['sha256']


def prepare_weight(root, item):
    path = cache_path(root, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / '.deploy/models').chmod(0o755)
    path.parent.chmod(0o755)
    with (path.parent / (item['sha256'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if verified(path, item):
            print(f"OK {item['alias']}: verified cache reused")
            return
        fd, temporary = tempfile.mkstemp(prefix='.' + item['sha256'], dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                local = root / item.get('lfs_path', '.deploy/no-local-weight')
                if item.get('lfs_path') and verified(local, item):
                    with local.open('rb') as source:
                        shutil.copyfileobj(source, output)
                else:
                    url = item.get('source_url') or (
                        f"https://huggingface.co/{item['model_id']}/resolve/{item['revision']}/model.safetensors")
                    if not url.startswith('https://'):
                        raise ValueError('model source must use HTTPS')
                    # No provider token or platform credential is sent to model hosts.
                    with urlopen(Request(url, headers={'User-Agent': 'FineVision-deploy/1'}), timeout=60) as source:
                        size = 0
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            if size > item['size_bytes']:
                                raise ValueError('download exceeds registered size')
                            output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not verified(Path(temporary), item):
                raise ValueError('download SHA-256 or size mismatch')
            os.chmod(temporary, 0o644)  # Readable by non-root compute containers.
            os.replace(temporary, path)
            print(f"OK {item['alias']}: prepared and verified")
        finally:
            Path(temporary).unlink(missing_ok=True)


def initialize(root, env_file):
    if not env_file.exists():
        if env_file != root / '.env':
            raise ValueError('custom --env-file does not exist')
        text = (root / '.env.example').read_text()
    else:
        text = env_file.read_text()
    lines = text.splitlines()
    for key in ('FINEVISION_AGENT_DELEGATION_KEY', 'FINEVISION_AGENT_TOOLS_SIGNING_KEY',
                'FINEVISION_AGENT_DB_PASSWORD'):
        matches = [i for i, line in enumerate(lines)
                   if re.match(r'^\s*(?:export\s+)?' + key + r'\s*=', line)]
        if len(matches) > 1:
            raise ValueError(f'{key}: duplicate entries')
        if not matches:
            lines.append(key + '=' + secrets.token_hex(32))
        elif not lines[matches[0]].split('=', 1)[1].strip('"\' '):
            lines[matches[0]] = key + '=' + secrets.token_hex(32)
    updated = '\n'.join(lines) + '\n'
    if not env_file.exists() or updated != text:
        atomic_write(env_file, updated)
    env_file.chmod(0o600)
    print('Configuration initialized; existing nonempty secrets preserved.')


def overlay(root, options, items):
    services = {
        'go-control-plane': {'healthcheck': {'test': ['CMD', 'wget', '-q', '--spider', 'http://127.0.0.1:8000/api/health'],
                             'interval': '10s', 'timeout': '3s', 'retries': 12, 'start_period': '20s'}},
        'go-llm-gateway': {'healthcheck': {'test': ['CMD', 'wget', '-q', '--spider', 'http://127.0.0.1:8080/health'],
                             'interval': '10s', 'timeout': '3s', 'retries': 6}},
    }
    if options['worker']:
        for service in WORKERS:
            services[service] = {'profiles': ['local-worker']}
        for service in WORKERS[:2]:
            services[service]['environment'] = {'FINEVISION_COMPUTE_DEVICE': options['device']}
        services[WORKERS[0]]['environment']['FINEVISION_LINEAR_HEAD_DEVICE'] = options['device']
        if options['device'] == 'cuda':
            for service in WORKERS:
                services[service]['gpus'] = 'all'
            services['hardware-collector']['environment'] = {'NVIDIA_VISIBLE_DEVICES': 'all', 'NVIDIA_DRIVER_CAPABILITIES': 'utility'}
    if options['observability']:
        for service in CORE + (WORKERS if options['worker'] else []):
            services.setdefault(service, {}).setdefault('environment', {})['FINEVISION_OBSERVABILITY_ENABLED'] = 'true'
    if options['with_models']:
        mount = {'type': 'bind', 'source': str(root / '.deploy/models'), 'target': '/opt/finevision/pretrained', 'read_only': True}
        # Workers see the exact checked cache, including external published weights.
        for service in ['python-artifact-runtime'] + WORKERS[:2]:
            spec = services.setdefault(service, {})
            spec.setdefault('volumes', []).append(mount)
            spec.setdefault('environment', {}).update({i['environment_key']: '/opt/finevision/pretrained/sha256/' + i['sha256'] for i in items})
        script = ['set -eu', 'mc alias set finevision http://object-store:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null']
        for item in items:
            if not item.get('lfs_path'):
                continue
            sha = item['sha256']
            target = f"finevision/finevision-artifacts/pretrained/{item['category']}/{sha[:2]}/{sha}"
            # Existing object data must also match; do not trust only its metadata.
            script += [f'actual="$(mc cat {target} 2>/dev/null | sha256sum)"',
                       f'if [ "${{actual%% *}}" != "{sha}" ]; then',
                       f'  mc cp /opt/finevision/pretrained/sha256/{sha} {target}',
                       f'  actual="$(mc cat {target} | sha256sum)"',
                       f'  test "${{actual%% *}}" = "{sha}"', 'fi',
                       f'echo "OK registered {item["alias"]}"']
        # Escape dollar signs for Compose interpolation; never interpolate secrets here.
        services['pretrained-weight-init'] = {'volumes': [mount], 'entrypoint': ['/bin/sh', '-ec', '\n'.join(script).replace('$', '$$')]}
    return {'services': services}


def resolve(root, args):
    project = args.project_name or 'finevision'
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('invalid --project-name')
    state_file = root / '.deploy' / project / 'state.json'
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    if previous and previous.get('version') != 1:
        raise ValueError('unsupported saved deployment state version')
    env_file = Path(args.env_file or previous.get('env_file', root / '.env'))
    if not env_file.is_absolute():
        env_file = root / env_file
    env_file = env_file.resolve()
    if any(c in str(env_file) for c in '\n\r\t'):
        raise ValueError('invalid env-file path')
    if args.env_file and not env_file.exists():
        raise ValueError('custom --env-file does not exist')
    values = env_values(env_file)
    options = {'worker': False, 'with_models': False, 'models': 'all', 'device': 'cpu', 'observability': False}
    options.update(previous.get('options', {}))
    # Operational commands use the saved composition, even if .env switches changed.
    if args.operation in ('up', 'doctor') or not previous:
        for key, variable in SETTINGS.items():
            if variable in values:
                options[key] = values[variable]
    for key in options:
        value = getattr(args, key, None)
        if value is not None:
            options[key] = value
    for key in ('worker', 'with_models', 'observability'):
        options[key] = boolean(options[key])
    if args.models is not None:
        options['with_models'] = True
    if options['with_models']:
        options['worker'] = True
    if options['device'] not in ('cpu', 'cuda'):
        raise ValueError('--device must be cpu or cuda')
    if options['device'] == 'cuda' and not options['worker']:
        raise ValueError('--device cuda requires --with-worker or --with-models')
    items = selection(root, options['models'])
    options['models'] = ','.join(i['alias'] for i in items)
    # A saved component cannot disappear just because a later invocation omits a flag.
    for key in ('worker', 'with_models', 'observability'):
        if previous.get('options', {}).get(key) and not options[key]:
            raise ValueError(f'{key}: disabling an existing component requires an explicit stop; omission does not remove it')
    services = CORE + (WORKERS if options['worker'] else []) + (OBSERVABILITY if options['observability'] else [])
    data = {'version': 1, 'project': project, 'env_file': str(env_file), 'options': options, 'services': services}
    doc = overlay(root, options, items)
    digest = hashlib.sha256(json.dumps([data, doc], sort_keys=True).encode()).hexdigest()[:20]
    folder = root / '.deploy' / project / 'plans'
    plan_file, override_file = folder / (digest + '.json'), folder / (digest + '.compose.json')
    data.update(plan_file=str(plan_file), override_file=str(override_file), state_file=str(state_file))
    if args.write:
        atomic_write(plan_file, json.dumps(data, indent=2) + '\n')
        atomic_write(override_file, json.dumps(doc, indent=2) + '\n')
    if args.summary:
        print(f'Project: {project}\nConfig: {env_file}\nDevice: {options["device"]}')
        print('Local workers: ' + ('enabled' if options['worker'] else 'disabled'))
        print('Observability: ' + ('enabled' if options['observability'] else 'disabled'))
        print('Services: ' + ', '.join(services))
        if options['with_models']:
            print('Models: ' + options['models'])
            print(f'Registered total: {sum(i["size_bytes"] for i in items):,} bytes (verified cache reused)')
        print('Sequence: initialize -> validate -> models -> build/pull -> migrate/start -> health -> save selection')
        return
    for key in ('project', 'env_file', 'plan_file', 'override_file', 'state_file'):
        print(f'{key}\t{data[key]}')
    for key, value in options.items():
        print(f'{key}\t{int(value) if isinstance(value, bool) else value}')
    for service in services:
        print('service\t' + service)


def check_status(path, services):
    raw = Path(path).read_text().strip()
    if not raw:
        raise ValueError('no service containers found')
    try:
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in raw.splitlines()]
    failures = []
    for service in services:
        matching = [row for row in rows if row.get('Service') == service]
        if not matching or any(row.get('State') != 'running' or row.get('Health') not in ('', None, 'healthy') for row in matching):
            failures.append(service)
    if failures:
        raise ValueError('services not ready: ' + ', '.join(failures))
    print(f'OK {len(services)} services running; configured health checks passed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('resolve')
    p.add_argument('--operation', default='up')
    p.add_argument('--env-file')
    p.add_argument('--project-name')
    p.add_argument('--device')
    p.add_argument('--models')
    p.add_argument('--worker', action='store_const', const=True)
    p.add_argument('--with-models', action='store_const', const=True)
    p.add_argument('--observability', action='store_const', const=True)
    p.add_argument('--write', action='store_true')
    p.add_argument('--summary', action='store_true')
    p = sub.add_parser('saved-env')
    p.add_argument('--project-name', default='finevision')
    p = sub.add_parser('init')
    p.add_argument('--env-file', type=Path, required=True)
    p = sub.add_parser('save')
    p.add_argument('plan', type=Path)
    p = sub.add_parser('models')
    p.add_argument('action', choices=['list', 'pull', 'check'])
    p.add_argument('--models', default='all')
    p = sub.add_parser('health')
    p.add_argument('path')
    p.add_argument('services', nargs='+')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == 'resolve':
        resolve(root, args)
    elif args.command == 'saved-env':
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,62}', args.project_name):
            raise ValueError('invalid project')
        path = root / '.deploy' / args.project_name / 'state.json'
        if path.exists():
            print(json.loads(path.read_text())['env_file'])
    elif args.command == 'init':
        initialize(root, args.env_file)
    elif args.command == 'save':
        data = json.loads(args.plan.read_text())
        data['applied_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        atomic_write(data['state_file'], json.dumps(data, indent=2) + '\n')
    elif args.command == 'health':
        check_status(args.path, args.services)
    else:
        for item in selection(root, args.models):
            if args.action == 'list':
                source = 'Git LFS / pinned upstream' if item.get('lfs_path') else 'published HTTPS'
                print(f"{item['alias']:20} {item['size_bytes']:>12,} B  {source}  {item['license']}")
            elif args.action == 'pull':
                prepare_weight(root, item)
            elif verified(cache_path(root, item), item) or (item.get('lfs_path') and verified(root / item['lfs_path'], item)):
                print(f"OK {item['alias']}: verified")
            else:
                raise ValueError(f"{item['alias']}: missing or corrupt; run ./deploy.sh models pull")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        # Do not include environment values or remote response bodies in diagnostics.
        print(f'Deployment error: {error}', file=sys.stderr)
        sys.exit(1)
