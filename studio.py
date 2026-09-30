#!/usr/bin/env python3
"""Local, declarative appearance transactions. Standard library only."""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
HOME = Path.home()
PREFIXES = ('.themes', '.icons', '.local/share/themes', '.local/share/icons',
            '.config/gtk-2.0', '.config/gtk-3.0', '.config/gtk-4.0',
            '.config/kitty', '.config/fastfetch', '.config/cava', '.config/mpv',
            '.config/qt5ct', '.config/qt6ct', '.config/Kvantum')
SCHEMAS = {
    'org.cinnamon.desktop.interface': {'gtk-theme', 'icon-theme', 'cursor-theme', 'cursor-size', 'font-name', 'document-font-name', 'monospace-font-name'},
    'org.cinnamon.desktop.wm.preferences': {'theme', 'titlebar-font'},
    'org.cinnamon.desktop.background': {'picture-uri', 'picture-options', 'primary-color', 'secondary-color', 'color-shading-type'},
    'org.cinnamon.desktop.screensaver': {'font-time', 'font-date', 'font-message'},
    'org.cinnamon.theme': {'name'},
}
GUARDED = {'org.cinnamon.theme'}
MUSIC = ('play', 'pause', 'next', 'prev', 'playalbum', 'nextalbum', 'prevalbum',
         'mpv-vis', 'ocean-status', 'now-playing', 'ocean-palette')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read(path):
    return json.loads(Path(path).read_text())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def gsettings(*args):
    p = subprocess.run(['gsettings', *args], text=True, capture_output=True, timeout=15)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'gsettings failed')
    return p.stdout.strip()


def node(path):
    if path.is_symlink():
        return {'kind': 'symlink', 'target': os.readlink(path)}
    if not path.exists():
        return {'kind': 'missing'}
    if not path.is_file():
        raise ValueError(f'Expected regular file: {path}')
    data = path.read_bytes()
    return {'kind': 'file', 'sha256': digest(data), 'mode': path.stat().st_mode & 0o777}


def protected():
    paths = [HOME / '.local/bin' / x for x in MUSIC]
    paths += [HOME / p for p in ('.bashrc', '.bash_aliases', '.profile', '.zshrc')]
    audit = ROOT / 'audits/applications.json'
    if audit.exists():
        paths += [Path(p) for p in read(audit).get('protected_files', [])]
    result = {}
    for p in sorted(set(paths)):
        try:
            n = node(p)
            if p.is_symlink() and p.is_file():
                n['resolved_sha256'] = digest(p.read_bytes())
            result[str(p)] = n
        except (OSError, ValueError) as e:
            result[str(p)] = {'kind': 'unreadable', 'error': str(e)}
    return result


