#!/usr/bin/python3
"""Rehearse one rail profile's panel transitions in a private Cinnamon: to-rail -> to-home -> undo -> restore.

The real desktop, its D-Bus session and its dconf database are never touched. The run gets a private HOME and
private XDG directories, a dbus-run-session bus (with its own dconf service writing under the private
XDG_CONFIG_HOME) and a private X server (Xvfb when installed, otherwise a nested Xephyr window). The parent only
READS the user's current panel layout (or a --standin file) and copies the listed applets' code and settings files
into the run. The private shell starts on that copy, and panel_layout.py's own code path then does these things,
each checked in the running shell:
- a to-rail killed mid-write (after panels-enabled; Cinnamon then trims the zone arrays and deletes the c-eyes
  files) is recovered by live.restore's own panel calls (live_restore_path: validate_problems, restore_preflight,
  restore_panel(automatic=True), validate again), back to the copied layout;
- plan_transition -> prepare_receipt -> apply_panel turns the copy into the rail;
- a to-home killed mid-write (Cinnamon re-creates panel 2 and seeds its zone entries) is recovered the same way,
  back to the rail;
- a to-home takes it back;
- the undo of that to-home re-adds the same calendar id;
- the restore of the first receipt returns the copied layout.
Every restore in the rehearsal goes through live_restore_path (DESIGN Amendment 1 §7).

The report `verification/isolated-latest/<slug>.panel.json` carries panel_code_sha256 and panel_spec_sha256. It
is the gate that live.plan requires before a to-rail.

Port of the rail rehearsal in presets/indigo-lunchbox/isolate.py, with the same privacy mechanics as the
collection's isolate.py. isolate.py itself stays unchanged. This is a GUI test: the coordinator runs it, serially.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = ROOT / 'verification' / 'isolated-panel'
DEFAULT_SCREEN = '1600x1000'
REHEARSAL = ['kill-to-rail', 'recover-home', 'to-rail', 'kill-to-home', 'recover-rail', 'to-home', 'undo', 'restore']
KILL_AFTER = 2  # set_rows: to-rail enabled-applets + panels-enabled (destroys panel 2); to-home panels-height + panels-enabled (creates it)
NORMAL_SLUG = 'isolated-normal-profile'  # the to-home target: any profile without panel_layout
# Keys the parent copies from the live session (or the standin) into the private one; read-only on the live side.
LAYOUT_KEYS = ('enabled-applets', 'panels-enabled', 'panels-height', 'panels-autohide', 'panels-show-delay', 'panels-hide-delay',
               'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes', 'panel-zone-text-sizes', 'enabled-desklets', 'next-applet-id')
REQUIRED_STANDIN = ('enabled-applets', 'panels-enabled', 'panels-height', 'next-applet-id')
PRIMARY = ('menu@cinnamon.org', 'workspace-switcher@cinnamon.org', 'grouped-window-list@cinnamon.org', 'calendar@cinnamon.org')
PANEL_LOC = {'top': 0, 'bottom': 1, 'left': 2, 'right': 3}  # Cinnamon panel.js PanelLoc
SPICE_RE = re.compile(r'([A-Za-z0-9][A-Za-z0-9@._+-]*)/([0-9]+)\.json')
ENV_RUN, ENV_PROFILE = 'CURRENT_COLLECTION_PANEL_RUN', 'CURRENT_COLLECTION_PANEL_PROFILE'
PANELS_JS = ('imports.ui.main.panelManager.panels.filter(p=>p).map(p=>({id:p.panelId,pos:p.panelPosition,height:p.actor.height,width:p.actor.width,'
             'monitor_height:global.display.get_monitor_geometry(p.monitorIndex).height,'
             'zone_heights:[p._leftBox,p._centerBox,p._rightBox].map(z=>z.get_preferred_height(-1)[1])}))')
LOADED_JS = "imports.ui.appletManager.definitions.filter(d=>d.applet).map(d=>d.uuid+':'+d.applet_id)"
# Diagnostic only (never part of the pass criteria): the rail, its zone boxes and applet actors (Indigo isolate.py).
MEASURE_JS = r'''(() => {
  const Main = imports.ui.main, AM = imports.ui.appletManager, St = imports.gi.St;
  const safe = f => { try { return f(); } catch (e) { return 'error: ' + e; } };
  const box = a => safe(() => { const b = a.get_allocation_box(); return {x: b.x1, y: b.y1, w: b.x2 - b.x1, h: b.y2 - b.y1}; });
  const pref = a => safe(() => { const [minH, natH] = a.get_preferred_height(-1); const [minW, natW] = a.get_preferred_width(-1); return {minH, natH, minW, natW}; });
  const owner = new Map();
  AM.definitions.forEach(d => { if (d.applet && d.applet.actor) owner.set(d.applet.actor, d.uuid + ':' + d.applet_id); });
  const desc = (a, depth) => {
    const o = {actor: String(a).slice(0, 90), alloc: box(a), pref: pref(a)};
    if (owner.has(a)) o.key = owner.get(a);
    if (depth > 0) o.children = a.get_children().filter(c => c.visible).map(c => desc(c, depth - 1));
    return o;
  };
  const p = Main.panelManager.panels.filter(x => x)[0];
  const m = global.display.get_monitor_geometry(p.monitorIndex);
  return {screen: {w: global.screen_width, h: global.screen_height}, monitor: {x: m.x, y: m.y, w: m.width, h: m.height},
          ui_scale: global.ui_scale, panel: {id: p.panelId, pos: p.panelPosition, alloc: box(p.actor), pref: pref(p.actor)},
          zones: Object.fromEntries([['left', p._leftBox], ['center', p._centerBox], ['right', p._rightBox]].map(([n, z]) =>
            [n, {alloc: box(z), pref: pref(z), applets: z.get_children().filter(c => c.visible).map(c => desc(c, 2))}]))};
})()'''


# ------------------------------------------------------------------ pure helpers (unit-tested)

class Killed(BaseException):
    """A simulated kill mid-write: like SIGKILL, no except-Exception recovery in the writer runs."""


def kill_after(pl, n):
    """Make pl.set_row raise Killed right after its n-th write (then un-patch). Returns an un-patch function."""
    original, calls = pl.set_row, []
    def set_row(row, target):
        original(row, target)
        calls.append(row['key'])
        if len(calls) >= n:
            pl.set_row = original
            raise Killed('killed after ' + ', '.join(calls))
    pl.set_row = set_row
    def undo(): pl.set_row = original
    return undo


def live_restore_path(pl, path, state_dir, save, bus):
    """live.restore's panel calls (API.md v1.2 §A6) on the journaled receipt, as the 300 s timer runs them:
    validate_problems -> restore_preflight -> restore_panel(automatic=True) -> validate_problems again.
    Returns (result, data, problems after)."""
    data = json.loads(Path(path).read_text())
    problems = pl.validate_problems(data)
    if problems: raise RuntimeError('validate refused: ' + '; '.join(pl.explain_problems(data['panel'], problems)))
    deferred = pl.restore_preflight(path, data, state_dir)
    data['status'] = 'restoring'; save(path, data)
    result = pl.restore_panel(path, data, save, bus, deferred, automatic=True)
    data['status'] = 'restored' if result['status'] == 'restored' else 'recovery-required'; save(path, data)
    return result, data, pl.validate_problems(data)


def load_panel_layout(path=None):
    """The panel_layout.py beside this file (the exact code the gate hashes)."""
    path = Path(path or ROOT / 'panel_layout.py')
    spec = importlib.util.spec_from_file_location('collection_panel_layout_isolated', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def validate_screen(value: str) -> str:
    match = re.fullmatch(r'([0-9]{3,5})x([0-9]{3,5})', value)
    if not match or not 640 <= int(match.group(1)) <= 16384 or not 480 <= int(match.group(2)) <= 16384:
        raise argparse.ArgumentTypeError('screen must be WIDTHxHEIGHT within 640x480..16384x16384')
    return value


def rail_thickness(panel: dict) -> int:
    """The rail's thickness in logical px: its panels_height entry (panel_layout.panel has exactly one panel)."""
    return int(panel['panels_height'][0].split(':')[1])


