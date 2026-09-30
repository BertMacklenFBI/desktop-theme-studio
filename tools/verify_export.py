#!/usr/bin/python3
"""Read-only development export verification; never starts a desktop or installer."""
from pathlib import Path
import argparse
import hashlib
import json
import re

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--local-secrets', type=Path, help='Optional local env file; values never printed')
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads((root / 'export-manifest.json').read_text())
    expected = {e['path']: e for e in manifest['files']}
    errors = []
    secrets = []
    if args.local_secrets:
        for line in args.local_secrets.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if any(part in key.upper() for part in ['KEY', 'TOKEN', 'SECRET', 'PASSWORD']):
                    value = value.strip().strip('"\'')
                    if len(value) >= 12:
                        secrets.append(value.encode())
    patterns = {
        'OpenAI credential': rb'(?<![A-Za-z0-9_])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',
        'GitHub credential': rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})',
        'AWS credential': rb'AKIA[A-Z0-9]{16}',
        'private key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    }
    actual = set()
    count = 0
    total = 0
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if '.git' in rel.parts:
            continue
        if p.is_symlink():
            errors.append({'path': str(rel), 'reason': 'unexpected symlink'})
            continue
        if not p.is_file():
            continue
        actual.add(str(rel))
        data = p.read_bytes()
        count += 1
        total += len(data)
        if p.stat().st_nlink != 1:
            errors.append({'path': str(rel), 'reason': 'shared hardlink'})
        if len(data) >= 100 * 1024 * 1024:
            errors.append({'path': str(rel), 'reason': 'GitHub file limit'})
        if any(part in {'__pycache__', '.claude', '.build', 'node_modules', 'state', 'verification', 'backups'} for part in rel.parts) or p.name.startswith('.env') or p.suffix in {'.pyc', '.log', '.jsonl', '.pid', '.sock'}:
            errors.append({'path': str(rel), 'reason': 'forbidden runtime/credential artifact'})
        if str(rel) in expected:
            e = expected[str(rel)]
            if len(data) != e['bytes'] or hashlib.sha256(data).hexdigest() != e['sha256']:
                errors.append({'path': str(rel), 'reason': 'manifest byte mismatch'})
        for label, pattern in patterns.items():
            if re.search(pattern, data):
                errors.append({'path': str(rel), 'reason': label + ' pattern'})
        if any(value in data for value in secrets):
            errors.append({'path': str(rel), 'reason': 'exact local credential match'})
    extras = actual - set(expected) - {'export-manifest.json'}
    missing = set(expected) - actual
    errors.extend({'path': p, 'reason': 'unmanifested file'} for p in sorted(extras))
    errors.extend({'path': p, 'reason': 'missing file'} for p in sorted(missing))
    status = json.loads((root / 'development-status.json').read_text())
    if status['all_ready_for_release'] or len(status['candidates']) != 8:
        errors.append({'path': 'development-status.json', 'reason': 'development gate mismatch'})
    for t in status['candidates']:
        if any(t[k] for k in ['release_eligible', 'switch_enabled', 'export_installable', 'source_acceptance_applies_to_export']):
            errors.append({'path': 'development-status.json', 'reason': 'enabled gate for ' + t['slug']})
    profiles = json.loads((root / 'refinements/cinnamon-current-collection/profiles.json').read_text())['profiles']
    if len(profiles) != 6 or any(p.get('release_status') != 'disabled-pending-current-native-interaction-recovery-and-independent-acceptance' for p in profiles.values()):
        errors.append({'path': 'profiles.json', 'reason': 'candidate routes enabled or wrong count'})
    result = {'status': 'PASS' if not errors else 'FAIL', 'files': count, 'bytes': total,
              'manifest_sha256': hashlib.sha256((root / 'export-manifest.json').read_bytes()).hexdigest(),
              'local_credentials_compared': bool(secrets), 'errors': errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1

if __name__ == '__main__':
    raise SystemExit(main())