@contextlib.contextmanager
def locked():
    STATE.mkdir(exist_ok=True, mode=0o700)
    with (STATE / 'lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def target_path(raw):
    if not raw.startswith('~/'):
        raise ValueError('Destinations must use ~/ and target a supported appearance directory')
    relative = Path(raw[2:])
    if '..' in relative.parts or not any(str(relative).startswith(p + '/') for p in PREFIXES):
        raise ValueError(f'Unsupported destination: {raw}')
    path = HOME / relative
    for parent in [path, *path.parents]:
        if parent == HOME:
            break
        if parent.is_symlink():
            raise ValueError(f'Symlink destination needs an explicit adapter: {parent}')
    return path


def validate(manifest):
    manifest = Path(manifest).resolve()
    data = read(manifest)
    if data.get('version') != 1 or not data.get('name'):
        raise ValueError('Manifest requires version: 1 and name')
    files, settings, seen = [], [], set()
    protected_paths = set(protected())
    for entry in data.get('files', []):
        source = (manifest.parent / entry['source']).resolve()
        if not source.is_relative_to(manifest.parent) or not source.is_file():
            raise ValueError(f'Source must be a regular file inside the preset: {source}')
        dest = target_path(entry['destination'])
        if str(dest) in protected_paths or str(dest) in seen:
            raise ValueError(f'Protected or duplicate destination: {dest}')
        seen.add(str(dest))
        payload = source.read_bytes()
        if len(payload) > 32 * 1024 * 1024:
            raise ValueError(f'File exceeds 32 MiB limit: {source}')
        mode = entry.get('mode', 0o644)
        if mode not in (0o600, 0o644):
            raise ValueError('Appearance file modes must be 384 (0600) or 420 (0644)')
        files.append({'destination': str(dest), 'source': str(source), 'mode': mode,
                      'content': base64.b64encode(payload).decode(),
                      'after': {'kind': 'file', 'sha256': digest(payload), 'mode': mode}})
    for entry in data.get('settings', []):
        schema, key, value = entry['schema'], entry['key'], entry['value']
        if key not in SCHEMAS.get(schema, set()) or not isinstance(value, str):
            raise ValueError(f'Unsupported appearance setting or non-GVariant string: {schema} {key}')
        if schema in GUARDED:
            raise ValueError('Cinnamon shell changes require the existing isolated test and trial guard; see docs/rollout.md')
        identity = schema + '/' + key
        if identity in seen:
            raise ValueError(f'Duplicate setting: {identity}')
        seen.add(identity)
        before = gsettings('get', schema, key)
        # Validate syntax and schema range without writing the settings database.
        value = validate_variant(schema, key, value)
        settings.append({'schema': schema, 'key': key, 'value': value, 'before': before,
                         'before_user': user_value(schema, key)})
    if not files and not settings:
        raise ValueError('Manifest has no operations')
    return data, files, settings


def validate_variant(schema, key, value):
    # GI is supplied by Linux Mint's system Python; no package installation required.
    from gi.repository import Gio, GLib
    source = Gio.SettingsSchemaSource.get_default()
    skey = source.lookup(schema, True).get_key(key)
    variant = GLib.Variant.parse(skey.get_value_type(), value, None, None)
    if not skey.range_check(variant):
        raise ValueError(f'Value outside schema range: {schema} {key}')
    return variant.print_(True)


def user_value(schema, key):
    from gi.repository import Gio
    variant = Gio.Settings.new(schema).get_user_value(key)
    return variant.print_(True) if variant is not None else None


def atomic_file(path, payload, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.studio-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as f:
            os.chmod(temp, mode)
            f.write(payload)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def restore_record(folder, rollback=False):
    try:
        return _restore_record(folder, rollback)
    except Exception as error:
        journal_path = folder / 'transaction.json'
        if journal_path.exists():
            journal = read(journal_path)
            journal['last_restore_error'] = str(error)
            if journal.get('status') == 'restoring':
                journal['status'] = 'recovery-required'
            dump(journal_path, journal)
        raise


def _restore_record(folder, rollback=False):
    journal_path = folder / 'transaction.json'
    journal = read(journal_path)
    conflicts = []
    # Preflight every recorded operation before touching anything.
    for op in journal['operations']:
        if op['type'] == 'file':
            p = Path(op['destination'])
            target_path('~/' + str(p.relative_to(HOME)))
            actual = node(p)
            acceptable = [op['after'], op['before']]
        else:
            actual = gsettings('get', op['schema'], op['key'])
            acceptable = [op['value'], op['before']]
        if actual not in acceptable:
            conflicts.append(op.get('destination', op.get('key')))
    if conflicts:
        raise RuntimeError('Restore refused because targets changed after apply: ' + ', '.join(conflicts))
    # Verify every backup before beginning restoration.
    for op in journal['operations']:
        if op['type'] == 'file' and op['before']['kind'] == 'file':
            if digest((folder / op['backup']).read_bytes()) != op['before']['sha256']:
                raise RuntimeError(f'Backup checksum mismatch: {op["destination"]}')
    journal['status'] = 'restoring'
    dump(journal_path, journal)
    for op in reversed(journal['operations']):
        if op['type'] == 'file':
            p = Path(op['destination'])
            if op['before']['kind'] == 'missing':
                if p.exists():
                    p.unlink()
            else:
                payload = (folder / op['backup']).read_bytes()
                if digest(payload) != op['before']['sha256']:
                    raise RuntimeError(f'Backup checksum mismatch: {p}')
                atomic_file(p, payload, op['before']['mode'])
        else:
            if op.get('before_user', op['before']) is None:
                gsettings('reset', op['schema'], op['key'])
            else:
                gsettings('set', op['schema'], op['key'], op.get('before_user', op['before']))
            if gsettings('get', op['schema'], op['key']) != op['before']:
                raise RuntimeError(f'Restore verification failed: {op["key"]}')
    # Only remove empty directories created by this transaction.
    for p in reversed(journal.get('created_directories', [])):
        try:
            Path(p).rmdir()
        except OSError:
            pass
    journal['status'] = 'rolled-back' if rollback else 'restored'
    journal['restored_at'] = now()
    dump(journal_path, journal)
    return journal


def apply(manifest, commit=False):
    data, files, settings = validate(manifest)
    changes = [dict(type='file', **f) for f in files if node(Path(f['destination'])) != f['after']]
    changes += [dict(type='setting', **s) for s in settings if s['before'] != s['value']]
    preview = {'name': data['name'], 'mode': 'commit' if commit else 'preview',
               'changes': [{k: v for k, v in c.items() if k != 'content'} for c in changes]}
    if not commit or not changes:
        return preview
    with locked():
        # Recheck settings after locking so overlapping Studio transactions cannot use stale baselines.
        for s in settings:
            if gsettings('get', s['schema'], s['key']) != s['before']:
                raise RuntimeError('Settings changed during planning; rerun apply')
        txid = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        folder = STATE / 'transactions' / txid
        folder.mkdir(parents=True, mode=0o700)
        journal = {'version': 1, 'id': txid, 'name': data['name'], 'created_at': now(),
                   'status': 'applying', 'protected_before': protected(), 'operations': [],
                   'created_directories': []}
        dump(folder / 'transaction.json', journal)
        try:
            for n, change in enumerate(changes):
                op = {k: v for k, v in change.items() if k != 'content'}
                if op['type'] == 'file':
                    p = Path(op['destination'])
                    target_path('~/' + str(p.relative_to(HOME)))
                    op['before'] = node(p)
                    if op['before']['kind'] == 'file':
                        op['backup'] = f'{n}.backup'
                        shutil.copyfile(p, folder / op['backup'])
                        os.chmod(folder / op['backup'], 0o600)
                    missing = []
                    parent = p.parent
                    while not parent.exists():
                        missing.append(str(parent))
                        parent = parent.parent
                    journal['created_directories'] += list(reversed(missing))
                journal['operations'].append(op)
                dump(folder / 'transaction.json', journal)
                if op['type'] == 'file':
                    atomic_file(p, base64.b64decode(change['content']), op['mode'])
                    if node(p) != op['after']:
                        raise RuntimeError(f'File verification failed: {p}')
                else:
                    gsettings('set', op['schema'], op['key'], op['value'])
                    if gsettings('get', op['schema'], op['key']) != op['value']:
                        raise RuntimeError(f'Setting verification failed: {op["key"]}')
            if protected() != journal['protected_before']:
                raise RuntimeError('Protected command or shell file changed during application')
            journal['status'] = 'applied'
            dump(folder / 'transaction.json', journal)
        except Exception as error:
            try:
                restore_record(folder, rollback=True)
            except Exception as restore_error:
                journal['status'] = 'recovery-required'
                journal['error'] = str(error)
                journal['restore_error'] = str(restore_error)
                dump(folder / 'transaction.json', journal)
                raise RuntimeError(f'Apply failed: {error}; rollback incomplete: {restore_error}; receipt: {folder}') from error
            raise RuntimeError(f'Apply failed and recorded operations were restored: {error}; receipt: {folder}') from error
        return {'name': data['name'], 'status': 'applied', 'transaction': txid,
                'receipt': str(folder / 'transaction.json'), 'visual_verification': 'pending'}


def audit():
    groups = []
    for name in ('desktop', 'applications', 'system'):
        path = ROOT / 'audits' / (name + '.json')
        if path.exists():
            groups.append(read(path))
    surfaces = [dict(s, area=g['area']) for g in groups for s in g['surfaces']]
    result = {'generated_at': now(), 'note': 'Agent audit evidence is a dated snapshot; check probes selected configuration only.',
              'areas': len(groups), 'surfaces': surfaces}
    dump(ROOT / 'audits/coverage.json', result)
    lines = ['# Desktop coverage audit', '', f'Generated: {result["generated_at"]}', '',
             'Configuration observations are not visual or functional verification. See the three specialist reports for evidence and remaining checks.', '',
             '| Area | Surface | Status | Remaining verification / gaps |', '|---|---|---|---|']
    for s in surfaces:
        gap = '; '.join(s.get('gaps', [])) or str(s.get('verification', 'Pending visual verification'))
        cols = [s['area'], s['label'], s['status'], gap]
        lines.append('| ' + ' | '.join(str(x).replace('|', '/').replace('\n', ' ') for x in cols) + ' |')
    (ROOT / 'docs/coverage.md').write_text('\n'.join(lines) + '\n')
    return {'areas': len(groups), 'surfaces': len(surfaces), 'report': str(ROOT / 'docs/coverage.md')}


def snapshot():
    result = {'created_at': now(), 'protected': protected(), 'settings': [], 'files': {},
              'note': 'Read-only inventory baseline; file fingerprints are not full restore backups. Each committed transaction stores its own backups.'}
    seen = set()
    for name in ('desktop', 'applications', 'system'):
        p = ROOT / 'audits' / (name + '.json')
        if not p.exists():
            continue
        group = read(p)
        for setting in group.get('settings', []):
            identity = setting['schema'], setting['key']
            if identity in seen:
                continue
            seen.add(identity)
            try:
                value = gsettings('get', *identity)
                result['settings'].append({'schema': identity[0], 'key': identity[1], 'value': value})
            except Exception as e:
                result['settings'].append({'schema': identity[0], 'key': identity[1], 'error': str(e)})
        for path in group.get('config_files', []):
            try:
                result['files'][path] = node(Path(path))
                if Path(path).is_symlink() and Path(path).is_file():
                    result['files'][path]['resolved_sha256'] = digest(Path(path).read_bytes())
            except Exception as e:
                result['files'][path] = {'error': str(e), 'error_type': type(e).__name__}
    path = STATE / 'baselines' / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8] + '.json')
    dump(path, result)
    return {'baseline': str(path), 'protected_paths': len(result['protected']), 'settings': len(result['settings']), 'files': len(result['files'])}