def rail_edge(panel: dict) -> str:
    return panel['panels_enabled'][0].split(':')[2]


def rail_problems(panels, rail, edge='left'):
    """The rendered panels must be exactly one panel on the rail's edge, `rail` px wide, as tall as the monitor
    Cinnamon reports (`monitor_height`; Cinnamon sizes a vertical panel to it, never to its content), and its
    content must fit: the three zone boxes' natural heights (`zone_heights`) sum to at most the rail height."""
    if len(panels) != 1: return ['expected one panel, found ' + str(len(panels))]
    p = panels[0]; out = []
    if p.get('pos') != PANEL_LOC[edge]: out.append('panel ' + str(p.get('id')) + ' is not on the ' + edge + ' edge (PanelLoc ' + str(p.get('pos')) + ')')
    if p.get('width') != rail: out.append('rail width ' + str(p.get('width')) + ' != ' + str(rail))
    height, monitor, zones = p.get('height'), p.get('monitor_height'), p.get('zone_heights')
    if not isinstance(monitor, (int, float)) or isinstance(monitor, bool) or height != monitor:
        out.append('rail height ' + str(height) + ' != monitor height ' + str(monitor))
    if not (isinstance(zones, list) and len(zones) == 3 and all(isinstance(z, (int, float)) and not isinstance(z, bool) for z in zones)):
        out.append('zone natural heights unavailable: ' + str(zones))
    elif not isinstance(height, (int, float)) or sum(zones) > height:
        out.append('rail content ' + str(sum(zones)) + ' px (zones ' + str(zones) + ') overflows rail height ' + str(height))
    return out


def instances(entries):
    """[(uuid, id)] of enabled-applets entries (panelN:zone:order:uuid:id)."""
    out = []
    for entry in entries:
        parts = entry.split(':')
        if len(parts) == 5 and parts[4].isdigit(): out.append((parts[3], int(parts[4])))
    return out


