#!/usr/bin/python3
"""Rail panel-layout transitions for the current collection (`theme <profile>`): to-rail, to-home, none.

A PORT (not an import) of the reviewed rail writer in presets/indigo-lunchbox/desktop_control.py
(panel_problem :60-110, spices_paths .. restore_panel :394-743, same_setting :803-807), made generic over the
transition and driven by the optional profiles.json `panel_layout` (repairs/panel-rail-20260927/DESIGN.md §1-§3,
API in repairs/panel-rail-20260927/API.md).

Why a port: the preset controller binds ROOT/STATE/lineage.json at import, runs require_lineage, owns its own save
and scans the preset's state (its own header, :13-22). This module binds nothing at import: no gi, no preset files,
no profiles.json, no state folder. live.py passes every path and injects `save` (live.journal) and `bus`.

Indigo semantics kept as is: byte-exact snapshots of the settings files Cinnamon deletes (c-eyes) taken before the
enabled-applets write and written back first; a compare-and-set on next-applet-id that is never decremented;
PANEL_APPLY_ORDER / RESTORE_STEPS (panels-height before panels-enabled on restore); the whole zone arrays
journaled; Cinnamon's own calendar settings-file format with __md5__; settle; the late re-verify + ReloadXlet;
defer-as-rail instead of a mixed layout.
"""
import base64, copy, hashlib, json, os, re, subprocess, time, uuid
from pathlib import Path

SCHEMA = 1
PANEL = 'org.cinnamon'
ZONE_KEYS = ('panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes', 'panel-zone-text-sizes')
# Apply order (to-rail): applets first (while both panels exist nothing points at a missing panel), then the panels.
PANEL_APPLY_ORDER = ('enabled-applets', 'panels-enabled', 'panels-height', *ZONE_KEYS, 'enabled-desklets')
# Restore order of a to-rail (Indigo design.json panel_layout.restore steps 2-4): panels-height precedes
# panels-enabled so a re-created panel finds its own height instead of seeding Cinnamon's default.
RESTORE_STEPS = (('panels-height', 'panels-enabled', 'enabled-applets'), ZONE_KEYS, ('enabled-desklets',))
# PANEL_APPLY_ORDER grouped the same way: the restore steps of a to-home (it re-applies the rail).
APPLY_STEPS = (('enabled-applets', 'panels-enabled', 'panels-height'), ZONE_KEYS, ('enabled-desklets',))
TO_RAIL_RESTORE_ORDER = [
    '1 removed multi-instance applets settings files written back byte-exact (sha256 verified) before enabled-applets',
    '2 panels-height, panels-enabled, then enabled-applets from the journal; the new instance settings file removed',
    '3 panel-zone-icon-sizes, panel-zone-symbolic-icon-sizes, panel-zone-text-sizes: the full journaled arrays',
    '4 enabled-desklets from the journal',
    '5 menu-icon: live.py restores its own menu json action',
    '6 next-applet-id is left as is (never decremented)',
]
TO_HOME_RESTORE_ORDER = [
    '1 the rail calendar settings file written back byte-exact (sha256 verified) before enabled-applets re-adds it',
    '2 enabled-applets, panels-enabled, then panels-height from the journal (the same calendar id, no new allocation); '
    'the home settings files returned to their rail state (absent), as Cinnamon does',
    '3 panel-zone-icon-sizes, panel-zone-symbolic-icon-sizes, panel-zone-text-sizes: the full journaled arrays',
    '4 enabled-desklets from the journal',
    '5 next-applet-id is left as is (never decremented)',
]
TRANSITIONS = ('to-rail', 'to-home', 'none')
PHASES = ('planned', 'journaled', 'writing', 'applied', 'restore-1-files', 'restore-2', 'restore-3', 'restore-4',
          'restore-incomplete', 'restored')
SETTLED_STATUSES = ('kept', 'active', 'restored')
# Cinnamon's seeded zone entry for a panel that has none (panel.js DEFAULT_*_ICON_SIZE_VALUES / DEFAULT_TEXT_SIZE_VALUES).
SEEDED_ZONE = {'panel-zone-icon-sizes': 0, 'panel-zone-symbolic-icon-sizes': 28, 'panel-zone-text-sizes': 0}
SEEDED_HEIGHT = 40  # panel.js DEFAULT_PANEL_VALUES['panels-height'] for a panel with no entry
REACHED_PENDING_STATUSES = ('pending', 'kept', 'active')
POINTER_NAME = 'panel-epoch.json'
GATE_SUFFIX = '.panel.json'
TEMP_TAG = 'cinnamon_current_panel'
COUNTER_POLICY = 'never decremented on restore'
SETTLE_DELAY = 0.5
APPLET_RE = re.compile(r'panel([0-9]+):(left|center|right):([0-9]+):([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+|new)')
LIVE_APPLET_RE = re.compile(r'panel([0-9]+):(left|center|right|top|bottom):([0-9]+):([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+)(?::orient)?')
INSTANCE_RE = re.compile(r'([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+)')
PANEL_FIELDS = {'panels_enabled', 'panels_height', 'enabled_applets', 'remove', 'new_instance', 'zone_sizes', 'enabled_desklets'}
LAYOUT_FIELDS = {'schema', 'kind', 'provenance', 'panel'}
PROVENANCE_FIELDS = {'lineage', 'lineage_panel_sha256', 'design_panel_layout_sha256'}


class PanelOwnerError(RuntimeError):
    """The panel owner (state/panel-epoch.json and the receipts after it) refuses the switch."""


def _gi():
    from gi.repository import Gio, GLib
    return Gio, GLib


def config_dir():
    """Where Cinnamon keeps spices settings (GLib.get_user_config_dir), read at call time."""
    return Path(_gi()[1].get_user_config_dir())


# ------------------------------------------------------------------ profile data model (DESIGN §1)