def check(manifest=None):
    if manifest:
        data, files, settings = validate(manifest)
        mismatches = [f['destination'] for f in files if node(Path(f['destination'])) != f['after']]
        mismatches += [s['schema'] + '/' + s['key'] for s in settings if s['before'] != s['value']]
        return {'name': data['name'], 'ok': not mismatches, 'mismatches': mismatches, 'visual_verification': 'not performed'}
    baselines = sorted((STATE / 'baselines').glob('*.json'))
    if not baselines:
        raise ValueError('No baseline yet; run snapshot after the audit')
    baseline = read(baselines[-1])
    current = protected()
    protected_drift = [p for p, v in baseline['protected'].items() if current.get(p) != v]
    settings_drift, errors = [], []
    for s in baseline['settings']:
        if 'error' in s:
            errors.append(s)
            continue
        try:
            actual = gsettings('get', s['schema'], s['key'])
            if actual != s['value']:
                settings_drift.append(dict(s, actual=actual))
        except Exception as e:
            errors.append({'schema': s['schema'], 'key': s['key'], 'error': str(e)})
    file_drift, skipped = [], []
    for raw, expected in baseline['files'].items():
        p = Path(raw)
        if 'error' in expected:
            # The baseline has no fingerprint for this path. If it is still unreadable for the same
            # reason (permission), record an honest skip; anything else is a real error.
            try:
                fingerprint(p)
            except PermissionError as e:
                if baseline_permission_error(expected):
                    skipped.append({'path': raw, 'reason': f'unreadable (permission) at snapshot and now: {e}'})
                else:
                    errors.append({'path': raw, 'error': str(e), 'baseline_error': expected['error']})
            except Exception as e:
                errors.append({'path': raw, 'error': str(e), 'baseline_error': expected['error']})
            else:
                errors.append({'path': raw, 'error': 'readable now but the baseline has no fingerprint; review, then take a new snapshot',
                               'baseline_error': expected['error']})
            continue
        try:
            if fingerprint(p) != expected:
                file_drift.append(raw)
        except Exception as e:
            errors.append({'path': raw, 'error': str(e)})
    return {'ok': not (protected_drift or settings_drift or file_drift or errors), 'complete': not skipped,
            'baseline': str(baselines[-1]),
            'protected_drift': protected_drift, 'settings_drift': settings_drift, 'file_drift': file_drift,
            'errors': errors, 'skipped': skipped,
            'scope': 'Selected configuration fingerprints and protected files only; visual/login/boot checks remain separate.'}


