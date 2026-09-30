#!/usr/bin/python3
"""Tangerine Graphite desktop stage: install assets, trial with independent timed recovery, keep or restore.

The user's panel layout (org.cinnamon enabled-applets / panels-*) is only snapshotted for
evidence and is never written; the nothing-island and eyes applets stay where they are.
`plan` is read-only and prints every target without touching anything.
"""
import argparse, base64, configparser, contextlib, fcntl, hashlib, io, json, os, re, shutil, signal, subprocess, time, uuid
from pathlib import Path
from gi.repository import Gio, GLib

ROOT = LINEAGE_PRESET
HOME = Path.home()
STATE = ROOT / 'state'
LINEAGE_FILE = ROOT / 'lineage.json'


class LineageError(RuntimeError):
    def __init__(self, why): super().__init__('lineage: ' + str(LINEAGE_FILE) + ': ' + why)


def load_lineage():
    try: layout = json.loads(LINEAGE_FILE.read_text())
    except OSError as error: raise LineageError(error.strerror or type(error).__name__) from None
    except ValueError as error: raise LineageError('not valid JSON (' + str(error) + ')') from None
    if not (isinstance(layout, dict) and layout.get('schema') == 1 and set(layout) <= {'$comment', 'schema', 'desktop_control'}
            and isinstance(layout.get('desktop_control'), dict) and set(layout['desktop_control']) == {'name', 'slug', 'temp_tag'}):
        raise LineageError('unsupported desktop_control layout')
    LAYOUT = layout['desktop_control']
    if not (all(isinstance(LAYOUT[k], str) for k in ('name', 'slug', 'temp_tag'))
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 -]{0,63}', LAYOUT['name'])
            and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', LAYOUT['slug']) and re.fullmatch(r'[a-z0-9_]+', LAYOUT['temp_tag'])
            ):
        raise LineageError('unsafe desktop_control value')
    return LAYOUT


def require_lineage():
    """Gate for install/trial/plan/targets: a valid lineage.json whose slug is this folder and whose name is design.json's."""
    if LINEAGE_ERROR is not None: raise LINEAGE_ERROR
    try: design_name = json.loads((ROOT / 'design.json').read_text()).get('name')
    except (OSError, ValueError, AttributeError) as error: raise LineageError('cannot read the design.json name (' + str(error) + ')') from None
    if LAYOUT['slug'] != ROOT.name: raise LineageError('slug ' + repr(LAYOUT['slug']) + ' is not the preset folder ' + repr(ROOT.name))
    if LAYOUT['name'] != design_name: raise LineageError('name ' + repr(LAYOUT['name']) + ' is not the design.json name ' + repr(design_name))
    return LAYOUT


try: LAYOUT, LINEAGE_ERROR = load_lineage(), None
except Exception as error:  # never raises on import: recovery must not depend on this file
    LAYOUT, LINEAGE_ERROR = None, error if isinstance(error, LineageError) else LineageError(type(error).__name__ + ' (' + str(error) + ')')
NAME = LAYOUT['name'] if LAYOUT else None
SLUG = LAYOUT['slug'] if LAYOUT else None
SERVICE = 'ocean-silk-opacity.service'


def run(args, check=True):
    return subprocess.run(args, text=True, capture_output=True, timeout=15, check=check).stdout.strip()


def load(p): return json.loads(Path(p).read_text())


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.' + uuid.uuid4().hex)
    with tmp.open('x') as f:
        os.chmod(tmp, 0o600); json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(p)