def panel_problem(panel, allow_no_new=False):
    """Strict shape check of panel_layout.panel (verbatim port of desktop_control.panel_problem); a reason or None."""
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
    new = panel['new_instance']
    no_new = allow_no_new and new is None
    if not no_new and not (isinstance(new, dict) and set(new) == {'uuid', 'settings'} and isinstance(new['uuid'], str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@._+-]*', new['uuid']) and isinstance(new['settings'], dict) and new['settings']):
        return 'new_instance must be {uuid, settings} with a safe uuid and at least one setting'
    for key, value in ({} if no_new else new['settings']).items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or type(value) not in (bool, int, str) or (isinstance(value, str) and (len(value) > 256 or '\0' in value)):
            return 'unsafe new_instance setting ' + repr(key)
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
    if no_new and news: return 'horizontal layout without a new instance cannot allocate applets'
    if not no_new and news != [new['uuid']]: return 'exactly one enabled_applets entry must be the new ' + new['uuid'] + ' instance (id "new")'
    if not strings(panel['remove']): return 'remove must be a string list'
    for s in panel['remove']:
        m = INSTANCE_RE.fullmatch(s)
        if not m or int(m.group(2)) in ids: return 'bad remove entry (or it is also placed): ' + repr(s)
        ids.add(int(m.group(2)))
    sizes = panel['zone_sizes']
    if not (isinstance(sizes, dict) and set(sizes) <= {'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes'}
            and all(isinstance(v, dict) and set(v) == {'left', 'center', 'right'} and all(type(n) is int and 0 <= n <= 128 for n in v.values())
                    for v in sizes.values())):
        return 'zone_sizes must map panel-zone-(symbolic-)icon-sizes to {left, center, right} integers 0-128'
    if not (strings(panel['enabled_desklets']) and all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@._+-]*:[0-9]+:-?[0-9]+:-?[0-9]+', s) for s in panel['enabled_desklets'])):
        return 'enabled_desklets must be uuid:id:x:y strings'
    return None


def validate_layout(profile):
    """profiles.json entry check: None when the profile has no panel_layout or a valid one, else the reason."""
    if not isinstance(profile, dict): return 'profile must be an object'
    if 'panel_layout' not in profile: return None
    layout = profile['panel_layout']
    if not isinstance(layout, dict) or set(layout) != LAYOUT_FIELDS: return 'panel_layout must have exactly ' + ', '.join(sorted(LAYOUT_FIELDS))
    if type(layout['schema']) is not int or layout['schema'] != SCHEMA: return 'panel_layout.schema must be 1'
    if layout['kind'] not in ('rail', 'horizontal'): return 'panel_layout.kind must be "rail" or "horizontal"'
    provenance = layout['provenance']
    if not (isinstance(provenance, dict) and set(provenance) == PROVENANCE_FIELDS and isinstance(provenance['lineage'], str)
            and provenance['lineage'] and all(isinstance(provenance[k], str) and re.fullmatch(r'[0-9a-f]{64}', provenance[k])
                                              for k in ('lineage_panel_sha256', 'design_panel_layout_sha256'))):
        return 'panel_layout.provenance must be {lineage, lineage_panel_sha256, design_panel_layout_sha256} (audit-only sha256 hex)'
    panel = layout['panel']
    why = panel_problem(panel, allow_no_new=layout['kind']=='horizontal')
    if why: return 'panel_layout.panel: ' + why
    if layout['kind']=='rail':
        if len(panel['panels_enabled']) != 1: return 'panel_layout.panel must enable exactly one panel'
        if panel['panels_enabled'][0].split(':')[2] not in ('left', 'right'): return 'the rail panel must be on the left or right edge'
    else:
        positions=[entry.split(':')[2] for entry in panel['panels_enabled']]
        if positions not in (['bottom'], ['top','bottom']): return 'horizontal layout must be one bottom or top/bottom panels'
        if panel['new_instance'] is not None: return 'horizontal layout must preserve existing applet instance IDs'
    menus = [e for e in panel['enabled_applets'] if APPLET_RE.fullmatch(e).group(4) == 'menu@cinnamon.org']
    if len(menus) != 1: return 'panel_layout.panel must place exactly one menu@cinnamon.org instance'
    return None


def canonical_sha256(value):
    """sha256 of canonical JSON (sort_keys, no spaces, UTF-8): desktop_control.rail_fingerprint's canonicalisation."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _layout_of(value):
    return value['panel_layout'] if isinstance(value, dict) and 'panel_layout' in value else value


def spec_sha256(profile_or_layout):
    return canonical_sha256(_layout_of(profile_or_layout))


def fingerprint(layout=None):
    """The rail code (this file's bytes) and, given a profile or panel_layout, its canonical spec: the isolate gate."""
    out = {'panel_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if layout is not None: out['panel_spec_sha256'] = spec_sha256(layout)
    return out


# ------------------------------------------------------------------ files (ported from desktop_control.py)

def record(p):
    p = Path(p)
    if p.is_symlink(): return {'kind': 'symlink', 'target': os.readlink(p)}
    if not p.exists(): return {'kind': 'absent'}
    if not p.is_file(): raise RuntimeError('Expected file/link: ' + str(p))
    return {'kind': 'file', 'data': base64.b64encode(p.read_bytes()).decode(), 'mode': p.stat().st_mode & 0o777}


def write(p, row):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if row['kind'] == 'absent':
        p.unlink(missing_ok=True); return
    tmp = p.with_name('.' + p.name + '-' + TEMP_TAG + '-' + uuid.uuid4().hex)
    if row['kind'] == 'symlink': tmp.symlink_to(row['target'])
    else:
        tmp.write_bytes(base64.b64decode(row['data'])); tmp.chmod(row.get('mode', 0o644))
    tmp.replace(p)


def text_row(text): return {'kind': 'file', 'data': base64.b64encode(text.encode()).decode(), 'mode': 0o644}


def snapshot_row(p):
    rec = record(p)
    return {'record': rec, 'sha256': hashlib.sha256(base64.b64decode(rec['data'])).hexdigest() if rec['kind'] == 'file' else None}


def snapshot_source(snap):
    """The byte-exact sidecar copy in state/<id>/spices/ when it still matches its sha256, else the receipt's own bytes."""
    copy_ = Path(snap['copy']) if snap.get('copy') else None
    if copy_ and copy_.is_file() and not copy_.is_symlink() and hashlib.sha256(copy_.read_bytes()).hexdigest() == snap['sha256']:
        return {'kind': 'file', 'data': base64.b64encode(copy_.read_bytes()).decode(), 'mode': snap['record']['mode']}
    return snap['record']


def atomic_save(path, value):
    """Durable private JSON write (fsync + rename + directory fsync) for the owner pointer and for tests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    with tmp.open('x') as f:
        os.chmod(tmp, 0o600); json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def default_bus(method, *args):
    return subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path', '/org/Cinnamon', '--method', method, *args],
                          text=True, capture_output=True, timeout=15, check=True).stdout.strip()


# ------------------------------------------------------------------ Cinnamon spices (ported)

def spices_paths(applet, ident):
    """Both files Cinnamon deletes when a multi-instance applet leaves enabled-applets (appletManager._removeAppletConfigFile)."""
    name = str(ident) + '.json'
    return [config_dir() / 'cinnamon/spices' / applet / name, Path.home() / '.cinnamon/configs' / applet / name]


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
    GLib = _gi()[1]
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


# ------------------------------------------------------------------ settings rows

def zone_map(printed):
    """{panelId: entry} of a printed panel-zone-* GVariant string, or None when it is not a JSON array of
    distinct panel entries (Cinnamon serializes these without spaces and in its own key order)."""
    GLib = _gi()[1]
    try: value = json.loads(GLib.Variant.parse(None, printed, None, None).unpack())
    except Exception: return None
    if not isinstance(value, list): return None
    out = {}
    for entry in value:
        if not isinstance(entry, dict) or type(entry.get('panelId')) is not int or entry['panelId'] in out: return None
        out[entry['panelId']] = entry
    return out


def _zone_seeded(key, pid, entry):
    size = SEEDED_ZONE[key]
    return entry == {'panelId': pid, 'left': size, 'center': size, 'right': size}


def zone_covers(actual, expected):
    """Strict against one side: every panel of `expected` has exactly its entry (entries of panels that side does
    not have are inert and ignored)."""
    a, e = zone_map(actual), zone_map(expected)
    if a is None or e is None: return actual == expected
    return all(a.get(pid) == entry for pid, entry in e.items())


def zone_exact(actual, expected):
    a, e = zone_map(actual), zone_map(expected)
    if a is None or e is None: return actual == expected
    return a == e


def zone_either(key, actual, before, after, seeded=False):
    """Per panelId: each live entry is before's or after's; a missing entry is fine only for a panel this transition
    creates or destroys (trimmed on destroy). With seeded=True (a TRANSITIONAL receipt only) Cinnamon's seeded
    default is also accepted for such a panel; a settled receipt compares strictly, so a user's zone size that
    happens to equal the default is never taken for Cinnamon's own write (review round 2, C)."""
    a, b, f = zone_map(actual), zone_map(before), zone_map(after)
    if a is None or b is None or f is None: return actual in (before, after)
    changed = set(b) ^ set(f)
    for pid, entry in a.items():
        if entry == b.get(pid) or entry == f.get(pid): continue
        if seeded and pid in changed and _zone_seeded(key, pid, entry): continue
        return False
    return all(pid in a or pid in changed for pid in set(b) | set(f))


def _same_key(key, actual, expected):
    if key in ZONE_KEYS: return zone_covers(actual, expected)
    return actual == expected


def same_setting(row, actual, expected):
    if row['schema'] == PANEL: return _same_key(row['key'], actual, expected)
    return actual == expected


def exact_setting(row, actual, expected):
    if row['schema'] == PANEL and row['key'] in ZONE_KEYS: return zone_exact(actual, expected)
    return actual == expected


def height_map(printed):
    """{panelId: height} of a printed panels-height GVariant ('id:height' strings), or None."""
    GLib = _gi()[1]
    try: value = GLib.Variant.parse(None, printed, None, None).unpack()
    except Exception: return None
    out = {}
    for item in value if isinstance(value, list) else [None]:
        m = re.fullmatch(r'([0-9]+):([0-9]+)', item) if isinstance(item, str) else None
        if not m or int(m.group(1)) in out: return None
        out[int(m.group(1))] = int(m.group(2))
    return out


def height_either(actual, before, after, seeded=False):
    """panels-height like the zone arrays (Amendment 2 §3): strict (before or after, exactly) unless `seeded` (a
    transitional receipt), which accepts per panelId before's or after's height, Cinnamon's seeded 40 for a panel
    this transition creates or destroys, and a missing entry for such a panel."""
    if actual in (before, after) or not seeded: return actual in (before, after)
    a, b, f = height_map(actual), height_map(before), height_map(after)
    if a is None or b is None or f is None: return False
    changed = set(b) ^ set(f)
    for pid, height in a.items():
        if height == b.get(pid) or height == f.get(pid): continue
        if pid in changed and height == SEEDED_HEIGHT: continue
        return False
    return all(pid in a or pid in changed for pid in set(b) | set(f))


def _row_either(row, actual, seeded=False):
    if row['schema'] == PANEL and row['key'] in ZONE_KEYS: return zone_either(row['key'], actual, row['before'], row['after'], seeded)
    if row['schema'] == PANEL and row['key'] == 'panels-height': return height_either(actual, row['before'], row['after'], seeded)
    return actual in (row['before'], row['after'])


def schema_upgraded_equal(applet, live, recorded):
    """True when Cinnamon rewrote a recorded settings file only because its __md5__ went stale after an applet
    update (settings.js _checkSettingsFile): the live md5 is the installed schema's, the recorded one is not, and
    every recorded value whose key the live file still has is unchanged."""
    if not (isinstance(live, dict) and isinstance(recorded, dict) and live.get('kind') == recorded.get('kind') == 'file'): return False
    try:
        new, old = json.loads(base64.b64decode(live['data'])), json.loads(base64.b64decode(recorded['data']))
        raw_schema = (xlet_dir(applet) / 'settings-schema.json').read_bytes()
        schema_doc = json.loads(raw_schema)
    except Exception: return False
    schema = hashlib.md5(raw_schema, usedforsecurity=False).hexdigest()
    if not (isinstance(new, dict) and isinstance(old, dict) and isinstance(schema_doc, dict)): return False
    if new.get('__md5__') != schema or old.get('__md5__') == schema: return False
    for key, props in old.items():
        if key == '__md5__' or not isinstance(props, dict) or 'value' not in props or key not in new: continue
        if not isinstance(new[key], dict) or new[key].get('value') != props['value']: return False
    # Keys only the upgraded file has were added by the schema update: they must still hold the schema default,
    # else the user has already set them and the file is a real edit (review round 2, B).
    for key, props in new.items():
        if key == '__md5__' or key in old or not isinstance(props, dict) or 'value' not in props: continue
        default = schema_doc.get(key, {}).get('default') if isinstance(schema_doc.get(key), dict) else None
        if not isinstance(schema_doc.get(key), dict) or 'default' not in schema_doc[key] or props['value'] != default: return False
    return True


def _record_accepts(raw, applet, allowed):
    """The live record of `raw` is one of `allowed` (or a schema upgrade of one of them)."""
    current = record(Path(raw))
    if current in allowed: return True
    return bool(applet) and any(schema_upgraded_equal(applet, current, rec) for rec in allowed if rec.get('kind') == 'file')


def live_printed(key, schema=PANEL):
    return _gi()[0].Settings.new(schema).get_value(key).print_(True)


def live_value(row): return live_printed(row['key'], row['schema'])


def set_row(row, target):
    """Write a journaled row: 'after' writes after_user, 'before' writes user; None means reset (the schema default)."""
    Gio, GLib = _gi()
    obj = Gio.Settings.new(row['schema'])
    value = (row['after_user'] if 'after_user' in row else row['after']) if target == 'after' else row['user']
    if value is None: obj.reset(row['key']); ok = True
    else: ok = obj.set_value(row['key'], GLib.Variant.parse(None, value, None, None))
    if not ok: raise RuntimeError('Setting rejected: ' + row['key'])


def settle(rows, target, rounds=4):
    """Cinnamon answers panel writes with writes of its own: destroying a panel trims its panel-zone-*-sizes
    entries, creating one seeds missing panel-zone/panels-height entries. Re-read after it has run and rewrite a
    row that lost that race (only rows this transaction just wrote); return the rows still off target."""
    Gio = _gi()[0]
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


# ------------------------------------------------------------------ owner pointer (DESIGN §2)

def pointer_path(state_dir): return Path(state_dir) / POINTER_NAME


def _receipt_in(state_dir, receipt):
    """The absolute receipt path when it is exactly <state_dir>/<one folder>/receipt.json, else None."""
    state = Path(os.path.abspath(state_dir))
    receipt = Path(receipt)
    if not receipt.is_absolute(): return None
    parts = receipt.parts
    if receipt.name != 'receipt.json' or receipt.parent.parent != state or receipt.parent.name in ('', '.', '..') or '..' in parts: return None
    return receipt


def read_pointer_record(state_dir):
    """(receipt path, panel_seq) named by state/panel-epoch.json, or None; a foreign or malformed pointer refuses."""
    p = pointer_path(state_dir)
    try: value = json.loads(p.read_text())
    except FileNotFoundError: return None
    except (OSError, ValueError) as error: raise PanelOwnerError('Unreadable panel owner pointer ' + str(p) + ': ' + str(error)) from None
    ok = isinstance(value, dict) and set(value) == {'receipt', 'panel_seq'}
    target = value.get('receipt') if ok else None
    receipt = _receipt_in(state_dir, target) if isinstance(target, str) else None
    if receipt is None or type(value.get('panel_seq')) is not int or value['panel_seq'] < 1:
        raise PanelOwnerError('Foreign or malformed panel owner pointer ' + str(p) + ': ' + json.dumps(value))
    return receipt, value['panel_seq']


def read_pointer(state_dir):
    record_ = read_pointer_record(state_dir)
    return record_[0] if record_ else None


def pointer_exists(state_dir): return os.path.lexists(pointer_path(state_dir))


def _seq_of(data, where):
    seq = data['panel'].get('panel_seq') if isinstance(data.get('panel'), dict) else None
    if type(seq) is not int or seq < 1: raise PanelOwnerError('Panel receipt has no panel_seq: ' + str(where))
    return seq


def write_pointer(state_dir, receipt_path, panel_seq=None):
    """Point the owner at a panel receipt; the seq is read from the journaled receipt unless given."""
    receipt = _receipt_in(state_dir, os.path.abspath(receipt_path))
    if receipt is None: raise PanelOwnerError('Refusing a panel owner pointer outside ' + str(state_dir) + ': ' + str(receipt_path))
    if panel_seq is None:
        try: panel_seq = _seq_of(json.loads(receipt.read_text()), receipt)
        except (OSError, ValueError) as error: raise PanelOwnerError('Cannot read the panel receipt for the pointer: ' + str(error)) from None
    if type(panel_seq) is not int or panel_seq < 1: raise PanelOwnerError('Invalid panel_seq ' + repr(panel_seq))
    atomic_save(pointer_path(state_dir), {'receipt': str(receipt), 'panel_seq': panel_seq})


def _panel_receipt(p):
    """A receipt's data when it carries a top-level panel section (cheap byte pre-filter first), else None."""
    try: raw = p.read_bytes()
    except OSError: return None
    if b'"panel":' not in raw: return None
    try: data = json.loads(raw)
    except ValueError: raise PanelOwnerError('Unreadable panel receipt ' + str(p)) from None
    return data if isinstance(data, dict) and isinstance(data.get('panel'), dict) else None


def panel_receipts(state_dir):
    """[(path, data, panel_seq)] of every panel-bearing receipt, ordered by panel_seq (never by folder name: the
    local-time stamps invert across DST and clock steps). A missing or duplicate seq refuses."""
    out, seen = [], {}
    for p in sorted(Path(state_dir).glob('*/receipt.json')):
        data = _panel_receipt(p)
        if data is None: continue
        seq = _seq_of(data, p)
        if seq in seen: raise PanelOwnerError('Two panel receipts share panel_seq ' + str(seq) + ': ' + str(seen[seq]) + ', ' + str(p))
        seen[seq] = p
        out.append((p, data, seq))
    return sorted(out, key=lambda item: item[2])


def derive_state(state_dir):
    """The panel state the next switch starts from: the owner receipt's status decides (kept -> state_after,
    restored -> state_before, anything else refuses); no owner at all is the legacy home."""
    state_dir = Path(state_dir)
    receipts = panel_receipts(state_dir)
    max_seq = max((seq for _, _, seq in receipts), default=0)
    pointer = read_pointer_record(state_dir)
    candidate = None
    if pointer is not None:
        path, seq = pointer
        try: data = json.loads(path.read_text())
        except FileNotFoundError: raise PanelOwnerError('Panel owner receipt is missing: ' + str(path)) from None
        except (OSError, ValueError) as error: raise PanelOwnerError('Panel owner receipt is unreadable: ' + str(path) + ' (' + str(error) + ')') from None
        if not isinstance(data, dict) or not isinstance(data.get('panel'), dict):
            raise PanelOwnerError('Panel owner receipt has no panel section: ' + str(path))
        if _seq_of(data, path) != seq: raise PanelOwnerError('Panel owner pointer seq ' + str(seq) + ' differs from its receipt ' + str(path))
        candidate = (path, data, seq, 'pointer')
    # Cross-check: a panel receipt with a higher seq that is not restored is the real owner (the crash window
    # between its first journal and the pointer write). Later restored receipts have no net effect.
    floor = candidate[2] if candidate else 0
    for path, data, seq in receipts:
        if seq > floor and data.get('status') != 'restored': candidate = (path, data, seq, 'scan')
    if candidate is None:
        return {'state': 'home', 'source': 'legacy', 'owner': None, 'owner_status': None, 'owner_transition': None,
                'owner_seq': 0, 'max_seq': max_seq, 'pointer': None, 'home': None, 'rail': None}
    path, data, seq, source = candidate
    panel, status = data['panel'], data.get('status')
    if status == 'kept': state = panel.get('state_after')
    elif status == 'restored': state = panel.get('state_before')
    else: raise PanelOwnerError('Unresolved panel receipt ' + str(path) + ' (status ' + repr(status) + '); restore or keep it first')
    if state not in ('home', 'rail'): raise PanelOwnerError('Panel receipt ' + str(path) + ' has no valid panel state')
    return {'state': state, 'source': source, 'owner': str(path), 'owner_status': status,
            'owner_transition': panel.get('transition'), 'owner_seq': seq, 'max_seq': max_seq,
            'pointer': str(pointer[0]) if pointer else None, 'home': panel.get('home'), 'rail': panel.get('rail')}


def later_panel_receipts(state_dir, receipt_path):
    """[(path, status)] of panel receipts with a higher panel_seq than `receipt_path` that are not restored."""
    try: this = _panel_receipt(Path(receipt_path))
    except PanelOwnerError: raise
    if this is None: return []
    seq = _seq_of(this, receipt_path)
    return [(str(p), data.get('status')) for p, data, s in panel_receipts(state_dir) if s > seq and data.get('status') != 'restored']


def restore_order_problem(state_dir, receipt_path):
    """Panel receipts are restored last-in first-out (theme restore walks back that way); restoring an older one
    while a later one still stands would leave the owner state wrong. A refusal text, or None."""
    later = later_panel_receipts(state_dir, receipt_path)
    if not later: return None
    return 'Restore the later panel receipt(s) first: ' + ', '.join(p + ' (' + str(s) + ')' for p, s in later)


def _describe(value):
    if isinstance(value, dict) and 'kind' in value:
        if value['kind'] == 'file': return 'file sha256 ' + hashlib.sha256(base64.b64decode(value['data'])).hexdigest()[:12]
        if value['kind'] == 'symlink': return 'symlink -> ' + str(value.get('target'))
        return value['kind']
    return str(value)


def _explain_rows(rows, problems, expected_of):
    out = []
    for item in problems:
        row = rows.get(item)
        if row is None: continue
        try: live = live_value(row)
        except Exception as error: live = 'unreadable (' + str(error) + ')'
        out.append(item + ': live ' + live + ', expected ' + ' or '.join(expected_of(row)))
    return out


def check_rail_live(rail):
    """The live layout must be exactly the owner's rail; anything else is an external edit to reconcile."""
    if not isinstance(rail, dict) or not isinstance(rail.get('rows'), dict): raise PanelOwnerError('Panel owner has no rail block')
    off = [key for key in PANEL_APPLY_ORDER if key not in rail['rows'] or not _same_key(key, live_printed(key), rail['rows'][key])]
    if off:
        lines = [key + ': live ' + live_printed(key) + ', expected ' + str(rail['rows'].get(key)) for key in off]
        raise RuntimeError('Panel layout changed externally; reconcile before continuing (' + '; '.join(lines) + ')')


def legacy_layout_problems(recorded, state_dir):
    """base.layout() keys whose live value differs from a receipt without a panel section; next-applet-id higher than
    recorded is tolerated only once a panel owner pointer exists (a rail transaction bumped it on purpose)."""
    tolerate = pointer_exists(state_dir)
    out = []
    for key, printed in recorded.items():
        current = live_printed(key)
        if current == printed: continue
        if key == 'next-applet-id' and tolerate:
            try:
                if int(current) >= int(printed): continue
            except ValueError: pass
        out.append(key)
    return out


# ------------------------------------------------------------------ planning (read-only)

def _row(cinnamon, name, after, after_user):
    user = cinnamon.get_user_value(name)
    return {'schema': PANEL, 'key': name, 'before': cinnamon.get_value(name).print_(True),
            'user': user.print_(True) if user is not None else None, 'after': after, 'after_user': after_user}


def _panel_settings():
    Gio = _gi()[0]
    cinnamon = Gio.Settings.new(PANEL)
    listed = cinnamon.props.settings_schema.list_keys()
    for name in (*PANEL_APPLY_ORDER, 'next-applet-id'):
        if name not in listed: raise RuntimeError('org.cinnamon has no key ' + name)
    return cinnamon


def _section(transition, profile, rail_profile, spec, settings, files, snapshots, insurance, removed, counter, calendar,
             home, rail, apply_order, restore_steps, restore_order, insurance_apply=None):
    before, after = {'to-rail': ('home', 'rail'), 'to-home': ('rail', 'home'), 'none': ('rail', 'rail')}[transition]
    if [row['key'] for row in settings] != list(apply_order): raise RuntimeError('Unexpected panel rows')
    return {'schema': SCHEMA, 'transition': transition, 'state_before': before, 'state_after': after,
            'profile': profile, 'rail_profile': rail_profile, 'spec_sha256': spec,
            'settings': settings, 'files': files, 'snapshots': snapshots, 'insurance': insurance,
            'insurance_apply': insurance_apply or {}, 'removed': removed, 'next_applet_id': counter, 'calendar': calendar,
            'apply_order': list(apply_order), 'restore_steps': [list(step) for step in restore_steps],
            'restore_order': list(restore_order), 'home': home, 'rail': rail, 'gate': None, 'phase': 'planned'}


def plan_to_rail(slug, layout):
    """Read-only port of desktop_control.panel_targets: the rail rows, the new calendar instance and its file, the
    snapshots of the settings files Cinnamon will delete, and the home block (the user's layout, journaled)."""
    why = validate_layout({'panel_layout': layout})
    if why: raise RuntimeError('Unsafe panel_layout for ' + slug + ': ' + why)
    spec = layout['panel']
    Gio, GLib = _gi()
    cinnamon = _panel_settings()
    if list(cinnamon.get_strv('panels-enabled')) == spec['panels_enabled']:
        raise PanelOwnerError('The live panel layout already is the rail but is not owned by the collection (a preset rail?); '
                              'restore its owner before theme ' + slug)
    live = parse_live(cinnamon.get_strv('enabled-applets'))
    new_uuid = spec['new_instance']['uuid'] if spec['new_instance'] is not None else None
    counter = cinnamon.get_int('next-applet-id')
    new_id = counter if new_uuid is not None else None
    if new_id is not None and (new_id < 1 or new_id in live): raise RuntimeError('next-applet-id ' + str(new_id) + ' is already used: ' + live.get(new_id, ('', 'invalid'))[1])
    rail, placed = [], {}
    for entry in spec['enabled_applets']:
        pid, zone, order, applet, ident = APPLET_RE.fullmatch(entry).groups()
        if ident == 'new': ident = new_id
        else:
            ident = int(ident)
            if ident not in live or live[ident][0] != applet: raise RuntimeError('Expected applet instance missing: ' + applet + ':' + str(ident))
        placed[ident] = applet
        rail.append(':'.join(('panel' + pid, zone, order, applet, str(ident))))
    removed = []
    for item in spec['remove']:
        applet, ident = INSTANCE_RE.fullmatch(item).groups(); ident = int(ident)
        if ident not in live: continue
        if live[ident][0] != applet: raise RuntimeError('Instance ' + str(ident) + ' is ' + live[ident][0] + ', not ' + applet)
        removed.append({'uuid': applet, 'id': ident, 'entry': live[ident][1]})
    gone = {r['id'] for r in removed}
    unplaced = [live[i][1] for i in sorted(live) if i not in placed and i not in gone]
    if unplaced: raise RuntimeError('Live applets that the rail spec neither places nor removes (update the profile panel_layout): ' + ', '.join(unplaced))
    rows = []
    def row(name, value):
        printed = GLib.Variant(cinnamon.get_value(name).get_type_string(), value).print_(True)
        rows.append(_row(cinnamon, name, printed, printed))
    rail_panels = [int(s.split(':')[0]) for s in spec['panels_enabled']]
    row('enabled-applets', rail)
    row('panels-enabled', spec['panels_enabled'])
    row('panels-height', spec['panels_height'])
    for name in ZONE_KEYS:
        try: current = json.loads(cinnamon.get_string(name))
        except ValueError: raise RuntimeError('org.cinnamon ' + name + ' is not JSON') from None
        if not isinstance(current, list): raise RuntimeError('org.cinnamon ' + name + ' is not a JSON array')
        entries = []
        for pid in rail_panels:
            if name in spec['zone_sizes']: entries.append({'panelId': pid, **spec['zone_sizes'][name]})
            else:  # text sizes: keep the rail panel's own entry; other panels' entries return from the journal
                found = [e for e in current if isinstance(e, dict) and e.get('panelId') == pid]
                entries.append(found[0] if found else {'left': 0, 'center': 0, 'right': 0, 'panelId': pid})
        row(name, json.dumps(entries, separators=(',', ':')))
    row('enabled-desklets', spec['enabled_desklets'])
    target = []
    if new_uuid is not None:
        text, info = applet_settings_text(new_uuid, spec['new_instance']['settings'])
        if info['max_instances'] == 1: raise RuntimeError(new_uuid + ' is single-instance; a new instance cannot be added')
        target = spices_paths(new_uuid, new_id)
        for p in target:
            if os.path.lexists(p): raise RuntimeError('A settings file for the new instance id already exists: ' + str(p))
    snapshots = {}
    for r in removed:
        for p in spices_paths(r['uuid'], r['id']): snapshots[str(p)] = {'uuid': r['uuid'], 'id': r['id'], **snapshot_row(p)}
    # Insurance for kept/moved instances: Cinnamon keeps their files (a move is a 'changed' definition), so these
    # are only written back if one is missing at restore time; a file the user changed during the trial is left alone.
    insurance = {}
    for ident, applet in sorted(placed.items()):
        if ident == new_id: continue
        for p in spices_paths(applet, ident):
            rec = record(p)
            if rec['kind'] == 'file': insurance[str(p)] = rec
    calendar = ({'uuid': new_uuid, 'id': new_id, 'path': str(target[0]), 'settings': spec['new_instance']['settings'], **info}
                if new_uuid is not None else {})
    home = {'captured_by_profile': slug, 'captured_at': time.time(), 'captured_in': None,
            'rows': {r['key']: {'value': r['before'], 'user': r['user']} for r in rows},
            'snapshots': copy.deepcopy(snapshots), 'insurance': copy.deepcopy(insurance), 'removed': copy.deepcopy(removed)}
    spec_sha = spec_sha256(layout)
    rail_block = {'profile': slug, 'spec_sha256': spec_sha, 'rows': {r['key']: r['after'] for r in rows},
                  'calendar': copy.deepcopy(calendar), 'next_applet_id': new_id + 1 if new_id is not None else counter, 'captured_in': None}
    return _section('to-rail', slug, slug, spec_sha, rows,
                    ({str(target[0]): {'before': record(target[0]), 'after': text_row(text), 'uuid': new_uuid, 'id': new_id}} if new_uuid is not None else {}),
                    snapshots, insurance, removed,
                    {'before': counter, 'after': new_id + 1 if new_id is not None else counter, 'allocate': new_id is not None, 'policy': COUNTER_POLICY},
                    calendar, home, rail_block, PANEL_APPLY_ORDER, RESTORE_STEPS, TO_RAIL_RESTORE_ORDER)


def _owner_blocks(owner):
    home, rail = owner.get('home'), owner.get('rail')
    if not isinstance(home, dict) or not isinstance(rail, dict) or not isinstance(home.get('rows'), dict):
        raise PanelOwnerError('Panel owner ' + str(owner.get('owner')) + ' carries no home/rail block')
    return home, rail


def plan_to_home(owner):
    """Read-only: the rail -> home transition inside the new (normal) profile's own receipt. Rows go back to the
    journaled home (reset where home had no user value), the home c-eyes snapshots are the files written before
    enabled-applets, and the rail's calendar file (Cinnamon deletes it) is snapshotted for an undo."""
    home, rail = _owner_blocks(owner)
    check_rail_live(rail)
    GLib = _gi()[1]
    cinnamon = _panel_settings()
    live = parse_live(cinnamon.get_strv('enabled-applets'))
    counter = cinnamon.get_int('next-applet-id')
    order = [key for step in RESTORE_STEPS for key in step]
    missing = [key for key in order if key not in home['rows']]
    if missing: raise PanelOwnerError('Panel owner home block lacks ' + ', '.join(missing))
    rows = [_row(cinnamon, key, home['rows'][key]['value'], home['rows'][key]['user']) for key in order]
    home_live = parse_live(GLib.Variant.parse(None, home['rows']['enabled-applets']['value'], None, None).unpack())
    files = {}
    for raw, snap in home.get('snapshots', {}).items():
        if snap['record']['kind'] == 'absent': continue
        current = record(Path(raw))
        if current not in (snap['record'], {'kind': 'absent'}):
            raise RuntimeError('A different file is at a home settings snapshot path; move it aside first: ' + raw)
        files[raw] = {'before': current, 'after': copy.deepcopy(snap['record']), 'uuid': snap['uuid'], 'id': snap['id']}
    calendar = copy.deepcopy(rail['calendar'])
    removed = [{'uuid': applet, 'id': ident, 'entry': entry} for ident, (applet, entry) in sorted(live.items()) if ident not in home_live]
    snapshots = {}
    for r in removed:
        for p in spices_paths(r['uuid'], r['id']): snapshots[str(p)] = {'uuid': r['uuid'], 'id': r['id'], **snapshot_row(p)}
    insurance = {}
    for ident, (applet, _) in sorted(live.items()):
        if ident not in home_live: continue
        for p in spices_paths(applet, ident):
            rec = record(p)
            if rec['kind'] == 'file': insurance[str(p)] = rec
    return _section('to-home', None, rail['profile'], rail['spec_sha256'], rows, files, snapshots, insurance, removed,
                    {'before': counter, 'after': counter, 'allocate': False, 'policy': COUNTER_POLICY},
                    calendar, copy.deepcopy(home), copy.deepcopy(rail), order, APPLY_STEPS, TO_HOME_RESTORE_ORDER,
                    insurance_apply=copy.deepcopy(home.get('insurance', {})))


def plan_none(owner):
    """Read-only: rail -> the same rail. No writes; home and rail are carried forward."""
    home, rail = _owner_blocks(owner)
    check_rail_live(rail)
    counter = _panel_settings().get_int('next-applet-id')
    return _section('none', rail['profile'], rail['profile'], rail['spec_sha256'], [], {}, {}, {}, [],
                    {'before': counter, 'after': counter, 'allocate': False, 'policy': COUNTER_POLICY},
                    copy.deepcopy(rail['calendar']), copy.deepcopy(home), copy.deepcopy(rail), (), (), ())


def gate_status(report_path, slug, spec_sha, required):
    """The isolated rail rehearsal report (<gate_root>/<slug>.panel.json) against this code and the spec."""
    code = fingerprint()['panel_code_sha256']
    out = {'report': str(report_path), 'required': bool(required), 'status': 'passed', 'panel_code_sha256': code,
           'panel_spec_sha256': spec_sha, 'reasons': []}
    try: report = json.loads(Path(report_path).read_text())
    except FileNotFoundError:
        out.update(status='missing', reasons=['no isolated rail rehearsal report']); return out
    except (OSError, ValueError) as error:
        out.update(status='failed', reasons=['unreadable report: ' + str(error)]); return out
    if not isinstance(report, dict):
        out.update(status='failed', reasons=['report is not an object']); return out
    if report.get('status') != 'passed':
        out['status'] = 'failed'; out['reasons'].append('report status ' + repr(report.get('status')))
    stale = []
    if report.get('profile') != slug: stale.append('report is for ' + repr(report.get('profile')))
    if report.get('panel_code_sha256') != code: stale.append('panel_layout.py changed since the rehearsal')
    if report.get('panel_spec_sha256') != spec_sha: stale.append('panel_layout spec changed since the rehearsal')
    if stale:
        out['reasons'] += stale
        if out['status'] == 'passed': out['status'] = 'stale'
    return out


def gate_problem(panel):
    """A refusal only when the transition requires a passing rehearsal of the exact code and spec (to-rail)."""
    if not panel: return None
    gate = panel.get('gate')
    if gate is None:
        return 'Panel gate was not evaluated' if panel.get('transition') == 'to-rail' else None
    if gate.get('required') and gate.get('status') != 'passed':
        return ('A passing isolated rail rehearsal of the exact panel code and spec is required (isolate_panel.py '
                + str(panel.get('rail_profile')) + '): ' + '; '.join(gate.get('reasons') or [gate.get('status', '?')]))
    return None


def plan_transition(slug, profile, state_dir, gate_root):
    """live.plan's single entry: None for normal -> normal, else the receipt's panel section (phase 'planned')."""
    why = validate_layout(profile)
    if why: raise RuntimeError('Unsafe panel_layout in profile ' + slug + ': ' + why)
    layout = profile.get('panel_layout')
    owner = derive_state(state_dir)
    if owner['state'] == 'home':
        if layout is None: return None
        panel = plan_to_rail(slug, layout)
        panel['gate'] = gate_status(Path(gate_root) / (slug + GATE_SUFFIX), slug, panel['spec_sha256'], True)
        panel['panel_seq'] = owner['max_seq'] + 1
        return panel
    _, rail = _owner_blocks(owner)
    if layout is None:
        panel = plan_to_home(owner)
        panel['profile'] = slug
    else:
        if slug != rail['profile']:
            raise RuntimeError('The live rail belongs to ' + repr(rail['profile']) + '; switch to a normal profile before theme ' + slug)
        if spec_sha256(layout) != rail['spec_sha256']:
            raise RuntimeError('The ' + slug + ' panel_layout changed since its rail was applied; switch to a normal profile first')
        panel = plan_none(owner)
    panel['gate'] = gate_status(Path(gate_root) / (rail['profile'] + GATE_SUFFIX), rail['profile'], rail['spec_sha256'], False)
    panel['panel_seq'] = owner['max_seq'] + 1
    return panel


def plan_summary(panel):
    """Read-only digest for printing a plan (port of desktop_control.panel_plan)."""
    return {'transition': panel['transition'], 'state_before': panel['state_before'], 'state_after': panel['state_after'],
            'writes_panel_layout': panel['transition'] != 'none',
            'settings': [{'key': row['key'], 'before': row['before'], 'after': row['after'],
                          'changes': not same_setting(row, row['before'], row['after'])} for row in panel['settings']],
            'files': {raw: {'before': row['before']['kind'], 'after': row['after']['kind']} for raw, row in panel['files'].items()},
            'removed': panel['removed'],
            'calendar': {k: panel['calendar'].get(k) for k in ('uuid', 'id', 'path', 'settings', 'md5')},
            'next_applet_id': panel['next_applet_id'],
            'snapshots': {raw: {'kind': snap['record']['kind'], 'sha256': snap['sha256']} for raw, snap in panel['snapshots'].items()},
            'insurance_files': sorted(panel['insurance']), 'restore_order': panel['restore_order'], 'gate': panel.get('gate')}


# ------------------------------------------------------------------ validate / check helpers

def phase_target(panel):
    phase = panel.get('phase')
    if phase in ('planned', 'journaled', 'restored'): return 'before'
    return 'either'  # writing, applied, restore-N, restore-incomplete


def _pick(item, target):
    return {'before': [item['before']], 'after': [item['after']], 'either': [item['before'], item['after']]}[target]


def _phase_transitional(panel):
    return panel.get('phase') in ('writing', 'restore-1-files', 'restore-2', 'restore-3', 'restore-4', 'restore-incomplete')


def panel_problems(panel, target=None, transitional=None):
    """Live drift against a panel section: rows, files, snapshots and the counter for target before/after/either.
    Zone arrays compare per panelId (either: trimmed entries of created/destroyed panels; Cinnamon's seeded
    defaults too only when `transitional`, which defaults to the phase: writing/restore-N/restore-incomplete);
    settings files accept a schema-upgrade rewrite by Cinnamon."""
    if not panel: return []
    seeded = _phase_transitional(panel) if transitional is None else bool(transitional)
    target = target or phase_target(panel)
    if target not in ('before', 'after', 'either'): raise ValueError('target must be before, after or either')
    out = []
    if panel['transition'] == 'none':
        rows = (panel.get('rail') or {}).get('rows') or {}
        out += [PANEL + '/' + key for key in PANEL_APPLY_ORDER if key not in rows or not _same_key(key, live_printed(key), rows[key])]
    for row in panel['settings']:
        current = live_value(row)
        ok = _row_either(row, current, seeded) if target == 'either' else same_setting(row, current, row[target])
        if not ok: out.append(row['schema'] + '/' + row['key'])
    for raw, row in panel['files'].items():
        try:
            if not _record_accepts(raw, row.get('uuid'), _pick(row, target)): out.append(raw)
        except Exception: out.append(raw)
    if target != 'after':
        for raw, snap in panel['snapshots'].items():
            allowed = [snap['record']] if target == 'before' else [snap['record'], {'kind': 'absent'}]
            try:
                if not _record_accepts(raw, snap.get('uuid'), allowed): out.append(raw)
            except Exception: out.append(raw)
    counter = panel['next_applet_id']
    if _gi()[0].Settings.new(PANEL).get_int('next-applet-id') < counter['after' if target == 'after' else 'before']:
        out.append(PANEL + '/next-applet-id')
    return out


def explain_problems(panel, problems, target=None):
    """One 'what: live X, expected Y [or Z]' line per problem, so a refusal names what to revert by hand."""
    target = target or phase_target(panel)
    problems = list(dict.fromkeys(problems))  # each problem once
    out = []
    try:
        if panel['transition'] == 'none':
            rail = (panel.get('rail') or {}).get('rows') or {}
            rows = {PANEL + '/' + key: {'schema': PANEL, 'key': key, 'before': rail.get(key), 'after': rail.get(key)} for key in PANEL_APPLY_ORDER}
        else:
            rows = {row['schema'] + '/' + row['key']: row for row in panel['settings']}
        known = _explain_rows(rows, problems, lambda row: sorted({str(v) for v in _pick(row, target)}))
        out += known
        described = {line.split(': live ', 1)[0] for line in known}
        for item in problems:
            if item in described: continue
            if item == PANEL + '/next-applet-id':
                counter = panel['next_applet_id']
                out.append(item + ': live ' + str(_gi()[0].Settings.new(PANEL).get_int('next-applet-id')) + ', expected >= '
                           + str(counter['after' if target == 'after' else 'before']))
            elif item in panel['files']:
                row = panel['files'][item]
                out.append(item + ': live ' + _describe(record(Path(item))) + ', expected ' + ' or '.join(_describe(r) for r in _pick(row, target)))
            elif item in panel['snapshots']:
                out.append(item + ': live ' + _describe(record(Path(item))) + ', expected ' + _describe(panel['snapshots'][item]['record'])
                           + ('' if target == 'before' else ' or absent'))
            else: out.append(item)
    except Exception as error:
        out.append('(could not describe every problem: ' + str(error) + ')')
    return out


def mark_pending(panel):
    """live.apply calls this right before journaling status 'pending': from then on the receipt has handed the user a
    desktop, and foreign values are deferred, never overwritten (Amendment 2 §2). mark_kept implies it."""
    panel.setdefault('reached_pending', time.time())


def reached_pending(data):
    """True once the receipt was pending/kept/active (now, or recorded by mark_pending/mark_kept/restore_preflight)."""
    panel = data.get('panel') or {}
    return data.get('status') in REACHED_PENDING_STATUSES or bool(panel.get('reached_pending') or panel.get('kept'))


def mark_kept(panel):
    """live.keep calls this before journaling status 'kept': the receipt is settled from then on, even if a later
    refused restore leaves it 'recovery-required' at phase 'applied'."""
    panel['kept'] = time.time()
    panel.setdefault('reached_pending', panel['kept'])


def receipt_transitional(data):
    """Amendment 1 §1: True while the receipt is mid-transition (its own apply/restore/failure), False once settled
    (kept/active/restored, or kept and refused later) or not yet journaled (live.plan data has no status)."""
    status = data.get('status')
    panel = data.get('panel') or {}
    if status is None or status in SETTLED_STATUSES: return False
    if panel.get('kept') and panel.get('phase') == 'applied': return False
    return True


def validate_problems(data):
    """live.validate for a panel receipt: strict (phase target) when settled; [] when transitional (its off rows are
    deferred conflicts of the restore, never refusals)."""
    panel = data.get('panel')
    if not panel or receipt_transitional(data): return []
    return panel_problems(panel)


def _entries(printed):
    GLib = _gi()[1]
    value = GLib.Variant.parse(None, printed, None, None).unpack()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value): raise ValueError('not a string list')
    return value


def foreign_instances(row, live):
    """Applet instance ids in a live enabled-applets value that are in neither the row's before nor its after."""
    known = set(parse_live(_entries(row['before']))) | set(parse_live(_entries(row['after'])))
    return sorted(ident for ident in parse_live(_entries(live)) if ident not in known)


def plausible_row(row, live):
    """Amendment 3 §1: the live value is, element-wise, the before value, the after value or Cinnamon's seeded or
    trimmed form (zones, heights) -- what only this transaction and Cinnamon could have written. Anything else
    (e.g. an applet instance id in neither side: a user-added applet) is foreign."""
    key = row['key']
    try:
        if row['schema'] != PANEL: return live in (row['before'], row['after'])
        if key in ZONE_KEYS: return zone_either(key, live, row['before'], row['after'], True)
        if key == 'panels-height': return height_either(live, row['before'], row['after'], True)
        if key in ('enabled-applets', 'panels-enabled', 'enabled-desklets'):
            allowed = set(_entries(row['before'])) | set(_entries(row['after']))
            if any(entry not in allowed for entry in _entries(live)): return False
            return not (key == 'enabled-applets' and foreign_instances(row, live))
        return live in (row['before'], row['after'])
    except Exception: return False


def mark_restore_attempted(panel):
    """Amendment 3 §2: the first restore attempt closes the overwrite window for good."""
    panel.setdefault('restore_attempted', time.time())


def restore_conflicts(data):
    """{'transitional', 'reached_pending', 'overwrite_window', 'refuse', 'defer', 'overwrite'}. A settled receipt
    refuses its conflicts; a transitional one defers them, except inside the overwrite window (never reached pending
    AND no restore attempted yet), where an off ROW whose live value is plausible (plausible_row) is overwritten back
    to the restore target. Implausible rows (foreign applet ids, user values) and files are always deferred
    (Amendment 2 §2, Amendment 3 §1-§2)."""
    panel = data['panel']
    transitional, pending = receipt_transitional(data), reached_pending(data)
    window = transitional and not pending and not panel.get('restore_attempted')
    conflicts = panel_conflicts(panel, transitional)
    out = {'transitional': transitional, 'reached_pending': pending, 'overwrite_window': window, 'refuse': [], 'defer': [], 'overwrite': []}
    if not transitional: out['refuse'] = conflicts; return out
    rows = {row['schema'] + '/' + row['key']: row for row in panel['settings']}
    for item in conflicts:
        row = rows.get(item)
        if window and row is not None and plausible_row(row, live_value(row)): out['overwrite'].append(item)
        else: out['defer'].append(item)
    return out


def restore_preflight(path, data, state_dir):
    """live.restore's panel preflight, before anything is written: last-in-first-out order, settled conflicts refuse,
    then the owner pointer moves to this receipt. Returns the deferred conflicts for restore_panel(automatic=True)."""
    panel = data['panel']
    if data.get('status') in REACHED_PENDING_STATUSES: panel.setdefault('reached_pending', time.time())  # before live sets 'restoring'
    why = restore_order_problem(state_dir, path)
    if why: raise RuntimeError(why)
    conflicts = restore_conflicts(data)
    mark_restore_attempted(panel)  # after taking this attempt's conflicts: later attempts defer (Amendment 3 §2)
    if conflicts['refuse']:
        raise RuntimeError('Restore conflict: ' + '; '.join(explain_problems(panel, conflicts['refuse'], 'either')))
    write_pointer(state_dir, path, panel.get('panel_seq'))
    return list(conflicts['defer'])


# ------------------------------------------------------------------ writing

def keep_snapshot_copies(path, panel):
    """Byte-exact sidecar copies of the snapshotted settings files in the receipt's folder (the receipt holds them too)."""
    config = str(config_dir())
    for raw, snap in panel['snapshots'].items():
        if snap['record']['kind'] != 'file': continue
        copy_ = Path(path).parent / 'spices' / snap['uuid'] / (str(snap['id']) + ('.json' if raw.startswith(config) else '.legacy.json'))
        copy_.parent.mkdir(parents=True, exist_ok=True)
        copy_.write_bytes(base64.b64decode(snap['record']['data'])); copy_.chmod(0o600)
        if hashlib.sha256(copy_.read_bytes()).hexdigest() != snap['sha256']: raise RuntimeError('Snapshot copy differs: ' + str(copy_))
        snap['copy'] = str(copy_)


def prepare_receipt(path, panel):
    """Between mkdir and the first journal: sidecars, the home/rail origin, phase 'journaled' (does not journal)."""
    keep_snapshot_copies(path, panel)
    if panel['transition'] == 'to-rail':
        panel['home']['snapshots'] = copy.deepcopy(panel['snapshots'])
        panel['home']['captured_in'] = panel['rail']['captured_in'] = str(path)
    panel['phase'] = 'journaled'


def _late_rewrite(panel, items, want, bus, errors=None, conflicts=None):
    """Rewrite settings files Cinnamon deleted or re-seeded after the enabled-applets write, then reload their applets.
    `items` {raw: {uuid, ...}}; `want(item)` the record. With `errors` the failures are collected, else they raise."""
    reload = set()
    for raw, item in items.items():
        expected = want(item)
        if expected['kind'] == 'absent' or (conflicts is not None and raw in conflicts): continue
        p = Path(raw)
        try:
            if record(p) == expected: continue
            write(p, expected); reload.add(item['uuid'])
            panel.setdefault('late_rewrites', []).append(raw)
            if record(p) != expected: raise RuntimeError('Settings file still differs after the late rewrite: ' + raw)
        except Exception as error:
            if errors is None: raise
            errors.append(str(error)); conflicts.append(raw)
    for applet in sorted(reload):
        try:
            bus('org.Cinnamon.ReloadXlet', applet, 'APPLET')  # the re-added instances load the restored files
            panel.setdefault('reloads', []).append(applet)
        except Exception as error:
            if errors is None: raise
            errors.append('ReloadXlet ' + applet + ': ' + str(error)); conflicts.append('reload:' + applet)


def apply_panel(path, data, save, bus=None):
    """Write the receipt's forward transition. to-rail order (desktop_control.apply_panel): files, compare-and-set
    next-applet-id, rows in apply_order, settle. to-home: files (home snapshots) and missing insurance, rows in
    RESTORE_STEPS order, settle, late re-verify + ReloadXlet. none: verify only. Raises on any failure."""
    if not data.get('guard_armed'): raise RuntimeError('Independent recovery must be armed before panel writes')
    bus = bus or default_bus
    Gio = _gi()[0]
    panel = data['panel']
    if panel['transition'] == 'none':
        off = panel_problems(panel, 'after')
        if off: raise RuntimeError('Panel layout changed externally; reconcile before continuing (' + ', '.join(off) + ')')
        panel['phase'] = 'applied'; save(path, data); return
    for raw, snap in panel['snapshots'].items():
        if record(Path(raw)) != snap['record']: raise RuntimeError('Applet settings file changed since it was journaled: ' + raw)
    for raw, row in panel['files'].items():
        if record(Path(raw)) != row['before']: raise RuntimeError('Panel settings file changed since plan: ' + raw)
    panel['phase'] = 'writing'; save(path, data)
    for raw, row in panel['files'].items(): write(Path(raw), row['after'])
    for raw, rec in panel.get('insurance_apply', {}).items():
        if not os.path.lexists(raw):
            write(Path(raw), rec); panel.setdefault('insurance_applied', []).append(raw)
    cinnamon = Gio.Settings.new(PANEL)
    counter = panel['next_applet_id']
    if counter['allocate']:
        if cinnamon.get_int('next-applet-id') != counter['before']:
            raise RuntimeError('next-applet-id changed since plan; refusing to reuse instance id ' + str(counter['before']))
        if not cinnamon.set_int('next-applet-id', counter['after']): raise RuntimeError('Setting rejected: next-applet-id')
        counter['written'] = True
    save(path, data)
    # Every instance leaving the panel is copied right before the enabled-applets write (Amendment 3 §3; v1.5 also
    # for to-rail: an applet added since validate). A copy that cannot be made fails the apply before any row.
    if keep_leaving_copies(path, panel, 'after'): save(path, data)
    rows = {row['key']: row for row in panel['settings']}
    for key in panel['apply_order']: set_row(rows[key], 'after')
    Gio.Settings.sync()
    off = settle(panel['settings'], 'after')
    if off: raise RuntimeError('Panel settings did not settle: ' + ', '.join(off))
    if panel['transition'] == 'to-home':
        _late_rewrite(panel, panel['files'], lambda row: row['after'], bus)
    panel['phase'] = 'applied'; save(path, data)


def panel_conflicts(panel, transitional=None):
    """External edits to anything the transition wrote or will write back; never overwritten silently. Rows compare
    as either (zone arrays per panelId; Cinnamon's seeded defaults only for a transitional receipt, which defaults
    to the phase); files accept schema upgrades."""
    seeded = _phase_transitional(panel) if transitional is None else bool(transitional)
    out = []
    for row in panel['settings']:
        try:
            if not _row_either(row, live_value(row), seeded): out.append(row['schema'] + '/' + row['key'])
        except Exception: out.append(row['schema'] + '/' + row['key'])
    for raw, row in panel['files'].items():
        try:
            if not _record_accepts(raw, row.get('uuid'), [row['before'], row['after']]): out.append(raw)
        except Exception: out.append(raw)
    for raw, snap in panel['snapshots'].items():
        if snap['record']['kind'] == 'absent': continue
        try:
            if not _record_accepts(raw, snap.get('uuid'), [snap['record'], {'kind': 'absent'}]): out.append(raw)
        except Exception: out.append(raw)
    return out


def _live_state(panel):
    """What the live layout is now, for messages: 'the home'/'the rail' when the live panels-enabled (the panels the
    user sees) is one side's, else 'a mixed layout'. Applets a user added do not change which layout it is."""
    try:
        row = next(r for r in panel['settings'] if r['key'] == 'panels-enabled')
        live = live_value(row)
        for side, state in (('before', panel['state_before']), ('after', panel['state_after'])):
            if same_setting(row, live, row[side]): return 'the ' + state
    except Exception: pass
    return 'a mixed layout'


def defer_layout(panel, conflicts, rows, why, items=()):
    """Leave the whole current layout in place (a working desktop) for a manual restore; never a mixed layout."""
    added = []
    for key in rows:
        if PANEL + '/' + key not in conflicts: conflicts.append(PANEL + '/' + key); added.append(PANEL + '/' + key)
    panel['restore_deferred_rows'] = added  # held back with the layout, not off themselves
    panel['restore_deferred_items'] = list(items)  # already explained inside restore_deferred
    panel['restore_deferred'] = 'layout left as ' + _live_state(panel) + ': ' + why


def restore_panel(path, data, save, bus=None, conflicts=None, automatic=True):
    """The reverse of the receipt's own transition (port of desktop_control.restore_panel, generic over
    panel['restore_steps']): (1) snapshots byte-exact + missing insurance, (2..) the row groups with the files
    returned to 'before' right after the group holding enabled-applets, settle, the late re-verify of (1) with
    ReloadXlet; next-applet-id is never changed. phase 'restored' only when the run is clean, else
    'restore-incomplete' (retryable). automatic=True never raises an Exception (a kill still passes through);
    automatic=False raises on the first error (Indigo's manual restore)."""
    bus = bus or default_bus
    panel = data['panel']
    conflicts = [] if conflicts is None else conflicts
    mark_restore_attempted(panel)
    # Each run decides afresh; the history of earlier runs is kept.
    if panel.get('restore_deferred'): panel.setdefault('restore_deferred_earlier', []).append(panel.pop('restore_deferred'))
    panel.pop('restore_deferred_rows', None); panel.pop('restore_deferred_items', None)
    if panel.get('restore_errors'): panel.setdefault('restore_errors_earlier', []).extend(panel['restore_errors'])
    errors = panel['restore_errors'] = []
    journal_errors = []

    def journal():
        try: save(path, data)
        except Exception as error:
            if not automatic: raise
            journal_errors.append(str(error)); panel.setdefault('journal_errors', []).append(str(error))

    def failed(error, key=None):
        if not automatic: raise error
        errors.append(str(error) or type(error).__name__)
        if key is not None and key not in conflicts: conflicts.append(key)

    def sync():
        try: _gi()[0].Settings.sync()
        except Exception as error: failed(error)

    try:
        _restore_steps(path, panel, conflicts, bus, automatic, journal, failed, sync, errors)
    except Exception as error:  # anything unforeseen: record it, the finish below still journals
        failed(error)
    try: panel['next_applet_id']['at_restore'] = _gi()[0].Settings.new(PANEL).get_int('next-applet-id')
    except Exception as error: failed(error)
    deferred = bool(panel.get('restore_deferred'))
    clean = not errors and not conflicts and not deferred
    panel['phase'] = 'restored' if clean else 'restore-incomplete'
    before = len(journal_errors)
    journal()
    result = {'status': 'deferred' if deferred else 'restored' if clean else 'recovery-required', 'conflicts': conflicts}
    if not clean:
        held = set(panel.get('restore_deferred_rows', []) + panel.get('restore_deferred_items', [])) if deferred else set()
        # Each problem once: not again when the deferral message already explains it, never twice in the list.
        items = list(dict.fromkeys(c for c in conflicts if c not in held and not c.startswith('reload:')))
        result['explain'] = explain_problems(panel, items, 'either')
        if deferred: result['explain'].insert(0, panel['restore_deferred'])
    if len(journal_errors) > before:
        result['status'] = 'deferred' if deferred else 'recovery-required'
        result['save_error'] = journal_errors[-1]
    return result


def _instance_of(panel, raw):
    item = panel['files'].get(raw) or panel['snapshots'].get(raw) or {}
    return (item['uuid'], item['id']) if 'uuid' in item and 'id' in item else None


def _fsync_dir(folder):
    fd = os.open(folder, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _copy_settings_file(path, panel, raw, instance, state, kind='conflict'):
    """One durable byte copy of a live settings file into the receipt's spices folder: written 0600, fsynced, its
    folder fsynced, sha256-verified, recorded in panel['<kind>_copies'][raw] ('conflict' for a real conflict,
    'leaving' for the routine copy of an instance leaving the panel). A partial copy is removed when writing fails
    (ENOSPC), so a retry starts clean. Nothing is copied when those exact bytes are already kept (in either
    registry, or as the receipt's own snapshot). Returns the copy or None."""
    p = Path(raw)
    data = p.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    registry = panel.setdefault(kind + '_copies', {})
    for other in ('conflict_copies', 'leaving_copies'):
        if any(c.get('sha256') == digest for c in panel.get(other, {}).get(raw, [])): return None
    snap = panel['snapshots'].get(raw)
    if kind == 'leaving' and snap and snap.get('sha256') == digest: return None  # already in the receipt and its sidecar
    if 'stamp' not in state: state.update(config=str(config_dir()), stamp=time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    name = str(instance[1]) + ('' if raw.startswith(state['config']) else '.legacy') + '-' + kind + '-' + state['stamp'] + '.json'
    copy_ = Path(path).parent / 'spices' / instance[0] / name
    copy_.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(copy_, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            view = memoryview(data)
            while view: view = view[os.write(fd, view):]
            os.fsync(fd)
        finally: os.close(fd)
        if hashlib.sha256(copy_.read_bytes()).hexdigest() != digest: raise RuntimeError('Settings copy differs: ' + str(copy_))
        _fsync_dir(copy_.parent)
    except BaseException:
        try: copy_.unlink()
        except OSError: pass
        raise
    registry.setdefault(raw, []).append({'copy': str(copy_), 'sha256': digest, 'at': time.time()})
    return str(copy_)


def keep_conflict_copies(path, panel, conflicts):
    """Byte copies of every conflicting journaled settings file as it is now (the user's edit), in
    state/<id>/spices/<uuid>/<id>[.legacy]-conflict-<ts>.json, recorded in panel['conflict_copies'] (sha256
    verified; a copy with the same bytes is not repeated). Nothing a restore or Cinnamon does later can lose them."""
    made, state = [], {}
    for raw in conflicts:
        instance = _instance_of(panel, raw)
        p = Path(raw)
        if instance is None or p.is_symlink() or not p.is_file(): continue
        copy_ = _copy_settings_file(path, panel, raw, instance, state)
        if copy_: made.append(copy_)
    return made


def _leaving_instances(panel, target):
    """{id: uuid} of the applet instances that leave enabled-applets when it is written to `target` ('before' in a
    restore, 'after' in an apply): in the live value or the other side, but not in the target."""
    row = next((r for r in panel['settings'] if r['key'] == 'enabled-applets'), None)
    if row is None: return {}
    other = 'after' if target == 'before' else 'before'
    goal = parse_live(_entries(row[target]))
    now = {**parse_live(_entries(row[other])), **parse_live(_gi()[0].Settings.new(PANEL).get_strv('enabled-applets'))}
    return {ident: applet for ident, (applet, _) in now.items() if ident not in goal}


def _leaving_paths(panel, rows=None, target='before'):
    """Settings-file paths (both config roots) of the instances leaving enabled-applets towards `target`."""
    return {str(p) for ident, applet in _leaving_instances(panel, target).items() for p in spices_paths(applet, ident)}


def keep_leaving_copies(path, panel, target):
    """Amendment 3 §3: byte copies of the settings files (both config roots) of EVERY instance that leaves the panel
    when enabled-applets is written to `target` -- journaled or not (a user-added applet) -- before that write
    (Cinnamon deletes them). Raises when a present file cannot be copied; the caller defers (restore) or fails."""
    made, state = [], {}
    for ident, applet in sorted(_leaving_instances(panel, target).items()):
        for p in spices_paths(applet, ident):
            if not os.path.lexists(p): continue
            if p.is_symlink():
                # A dotfile-managed link: Cinnamon deletes only the link; its target stays. Record it (the receipt
                # layer restores links), never defer for it.
                entry = {'kind': 'symlink', 'target': os.readlink(p), 'at': time.time()}
                kept = panel.setdefault('leaving_copies', {}).setdefault(str(p), [])
                if not any(c.get('kind') == 'symlink' and c.get('target') == entry['target'] for c in kept): kept.append(entry)
                continue
            if not p.is_file(): raise RuntimeError('Cannot copy a non-regular settings file: ' + str(p))
            copy_ = _copy_settings_file(path, panel, str(p), (applet, ident), state, 'leaving')
            if copy_: made.append(copy_)
    return made


def _restore_steps(path, panel, conflicts, bus, automatic, journal, failed, sync, errors):
    if panel['transition'] == 'none' or not panel['settings']: return
    rows = {row['key']: row for row in panel['settings']}
    # The user's edits to journaled settings files (deferred conflicts) are copied before anything else runs.
    try:
        if keep_conflict_copies(path, panel, conflicts): journal()
    except Exception as error: failed(RuntimeError('conflict copy: ' + str(error)))
    # Every instance that leaves the panel in this direction is copied too, before anything else is written
    # (Amendment 3 §3); a copy that cannot be made keeps the whole layout.
    try:
        if keep_leaving_copies(path, panel, 'before'): journal()
    except Exception as error:
        errors.append('leaving-instance copy: ' + str(error))
        defer_layout(panel, conflicts, rows, 'the settings files of applets leaving the panel could not be copied (' + str(error)
                     + '); nothing was written'); return
    steps = [tuple(step) for step in panel['restore_steps']]
    group = [PANEL + '/' + key for key in steps[0] if PANEL + '/' + key in conflicts]
    if group:
        # The first group (panels-height, panels-enabled, enabled-applets) only makes sense together.
        defer_layout(panel, conflicts, rows, 'externally changed: ' + '; '.join(explain_problems(panel, group, 'either')), group); return
    # (1) Settings files Cinnamon deleted in the apply direction, byte-exact, BEFORE enabled-applets re-adds them.
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
            blocked.append(raw); failed(error, raw)
    for raw, rec in panel.get('insurance', {}).items():
        try:
            if not os.path.lexists(raw):
                write(Path(raw), rec); panel.setdefault('insurance_restored', []).append(raw)
        except Exception as error: failed(error, raw)
    panel['phase'] = 'restore-1-files'; journal()
    if blocked:
        # Re-adding those instances without their files would let Cinnamon install schema defaults over the
        # user's values; keep the current layout (a working desktop) and leave it for a manual restore.
        defer_layout(panel, conflicts, rows, 'settings files not restored: ' + '; '.join(explain_problems(panel, blocked, 'either')), blocked); return
    # A conflicting settings file of an applet that leaves the panel in this direction would be deleted by Cinnamon
    # as soon as enabled-applets is written (review round 2, A): keep the whole layout and name the file.
    try: at_risk = [raw for raw in conflicts if raw in _leaving_paths(panel)]
    except Exception as error:
        failed(RuntimeError('cannot tell which applets leave the panel: ' + str(error)))
        at_risk = [raw for raw in conflicts if _instance_of(panel, raw)]
    if at_risk:
        copies = [c['copy'] for raw in at_risk for c in panel.get('conflict_copies', {}).get(raw, [])[-1:]]
        defer_layout(panel, conflicts, rows, 'settings files changed during the transition would be deleted by Cinnamon when their '
                     'applets leave the panel: ' + '; '.join(explain_problems(panel, at_risk, 'either'))
                     + ('; byte copies: ' + ', '.join(copies) if copies else '')
                     + '; move or revert them, then retry', at_risk); return
    written = []
    for number, step in enumerate(steps, 2):
        for key in step:
            row = rows[key]
            if PANEL + '/' + key in conflicts: continue
            try:
                if not exact_setting(row, live_value(row), row['before']): set_row(row, 'before')
                written.append(row)
            except Exception as error: failed(error, PANEL + '/' + key)
        sync()
        if 'enabled-applets' in step and PANEL + '/enabled-applets' not in conflicts:
            # The instances this transition added have left enabled-applets; Cinnamon deletes their files too.
            for raw, row in panel['files'].items():
                if raw in conflicts: continue
                try: write(Path(raw), row['before'])
                except Exception as error: failed(error, raw)
        panel['phase'] = 'restore-' + str(number); journal()
    try: off = settle(written, 'before')
    except Exception as error:
        failed(RuntimeError('settle: ' + str(error)))
        off = []
        for row in written:
            try: ok = same_setting(row, live_value(row), row['before'])
            except Exception: ok = False
            if not ok: off.append(row['schema'] + '/' + row['key'])
    for key in off:
        if key not in conflicts: conflicts.append(key)
    # Late re-verify of step 1: Cinnamon deletes a removed instance's settings file asynchronously in its
    # enabled-applets handler. On a fast failure path step 1 may have found the file still present, and the
    # delayed deletion (or a schema-default install on re-add) then lands after step 2. Rewrite and reload once.
    if PANEL + '/enabled-applets' not in conflicts:
        _late_rewrite(panel, panel['snapshots'], snapshot_source, bus, errors, conflicts)
        journal()
