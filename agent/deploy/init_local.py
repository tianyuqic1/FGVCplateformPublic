"""Fill only missing Agent local secrets; never print their values."""
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parents[2] / '.env'
    text = target.read_text() if target.exists() else ''
    lines = text.splitlines()
    changed = []
    for key in ('FINEVISION_AGENT_DELEGATION_KEY', 'FINEVISION_AGENT_TOOLS_SIGNING_KEY', 'FINEVISION_AGENT_DB_PASSWORD'):
        matches = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if len(matches) > 1:
            raise SystemExit(f'{key}: duplicate entries; resolve before initializing')
        if not matches:
            lines.append(key + '=' + secrets.token_hex(32))
            changed.append(key)
        elif not lines[matches[0]].split('=', 1)[1].strip('"\' '):
            lines[matches[0]] = key + '=' + secrets.token_hex(32)
            changed.append(key)
    if changed:
        target.write_text('\n'.join(lines) + '\n')
    if target.exists():
        target.chmod(0o600)
    print('Agent local secrets: ' + (', '.join(changed) + ' initialized' if changed else 'existing values preserved'))


if __name__ == '__main__':
    main()