def fingerprint(p):
    actual = node(p)
    if p.is_symlink() and p.is_file():
        actual['resolved_sha256'] = digest(p.read_bytes())
    return actual


def baseline_permission_error(entry):
    # Newer baselines record the exception class; older ones only kept str(error).
    if 'error_type' in entry:
        return entry['error_type'] == 'PermissionError'
    return entry['error'].startswith('[Errno 13]') or 'Permission denied' in entry['error']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('audit', help='Combine dated specialist inventory reports')
    commands.add_parser('snapshot', help='Capture current settings and file fingerprints without applying a theme')
    p = commands.add_parser('apply', help='Preview a preset; --commit installs with backups')
    p.add_argument('manifest')
    p.add_argument('--commit', action='store_true')
    p = commands.add_parser('check', help='Check latest inventory baseline or a supplied preset')
    p.add_argument('manifest', nargs='?')
    p = commands.add_parser('restore', help='Restore only a specific Studio transaction')
    p.add_argument('transaction')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run Studio as your desktop user. System stages use separate reviewed installers.')
    try:
        if args.command == 'apply':
            result = apply(args.manifest, args.commit)
        elif args.command == 'restore':
            if Path(args.transaction).name != args.transaction or args.transaction in ('.', '..'):
                raise ValueError('Use a transaction ID, not a path')
            with locked():
                result = restore_record(STATE / 'transactions' / args.transaction)
            result = {k: result[k] for k in ('id', 'status', 'restored_at')}
        elif args.command == 'check':
            result = check(args.manifest)
        elif args.command == 'snapshot':
            with locked():
                result = snapshot()
        else:
            result = audit()
        print(json.dumps(result, indent=2))
        if result.get('ok') is False:
            return 1
        if result.get('complete') is False:
            # ok covers everything readable; the only incompleteness check() reports is
            # paths unreadable by permission both at snapshot time and now.
            print(f'INCOMPLETE: skipped {len(result["skipped"])} unreadable paths', file=sys.stderr)
            for s in result['skipped']:
                print(f'  {s["path"]}: {s["reason"]}', file=sys.stderr)
        return 0
    except Exception as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
