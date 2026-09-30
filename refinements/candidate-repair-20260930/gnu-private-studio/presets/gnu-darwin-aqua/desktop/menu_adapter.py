#!/usr/bin/env python3
"""GNU-Darwin Aqua optional stage: point the existing Cinnamon menu applet at the preset's original GD monogram.

Contract (driven by ../theme.py):
  plan                              read-only JSON: actions/touched_paths/gsettings/dconf/skipped
  apply   --state P [--commit]      preview unless --commit; P must be fresh; writes the receipt P
  check   --state P                 exit 0 when live values match the receipt target, else 1
  restore --state P [--commit]      revert only the two patched keys and the copied PNG

Only `menu-icon` and `menu-custom` of instance 17 are patched, key-level, preserving every other key and
the file's serialization style. `menu-label` is never written: its hash is recorded at plan time and any
change between plan and apply (or before restore) refuses the write. The applet is reloaded with
org.Cinnamon.updateSetting only in apply/restore, never in plan. A missing 17.json is reported as skipped.
"""
import argparse, ast, base64, hashlib, json, os, struct, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLUG = 'gnu-darwin-aqua'
THEME = 'GNU-Darwin Aqua'
UUID = 'menu@cinnamon.org'
INSTANCE = '17'
KEYS = ('menu-icon', 'menu-custom')
LABEL_KEY = 'menu-label'
EXPECTED_LABEL = 'le cinabon'
LOGO = HERE.parent / 'artwork/menu-logo.png'
LOGO_NAME = SLUG + '-menu-logo.png'
MISSING = {'__gnu_darwin_aqua_missing__': True}
STYLES = ((2, False, '\n'), (4, False, ''), (4, False, '\n'), (2, False, ''), (2, True, '\n'), (4, True, ''), (4, True, '\n'), (2, True, ''))


def sha(data): return hashlib.sha256(data).hexdigest()
def load(path): return json.loads(Path(path).read_text())
def spices(): return Path.home() / '.config/cinnamon/spices' / UUID
def settings_path(): return spices() / (INSTANCE + '.json')
def logo_target(): return spices() / LOGO_NAME


def active():
    """Read-only: the existing instance 17 must still be enabled; never select another instance."""
    r = subprocess.run(['gsettings', 'get', 'org.cinnamon', 'enabled-applets'], capture_output=True, text=True, check=True, timeout=8)
    if not any(x.split(':')[-2:] == [UUID, INSTANCE] for x in ast.literal_eval(r.stdout)):
        raise RuntimeError('Expected existing menu instance 17 is no longer enabled; refusing to select another instance.')


def label_hash(data):
    if LABEL_KEY not in data or 'value' not in data[LABEL_KEY]: raise RuntimeError('Menu schema changed: ' + LABEL_KEY)
    return sha(json.dumps(data[LABEL_KEY]['value'], ensure_ascii=False).encode())


def style_of(raw):
    """Detect the serialization the file currently uses so a key-level patch keeps every other byte."""
    data = json.loads(raw)
    for indent, ascii_only, newline in STYLES:
        if (json.dumps(data, indent=indent, ensure_ascii=ascii_only) + newline).encode() == raw: return indent, ascii_only, newline
    return 4, False, ''  # Cinnamon's own JSON.stringify(data, null, 4)


def serialize(data, style):
    indent, ascii_only, newline = style
    return (json.dumps(data, indent=indent, ensure_ascii=ascii_only) + newline).encode()


def atomic(path, data, private=False, expected=None, mode=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if expected is not None and sha(path.read_bytes()) != expected: raise RuntimeError('Concurrent file update: ' + str(path))
    if mode is None: mode = 0o600 if private else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.' + SLUG + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, mode)
        if expected is not None and sha(path.read_bytes()) != expected: raise RuntimeError('Concurrent file update: ' + str(path))
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def save(path, state): atomic(path, (json.dumps(state, indent=2) + '\n').encode(), private=True)


def png_check():
    if not LOGO.is_file(): raise RuntimeError('Menu artwork missing: ' + str(LOGO))
    raw = LOGO.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR': raise RuntimeError('Menu artwork is not a PNG')
    width, height = struct.unpack('>II', raw[16:24])
    if not (0 < width <= 8192 and 0 < height <= 8192): raise RuntimeError('Invalid menu PNG dimensions')
    return {'source': str(LOGO), 'destination': str(logo_target()), 'sha256': sha(raw), 'bytes': len(raw), 'dimensions': [width, height]}


def refresh(value):
    """Cinnamon's remoteUpdate re-reads the whole file (both keys) on one updateSetting call."""
    r = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path', '/org/Cinnamon', '--method',
                        'org.Cinnamon.updateSetting', UUID, INSTANCE, 'menu-icon', json.dumps(value)],
                       capture_output=True, text=True, check=True, timeout=8)
    return {'method': 'org.Cinnamon.updateSetting', 'uuid': UUID, 'instance': INSTANCE, 'result': r.stdout.strip(), 'visual_verified': False}


