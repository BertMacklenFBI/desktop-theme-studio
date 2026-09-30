#!/usr/bin/python3
"""GNU-Darwin Workstation desktop stage: install assets, move the panel to the approved right launcher rail and bottom application row, trial with
independent timed recovery, keep or restore.

Unlike the tangerine-graphite lineage, this preset WRITES the panel layout (the design specifies the two-panel
right launcher rail and bottom application row): org.cinnamon enabled-applets / panels-enabled / panels-height / panel-zone-*-sizes /
enabled-desklets, next-applet-id (bumped, never decremented), plus two new launcher/calendar instances and
their settings files. Everything is journaled in the receipt before the first write, and the settings files that
Cinnamon deletes for removed multi-instance applets (c-eyes 41/44) are snapshotted byte-exact first. Restore
follows design.json panel_layout.restore; the menu icon belongs to desktop/menu_adapter.py (theme.py stage).
`plan` is read-only and prints every target without touching anything.
"""
# WHY THIS IS A FULL COPY, NOT A LINEAGE SHIM
# This file started as a byte copy of lib/lineage/desktop_control.py (the tangerine-graphite lineage core,
# sha256 3aed2949...8d224ac1 on 2026-09-26) and adds the taskbar panel writer, modelled on how
# lib/lineage/desktop_control_panel.py journals the panel keys. lib/lineage/ is shared live code: other
# presets' shims load it at run time, including for the restore of receipts that already exist. Adding a
# panel writer there would change their code under them and needs a guarded publication (new
# repairs/lineage-lib-* folder, PUBLISH.md trust review, golden re-runs). A preset-local full copy keeps the
# change contained, like red-panda-overtime and pastel-leather. Values live in ./lineage.json (validated
# strictly below and cross-checked against design.json panel_layout). Shared-lineage fixes do not reach this
# file automatically: diff it against lib/lineage/desktop_control.py when the lineage changes.
import argparse, base64, configparser, contextlib, fcntl, hashlib, io, json, os, re, shutil, signal, subprocess, time, uuid
from pathlib import Path
from gi.repository import Gio, GLib

# A shim used to pass the preset folder as LINEAGE_PRESET; this full copy lives in the preset folder itself.
ROOT = Path(globals().get('LINEAGE_PRESET') or Path(__file__).resolve().parent)
HOME = Path.home()
CONFIG = Path(GLib.get_user_config_dir())  # where Cinnamon keeps spices settings (GLib.get_user_config_dir)
STATE = ROOT / 'state'
LINEAGE_FILE = ROOT / 'lineage.json'
MONITOR_UUID = 'gnu-darwin-workstation-monitor@desktop-theme-studio'
MONITOR_SOURCE = ROOT / 'desktop/applets' / MONITOR_UUID


def design_appearance():
    """Return appearance values derived from design.json typography and cursor geometry."""
    design = json.loads((ROOT / 'design.json').read_text())
    typography = design['typography']
    def font(family, size, weight=400):
        style = ' Bold' if weight >= 700 else (' Medium' if weight >= 500 else '')
        return f'{family}{style} {size}'
    return {
        'ui_font': font(typography['ui_family'], typography['ui_size_pt']),
        'document_font': font(typography['ui_family'], typography['ui_size_pt']),
        'monospace_font': font(typography['monospace_family'], typography['monospace_size_pt']),
        'title_font': font(typography['title_family'], typography['title_size_pt'], typography['title_weight']),
        'cursor_size': int(design['geometry']['cursor_size']),
        'prefer_dark': False,
        'button_layout': design['geometry']['button_layout'],
    }


def color_scheme_preference(allowed):
    """Prefer a light color scheme when supported, else use the schema default value."""
    allowed = set(allowed)
    return 'prefer-light' if 'prefer-light' in allowed else ('default' if 'default' in allowed else None)


PANEL = 'org.cinnamon' 
ZONE_KEYS = ('panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes', 'panel-zone-text-sizes')
# Apply order: applets first (while both panels exist nothing points at a missing panel), then the panels.
PANEL_APPLY_ORDER = ('panel-launchers', 'enabled-applets', 'panels-enabled', 'panels-height', *ZONE_KEYS, 'enabled-desklets')
# design.json panel_layout.restore steps 2-4; panels-height precedes panels-enabled so a re-created panel
# finds its own height instead of seeding Cinnamon's default.
RESTORE_STEPS = (('panels-height', 'panels-enabled', 'enabled-applets'), ZONE_KEYS, ('enabled-desklets', 'panel-launchers'))
RESTORE_ORDER = [
    '1 removed multi-instance applets settings files written back byte-exact (sha256 verified) before enabled-applets',
    '2 panels-height, panels-enabled, then enabled-applets from the journal; the new instance settings file removed',
    '3 panel-zone-icon-sizes, panel-zone-symbolic-icon-sizes, panel-zone-text-sizes: the full journaled arrays',
    '4 enabled-desklets from the journal',
    '5 menu-icon/menu-custom: desktop/menu_adapter.py restores its own menu.json receipt (theme.py, right after this core)',
    '6 next-applet-id is left as is (never decremented)',
]
SETTLE_DELAY = 0.5
APPLET_RE = re.compile(r'panel([0-9]+):(left|center|right):([0-9]+):([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+|new)')
LIVE_APPLET_RE = re.compile(r'panel([0-9]+):(left|center|right|top|bottom):([0-9]+):([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+)(?::(orient))?')
INSTANCE_RE = re.compile(r'([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+)')
PANEL_FIELDS = {'panels_enabled', 'panels_height', 'enabled_applets', 'remove', 'new_instances', 'zone_sizes', 'enabled_desklets'}


class LineageError(RuntimeError):
    def __init__(self, why): super().__init__('lineage: ' + str(LINEAGE_FILE) + ': ' + why)


def valid_launcher_ids(value):
    return (isinstance(value, list) and 1 <= len(value) <= 16 and all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.desktop', v) for v in value)
            and len(set(value)) == len(value))