def eval_json(result):
    """Decode a gdbus-printed org.Cinnamon.Eval answer '(true, <JSON.stringify(value)>)' into the JS value."""
    from gi.repository import GLib
    ok, text = GLib.Variant.parse(None, result, None, None).unpack()
    if not ok: raise RuntimeError('Eval failed: ' + text)
    return json.loads(text)


def load_standin(path):
    """A --standin file: {"gsettings": {key: printed GVariant}, "spices": {"<uuid>/<id>.json": "<abs source>"}}.
    Returns (gsettings, {relative: Path}); refuses unknown keys, unsafe names and non-regular sources."""
    from gi.repository import GLib
    doc = json.loads(Path(path).read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get('gsettings'), dict): raise RuntimeError('standin needs a "gsettings" object')
    gsettings = doc['gsettings']
    unknown = sorted(set(gsettings) - set(LAYOUT_KEYS))
    if unknown: raise RuntimeError('standin has keys outside the panel layout: ' + ', '.join(unknown))
    missing = [k for k in REQUIRED_STANDIN if k not in gsettings]
    if missing: raise RuntimeError('standin lacks ' + ', '.join(missing))
    for key, printed in gsettings.items():
        if not isinstance(printed, str): raise RuntimeError('standin value for ' + key + ' must be a printed GVariant string')
        try: GLib.Variant.parse(None, printed, None, None)
        except Exception: raise RuntimeError('standin value for ' + key + ' is not a GVariant: ' + printed) from None
    spices = {}
    raw = doc.get('spices', {})
    if not isinstance(raw, dict): raise RuntimeError('standin "spices" must be an object')
    for relative, source in raw.items():
        if not isinstance(relative, str) or not SPICE_RE.fullmatch(relative): raise RuntimeError('unsafe standin spices name ' + repr(relative))
        source = Path(source) if isinstance(source, str) else None
        if source is None or not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise RuntimeError('standin spices source must be an absolute regular file: ' + str(raw[relative]))
        spices[relative] = source
    return gsettings, spices


def live_is_rail(gsettings, panel):
    """True when a printed layout's panels-enabled already is the profile's rail (e.g. the preset rail is live)."""
    from gi.repository import GLib
    printed = gsettings.get('panels-enabled')
    return printed is not None and list(GLib.Variant.parse(None, printed, None, None).unpack()) == panel['panels_enabled']


def report_fingerprints(pl, item):
    """The gate's two hashes: this panel_layout.py and the profile's canonical panel_layout."""
    return pl.fingerprint(item)


def latest_report_path(verify_root, slug):
    return Path(verify_root).parent / 'isolated-latest' / (slug + '.panel.json')


def xserver_command(fd, screen, title, choice=None):
    """Prefer headless Xvfb; fall back to a nested Xephyr window. CURRENT_COLLECTION_PANEL_XSERVER overrides."""
    choice = choice or os.environ.get('CURRENT_COLLECTION_PANEL_XSERVER') or ('Xvfb' if shutil.which('Xvfb') else 'Xephyr')
    if choice == 'Xvfb':
        return ['Xvfb', '-displayfd', str(fd), '-screen', '0', screen + 'x24', '-nolisten', 'tcp', '-noreset']
    if choice != 'Xephyr': raise RuntimeError('Unsupported X server ' + repr(choice))
    return ['Xephyr', '-displayfd', str(fd), '-screen', screen, '-nolisten', 'tcp', '-noreset', '-no-host-grab', '-title', title]


def privacy_problems(env, run):
    """Every reason the worker must not write: any live display, bus, HOME or settings location."""
    run = str(run); out = []
    if not env.get('DISPLAY') or env.get('DISPLAY') == env.get('CURRENT_COLLECTION_HOST_DISPLAY'): out.append('DISPLAY is the host display')
    if not env.get('DBUS_SESSION_BUS_ADDRESS') or env.get('DBUS_SESSION_BUS_ADDRESS') == env.get('CURRENT_COLLECTION_HOST_BUS'):
        out.append('session bus is the host bus')
    if not env.get('HOME') or env.get('HOME') == env.get('CURRENT_COLLECTION_HOST_HOME'): out.append('HOME is the host HOME')
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        if not str(env.get(key, '')).startswith(run + '/'): out.append(key + ' is outside the run')
    if env.get('GSETTINGS_BACKEND') != 'dconf' or env.get('DCONF_PROFILE'): out.append('settings backend is not the private dconf')
    return out


def profile(slug: str) -> dict:
    candidate_file = os.environ.get('CURRENT_COLLECTION_PROFILE_CANDIDATES')
    spec = json.loads(Path(candidate_file or ROOT / 'profiles.json').read_text(encoding='utf-8'))
    value = spec['profiles'].get(slug)
    if value is None: raise KeyError(f'Unknown profile: {slug}')
    return value


# ------------------------------------------------------------------ parent (read-only on the live side)

def read_live_layout():
    from gi.repository import Gio
    cinnamon = Gio.Settings.new('org.cinnamon')
    listed = cinnamon.props.settings_schema.list_keys()
    return {key: cinnamon.get_value(key).print_(True) for key in LAYOUT_KEYS if key in listed}


