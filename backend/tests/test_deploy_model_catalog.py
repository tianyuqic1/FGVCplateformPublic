"""Deployment and runtime must agree on the bytes and mounts for every model."""
import json
from pathlib import Path

from finevision.compute.pretrained_weights import MANAGED_WEIGHTS


def test_deployment_registry_matches_runtime_weights():
    root = Path(__file__).resolve().parents[2]
    registry = json.loads((root / 'weights/manifest.json').read_text())
    aliases = set()
    for item in registry['weights'] + registry.get('external_weights', []):
        runtime = MANAGED_WEIGHTS[item['preset']]
        assert (item['sha256'], item['size_bytes'], item['environment_key'], item['category']) == (
            runtime.sha256, runtime.size_bytes, runtime.environment_key, runtime.category)
        assert item.get('source_url') == runtime.source_url
        assert item['alias'] not in aliases
        aliases.add(item['alias'])