def allocate_instances(spec, live, counter):
    """Plan both instances together; reject partial prior application and counter collisions."""
    reused = [applied_instance(live, item['uuid']) for item in spec['new_instances']]
    if any(path for path, ident in reused) and not (all(path for path, ident in reused) and len({str(path) for path, ident in reused}) == 1):
        raise RuntimeError('Only part of an earlier instance allocation is live; restore/reconcile its receipt first')
    applied = reused[0][0]
    result = []
    for offset, item in enumerate(spec['new_instances']):
        ident = reused[offset][1] if applied else counter + offset
        if type(ident) is not int or ident < 1 or (not applied and ident in live): raise RuntimeError('New instance counter collision')
        result.append({**item, 'id': ident})
    if len({i['id'] for i in result}) != len(result): raise RuntimeError('Duplicate new instance IDs')
    return applied, result


def panel_problem(panel):
    """Strict shape check of lineage.json desktop_control.panel (the load_lineage() style); a reason or None."""
    if not isinstance(panel, dict) or set(panel) != PANEL_FIELDS: return 'panel must have exactly ' + ', '.join(sorted(PANEL_FIELDS))
    strings = lambda v: isinstance(v, list) and all(isinstance(s, str) for s in v)
    if not (strings(panel['panels_enabled']) and panel['panels_enabled']): return 'panels_enabled must be a non-empty string list'
    enabled = set()
    for s in panel['panels_enabled']:
        m = re.fullmatch(r'([0-9]+):([0-9]+):(top|bottom|left|right)', s)
        if not m or int(m.group(1)) in enabled: return 'bad or duplicate panels_enabled entry ' + repr(s)
        enabled.add(int(m.group(1)))
    if not strings(panel['panels_height']): return 'panels_height must be a string list'
    heights = set()
    for s in panel['panels_height']:
        m = re.fullmatch(r'([0-9]+):([0-9]+)', s)
        if not m or int(m.group(1)) in heights or not 16 <= int(m.group(2)) <= 200: return 'bad or duplicate panels_height entry ' + repr(s)
        heights.add(int(m.group(1)))
    if heights != enabled: return 'panels_height must name exactly the enabled panels'
    new = panel['new_instances']
    if not (isinstance(new, list) and len(new) == 3 and all(isinstance(n, dict) and set(n) == {'uuid', 'settings'} for n in new)
            and [n['uuid'] for n in new] == ['panel-launchers@cinnamon.org', MONITOR_UUID, 'calendar@cinnamon.org']):
        return 'new_instances must contain ordered launcher, monitor and calendar specifications'
    for instance in new:
        if not isinstance(instance['settings'], dict) or not instance['settings']: return 'new instance settings missing'
        if instance['uuid'] == MONITOR_UUID and instance['settings'] != {'interval-seconds': 1}: return 'monitor interval must be exactly one second'
        for key, value in instance['settings'].items():
            if instance['uuid'] == 'panel-launchers@cinnamon.org' and key == 'launcherList':
                if not valid_launcher_ids(value): return 'unsafe launcherList desktop IDs'
            elif not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or type(value) not in (bool, int, str) or (isinstance(value, str) and (len(value) > 256 or '\0' in value)):
                return 'unsafe new instance setting ' + repr(key)
    applets = panel['enabled_applets']
    if not (strings(applets) and applets): return 'enabled_applets must be a non-empty string list'
    slots, ids, news = set(), set(), []
    for s in applets:
        m = APPLET_RE.fullmatch(s)
        if not m: return 'bad enabled_applets entry ' + repr(s)
        pid, zone, order, applet, ident = m.groups()
        if int(pid) not in enabled: return 'enabled_applets entry on a panel that is not enabled: ' + s
        if (pid, zone, int(order)) in slots: return 'duplicate zone order: ' + s
        slots.add((pid, zone, int(order)))
        if ident == 'new': news.append(applet); continue
        if int(ident) in ids: return 'duplicate instance id: ' + s
        ids.add(int(ident))
    if news != [n['uuid'] for n in new]: return 'enabled_applets must allocate exactly the ordered launcher, monitor and calendar instances'
    if not strings(panel['remove']): return 'remove must be a string list'
    for s in panel['remove']:
        m = INSTANCE_RE.fullmatch(s)
        if not m or int(m.group(2)) in ids: return 'bad remove entry (or it is also placed): ' + repr(s)
        ids.add(int(m.group(2)))
    sizes = panel['zone_sizes']
    if not (isinstance(sizes, dict) and set(sizes) == {str(pid) for pid in enabled}
            and all(isinstance(groups, dict) and set(groups) == {'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes'}
                    and all(isinstance(v, dict) and set(v) == {'left', 'center', 'right'}
                            and all(type(n) is int and 0 <= n <= 128 for n in v.values()) for v in groups.values())
                    for groups in sizes.values())):
        return 'zone_sizes must name every panel with regular and symbolic {left, center, right} sizes 0-128'
    if not (strings(panel['enabled_desklets']) and all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@._+-]*:[0-9]+:-?[0-9]+:-?[0-9]+', s) for s in panel['enabled_desklets'])):
        return 'enabled_desklets must be uuid:id:x:y strings'
    return None


def load_lineage(path=None):
    path = Path(path or LINEAGE_FILE)
    try: layout = json.loads(path.read_text())
    except OSError as error: raise LineageError(error.strerror or type(error).__name__) from None
    except ValueError as error: raise LineageError('not valid JSON (' + str(error) + ')') from None
    if not (isinstance(layout, dict) and layout.get('schema') == 1 and set(layout) <= {'$comment', 'schema', 'desktop_control'}
            and isinstance(layout.get('desktop_control'), dict) and set(layout['desktop_control']) == {'name', 'slug', 'temp_tag', 'panel'}):
        raise LineageError('unsupported desktop_control layout')
    LAYOUT = layout['desktop_control']
    if not (all(isinstance(LAYOUT[k], str) for k in ('name', 'slug', 'temp_tag'))
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 -]{0,63}', LAYOUT['name'])
            and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', LAYOUT['slug']) and re.fullmatch(r'[a-z0-9_]+', LAYOUT['temp_tag'])
            ):
        raise LineageError('unsafe desktop_control value')
    why = panel_problem(LAYOUT['panel'])
    if why: raise LineageError('unsafe desktop_control.panel: ' + why)
    return LAYOUT