def skipped_plan(path):
    return {'version': 1, 'theme': THEME, 'id': SLUG, 'created_at': datetime.now(timezone.utc).isoformat(), 'uuid': UUID, 'instance': INSTANCE,
            'path': str(path), 'present': False, 'status': 'skipped', 'actions': [], 'touched_paths': [], 'gsettings': [], 'dconf': [],
            'skipped': [str(path) + ' absent; menu applet icon stage skipped.']}


def plan():
    """Read-only. Nothing under HOME is written and Cinnamon is not signalled."""
    path = settings_path()
    if not path.is_file(): return skipped_plan(path)
    active()
    raw = path.read_bytes(); data = json.loads(raw)
    if any(k not in data or 'value' not in data[k] for k in KEYS): raise RuntimeError('Menu schema changed')
    artwork = png_check()
    target = logo_target()
    if target.exists():
        existing = target.read_bytes()
        artwork['destination_before'] = {'sha256': sha(existing), 'bytes': len(existing)}
        if sha(existing) != artwork['sha256']: artwork['destination_before']['base64'] = base64.b64encode(existing).decode()
    else: artwork['destination_before'] = None
    after = {'menu-icon': str(target), 'menu-custom': True}
    actions = [{'kind': 'json', 'path': str(path), 'keys': [k, 'value'], 'before': data[k]['value'], 'after': after[k], 'applied': False, 'attempted': False} for k in KEYS]
    label = data[LABEL_KEY]['value']
    skipped = []
    if label != EXPECTED_LABEL: skipped.append('menu-label is ' + json.dumps(label, ensure_ascii=False) + ' rather than the briefed "' + EXPECTED_LABEL + '"; it is hash-guarded and never written.')
    return {'version': 1, 'theme': THEME, 'id': SLUG, 'created_at': datetime.now(timezone.utc).isoformat(), 'uuid': UUID, 'instance': INSTANCE,
            'path': str(path), 'present': True, 'status': 'planned', 'style': list(style_of(raw)), 'label_key': LABEL_KEY, 'label_sha256': label_hash(data),
            'menu_label': label, 'label_matches_brief': label == EXPECTED_LABEL, 'artwork': artwork, 'actions': actions,
            'touched_paths': sorted({str(path), str(target)}), 'gsettings': [], 'dconf': [], 'skipped': skipped, 'writes': 0}


def set_values(state, target):
    """Patch only the journaled keys, key-level, in the file's own serialization; the label hash gates every write."""
    active()
    path = Path(state['path']); raw = path.read_bytes(); data = json.loads(raw)
    if label_hash(data) != state['label_sha256']: raise RuntimeError('Menu label changed since plan; refusing to write ' + str(path))
    for action in state['actions']:
        key = action['keys'][0]
        if action['kind'] != 'json' or key not in KEYS or action['keys'] != [key, 'value']: raise RuntimeError('Unexpected journal action')
        if key not in data or 'value' not in data[key]: raise RuntimeError('Menu schema changed: ' + key)
        if data[key]['value'] not in (action['before'], action['after']): raise RuntimeError('Menu icon apply/restore conflict: ' + key)
    for action in state['actions']: data[action['keys'][0]]['value'] = action[target]
    if label_hash(data) != state['label_sha256']: raise RuntimeError('Menu label invariant failed')
    atomic(path, serialize(data, style_of(raw)), expected=sha(raw))
    if label_hash(load(path)) != state['label_sha256']: raise RuntimeError('Menu label invariant failed after write')


def place_logo(state):
    art = state['artwork']; target = Path(art['destination'])
    raw = LOGO.read_bytes()
    if sha(raw) != art['sha256']: raise RuntimeError('Menu artwork changed since plan')
    if target.exists() and sha(target.read_bytes()) == art['sha256']: return
    atomic(target, raw, mode=0o644)


def remove_logo(state):
    art = state.get('artwork')
    if not art: return
    target = Path(art['destination']); before = art.get('destination_before')
    if before is None: target.unlink(missing_ok=True); return
    if target.exists() and sha(target.read_bytes()) == before['sha256']: return
    if 'base64' in before: atomic(target, base64.b64decode(before['base64']), mode=0o644)
    # Identical content before and after: the file was already the preset's mark; leave it.