@contextlib.contextmanager
def lock():
    STATE.mkdir(exist_ok=True)
    with (STATE / '.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def fingerprint(path):
    h = hashlib.sha256()
    for p in sorted(path.rglob('*')):
        if p.is_file(): h.update(str(p.relative_to(path)).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def tree_record(path, content_overrides=None):
    """Hash directory structure, modes and symlink targets without following aliases."""
    if path.is_symlink(): return {'kind': 'symlink', 'target': os.readlink(path)}
    if not path.exists(): return {'kind': 'absent'}
    if not path.is_dir(): raise RuntimeError('Expected asset directory: ' + str(path))
    rows = []
    for p in [path, *sorted(path.rglob('*'))]:
        name = str(p.relative_to(path))
        if p.is_symlink(): rows.append([name, 'symlink', os.readlink(p)])
        elif p.is_dir(): rows.append([name, 'directory', p.stat().st_mode & 0o777])
        elif p.is_file():
            content = content_overrides[str(p)] if content_overrides and str(p) in content_overrides else p.read_bytes()
            rows.append([name, 'file', p.stat().st_mode & 0o777, hashlib.sha256(content).hexdigest()])
        else: raise RuntimeError('Unsupported node in asset tree: ' + str(p))
    return {'kind': 'directory', 'sha256': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()}


def asset_locations():
    require_lineage()
    return [(ROOT / 'desktop' / name, HOME / parent / name)
            for name, parent in ((NAME, '.themes'), (NAME + ' icons', '.icons'), (NAME + ' cursors', '.icons'))]


def validate_source():
    require_lineage()
    report = load(ROOT / 'verification/isolated-latest.json')
    source = ROOT / 'desktop' / NAME
    if report['status'] != 'passed' or report['source_sha256'] != fingerprint(source):
        raise RuntimeError('A passing isolated test of the exact theme source is required')
    for src, dst in asset_locations():
        if src.is_symlink() or not src.is_dir(): raise RuntimeError('Missing regular source directory: ' + str(src))
        for parent in dst.parents:
            if parent == HOME: break
            if parent.is_symlink(): raise RuntimeError('Asset destination ancestor is a symlink: ' + str(parent))


def copy_asset(src, dst):
    if src.is_symlink(): dst.symlink_to(os.readlink(src))
    else: shutil.copytree(src, dst, symlinks=True)


def remove_asset(path, expected):
    """Only delete a known transaction-owned copy after a fresh content check."""
    actual = tree_record(path)
    if actual == {'kind': 'absent'}: return
    if actual != expected: raise RuntimeError('Asset cleanup conflict: ' + str(path))
    if path.is_symlink(): path.unlink()
    else: shutil.rmtree(path)


def prepare_assets(path, ident):
    """Copy source and original trees before the timer and before any live rename."""
    rows = []
    backup_root = path.parent / 'assets'
    backup_root.mkdir(parents=True, exist_ok=True)
    for n, (src, dst) in enumerate(asset_locations()):
        dst.parent.mkdir(parents=True, exist_ok=True)
        before, after = tree_record(dst), tree_record(src)
        token = '.' + SLUG + '-' + ident + '-' + str(n)
        row = {'destination': str(dst), 'before': before, 'after': after, 'phase': 'prepared',
               'stage': str(dst.parent / (token + '.stage')), 'parked': str(dst.parent / (token + '.previous')),
               'rejected': str(dst.parent / (token + '.rejected')), 'restore_stage': str(dst.parent / (token + '.restore')),
               'backup': str(backup_root / (str(n) + '.before'))}
        for key in ('stage', 'parked', 'rejected', 'restore_stage', 'backup'):
            if os.path.lexists(row[key]): raise RuntimeError('Existing transaction asset: ' + row[key])
        if before['kind'] != 'absent':
            copy_asset(dst, Path(row['backup']))
            if tree_record(Path(row['backup'])) != before: raise RuntimeError('Original changed during asset backup: ' + str(dst))
        copy_asset(src, Path(row['stage']))
        if tree_record(Path(row['stage'])) != after or tree_record(src) != after:
            raise RuntimeError('Source changed during asset staging: ' + str(src))
        if tree_record(dst) != before: raise RuntimeError('Installed assets changed during staging: ' + str(dst))
        rows.append(row)
    return rows


def swap_assets(path, data):
    # The caller must arm independent recovery before entering this function.
    if not data.get('guard_armed'): raise RuntimeError('Independent recovery must be armed before asset swaps')
    for row in data.get('assets', []):
        dst, stage, parked = (Path(row[k]) for k in ('destination', 'stage', 'parked'))
        if tree_record(dst) != row['before'] or tree_record(stage) != row['after']:
            raise RuntimeError('Assets changed before swap: ' + str(dst))
        if row['before'] == row['after']:
            row['phase'] = 'unchanged'; save(path, data); continue
        row['phase'] = 'parking'; save(path, data)
        if row['before']['kind'] != 'absent': dst.rename(parked)
        row['phase'] = 'parked'; save(path, data)
        stage.rename(dst)
        row['phase'] = 'installed'; save(path, data)
        if tree_record(dst) != row['after']: raise RuntimeError('Asset swap verification failed: ' + str(dst))


def asset_restore_problem(row):
    try:
        actual = tree_record(Path(row['destination']))
        if actual not in (row['before'], row['after'], {'kind': 'absent'}):
            return 'installed tree changed'
        if actual == {'kind': 'absent'} and row['before']['kind'] != 'absent' and row['phase'] not in ('parking', 'parked', 'restoring'):
            return 'installed tree was removed externally'
        if row['before']['kind'] != 'absent' and tree_record(Path(row['backup'])) != row['before']:
            return 'original asset backup changed'
        for key, expected in (('stage', row['after']), ('parked', row['before']),
                              ('rejected', row['after']), ('restore_stage', row['before'])):
            if tree_record(Path(row[key])) not in (expected, {'kind': 'absent'}): return key + ' tree changed'
    except Exception as error: return str(error)
    return None


def restore_asset(path, data, row):
    problem = asset_restore_problem(row)
    if problem: raise RuntimeError(problem + ': ' + row['destination'])
    dst, parked, rejected, staged = (Path(row[k]) for k in ('destination', 'parked', 'rejected', 'restore_stage'))
    if tree_record(dst) != row['before']:
        original = parked
        if row['before']['kind'] != 'absent' and tree_record(original) != row['before']:
            original = staged
            if tree_record(staged) == {'kind': 'absent'}: copy_asset(Path(row['backup']), staged)
            if tree_record(staged) != row['before']: raise RuntimeError('Restore asset copy differs: ' + str(staged))
        row['phase'] = 'restoring'; save(path, data)
        if tree_record(dst) != {'kind': 'absent'}:
            remove_asset(rejected, row['after'])
            dst.rename(rejected)
        if row['before']['kind'] != 'absent': original.rename(dst)
        if tree_record(dst) != row['before']: raise RuntimeError('Asset restore verification failed: ' + str(dst))
    row['phase'] = 'restored'; save(path, data)
    for key, expected in (('stage', row['after']), ('parked', row['before']),
                          ('rejected', row['after']), ('restore_stage', row['before'])):
        remove_asset(Path(row[key]), expected)


def cleanup_kept_assets(path, data):
    # Receipt backups remain available for manual restore. Only known temporary
    # copies beside the active trees are removed after the user keeps the trial.
    errors = []
    for row in data.get('assets', []):
        for key, expected in (('stage', row['after']), ('parked', row['before']),
                              ('rejected', row['after']), ('restore_stage', row['before'])):
            try: remove_asset(Path(row[key]), expected)
            except Exception as error: errors.append(str(error))
    if errors: data['asset_cleanup_errors'] = errors
    save(path, data)


def record(p):
    if p.is_symlink(): return {'kind': 'symlink', 'target': os.readlink(p)}
    if not p.exists(): return {'kind': 'absent'}
    if not p.is_file(): raise RuntimeError('Expected file/link: ' + str(p))
    return {'kind': 'file', 'data': base64.b64encode(p.read_bytes()).decode(), 'mode': p.stat().st_mode & 0o777}


def write(p, row):
    p.parent.mkdir(parents=True, exist_ok=True)
    if row['kind'] == 'absent':
        p.unlink(missing_ok=True); return
    tmp = p.with_name('.' + p.name + '-' + (LAYOUT['temp_tag'] if LAYOUT else ROOT.name.replace('-', '_')) + '-' + uuid.uuid4().hex)
    if row['kind'] == 'symlink': tmp.symlink_to(row['target'])
    else:
        tmp.write_bytes(base64.b64decode(row['data'])); tmp.chmod(row.get('mode', 0o644))
    tmp.replace(p)


def text_row(text): return {'kind': 'file', 'data': base64.b64encode(text.encode()).decode(), 'mode': 0o644}


def ini(p, section, values):
    cfg = configparser.ConfigParser(interpolation=None); cfg.optionxform = str
    if p.exists(): cfg.read(p)
    if section not in cfg: cfg[section] = {}
    cfg[section].update(values)
    out = io.StringIO(); cfg.write(out, space_around_delimiters=False)
    return text_row(out.getvalue())


def targets():
    require_lineage()
    result = []
    def key(schema, name, value):
        obj = Gio.Settings.new(schema)
        if name in obj.props.settings_schema.list_keys():
            variant = GLib.Variant(obj.get_value(name).get_type_string(), value)
            original = obj.get_user_value(name)
            result.append({'schema': schema, 'key': name, 'before': obj.get_value(name).print_(True),
                           'user': original.print_(True) if original is not None else None, 'after': variant.print_(True)})
    for schema in ('org.cinnamon.desktop.interface', 'org.gnome.desktop.interface'):
        for name, value in {'gtk-theme': NAME, 'icon-theme': NAME + ' icons', 'cursor-theme': NAME + ' cursors',
                            'cursor-size': 28, 'font-name': 'Ubuntu 11', 'document-font-name': 'Ubuntu 11',
                            'monospace-font-name': 'Hack 11', 'color-scheme': 'prefer-dark',
                            'gtk-application-prefer-dark-theme': True}.items(): key(schema, name, value)
    key('org.cinnamon.desktop.wm.preferences', 'theme', NAME)
    key('org.cinnamon.desktop.wm.preferences', 'titlebar-font', 'Ubuntu Medium 11')
    key('org.cinnamon.desktop.background', 'picture-uri', (ROOT / 'artwork/wallpaper.png').as_uri())
    key('org.cinnamon.desktop.background', 'picture-options', 'zoom')
    key('org.cinnamon.desktop.background.slideshow', 'slideshow-enabled', False)
    # Panel layout is PRESERVED: the user's current applets (nothing-island, c-eyes, ...) and
    # panel topology are recorded in the receipt for evidence only and never written.
    layout = panel_layout()
    if not any(':menu@cinnamon.org:' in e for e in layout['enabled-applets']): raise RuntimeError('Expected menu instance missing')
    # Change shell last, after supporting files and the independent timer exist.
    key('org.cinnamon.theme', 'name', NAME)
    files = {}
    for version in ('3.0', '4.0'):
        p = HOME / ('.config/gtk-' + version + '/settings.ini')
        files[str(p)] = ini(p, 'Settings', {'gtk-theme-name': NAME, 'gtk-icon-theme-name': NAME + ' icons',
                                           'gtk-cursor-theme-name': NAME + ' cursors', 'gtk-cursor-theme-size': '28',
                                           'gtk-font-name': 'Ubuntu 11', 'gtk-application-prefer-dark-theme': '1'})
    for name in ('gtk.css', 'gtk-dark.css', 'assets'):
        p = HOME / '.config/gtk-4.0' / name
        files[str(p)] = {'kind': 'symlink', 'target': str(HOME / '.themes' / NAME / 'gtk-4.0' / name)}
    p = HOME / '.gtkrc-2.0'; contents = p.read_text() if p.exists() else ''
    for key, value in {'gtk-theme-name': NAME, 'gtk-icon-theme-name': NAME + ' icons', 'gtk-cursor-theme-name': NAME + ' cursors', 'gtk-font-name': 'Ubuntu 11'}.items():
        line = key + '="' + value + '"'
        if re.search(r'^' + key + r'\s*=.*$', contents, re.M): contents = re.sub(r'^' + key + r'\s*=.*$', line, contents, flags=re.M)
        else: contents += '\n' + line + '\n'
    files[str(p)] = text_row(contents)
    for relative in ('.icons/default/index.theme', '.local/share/icons/default/index.theme'):
        p = HOME / relative; files[str(p)] = ini(p, 'Icon Theme', {'Inherits': NAME + ' cursors'})
    # Text surfaces are opaque in this design: the old translucency helper is stopped for the
    # trial and its autostart hidden; both are journaled and restored. mint-dashboard is left alone.
    p = HOME / '.config/autostart/ocean-silk-opacity.desktop'
    if p.exists(): files[str(p)] = ini(p, 'Desktop Entry', {'Hidden': 'true'})
    return result, files, layout


def panel_layout():
    """Read-only snapshot of the user's panel layout; never written by this preset."""
    cinnamon = Gio.Settings.new('org.cinnamon')
    return {name: cinnamon.get_value(name).unpack() for name in
            ('enabled-applets', 'panels-enabled', 'panels-height', 'panels-autohide', 'enabled-desklets',
             'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes') if name in cinnamon.props.settings_schema.list_keys()}


def bus(method, *args):
    return run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path', '/org/Cinnamon', '--method', method, *args])


def checkpoint_path():
    return STATE / load(STATE / 'latest.json')['id'] / 'desktop.json'


def same_setting(row, actual, expected):
    if row['schema'] == 'org.cinnamon' and row['key'] in ('panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes'):
        # Cinnamon serializes these JSON strings without spaces after accepting them.
        return json.loads(GLib.Variant.parse(None, actual, None, None).unpack()) == json.loads(GLib.Variant.parse(None, expected, None, None).unpack())
    return actual == expected


def restore(path, automatic=False):
    data = load(path)
    if automatic and data['status'] not in ('applying', 'pending', 'restoring', 'recovery-required'): return {'status': data['status'], 'action': 'timer ignored'}
    if data['status'] == 'restored': return {'status': 'restored'}
    # External modifications are never silently overwritten by manual recovery.
    conflicts = []
    for raw, row in data['files'].items():
        if record(Path(raw)) not in (row['before'], row['after']): conflicts.append(raw)
    for row in data['settings']:
        current = Gio.Settings.new(row['schema']).get_value(row['key']).print_(True)
        if not any(same_setting(row, current, value) for value in (row['before'], row['after'])): conflicts.append(row['schema'] + '/' + row['key'])
    for row in data.get('assets', []):
        if asset_restore_problem(row): conflicts.append(row['destination'])
    if conflicts and not automatic: raise RuntimeError('Restore conflicts: ' + ', '.join(conflicts))
    data['status'] = 'restoring'; save(path, data)
    try:
        # Restore same-name source trees before reloading the shell. A conflict in one
        # tree must not prevent automatic recovery of the other trees or settings.
        for row in data.get('assets', []):
            if row['destination'] in conflicts: continue
            try: restore_asset(path, data, row)
            except Exception:
                if not automatic: raise
                conflicts.append(row['destination'])
        # Shell was appended last during apply; restore it first even when another file has drifted.
        for row in reversed(data['settings']):
            if row['schema'] + '/' + row['key'] in conflicts: continue
            obj = Gio.Settings.new(row['schema'])
            if row['user'] is None: obj.reset(row['key'])
            else: obj.set_value(row['key'], GLib.Variant.parse(None, row['user'], None, None))
        Gio.Settings.sync()
        for raw, row in data['files'].items():
            if raw not in conflicts: write(Path(raw), row['before'])
        run(['systemctl', '--user', 'start' if data['opacity_active'] else 'stop', SERVICE])
        try: bus('org.Cinnamon.ReloadTheme')
        except subprocess.CalledProcessError: pass
        data['status'] = 'recovery-required' if conflicts else 'restored'
        data['conflicts'] = conflicts; save(path, data)
    except Exception as error:
        data['status'] = 'recovery-required'; data['error'] = str(error); save(path, data); raise
    return {'status': data['status'], 'receipt': str(path)}


def install():
    validate_source()
    copied = []
    # Standalone installation never replaces an existing different/linked directory.
    for src, dst in asset_locations():
        if dst.exists() or dst.is_symlink():
            if dst.is_symlink() or tree_record(dst) != tree_record(src): raise RuntimeError('Existing destination differs: ' + str(dst))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copytree(src, dst, symlinks=True); copied.append(str(dst))
    return {'installed_assets': copied}


def trial():
    validate_source()
    if (STATE / 'latest.json').exists():
        old = load(checkpoint_path())
        recovery = checkpoint_path().parent / 'recovery.json'
        if recovery.exists() and load(recovery).get('status') == 'recovery-required':
            raise RuntimeError('Resolve incomplete coordinated recovery first')
        if old['status'] in ('applying', 'pending', 'recovery-required', 'restoring'): raise RuntimeError('Resolve the previous trial first')
    settings, files, layout = targets()
    if any(row['schema'] == 'org.cinnamon' for row in settings): raise RuntimeError('Panel layout keys must never be written')
    ident = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    path = STATE / ident / 'desktop.json'
    assets = prepare_assets(path, ident)
    validate_source()
    data = {'id': ident, 'status': 'applying', 'created': time.time(), 'deadline': time.time() + 180,
            'settings': settings, 'files': {p: {'before': record(Path(p)), 'after': row} for p, row in files.items()},
            'assets': assets, 'preserved_panel_layout': layout,
            'opacity_active': run(['systemctl', '--user', 'is-active', SERVICE], check=False) == 'active',
            'unit': SLUG + '-recovery-' + uuid.uuid4().hex[:10]}
    save(path, data); save(STATE / 'latest.json', {'id': ident})
    try:
        # Timer lives outside this process and survives a closed terminal.
        run(['systemd-run', '--user', '--quiet', '--collect', '--unit=' + data['unit'], '--on-active=180s',
             '--timer-property=AccuracySec=1s', '/usr/bin/python3', str(ROOT / 'theme.py'),
             'recover', '--receipt', str(path)])
        data['guard_armed'] = True; save(path, data)
        swap_assets(path, data)
        for raw, row in files.items(): write(Path(raw), row)
        run(['systemctl', '--user', 'stop', SERVICE])
        for row in settings:
            obj = Gio.Settings.new(row['schema'])
            if not obj.set_value(row['key'], GLib.Variant.parse(None, row['after'], None, None)):
                raise RuntimeError('Setting rejected: ' + row['key'])
        Gio.Settings.sync(); bus('org.Cinnamon.ReloadTheme')
        data['status'] = 'pending'; save(path, data)
        return dict(revised_assets=[r['destination'] for r in assets if r['before'] != r['after']],
                    status='pending', receipt=str(path), deadline=data['deadline'], keep_command='./theme.sh keep')
    except Exception:
        restore(path, automatic=True); raise


def check(path, overlay_state=None):
    overrides = {}
    if overlay_state and overlay_state.exists():
        overlay = load(overlay_state)
        # Without a valid lineage.json the theme tree comes from the receipt itself; no tree -> no projection.
        theme_root = str(HOME / '.themes' / NAME) + '/' if NAME else next((row['destination'] + '/' for row in load(path).get('assets', [])
                                                                          if Path(row['destination']).parent == HOME / '.themes'), None)
        for action in overlay['actions']:
            raw = action.get('path', '')
            if theme_root is None or not raw.startswith(theme_root) or not action.get('applied'): continue
            target = Path(raw)
            if action['kind'] != 'suffix' or target.is_symlink():
                raise RuntimeError('Only regular-file theme suffix overlays may be projected')
            current = target.read_bytes(); suffix = action['after'].encode()
            if not suffix or not current.endswith(suffix):
                raise RuntimeError('Theme overlay changed: ' + raw)
            overrides[raw] = current[:-len(suffix)]
    data = load(path); drift = []
    for row in data['settings']:
        if not same_setting(row, Gio.Settings.new(row['schema']).get_value(row['key']).print_(True), row['after']): drift.append(row['schema'] + '/' + row['key'])
    for raw, row in data['files'].items():
        if record(Path(raw)) != row['after']: drift.append(raw)
    for row in data.get('assets', []):
        try:
            if tree_record(Path(row['destination']), overrides) != row['after']: drift.append(row['destination'])
        except Exception: drift.append(row['destination'])
    bus('org.freedesktop.DBus.Peer.Ping')
    return {'status': data['status'], 'drift': drift, 'receipt': str(path)}


def plan():
    """Read-only: every setting, file and asset the trial would change, plus source readiness."""
    settings, files, layout = targets()
    try: validate_source(); source = 'ready'
    except Exception as error: source = str(error)
    assets = []
    for src, dst in asset_locations():
        try: state = tree_record(dst)
        except RuntimeError as error: state = {'kind': 'unsupported', 'error': str(error)}
        assets.append({'source': str(src), 'source_present': src.is_dir() and not src.is_symlink(),
                       'destination': str(dst), 'destination_state': state['kind'],
                       'changes': src.is_dir() and state['kind'] != 'unsupported' and tree_record(src) != state})
    return {'theme': NAME, 'settings': [{k: row[k] for k in ('schema', 'key', 'before', 'after')} for row in settings if not same_setting(row, row['before'], row['after'])],
            'unchanged_settings': sum(1 for row in settings if same_setting(row, row['before'], row['after'])),
            'files': {p: {'before': record(Path(p))['kind'], 'after': row['kind'], 'changes': record(Path(p)) != row} for p, row in files.items()},
            'assets': assets, 'preserved_panel_layout': layout, 'source_validation': source,
            'opacity_service_active': run(['systemctl', '--user', 'is-active', SERVICE], check=False) == 'active',
            'wallpaper_present': (ROOT / 'artwork/wallpaper.png').is_file(), 'writes': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'install', 'trial', 'keep', 'check', 'restore', 'recover'])
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--overlay-state', type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0: raise RuntimeError('Run as desktop user')
    if args.command == 'trial':
        def timeout(signum, frame): raise TimeoutError('Trial controller exceeded its 90-second budget')
        signal.signal(signal.SIGALRM, timeout); signal.alarm(90)
    if args.command == 'plan':
        print(json.dumps(plan(), indent=2)); return 0
    with lock():
        if args.command == 'install': result = install()
        elif args.command == 'trial': result = trial()
        else:
            path = args.receipt or checkpoint_path()
            if args.command in ('restore', 'recover'): result = restore(path, args.command == 'recover')
            else:
                result = check(path, args.overlay_state)
                if args.command == 'keep':
                    data = load(path)
                    if data['status'] != 'pending' or time.time() >= data['deadline'] or result['drift']:
                        raise RuntimeError('Cannot keep an expired, modified or unsuccessful trial')
                    data['status'] = 'kept'; save(path, data)
                    run(['systemctl', '--user', 'stop', data['unit'] + '.timer'], check=False)
                    cleanup_kept_assets(path, data)
                    result['status'] = 'kept'
        print(json.dumps(result, indent=2))
        return 1 if result.get('drift') or result.get('status') == 'recovery-required' else 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error': str(error)})); raise SystemExit(2)