def design_panel_problems(panel, design):
    """Differences between lineage.json's panel and design.json panel_layout (the new instance id normalized to 'new')."""
    layout = design.get('panel_layout') if isinstance(design, dict) else None
    if not isinstance(layout, dict): return ['design.json has no panel_layout']
    new_uuids = [n['uuid'] for n in panel['new_instances']]
    def normal(entry):
        parts = entry.split(':') if isinstance(entry, str) else []
        if len(parts) == 5 and parts[3] in new_uuids: parts[4] = 'new'
        return ':'.join(parts)
    problems = []
    if layout.get('panels-enabled') != panel['panels_enabled']: problems.append('panels-enabled')
    if layout.get('panels-height') != panel['panels_height']: problems.append('panels-height')
    applets = layout.get('enabled-applets')
    if not isinstance(applets, list) or [normal(e) for e in applets] != panel['enabled_applets']: problems.append('enabled-applets')
    removed = layout.get('removed_for_this_preset_only')
    if not isinstance(removed, list) or sorted(':'.join(str(e).split(':')[3:5]) for e in removed) != sorted(panel['remove']):
        problems.append('removed_for_this_preset_only')
    settings = layout.get('applet_settings')
    for instance in panel['new_instances']:
        uuid_ = instance['uuid']
        ours = [v for k, v in settings.items() if str(k).split(':')[0] == uuid_] if isinstance(settings, dict) else []
        if uuid_ == MONITOR_UUID and design.get('resource_monitor', {}).get('sampling', {}).get('interval_seconds') != instance['settings'].get('interval-seconds'): problems.append('resource_monitor interval')
        if uuid_ == 'calendar@cinnamon.org' and ours != [instance['settings']]: problems.append('applet_settings ' + uuid_)
        if uuid_ == 'panel-launchers@cinnamon.org' and not layout.get('launcher_policy'): problems.append('launcher_policy')
    if layout.get('icon_sizes_by_panel') != panel['zone_sizes']: problems.append('icon_sizes_by_panel')
    if layout.get('enabled_desklets') != panel['enabled_desklets']: problems.append('enabled_desklets')
    return problems


def require_lineage():
    """Gate for install/trial/plan/targets: a valid lineage.json whose slug is this folder and whose name and panel are design.json's."""
    if LINEAGE_ERROR is not None: raise LINEAGE_ERROR
    try: design = json.loads((ROOT / 'design.json').read_text()); design_name = design.get('name')
    except (OSError, ValueError, AttributeError) as error: raise LineageError('cannot read the design.json name (' + str(error) + ')') from None
    if LAYOUT['slug'] != ROOT.name: raise LineageError('slug ' + repr(LAYOUT['slug']) + ' is not the preset folder ' + repr(ROOT.name))
    if LAYOUT['name'] != design_name: raise LineageError('name ' + repr(LAYOUT['name']) + ' is not the design.json name ' + repr(design_name))
    problems = design_panel_problems(LAYOUT['panel'], design)
    if problems: raise LineageError('panel differs from design.json panel_layout: ' + ', '.join(problems))
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
    p = Path(p)
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
            for name, parent in ((NAME, '.themes'), (NAME + ' icons', '.icons'), (NAME + ' cursors', '.icons'))] + [(MONITOR_SOURCE, Path(GLib.get_user_data_dir()) / 'cinnamon/applets' / MONITOR_UUID)]