def commit(state, path):
    """Apply a previously computed plan; the label hash inside it is re-verified against the live file."""
    if state['status'] == 'skipped':
        save(path, state); print(json.dumps({'ok': True, 'status': 'skipped', 'skipped': state['skipped']}, indent=2)); return 0
    state['status'] = 'applying'; state['attempted'] = True; save(path, state)
    try:
        place_logo(state)
        set_values(state, 'after')
        for action in state['actions']: action['attempted'] = True; action['applied'] = True
        state['refresh'] = refresh(state['actions'][0]['after']); state['status'] = 'applied'; save(path, state)
    except Exception as exc:
        state['error'] = str(exc); save(path, state)
        try: restore(state, path); state['status'] = 'rolled-back'
        except Exception as err: state['status'] = 'recovery-required'; state['recovery_error'] = str(err)
        save(path, state); raise
    result = check(state); print(json.dumps(result, indent=2)); return 0 if result['ok'] else 1


def restore(state, path):
    if state.get('status') == 'skipped' or not state.get('present', True):
        state['status'] = 'restored'; save(path, state); return
    state['status'] = 'restoring'; save(path, state)
    if state.get('attempted'):
        path_ = Path(state['path'])
        if not path_.is_file(): state['restore_note'] = 'settings file absent at restore; key restore skipped'
        elif any(d.get(a['keys'][0], {}).get('value') != a['before'] for d in [json.loads(path_.read_bytes())] for a in state['actions']):
            set_values(state, 'before')  # only rewrite when a journaled key really moved; a refused apply leaves nothing to revert
        for action in state['actions']: action['applied'] = False; action['attempted'] = False
        remove_logo(state)
        state['restore_refresh'] = refresh(state['actions'][0]['before'])
    state['status'] = 'restored'; save(path, state)


def check(state):
    if state.get('status') == 'skipped' or not state.get('present', True):
        return {'ok': True, 'target': 'skipped', 'problems': [], 'uuid': UUID, 'instance': INSTANCE, 'visual_verified': False}
    active()
    path = Path(state['path']); target = 'before' if state.get('status') in ('restored', 'rolled-back', 'planned') else 'after'
    problems = []
    if not path.is_file(): return {'ok': False, 'target': target, 'problems': ['settings file missing'], 'uuid': UUID, 'instance': INSTANCE, 'visual_verified': False}
    data = json.loads(path.read_bytes())
    for action in state['actions']:
        key = action['keys'][0]
        if key not in data or 'value' not in data[key] or data[key]['value'] != action[target]: problems.append(key)
    if label_hash(data) != state['label_sha256']: problems.append(LABEL_KEY + ' changed')
    art = state['artwork']; dest = Path(art['destination'])
    if target == 'after':
        if not dest.is_file(): problems.append('logo copy missing')
        elif sha(dest.read_bytes()) != art['sha256']: problems.append('logo copy content changed')
    else:
        before = art.get('destination_before')
        if before is None and dest.exists(): problems.append('logo copy left behind')
        if before is not None and (not dest.is_file() or sha(dest.read_bytes()) != before['sha256']): problems.append('pre-existing logo file not as found')
    return {'ok': not problems, 'target': target, 'problems': problems, 'uuid': UUID, 'instance': INSTANCE, 'visual_verified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'apply', 'check', 'restore']); p.add_argument('--state', type=Path); p.add_argument('--commit', action='store_true')
    a = p.parse_args()
    if a.command == 'plan': print(json.dumps(plan(), indent=2)); return 0
    if not a.state: p.error('--state required')
    if a.command == 'check':
        result = check(load(a.state)); print(json.dumps(result, indent=2)); return 0 if result['ok'] else 1
    if a.command == 'restore':
        state = load(a.state)
        if not a.commit: print(json.dumps({'preview': True, 'keys': [x['keys'][0] for x in state.get('actions', [])], 'target': 'before'}, indent=2)); return 0
        restore(state, a.state); result = check(state); print(json.dumps(result, indent=2)); return 0 if result['ok'] else 1
    state = plan()
    if not a.commit: print(json.dumps({'preview': True, **state}, indent=2)); return 0
    if a.state.exists(): raise RuntimeError('Use a fresh state path')
    return commit(state, a.state)


if __name__ == '__main__':
    try: sys.exit(main() or 0)
    except Exception as exc: print(json.dumps({'ok': False, 'error': str(exc)}, indent=2)); sys.exit(2)