def copy_applets(run, gsettings, spices):
    """Copy the listed instances' settings files (standin sources first, else the live file) and the user-installed
    applet code into the run; never writes outside it."""
    from gi.repository import GLib
    live_config = Path(GLib.get_user_config_dir()) / 'cinnamon/spices'
    live_applets = Path(GLib.get_user_data_dir()) / 'cinnamon/applets'
    copied = {}
    entries = GLib.Variant.parse(None, gsettings['enabled-applets'], None, None).unpack()
    for applet, ident in instances(entries):
        name = applet + '/' + str(ident) + '.json'
        source = spices.get(name, live_config / name)
        if source.is_file() and not source.is_symlink():
            target = run / 'config/cinnamon/spices' / name
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
            copied[name] = {'source': str(source), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        code = live_applets / applet
        if code.is_dir() and not (run / 'data/cinnamon/applets' / applet).exists():
            shutil.copytree(code, run / 'data/cinnamon/applets' / applet, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
    return copied


def cleanup_fixture(run: Path) -> None:
    """Remove only this runner's disposable HOME/XDG copy trees (the report, logs and receipts stay)."""
    for name in ('home', 'config', 'data', 'cache', 'state'):
        path = run / name
        if path.exists(): shutil.rmtree(path)


def parent(slug: str, standin_file=None) -> int:
    item = profile(slug)
    pl = load_panel_layout()
    if 'panel_layout' not in item: raise RuntimeError(slug + ' has no panel_layout; nothing to rehearse')
    why = pl.validate_layout(item)
    if why: raise RuntimeError('Unsafe panel_layout: ' + why)
    generated_root = Path(os.environ.get('CURRENT_COLLECTION_GENERATED_ROOT', ROOT / 'generated'))
    verify_root = Path(os.environ.get('CURRENT_COLLECTION_VERIFY_ROOT', DEFAULT_RUN))
    generated = generated_root / slug
    sources = {'theme': generated / 'desktop' / item['theme'], 'icons': generated / 'icons' / item['icons'],
               'cursors': generated / 'cursors' / item['cursor'], 'wallpaper': generated / 'artwork' / 'wallpaper.png'}
    fingerprints = report_fingerprints(pl, item)
    run = verify_root / slug / time.strftime('%Y%m%d-%H%M%S')
    duplicate = 1
    while run.exists():
        run = verify_root / slug / (time.strftime('%Y%m%d-%H%M%S') + f'-{duplicate:02d}'); duplicate += 1
    run.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update({ENV_RUN: str(run), ENV_PROFILE: slug, 'CURRENT_COLLECTION_GENERATED_ROOT': str(generated_root),
                'CURRENT_COLLECTION_VERIFY_ROOT': str(verify_root),
                'CURRENT_COLLECTION_PANEL_FINGERPRINTS': json.dumps(fingerprints, sort_keys=True)})
    for name, part in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')):
        path = run / part; path.mkdir(mode=0o700); env[name] = str(path)
    runtime = tempfile.TemporaryDirectory(prefix='cinnamon-current-panel-')
    env['XDG_RUNTIME_DIR'] = runtime.name
    standin_source = str(Path(standin_file).resolve()) if standin_file else 'live'
    try:
        missing = [k for k, p in sources.items() if not p.exists()]
        if missing: raise RuntimeError(f'Build {slug} before its rail rehearsal (missing {", ".join(missing)})')
        shutil.copytree(sources['theme'], run / 'data/themes' / item['theme'], symlinks=True)
        shutil.copytree(sources['icons'], run / 'data/icons' / item['icons'], symlinks=True)
        shutil.copytree(sources['cursors'], run / 'data/icons' / item['cursor'], symlinks=True)
        if standin_file: gsettings, spices = load_standin(standin_file)
        else:
            gsettings, spices = read_live_layout(), {}
            if live_is_rail(gsettings, item['panel_layout']['panel']):
                raise RuntimeError('The live panel layout already is the rail; pass --standin <home layout JSON> (e.g. the preset fixture)')
        (run / 'standin-layout.json').write_text(json.dumps(gsettings, indent=2) + '\n')
        copied = copy_applets(run, gsettings, spices)
        (run / 'standin-spices.json').write_text(json.dumps(copied, indent=2) + '\n')
        gtk = run / 'config/gtk-3.0/settings.ini'; gtk.parent.mkdir(parents=True, exist_ok=True)
        ui_font = item.get('typography', {}).get('interface', 'Ubuntu 11')
        gtk.write_text('[Settings]\n' + f"gtk-theme-name={item['theme']}\ngtk-icon-theme-name={item['icons']}\n"
                       f"gtk-cursor-theme-name={item['cursor']}\ngtk-font-name={ui_font}\n", encoding='utf-8')
        (run / 'home/.config/autostart').mkdir(parents=True)
        env.update({'LIBGL_ALWAYS_SOFTWARE': '1', 'LP_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'CLUTTER_DEFAULT_FPS': '15',
                    'GSETTINGS_BACKEND': 'dconf', 'XDG_CURRENT_DESKTOP': 'X-Cinnamon', 'XDG_SESSION_TYPE': 'x11',
                    'CURRENT_COLLECTION_HOST_DISPLAY': os.environ.get('DISPLAY', ''),
                    'CURRENT_COLLECTION_HOST_BUS': os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''),
                    'CURRENT_COLLECTION_HOST_HOME': str(Path.home()), 'CURRENT_COLLECTION_PANEL_STANDIN': standin_source})
        for key in ('SESSION_MANAGER', 'GTK_THEME', 'DCONF_PROFILE', 'CINNAMON_VERSION'):
            env.pop(key, None)
        screen = os.environ.get('CURRENT_COLLECTION_PREVIEW_SCREEN', DEFAULT_SCREEN)
        read_fd, write_fd = os.pipe()
        with (run / 'xserver.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(xserver_command(write_fd, screen, f"{item['name']} — rail rehearsal"), pass_fds=(write_fd,), stdout=log, stderr=log)
            os.close(write_fd)
            try:
                if not select.select([read_fd], [], [], 15)[0]: raise RuntimeError('Private display startup timed out')
                number = os.read(read_fd, 32).decode('utf-8').strip()
                if not number.isdigit(): raise RuntimeError('Private display startup failed')
                env['DISPLAY'] = ':' + number
                completed = subprocess.run(['dbus-run-session', '--', sys.executable, '-B', __file__, 'worker'], env=env, timeout=300)
                cleanup_fixture(run)
                return completed.returncode
            finally:
                os.close(read_fd)
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
    except Exception as error:
        failed = {'profile': slug, 'name': item.get('name'), 'status': 'blocked', 'reason': str(error), 'run': str(run),
                  **fingerprints, 'rehearsal': REHEARSAL, 'standin_source': standin_source,
                  'fixture_cleanup': 'Private HOME/XDG copy trees removed; report and X server log retained.',
                  'live_settings_modified': False}
        cleanup_fixture(run)
        (run / 'report.json').write_text(json.dumps(failed, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(failed), flush=True)
        return 2
    finally:
        runtime.cleanup()


# ------------------------------------------------------------------ worker (inside the private session)

def worker() -> int:
    from gi.repository import Gio, GLib
    run = Path(os.environ[ENV_RUN]); slug = os.environ[ENV_PROFILE]
    problems = privacy_problems(os.environ, run)
    if not GLib.get_user_config_dir().startswith(str(run) + '/'): problems.append('GLib config dir is outside the run')
    if problems: raise SystemExit('refusing to write: ' + '; '.join(problems))
    item = profile(slug)
    pl = load_panel_layout()
    spec = item['panel_layout']['panel']
    thickness, edge = rail_thickness(spec), rail_edge(spec)
    generated = Path(os.environ['CURRENT_COLLECTION_GENERATED_ROOT']) / slug
    wallpaper = generated / 'artwork' / 'wallpaper.png'
    fingerprints = report_fingerprints(pl, item)
    report = {'profile': slug, 'name': item.get('name'), 'status': 'running', 'run': str(run), **fingerprints,
              'rehearsal': REHEARSAL, 'standin_source': os.environ.get('CURRENT_COLLECTION_PANEL_STANDIN'),
              'preview_screen': os.environ.get('CURRENT_COLLECTION_PREVIEW_SCREEN', DEFAULT_SCREEN),
              'live_settings_modified': False, 'checks': [], 'layout': {'rail_px': thickness, 'edge': edge}, 'receipts': {}}
    state = run / 'rehearsal'  # outside the private HOME/XDG trees, so the receipts stay as evidence

    def setting(schema, key, value):
        s = Gio.Settings.new(schema)
        s.set_value(key, GLib.Variant(s.get_value(key).get_type_string(), value)); Gio.Settings.sync()

    def bus(method, *args):
        p = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path', '/org/Cinnamon', '--method', method, *args],
                           capture_output=True, text=True, timeout=6)
        p.check_returncode(); return p.stdout.strip()

    def evaluate(code):
        result = bus('org.Cinnamon.Eval', code)
        if not result.startswith('(true,'): raise RuntimeError(result)
        return result

    def panels(): return eval_json(evaluate(PANELS_JS))

    def loaded(): return eval_json(evaluate(LOADED_JS))

    measurements = {'samples': []}

    def measure(label):
        """Diagnostic that never raises: rail/zone/applet sizes into rail-measurements.json and a screenshot."""
        sample = {'label': label, 'time': time.strftime('%H:%M:%S')}
        try: sample.update(eval_json(evaluate(MEASURE_JS)))
        except Exception as error: sample['error'] = str(error)
        measurements['samples'].append(sample)
        try: (run / 'rail-measurements.json').write_text(json.dumps(measurements, indent=2) + '\n')
        except Exception: pass
        try: subprocess.run(['scrot', '--overwrite', str(run / ('rail-' + label + '.png'))], timeout=8, capture_output=True)
        except Exception: pass

    counter = [0]

    def journal(path, data): pl.atomic_save(path, data)

    def transition(target_slug, target_profile, kill=None):
        panel = pl.plan_transition(target_slug, target_profile, state, run / 'no-gate')  # this run produces the gate
        counter[0] += 1
        path = state / (time.strftime('%Y%m%d-%H%M%S') + '-%02d' % counter[0] + uuid.uuid4().hex[:10]) / 'receipt.json'
        path.parent.mkdir(parents=True)
        data = {'id': path.parent.name, 'profile': target_slug, 'status': 'applying', 'panel': panel}
        pl.prepare_receipt(path, panel); journal(path, data); pl.write_pointer(state, path)
        data['guard_armed'] = True; journal(path, data)  # no timer: the private session is disposable
        if kill:
            undo = kill_after(pl, kill)
            try: pl.apply_panel(path, data, journal, bus)
            except Killed as killed: return path, str(killed)
            finally: undo()
            raise RuntimeError('The simulated kill did not happen')
        pl.apply_panel(path, data, journal, bus)
        pl.mark_pending(panel); data['status'] = 'pending'; journal(path, data)
        return path, data

    def keep(path, data):
        drift = pl.panel_problems(data['panel'], 'after')
        if drift: raise RuntimeError(data['panel']['transition'] + ' drifted before keep: ' + ', '.join(drift))
        pl.mark_kept(data['panel']); data['status'] = 'kept'; journal(path, data)

    def restore(path, data=None):
        result, restored, after = live_restore_path(pl, path, state, journal, bus)
        if result['status'] != 'restored' or restored['panel']['phase'] != 'restored':
            raise RuntimeError('Panel restore (live path): ' + json.dumps(result) + ' errors ' + json.dumps(restored['panel'].get('restore_errors')))
        if after: raise RuntimeError('validate after the restore: ' + '; '.join(pl.explain_problems(restored['panel'], after)))
        return restored

    def standin_exact(label):
        rows = {key: standin[key] for key in pl.PANEL_APPLY_ORDER if key in standin}
        off = [key for key, printed in rows.items() if not pl.exact_setting({'schema': 'org.cinnamon', 'key': key}, pl.live_printed(key), printed)]
        if off: raise RuntimeError(label + ': the private layout differs from the copy: ' + ', '.join(off))

    def expect_state(expected):
        derived = pl.derive_state(state)['state']
        if derived != expected: raise RuntimeError('Owner state ' + derived + ' != ' + expected)

    def rail_geometry(label):
        rendered = panels()
        if rendered:
            report['layout'][label] = {'rail_height': rendered[0].get('height'), 'monitor_height': rendered[0].get('monitor_height'),
                                       'zone_heights': rendered[0].get('zone_heights'), 'content_height': sum(rendered[0].get('zone_heights') or [])}
        problems = rail_problems(rendered, thickness, edge)
        if problems: raise RuntimeError('Rail geometry (' + label + '): ' + '; '.join(problems))
        report['checks'].append(label + ': one ' + edge + ' rail ' + str(thickness) + ' px wide, height == monitor height ' +
                                str(report['layout'][label]['monitor_height']) + '; content ' + str(report['layout'][label]['content_height']) + ' px fits')

    def applets_loaded(entries, label):
        want = [a + ':' + str(i) for a, i in instances(entries)]
        have = loaded()
        missing = [x for x in want if x not in have]
        if any(x.split(':')[0] in PRIMARY for x in missing): raise RuntimeError(label + ': applets not loaded: ' + ', '.join(missing))
        if missing: report.setdefault('warnings', []).append(label + ': service-bound applets not loaded in the private session: ' + ', '.join(missing))
        report['checks'].append(label + ': applets loaded ' + str(len(want) - len(missing)) + '/' + str(len(want)))

    def rows_of(panel, field):
        return [GLib.Variant.parse(None, row[field], None, None).unpack() for row in panel['settings'] if row['key'] == 'enabled-applets'][0]

    standin = json.loads((run / 'standin-layout.json').read_text())
    cinnamon = Gio.Settings.new('org.cinnamon')
    for key, printed in standin.items(): cinnamon.set_value(key, GLib.Variant.parse(None, printed, None, None))
    setting('org.cinnamon', 'enabled-extensions', [])
    for schema in ('org.cinnamon.desktop.interface', 'org.gnome.desktop.interface'):
        setting(schema, 'gtk-theme', item['theme']); setting(schema, 'icon-theme', item['icons'])
        setting(schema, 'font-name', item.get('typography', {}).get('interface', 'Ubuntu 11'))
    setting('org.cinnamon.desktop.interface', 'cursor-theme', item['cursor'])
    setting('org.cinnamon.theme', 'name', item['theme'])
    setting('org.cinnamon.desktop.wm.preferences', 'theme', item['theme'])
    for schema in ('org.cinnamon.desktop.background', 'org.gnome.desktop.background'):
        setting(schema, 'picture-uri', wallpaper.as_uri())
    standin_panels = GLib.Variant.parse(None, standin['panels-enabled'], None, None).unpack()
    log = (run / 'cinnamon.log').open('w')
    procs = []
    try:
        if json.loads(os.environ.get('CURRENT_COLLECTION_PANEL_FINGERPRINTS', '{}')) != fingerprints:
            raise RuntimeError('panel_layout.py or the profile changed between the parent and the worker')
        pl.SETTLE_DELAY = 1.0  # a full Cinnamon answers each panel write; give it time in software rendering
        shell = subprocess.Popen(['cinnamon'], stdout=log, stderr=log); procs.append(shell)
        procs.append(subprocess.Popen(['/usr/libexec/csd-background', '--exit-time=250'], stdout=log, stderr=log))
        deadline = time.monotonic() + 45
        while True:
            try: bus('org.freedesktop.DBus.Peer.Ping'); break
            except Exception:
                if shell.poll() is not None or time.monotonic() > deadline: raise RuntimeError('Isolated Cinnamon did not start')
                time.sleep(1)
        time.sleep(8)
        report['checks'].append('private shell started on the copied layout ' + str(standin_panels))
        if pl.derive_state(state)['state'] != 'home': raise RuntimeError('A fresh rehearsal must start at home')

        # 0. kill-to-rail + recover-home: the live restore path after a mid-write kill (Cinnamon trimmed panel 2)
        k1, how = transition(slug, item, kill=KILL_AFTER)
        report['receipts']['kill-to-rail'] = str(k1)
        time.sleep(3); measure('after-kill-to-rail')
        report['layout']['kill_to_rail_zone_icon_sizes'] = pl.live_printed('panel-zone-icon-sizes')
        restored = restore(k1)
        time.sleep(3)
        standin_exact('recover-home')
        for raw, s in restored['panel']['snapshots'].items():
            if s['record']['kind'] == 'file' and pl.record(Path(raw)) != s['record']:
                raise RuntimeError('recover-home: settings file not byte-exact: ' + raw)
        if len(panels()) != len(standin_panels): raise RuntimeError('recover-home: panel count differs: ' + json.dumps(panels()))
        expect_state('home')
        report['checks'].append('kill-to-rail (' + how + ') recovered by the live restore path: copied layout exact, settings files byte-exact, validate clean')

        # 1. to-rail
        r1, d1 = transition(slug, item)
        report['receipts']['to-rail'] = str(r1)
        if d1['panel']['transition'] != 'to-rail': raise RuntimeError('Expected a to-rail, got ' + d1['panel']['transition'])
        time.sleep(3); measure('after-to-rail')
        calendar = d1['panel']['calendar']
        report['layout'].update(rail=rows_of(d1['panel'], 'after'), calendar_id=calendar['id'])
        rail_geometry('to-rail')
        applets_loaded(rows_of(d1['panel'], 'after'), 'to-rail')
        if calendar['uuid'] == 'calendar@cinnamon.org':
            label = eval_json(evaluate('imports.ui.appletManager.filterDefinitionsByUUID("calendar@cinnamon.org").filter(d=>d.applet_id==' +
                                       str(calendar['id']) + ')[0].applet._applet_label.get_text()'))
            report['layout']['calendar_label'] = label
            if calendar['settings'].get('custom-format') == '%H%n%M' and not re.fullmatch(r'[0-9]{2}\n[0-9]{2}', label):
                raise RuntimeError('Calendar label is not HH over MM: ' + repr(label))
            report['checks'].append('to-rail: calendar ' + str(calendar['id']) + ' renders ' + repr(label))
        path = Path(calendar['path'])
        if pl.record(path) != d1['panel']['files'][str(path)]['after']:
            raise RuntimeError('Cinnamon rewrote the new calendar settings file (md5/format mismatch)')
        report['checks'].append('to-rail: calendar settings file accepted by Cinnamon byte-for-byte (md5 ' + calendar['md5'] + ')')
        deleted = [raw for raw, snap in d1['panel']['snapshots'].items() if snap['record']['kind'] == 'file' and not Path(raw).exists()]
        report['layout']['cinnamon_deleted_on_removal'] = deleted
        report['checks'].append('to-rail: removed multi-instance settings files deleted by Cinnamon: ' + str(len(deleted)))
        keep(r1, d1); expect_state('rail')
        report['checks'].append('to-rail: kept; the owner state is rail')

        # kill-to-home + recover-rail: Cinnamon re-created panel 2 and seeded its zone entries before the kill
        k2, how = transition(NORMAL_SLUG, {'name': 'isolated normal profile'}, kill=KILL_AFTER)
        report['receipts']['kill-to-home'] = str(k2)
        time.sleep(3); measure('after-kill-to-home')
        report['layout']['kill_to_home_zone_icon_sizes'] = pl.live_printed('panel-zone-icon-sizes')
        restore(k2)
        time.sleep(3)
        pl.check_rail_live(d1['panel']['rail'])
        if pl.record(Path(calendar['path'])) != d1['panel']['files'][calendar['path']]['after']:
            raise RuntimeError('recover-rail: calendar settings file not byte-exact')
        rail_geometry('recover-rail')
        expect_state('rail')
        report['checks'].append('kill-to-home (' + how + ') recovered by the live restore path: the rail exact, calendar ' + str(calendar['id']) + ' byte-exact')

        # 2. to-home (a normal profile)
        r2, d2 = transition(NORMAL_SLUG, {'name': 'isolated normal profile'})
        report['receipts']['to-home'] = str(r2)
        if d2['panel']['transition'] != 'to-home': raise RuntimeError('Expected a to-home, got ' + d2['panel']['transition'])
        time.sleep(3); measure('after-to-home')
        drift = pl.panel_problems(d2['panel'], 'after')
        if drift: raise RuntimeError('to-home: the home layout did not land: ' + ', '.join(drift))
        for raw, row in d2['panel']['files'].items():
            if pl.record(Path(raw)) != row['after']: raise RuntimeError('to-home: home settings file not byte-exact: ' + raw)
        if len(panels()) != len(standin_panels): raise RuntimeError('to-home: panel count differs from the copied layout: ' + json.dumps(panels()))
        applets_loaded(rows_of(d2['panel'], 'after'), 'to-home')
        report['layout']['to_home_calendar_file_deleted_by_cinnamon'] = not path.exists()
        report['checks'].append('to-home: journaled home layout, zone arrays and desklets exact; ' + str(len(d2['panel']['files'])) +
                                ' home settings files byte-exact; next-applet-id kept at ' + str(cinnamon.get_int('next-applet-id')))
        keep(r2, d2); expect_state('home')

        # 3. undo of the to-home: the rail again, with the same calendar id and no new allocation
        restore(r2)
        time.sleep(3); measure('after-undo')
        pl.check_rail_live(d2['panel']['rail'])
        snap = d2['panel']['snapshots'][str(path)]
        if pl.record(path) != snap['record']: raise RuntimeError('undo: calendar settings file not restored byte-exact')
        if cinnamon.get_int('next-applet-id') != d1['panel']['next_applet_id']['after']: raise RuntimeError('undo: next-applet-id changed')
        if 'calendar@cinnamon.org:' + str(calendar['id']) not in loaded() and calendar['uuid'] == 'calendar@cinnamon.org':
            raise RuntimeError('undo: calendar ' + str(calendar['id']) + ' not re-added')
        rail_geometry('undo')
        report['checks'].append('undo: rail back with calendar ' + str(calendar['id']) + ' (same id, file byte-exact, no allocation)')
        expect_state('rail')

        # 4. restore of the to-rail: the copied layout, byte-exact settings files, counter kept
        restore(r1)
        time.sleep(3); measure('after-restore')
        drift = [row['key'] for row in d1['panel']['settings'] if not pl.same_setting(row, pl.live_value(row), row['before'])]
        if drift: raise RuntimeError('restore: the restored private layout differs from the copy: ' + ', '.join(drift))
        for raw, s in d1['panel']['snapshots'].items():
            if s['record']['kind'] == 'file' and pl.record(Path(raw)) != s['record']:
                raise RuntimeError('restore: removed applet settings file not byte-exact after Cinnamon re-added it: ' + raw)
        if path.exists(): raise RuntimeError('restore: new calendar settings file left: ' + str(path))
        if cinnamon.get_int('next-applet-id') != d1['panel']['next_applet_id']['after']: raise RuntimeError('restore: next-applet-id changed')
        restored = panels()
        if len(restored) != len(standin_panels): raise RuntimeError('restore: panel count differs: ' + json.dumps(restored))
        report['layout']['restored_panels'] = restored
        expect_state('home')
        report['checks'].append('restore: layout, zone arrays, desklets exact; settings files byte-exact; calendar file gone; next-applet-id kept')
        if shell.poll() is not None: raise RuntimeError('Private shell exited')
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'; report['error'] = str(error)
        if 'shell' in locals() and shell.poll() is None:
            time.sleep(5); measure('after-failure')
            report['diagnostics'] = [str(run / 'rail-measurements.json')]
    finally:
        for p in reversed(procs):
            if p.poll() is None: p.terminate()
        for p in reversed(procs):
            try: p.wait(timeout=4)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
        log.close()
        (run / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        latest = latest_report_path(Path(os.environ.get('CURRENT_COLLECTION_VERIFY_ROOT', DEFAULT_RUN)), slug)
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'passed' else 1


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == 'worker':
        return worker()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('profile', help='rail profile slug (it must carry panel_layout)')
    parser.add_argument('--candidate-file', help='unregistered profile JSON with a top-level profiles object')
    parser.add_argument('--generated-root', type=Path, help='generated tree root for a candidate')
    parser.add_argument('--output-root', type=Path, help='rehearsal run directory (the report goes to ../isolated-latest)')
    parser.add_argument('--screen', type=validate_screen, default=DEFAULT_SCREEN, help='private X screen (default 1600x1000)')
    parser.add_argument('--standin', type=Path, help='home layout JSON {"gsettings": {...}, "spices": {...}} instead of the live layout')
    args = parser.parse_args()
    if os.geteuid() == 0: raise RuntimeError('Run as the desktop user')
    if args.candidate_file: os.environ['CURRENT_COLLECTION_PROFILE_CANDIDATES'] = str(Path(args.candidate_file).resolve())
    if args.generated_root: os.environ['CURRENT_COLLECTION_GENERATED_ROOT'] = str(args.generated_root.resolve())
    if args.output_root: os.environ['CURRENT_COLLECTION_VERIFY_ROOT'] = str(args.output_root.resolve())
    os.environ['CURRENT_COLLECTION_PREVIEW_SCREEN'] = args.screen
    return parent(args.profile, args.standin)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(2)