def taskbar_fingerprint():
    """sha256 of the taskbar code and data an isolated run exercised: this controller, lineage.json and
    design.json panel_layout (canonical JSON), and isolate.py. validate_source compares exact tested inputs."""
    layout = json.loads((ROOT / 'design.json').read_text()).get('panel_layout')
    canonical = json.dumps(layout, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return {'desktop_control.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'lineage.json': hashlib.sha256(LINEAGE_FILE.read_bytes()).hexdigest(),
            'design.json#panel_layout': hashlib.sha256(canonical).hexdigest(),
            'appearance-assets': {str(path.relative_to(ROOT)): fingerprint(path) for path in (ROOT / 'desktop' / (NAME + ' icons'), ROOT / 'desktop' / (NAME + ' cursors'), ROOT / 'artwork', ROOT / 'desktop/applets' / MONITOR_UUID)},
            'design.json#full': hashlib.sha256(json.dumps(json.loads((ROOT / 'design.json').read_text()), sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'design.json#appearance': hashlib.sha256(json.dumps(design_appearance(), sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'applications/adapter.py': hashlib.sha256((ROOT / 'applications/adapter.py').read_bytes()).hexdigest(),
            'applications/generated': fingerprint(ROOT / 'applications/generated'),
            'theme.py': hashlib.sha256((ROOT / 'theme.py').read_bytes()).hexdigest(),
            'enhance.py': hashlib.sha256((ROOT / 'enhance.py').read_bytes()).hexdigest(),
            'desktop/workspace-picker.css': hashlib.sha256((ROOT / 'desktop/workspace-picker.css').read_bytes()).hexdigest(),
            'desktop/workspace_adapter.py': hashlib.sha256((ROOT / 'desktop/workspace_adapter.py').read_bytes()).hexdigest(),
            'desktop/logo_adapter.py': hashlib.sha256((ROOT / 'desktop/logo_adapter.py').read_bytes()).hexdigest(),
            'workspace-native-source': fingerprint(xlet_dir('workspace-switcher@cinnamon.org')),
            'applications/runtime/composed.py': hashlib.sha256((ROOT / 'applications/runtime/composed.py').read_bytes()).hexdigest(),
            'isolate.py': hashlib.sha256((ROOT / 'isolate.py').read_bytes()).hexdigest()}


def validate_source():
    require_lineage()
    report = load(ROOT / 'verification/isolated-latest.json')
    source = ROOT / 'desktop' / NAME
    if report['status'] != 'passed' or report['source_sha256'] != fingerprint(source):
        raise RuntimeError('A passing isolated test of the exact theme source is required')
    if report.get('taskbar_sha256') != taskbar_fingerprint():
        raise RuntimeError('A passing isolated test of the exact taskbar code (desktop_control.py, lineage.json, design.json panel_layout and canonical appearance, isolate.py) is required')
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


# ------------------------------------------------------------------ taskbar panel layout (preset-local)

def spices_paths(applet, ident):
    """Both files Cinnamon deletes when a multi-instance applet leaves enabled-applets (appletManager._removeAppletConfigFile)."""
    name = str(ident) + '.json'
    return [CONFIG / 'cinnamon/spices' / applet / name, HOME / '.cinnamon/configs' / applet / name]


def parse_live(entries):
    """{instance id: (uuid, entry)} for the live enabled-applets; unknown shapes are refused, never guessed."""
    out = {}
    for entry in entries:
        m = LIVE_APPLET_RE.fullmatch(entry)
        if not m: raise RuntimeError('Unsupported enabled-applets entry: ' + entry)
        ident = int(m.group(5))
        if ident in out: raise RuntimeError('Duplicate applet instance id in enabled-applets: ' + str(ident))
        out[ident] = (m.group(4), entry)
    return out


def xlet_dir(applet):
    """The applet folder Cinnamon loads (extension.js findExtensionDirectory: user data dir first, then system dirs)."""
    if applet == MONITOR_UUID:
        if MONITOR_SOURCE.is_symlink() or not MONITOR_SOURCE.is_dir(): raise RuntimeError('Missing regular staged monitor applet')
        return MONITOR_SOURCE
    for base in (Path(GLib.get_user_data_dir()), *(Path(d) for d in GLib.get_system_data_dirs())):
        folder = base / 'cinnamon/applets' / applet
        if folder.is_dir(): return folder
    raise RuntimeError('Applet is not installed: ' + applet)


def applet_settings_text(applet, values):
    """Cinnamon's own settings file for a NEW instance (settings.js _loadTemplate + _doInstall + _saveToFile).

    Every settings-schema.json key in schema order; keys with a type and a default get 'value' (the default, or
    `values`); '__md5__' is the md5 of the schema text; serialized like JSON.stringify(data, null, 4). With a
    matching md5 Cinnamon loads the file as is and never rewrites it, so the receipt's bytes stay the live bytes."""
    folder = xlet_dir(applet)
    meta = json.loads((folder / 'metadata.json').read_text())
    if meta.get('multiversion'): raise RuntimeError('multiversion applets keep their schema in a version folder; not supported: ' + applet)
    if (folder / 'settings-override.json').exists(): raise RuntimeError('settings-override.json is not supported: ' + str(folder))
    raw = (folder / 'settings-schema.json').read_bytes()
    data = json.loads(raw.decode('utf-8'))
    for props in data.values():
        if isinstance(props, dict) and 'type' in props and 'default' in props: props['value'] = props['default']
    for key, value in values.items():
        props = data.get(key)
        if not isinstance(props, dict) or 'value' not in props: raise RuntimeError(applet + ' settings schema has no key ' + key)
        if type(value) is not type(props['default']): raise RuntimeError(applet + ' ' + key + ' expects ' + type(props['default']).__name__)
        props['value'] = value
    data['__md5__'] = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    return json.dumps(data, indent=4, ensure_ascii=False), {'schema': str(folder / 'settings-schema.json'), 'md5': data['__md5__'],
                                                             'max_instances': meta.get('max-instances', 1)}


def snapshot_row(p):
    rec = record(p)
    return {'record': rec, 'sha256': hashlib.sha256(base64.b64decode(rec['data'])).hexdigest() if rec['kind'] == 'file' else None}


def instance_evidence(applet, ident, keys):
    """Read-only: selected values of an instance's settings file (evidence for the plan and the receipt)."""
    p = spices_paths(applet, ident)[0]
    if not p.is_file(): return {'path': str(p), 'present': False}
    data = json.loads(p.read_bytes())
    out = {'path': str(p), 'present': True}
    for key in keys: out[key] = data.get(key, {}).get('value') if isinstance(data.get(key), dict) else None
    return out


def applied_instance(live, new_uuid):
    """(receipt, id) of this preset's own new instance from an earlier kept (or pending) trial that is still live."""
    found = (None, None)
    for receipt in sorted(STATE.glob('*/desktop.json')):
        try: data = load(receipt)
        except (OSError, ValueError): continue
        for instance in (data.get('panel') or {}).get('new_instances', []):
            ident = instance.get('id')
            if data.get('status') in ('kept', 'pending') and instance.get('uuid') == new_uuid and type(ident) is int and live.get(ident, ('',))[0] == new_uuid:
                found = (receipt, ident)
    return found


def panel_targets():
    """Read-only: the taskbar rows, the new instance and its file, the snapshots; nothing is written.

    When the taskbar is already applied (a kept trial's calendar instance is live) that instance is reused: no new id,
    no settings file write, no counter bump; the rows then show no change and the plan says 'already applied'."""
    spec = require_lineage()['panel']
    cinnamon = Gio.Settings.new(PANEL)
    listed = cinnamon.props.settings_schema.list_keys()
    for name in (*PANEL_APPLY_ORDER, 'next-applet-id'):
        if name not in listed: raise RuntimeError('org.cinnamon has no key ' + name)
    live = parse_live(cinnamon.get_strv('enabled-applets'))
    counter = cinnamon.get_int('next-applet-id')
    applied, allocated = allocate_instances(spec, live, counter)
    allocated_by_uuid = {item['uuid']: item for item in allocated}
    taskbar, placed = [], {}
    for entry in spec['enabled_applets']:
        pid, zone, order, applet, ident = APPLET_RE.fullmatch(entry).groups()
        if ident == 'new': ident = allocated_by_uuid[applet]['id']
        else:
            ident = int(ident)
            if ident not in live or live[ident][0] != applet: raise RuntimeError('Expected applet instance missing: ' + applet + ':' + str(ident))
        placed[ident] = applet
        taskbar.append(':'.join(('panel' + pid, zone, order, applet, str(ident))))
    removed = []
    for item in spec['remove']:
        applet, ident = INSTANCE_RE.fullmatch(item).groups(); ident = int(ident)
        if ident not in live: continue
        if live[ident][0] != applet: raise RuntimeError('Instance ' + str(ident) + ' is ' + live[ident][0] + ', not ' + applet)
        removed.append({'uuid': applet, 'id': ident, 'entry': live[ident][1]})
    gone = {r['id'] for r in removed}
    unplaced = [live[i][1] for i in sorted(live) if i not in placed and i not in gone]
    if unplaced: raise RuntimeError('Live applets that the taskbar spec neither places nor removes (update lineage.json and design.json): ' + ', '.join(unplaced))
    rows = []
    def row(name, value):
        variant = GLib.Variant(cinnamon.get_value(name).get_type_string(), value)
        user = cinnamon.get_user_value(name)
        rows.append({'schema': PANEL, 'key': name, 'before': cinnamon.get_value(name).print_(True),
                     'user': user.print_(True) if user is not None else None, 'after': variant.print_(True)})
    taskbar_panels = [int(s.split(':')[0]) for s in spec['panels_enabled']]
    row('panel-launchers', ['DEPRECATED'])
    row('enabled-applets', taskbar)
    row('panels-enabled', spec['panels_enabled'])
    row('panels-height', spec['panels_height'])
    for name in ZONE_KEYS:
        try: current = json.loads(cinnamon.get_string(name))
        except ValueError: raise RuntimeError('org.cinnamon ' + name + ' is not JSON') from None
        if not isinstance(current, list): raise RuntimeError('org.cinnamon ' + name + ' is not a JSON array')
        entries = []
        for pid in taskbar_panels:
            if name in spec['zone_sizes'][str(pid)]: entries.append({'panelId': pid, **spec['zone_sizes'][str(pid)][name]})
            else:  # text sizes: keep the taskbar panel's own entry; other panels' entries return from the journal
                found = [e for e in current if isinstance(e, dict) and e.get('panelId') == pid]
                entries.append(found[0] if found else {'left': 0, 'center': 0, 'right': 0, 'panelId': pid})
        row(name, json.dumps(entries, separators=(',', ':')))
    row('enabled-desklets', spec['enabled_desklets'])
    new_files = {}
    for instance in allocated:
        new_uuid, new_id = instance['uuid'], instance['id']
        if new_uuid == 'panel-launchers@cinnamon.org':
            for desktop_id in instance['settings']['launcherList']:
                try: info = Gio.DesktopAppInfo.new(desktop_id)
                except TypeError: info = None
                if info is None: raise RuntimeError('Launcher desktop ID is unavailable or its TryExec is missing: ' + desktop_id)
        text, info = applet_settings_text(new_uuid, instance['settings'])
        if info['max_instances'] == 1: raise RuntimeError(new_uuid + ' is single-instance')
        target = spices_paths(new_uuid, new_id)
        if applied is None:
            for path in target:
                if os.path.lexists(path): raise RuntimeError('New instance settings path already exists: ' + str(path))
            new_files[str(target[0])] = {'before': record(target[0]), 'after': text_row(text)}
        instance.update(path=str(target[0]), **info)
    snapshots = {}
    for r in removed:
        for p in spices_paths(r['uuid'], r['id']): snapshots[str(p)] = {'uuid': r['uuid'], 'id': r['id'], **snapshot_row(p)}
    # Insurance for kept/moved instances: Cinnamon keeps their files (a move is a 'changed' definition), so these
    # are only written back if one is missing at restore time; a file the user changed during the trial is left alone.
    insurance = {}
    for ident, applet in sorted(placed.items()):
        if ident in {item['id'] for item in allocated}: continue
        for p in spices_paths(applet, ident):
            rec = record(p)
            if rec['kind'] == 'file': insurance[str(p)] = rec
    menu = [i for i, a in placed.items() if a == 'menu@cinnamon.org']
    workspace = [i for i, a in placed.items() if a == 'workspace-switcher@cinnamon.org']
    evidence = {
        'menu': {**(instance_evidence('menu@cinnamon.org', menu[0], ('menu-icon', 'menu-custom')) if menu else {'present': False}),
                 'writer': 'desktop/menu_adapter.py via theme.py (receipt menu.json); this core never writes it'},
        'workspace_switcher': {**(instance_evidence('workspace-switcher@cinnamon.org', workspace[0], ('display-type',)) if workspace else {'present': False}),
                               'design': 'buttons', 'writer': 'not written by this preset (read-only evidence)'},
    }
    if menu and evidence['menu'].get('present'):
        label = json.loads(Path(evidence['menu']['path']).read_bytes()).get('menu-label', {}).get('value')
        evidence['menu']['menu_label_sha256'] = hashlib.sha256(json.dumps(label, ensure_ascii=False).encode()).hexdigest()
    return {'taskbar': taskbar, 'settings': rows,
            'files': new_files,
            'snapshots': snapshots, 'insurance': insurance, 'removed': removed,
            'next_applet_id': {'before': counter, 'after': counter if applied else counter + len(allocated), 'policy': 'never decremented on restore'},
            'already_applied': str(applied) if applied else None,
            'new_instances': allocated,
            'calendar': next(item for item in allocated if item['uuid'] == 'calendar@cinnamon.org'),
            'evidence': evidence, 'restore_order': RESTORE_ORDER}


def live_value(row): return Gio.Settings.new(row['schema']).get_value(row['key']).print_(True)


def set_row(row, target):
    """Write a journaled row: 'after' = the preset value, 'before' = the user's own value (or its reset)."""
    obj = Gio.Settings.new(row['schema'])
    if target == 'after': ok = obj.set_value(row['key'], GLib.Variant.parse(None, row['after'], None, None))
    elif row['user'] is None: obj.reset(row['key']); ok = True
    else: ok = obj.set_value(row['key'], GLib.Variant.parse(None, row['user'], None, None))
    if not ok: raise RuntimeError('Setting rejected: ' + row['key'])


def settle(rows, target, rounds=4):
    """Cinnamon answers panel writes with writes of its own: destroying a panel trims its panel-zone-*-sizes
    entries, creating one seeds missing panel-zone/panels-height entries. Re-read after it has run and rewrite a
    row that lost that race (only rows this transaction just wrote); return the rows still off target."""
    field = 'after' if target == 'after' else 'before'
    clean = 0
    for _ in range(rounds):
        time.sleep(SETTLE_DELAY)
        off = [row for row in rows if not same_setting(row, live_value(row), row[field])]
        if not off:
            clean += 1
            if clean >= 2 or not SETTLE_DELAY: return []
            continue
        clean = 0
        for row in off: set_row(row, target)
        Gio.Settings.sync()
    return [row['schema'] + '/' + row['key'] for row in rows if not same_setting(row, live_value(row), row[field])]


def keep_snapshot_copies(path, panel):
    """Byte-exact sidecar copies of the snapshotted settings files in the preset's state (the receipt holds them too)."""
    for raw, snap in panel['snapshots'].items():
        if snap['record']['kind'] != 'file': continue
        copy = path.parent / 'spices' / snap['uuid'] / (str(snap['id']) + ('.json' if raw.startswith(str(CONFIG)) else '.legacy.json'))
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(base64.b64decode(snap['record']['data'])); copy.chmod(0o600)
        if hashlib.sha256(copy.read_bytes()).hexdigest() != snap['sha256']: raise RuntimeError('Snapshot copy differs: ' + str(copy))
        snap['copy'] = str(copy)


def apply_panel(path, data):
    """Write the taskbar. Order: new instance file, next-applet-id (compare-and-set), enabled-applets, panels, zone
    arrays, desklets. The removed instances' files were snapshotted in the receipt before this runs."""
    if not data.get('guard_armed'): raise RuntimeError('Independent recovery must be armed before panel writes')
    panel = data['panel']
    for raw, snap in panel['snapshots'].items():
        if record(Path(raw)) != snap['record']: raise RuntimeError('Applet settings file changed since it was journaled: ' + raw)
    for raw, row in panel['files'].items():
        if record(Path(raw)) != row['before']: raise RuntimeError('New instance settings file changed since plan: ' + raw)
    panel['phase'] = 'writing'; save(path, data)
    for raw, row in panel['files'].items(): write(Path(raw), row['after'])
    cinnamon = Gio.Settings.new(PANEL)
    counter = panel['next_applet_id']
    if cinnamon.get_int('next-applet-id') != counter['before']:
        raise RuntimeError('next-applet-id changed since plan; refusing to reuse instance id ' + str(counter['before']))
    if not cinnamon.set_int('next-applet-id', counter['after']): raise RuntimeError('Setting rejected: next-applet-id')
    counter['written'] = True; save(path, data)
    for row in panel['settings']: set_row(row, 'after')
    Gio.Settings.sync()
    off = settle(panel['settings'], 'after')
    if off: raise RuntimeError('Panel settings did not settle: ' + ', '.join(off))
    panel['phase'] = 'applied'; save(path, data)


def panel_conflicts(panel):
    """External edits to anything the taskbar wrote or will write back; never overwritten silently."""
    out = []
    for row in panel['settings']:
        current = live_value(row)
        if not any(same_setting(row, current, value) for value in (row['before'], row['after'])): out.append(row['schema'] + '/' + row['key'])
    for raw, row in panel['files'].items():
        try:
            if record(Path(raw)) not in (row['before'], row['after']): out.append(raw)
        except Exception: out.append(raw)
    for raw, snap in panel['snapshots'].items():
        if snap['record']['kind'] == 'absent': continue
        try:
            if record(Path(raw)) not in (snap['record'], {'kind': 'absent'}): out.append(raw)
        except Exception: out.append(raw)
    return out


def snapshot_source(snap):
    """The byte-exact sidecar copy in state/<id>/spices/ when it still matches its sha256, else the receipt's own bytes."""
    copy = Path(snap['copy']) if snap.get('copy') else None
    if copy and copy.is_file() and not copy.is_symlink() and hashlib.sha256(copy.read_bytes()).hexdigest() == snap['sha256']:
        return {'kind': 'file', 'data': base64.b64encode(copy.read_bytes()).decode(), 'mode': snap['record']['mode']}
    return snap['record']


def defer_layout(panel, conflicts, rows, why):
    """Leave the whole taskbar in place (a working desktop) for a manual restore; never a mixed layout."""
    for key in rows:
        if PANEL + '/' + key not in conflicts: conflicts.append(PANEL + '/' + key)
    panel['restore_deferred'] = 'layout left as the taskbar: ' + why


def restore_panel(path, data, conflicts, automatic):
    """design.json panel_layout.restore, steps 1-4 and 6 (step 5, the menu icon, is the menu stage's receipt)."""
    panel = data['panel']
    rows = {row['key']: row for row in panel['settings']}
    previous_errors = panel.get('restore_errors', [])
    if previous_errors: panel.setdefault('restore_error_history', []).append(previous_errors[:])
    errors = panel['restore_errors'] = []  # current attempt only; successful retries clear the recovery gate
    panel['restore_verified'] = False
    group = [PANEL + '/' + key for key in RESTORE_STEPS[0] if PANEL + '/' + key in conflicts]
    if group:
        # panels-height, panels-enabled and enabled-applets only make sense together (automatic restore only;
        # a manual restore has already refused on these conflicts).
        defer_layout(panel, conflicts, rows, 'externally changed: ' + ', '.join(group))
        save(path, data); return
    # (1) Settings files of the removed multi-instance applets, byte-exact, BEFORE enabled-applets re-adds them.
    blocked = []
    for raw, snap in panel['snapshots'].items():
        if snap['record']['kind'] == 'absent': continue
        if raw in conflicts: blocked.append(raw); continue
        try:
            p = Path(raw)
            if record(p) != snap['record']: write(p, snap['record'])
            if record(p) != snap['record'] or (snap['sha256'] and hashlib.sha256(p.read_bytes()).hexdigest() != snap['sha256']):
                raise RuntimeError('Snapshot restore verification failed: ' + raw)
        except Exception as error:
            if not automatic: raise
            blocked.append(raw); errors.append(str(error)); conflicts.append(raw)
    for raw, rec in panel.get('insurance', {}).items():
        try:
            if not os.path.lexists(raw):
                write(Path(raw), rec); panel.setdefault('insurance_restored', []).append(raw)
        except Exception as error:
            if not automatic: raise
            errors.append(str(error)); conflicts.append(raw)
    panel['phase'] = 'restore-1-files'; save(path, data)
    if blocked:
        # Re-adding those instances without their files would let Cinnamon install schema defaults over the
        # user's values; keep the taskbar (a working desktop) and leave the layout for a manual restore.
        defer_layout(panel, conflicts, rows, 'settings files not restored: ' + ', '.join(blocked))
        save(path, data); return
    # (2) panels-height, panels-enabled, enabled-applets; (3) zone arrays; (4) desklets.
    written = []
    for number, step in enumerate(RESTORE_STEPS, 2):
        for key in step:
            row = rows[key]
            if PANEL + '/' + key in conflicts: continue
            try:
                if not same_setting(row, live_value(row), row['before']): set_row(row, 'before')
                written.append(row)
            except Exception as error:
                if not automatic: raise
                errors.append(str(error)); conflicts.append(PANEL + '/' + key)
        Gio.Settings.sync()
        if number == 2 and PANEL + '/enabled-applets' not in conflicts:
            # The new instance has left enabled-applets; Cinnamon deletes its file too (intended).
            for raw, row in panel['files'].items():
                if raw not in conflicts: write(Path(raw), row['before'])
        panel['phase'] = 'restore-' + str(number); save(path, data)
    for key in settle(written, 'before'):
        if key not in conflicts: conflicts.append(key)
    # Late re-verify of step 1: Cinnamon deletes a removed instance's settings file asynchronously in its
    # enabled-applets handler. On a fast trial-failure path step 1 may have found the file still present, and the
    # delayed deletion (or a schema-default install on re-add) then lands after step 2. Rewrite and reload once.
    if PANEL + '/enabled-applets' not in conflicts:
        reload = set()
        for raw, snap in panel['snapshots'].items():
            if snap['record']['kind'] == 'absent' or raw in conflicts: continue
            p = Path(raw)
            try:
                if record(p) == snap['record']: continue
                write(p, snapshot_source(snap)); reload.add(snap['uuid'])
                panel.setdefault('late_rewrites', []).append(raw)
                if record(p) != snap['record']: raise RuntimeError('Settings file still differs after the late rewrite: ' + raw)
            except Exception as error:
                errors.append(str(error)); conflicts.append(raw)
        for applet in sorted(reload):
            try: bus('org.Cinnamon.ReloadXlet', applet, 'APPLET')  # the re-added instances load the restored files
            except Exception as error: errors.append('ReloadXlet ' + applet + ': ' + str(error)); conflicts.append('reload:' + applet)
        save(path, data)
    # (6) next-applet-id stays where it is.
    panel['next_applet_id']['at_restore'] = Gio.Settings.new(PANEL).get_int('next-applet-id')
    mismatches = []
    for row in panel['settings']:
        try:
            if not same_setting(row, live_value(row), row['before']): mismatches.append(row['schema'] + '/' + row['key'])
        except Exception as error:
            errors.append(str(error)); mismatches.append(row['schema'] + '/' + row['key'])
    for key in mismatches:
        if key not in conflicts: conflicts.append(key)
    panel['restore_verified'] = not errors and not mismatches and not any(row['schema'] + '/' + row['key'] in conflicts for row in panel['settings'])
    panel['phase'] = 'restored' if panel['restore_verified'] else 'recovery-required'
    save(path, data)


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
    appearance = design_appearance()
    for schema in ('org.cinnamon.desktop.interface', 'org.gnome.desktop.interface'):
        values = {'gtk-theme': NAME, 'icon-theme': NAME + ' icons', 'cursor-theme': NAME + ' cursors',
                  'cursor-size': appearance['cursor_size'], 'font-name': appearance['ui_font'],
                  'document-font-name': appearance['document_font'],
                  'monospace-font-name': appearance['monospace_font'],
                  'gtk-application-prefer-dark-theme': appearance['prefer_dark']}
        settings = Gio.Settings.new(schema)
        if 'color-scheme' in settings.props.settings_schema.list_keys():
            kind, choices = settings.props.settings_schema.get_key('color-scheme').get_range().unpack()
            if kind == 'enum':
                scheme = color_scheme_preference(choices)
                if scheme is not None: values['color-scheme'] = scheme
        for name, value in values.items(): key(schema, name, value)
    key('org.cinnamon.desktop.wm.preferences', 'theme', NAME)
    key('org.cinnamon.desktop.wm.preferences', 'titlebar-font', appearance['title_font'])
    for schema in ('org.cinnamon.desktop.wm.preferences', 'org.gnome.desktop.wm.preferences'):
        key(schema, 'button-layout', appearance['button_layout'])
    key('org.cinnamon.desktop.background', 'picture-uri', (ROOT / 'artwork/wallpaper.png').as_uri())
    key('org.cinnamon.desktop.background', 'picture-options', 'zoom')
    key('org.cinnamon.desktop.background.slideshow', 'slideshow-enabled', False)
    # The panel layout is WRITTEN by this preset (approved taskbar); its rows live in the receipt's 'panel' section.
    panel = panel_targets()
    if not any(':menu@cinnamon.org:' in e for e in panel['taskbar']): raise RuntimeError('Expected menu instance missing')
    # Change shell last, after supporting files and the independent timer exist.
    key('org.cinnamon.theme', 'name', NAME)
    files = {}
    for version in ('3.0', '4.0'):
        p = HOME / ('.config/gtk-' + version + '/settings.ini')
        files[str(p)] = ini(p, 'Settings', {'gtk-theme-name': NAME, 'gtk-icon-theme-name': NAME + ' icons',
                                           'gtk-cursor-theme-name': NAME + ' cursors', 'gtk-cursor-theme-size': str(appearance['cursor_size']),
                                           'gtk-font-name': appearance['ui_font'], 'gtk-application-prefer-dark-theme': '0',
                                           'gtk-decoration-layout': appearance['button_layout']})
    for name in ('gtk.css', 'gtk-dark.css', 'assets'):
        p = HOME / '.config/gtk-4.0' / name
        files[str(p)] = {'kind': 'symlink', 'target': str(HOME / '.themes' / NAME / 'gtk-4.0' / name)}
    p = HOME / '.gtkrc-2.0'; contents = p.read_text() if p.exists() else ''
    for key, value in {'gtk-theme-name': NAME, 'gtk-icon-theme-name': NAME + ' icons', 'gtk-cursor-theme-name': NAME + ' cursors', 'gtk-font-name': appearance['ui_font']}.items():
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
    return result, files, panel


def bus(method, *args):
    return run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path', '/org/Cinnamon', '--method', method, *args])


def checkpoint_path():
    return STATE / load(STATE / 'latest.json')['id'] / 'desktop.json'


def same_setting(row, actual, expected):
    if row['schema'] == PANEL and row['key'] in ZONE_KEYS:
        # Cinnamon serializes these JSON strings without spaces (and in its own key order) after accepting them.
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
    if data.get('panel'): conflicts += panel_conflicts(data['panel'])
    if conflicts and not automatic: raise RuntimeError('Restore conflicts: ' + ', '.join(conflicts))
    data['status'] = 'restoring'; save(path, data)
    try:
        # Restore same-name source trees before reloading the shell. A conflict in one
        # tree must not prevent automatic recovery of the other trees or settings.
        for row in data.get('assets', []):
            if Path(row['destination']).name == MONITOR_UUID: continue  # keep applet code until its instances are removed
            if row['destination'] in conflicts: continue
            try: restore_asset(path, data, row)
            except Exception:
                if not automatic: raise
                conflicts.append(row['destination'])
        # The taskbar layout goes back first, in the design's order, then the appearance settings.
        if data.get('panel'): restore_panel(path, data, conflicts, automatic)
        for row in data.get('assets', []):
            if Path(row['destination']).name != MONITOR_UUID or row['destination'] in conflicts: continue
            if data.get('panel') and (data['panel'].get('phase') != 'restored' or not data['panel'].get('restore_verified')):
                conflicts.append(row['destination']); continue  # deferred layout must retain its running applet source
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
    settings, files, panel = targets()
    if any(row['schema'] == PANEL for row in settings): raise RuntimeError('org.cinnamon keys are written only through the journaled panel rows')
    if [row['key'] for row in panel['settings']] != list(PANEL_APPLY_ORDER): raise RuntimeError('Unexpected panel rows')
    ident = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    path = STATE / ident / 'desktop.json'
    assets = prepare_assets(path, ident)
    keep_snapshot_copies(path, panel)
    validate_source()
    data = {'id': ident, 'status': 'applying', 'created': time.time(), 'deadline': time.time() + 180,
            'settings': settings, 'files': {p: {'before': record(Path(p)), 'after': row} for p, row in files.items()},
            'assets': assets, 'panel': {**panel, 'phase': 'journaled'},
            'opacity_active': run(['systemctl', '--user', 'is-active', SERVICE], check=False) == 'active',
            'unit': SLUG + '-recovery-' + uuid.uuid4().hex[:10]}
    save(path, data); save(STATE / 'latest.json', {'id': ident})
    try:
        # Timer lives outside this process and survives a closed terminal; its recover covers the panel section.
        run(['systemd-run', '--user', '--quiet', '--collect', '--unit=' + data['unit'], '--on-active=180s',
             '--timer-property=AccuracySec=1s', '/usr/bin/python3', str(ROOT / 'theme.py'),
             'recover', '--receipt', str(path)])
        data['guard_armed'] = True; save(path, data)
        swap_assets(path, data)
        for raw, row in files.items(): write(Path(raw), row)
        run(['systemctl', '--user', 'stop', SERVICE])
        apply_panel(path, data)
        for row in settings:
            obj = Gio.Settings.new(row['schema'])
            if not obj.set_value(row['key'], GLib.Variant.parse(None, row['after'], None, None)):
                raise RuntimeError('Setting rejected: ' + row['key'])
        Gio.Settings.sync(); bus('org.Cinnamon.ReloadTheme')
        data['status'] = 'pending'; save(path, data)
        return dict(revised_assets=[r['destination'] for r in assets if r['before'] != r['after']],
                    calendar_instance=panel['calendar']['id'], status='pending', receipt=str(path),
                    deadline=data['deadline'], keep_command='./theme.sh keep')
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
    panel = data.get('panel')
    if panel:
        for row in panel['settings']:
            if not same_setting(row, live_value(row), row['after']): drift.append(row['schema'] + '/' + row['key'])
        for raw, row in panel['files'].items():
            if record(Path(raw)) != row['after']: drift.append(raw)
        if Gio.Settings.new(PANEL).get_int('next-applet-id') < panel['next_applet_id']['after']: drift.append(PANEL + '/next-applet-id')
    for row in data.get('assets', []):
        try:
            if tree_record(Path(row['destination']), overrides) != row['after']: drift.append(row['destination'])
        except Exception: drift.append(row['destination'])
    bus('org.freedesktop.DBus.Peer.Ping')
    return {'status': data['status'], 'drift': drift, 'receipt': str(path)}


def keep(path, overlay_state=None):
    result = check(path, overlay_state)
    data = load(path)
    if data['status'] != 'pending' or time.time() >= data['deadline'] or result['drift']:
        raise RuntimeError('Cannot keep an expired, modified or unsuccessful trial')
    data['status'] = 'kept'; save(path, data)
    run(['systemctl', '--user', 'stop', data['unit'] + '.timer'], check=False)
    cleanup_kept_assets(path, data)
    result['status'] = 'kept'
    return result


def panel_plan(panel):
    return {'writes_panel_layout': not panel['already_applied'], 'already_applied': panel['already_applied'],
            'settings': [{'key': row['key'], 'before': row['before'], 'after': row['after'],
                          'changes': not same_setting(row, row['before'], row['after'])} for row in panel['settings']],
            'taskbar': panel['taskbar'], 'removed': panel['removed'],
            'new_instances': panel['new_instances'],
            'calendar': {k: panel['calendar'][k] for k in ('uuid', 'id', 'path', 'settings', 'md5')},
            'next_applet_id': panel['next_applet_id'],
            'snapshots': {raw: {'kind': snap['record']['kind'], 'sha256': snap['sha256']} for raw, snap in panel['snapshots'].items()},
            'insurance_files': sorted(panel['insurance']), 'evidence': panel['evidence'], 'restore_order': panel['restore_order']}


def plan():
    """Read-only: every setting, file, asset and panel row the trial would change, plus source readiness."""
    settings, files, panel = targets()
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
            'assets': assets, 'panel_layout': panel_plan(panel), 'source_validation': source,
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
            elif args.command == 'keep': result = keep(path, args.overlay_state)
            else: result = check(path, args.overlay_state)
        print(json.dumps(result, indent=2))
        return 1 if result.get('drift') or result.get('status') == 'recovery-required' else 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error': str(error)})); raise SystemExit(2)
