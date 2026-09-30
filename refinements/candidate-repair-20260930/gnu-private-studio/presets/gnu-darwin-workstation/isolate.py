#!/usr/bin/python3
"""One bounded Cinnamon + GTK preview of GNU-Darwin Workstation on a private X display and settings bus.

The real desktop, its D-Bus session and its dconf database are never touched: a private HOME,
XDG directories, a dbus-run-session bus (with its own dconf service writing under the private
XDG_CONFIG_HOME) and a private X server (Xvfb when installed, otherwise a nested Xephyr window) host
the render. The parent only READS the user's current panel layout and copies the listed applets'
code and settings files into the private tree. The private shell starts on that copy of the layout;
desktop_control.py's own panel writer then turns it into the right64 launcher rail and bottom64 application row (with the new
calendar@cinnamon.org instance and its custom '%H:%M' format), the taskbar is checked and captured, and
the controller's own restore is rehearsed back to the copied layout. The report is the source gate
desktop_control.py requires before a live trial (verification/isolated-latest.json).
"""
import argparse
from pathlib import Path
import hashlib, json, os, re, select, shutil, subprocess, sys, tempfile, time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
NAME = 'GNU-Darwin Workstation'
MONITOR_UUID = 'gnu-darwin-workstation-monitor@desktop-theme-studio'
SOURCE = ROOT / 'desktop' / NAME
DESIGN = json.loads((ROOT / 'design.json').read_text())
LAUNCHER_COUNT = len(json.loads((ROOT / 'lineage.json').read_text())['desktop_control']['panel']['new_instances'][0]['settings']['launcherList'])
PANEL_HEIGHTS = {int(row.split(':')[0]): int(row.split(':')[1]) for row in DESIGN['panel_layout']['panels-height']}

def design_appearance():
    typography = DESIGN['typography']
    def font(family, size, weight=400):
        style = ' Bold' if weight >= 700 else (' Medium' if weight >= 500 else '')
        return f'{family}{style} {size}'
    return {'ui_font': font(typography['ui_family'], typography['ui_size_pt']),
            'document_font': font(typography['ui_family'], typography['ui_size_pt']),
            'monospace_font': font(typography['monospace_family'], typography['monospace_size_pt']),
            'title_font': font(typography['title_family'], typography['title_size_pt'], typography['title_weight']),
            'cursor_size': int(DESIGN['geometry']['cursor_size']), 'prefer_dark': False,
            'button_layout': DESIGN['geometry']['button_layout']}

def color_scheme_preference(allowed):
    allowed = set(allowed)
    return 'prefer-light' if 'prefer-light' in allowed else ('default' if 'default' in allowed else None)

APPEARANCE = design_appearance()
SCREEN = (2880, 1800)
RUN = Path(os.environ.get('GNU_DARWIN_WORKSTATION_TEST_RUN', ROOT / 'verification' / time.strftime('isolated-%Y%m%d-%H%M%S')))
# Keys the parent copies from the live session into the private one (read-only on the live side).
LAYOUT_KEYS = ('panel-launchers', 'enabled-applets', 'panels-enabled', 'panels-height', 'panels-autohide', 'panels-show-delay', 'panels-hide-delay',
               'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes', 'panel-zone-text-sizes', 'enabled-desklets', 'next-applet-id')
PRIMARY = (MONITOR_UUID, 'panel-launchers@cinnamon.org', 'menu@cinnamon.org', 'grouped-window-list@cinnamon.org', 'calendar@cinnamon.org')
# Diagnostic only (never part of the pass criteria): allocation/preferred sizes of the taskbar, its three zone boxes
# and every applet actor down to its tiles/buttons/icons, keyed by uuid:instance, plus monitor, scale and fonts.
MEASURE_JS = r'''(() => {
  const Main = imports.ui.main, AM = imports.ui.appletManager, St = imports.gi.St, Gio = imports.gi.Gio, Clutter = imports.gi.Clutter;
  const safe = f => { try { return f(); } catch (e) { return 'error: ' + e; } };
  const box = a => safe(() => { const b = a.get_allocation_box(); return {x: b.x1, y: b.y1, w: b.x2 - b.x1, h: b.y2 - b.y1}; });
  const pref = a => safe(() => { const [minH, natH] = a.get_preferred_height(-1); const [minW, natW] = a.get_preferred_width(-1); return {minH, natH, minW, natW}; });
  const node = a => safe(() => { const n = a.get_theme_node(); const side = k => n['get_' + k](St.Side.TOP) + '/' + n['get_' + k](St.Side.RIGHT) + '/' + n['get_' + k](St.Side.BOTTOM) + '/' + n['get_' + k](St.Side.LEFT);
                                  return {spacing: n.get_length('spacing'), padding: side('padding'), margin: side('margin'), border: side('border_width'), font: n.get_font().to_string()}; });
  const owner = new Map();
  AM.definitions.forEach(d => { if (d.applet && d.applet.actor) owner.set(d.applet.actor, d.uuid + ':' + d.applet_id); });
  const desc = (a, depth) => {
    const o = {actor: String(a).slice(0, 90), style: safe(() => a.get_style_class_name ? a.get_style_class_name() : null), alloc: box(a), pref: pref(a)};
    if (owner.has(a)) o.key = owner.get(a);
    if (a instanceof St.Widget) o.theme = node(a);
    if (depth > 0) o.children = a.get_children().filter(c => c.visible).map(c => desc(c, depth - 1));
    return o;
  };
  const panels = Main.panelManager.panels.filter(x => x);
  const iface = new Gio.Settings({schema_id: 'org.cinnamon.desktop.interface'});
  return {
    screen: {w: global.screen_width, h: global.screen_height},
    ui_scale: global.ui_scale, theme_scale: St.ThemeContext.get_for_stage(global.stage).scale_factor,
    theme_font: safe(() => St.ThemeContext.get_for_stage(global.stage).get_font().to_string()),
    clutter_resolution: safe(() => Clutter.Backend ? Clutter.get_default_backend().get_resolution() : null),
    text_scaling_factor: iface.get_double('text-scaling-factor'), scaling_factor: iface.get_uint('scaling-factor'),
    font_name: iface.get_string('font-name'),
    panels: panels.map(p => ({
    monitor: safe(() => global.display.get_monitor_geometry(p.monitorIndex)),
    panel: {id: p.panelId, pos: p.panelPosition, thickness_setting: safe(() => p._getProperty('panels-height', 'i')),
            actor_width: p.actor.width, actor_height: p.actor.height, alloc: box(p.actor), pref: pref(p.actor),
            fixed: {natural_height: p.actor.natural_height, natural_height_set: p.actor.natural_height_set, min_height_set: p.actor.min_height_set},
            toppanelHeight: p.toppanelHeight, bottompanelHeight: p.bottompanelHeight,
            margins: [p.margin_top, p.margin_right, p.margin_bottom, p.margin_left], theme: node(p.actor), children: p.actor.get_n_children()},
    zones: Object.fromEntries([['panelLeft', p._leftBox], ['panelCenter', p._centerBox], ['panelRight', p._rightBox]].map(([name, z]) =>
      [name, {alloc: box(z), pref: pref(z), theme: node(z), applets: z.get_children().filter(c => c.visible).map(c => desc(c, 4))}]))
    }))
  };
})()'''


def validate_screen(value: str) -> str:
    match = re.fullmatch(r"([0-9]{3,5})x([0-9]{3,5})", value)
    if not match or not 640 <= int(match.group(1)) <= 16384 or not 480 <= int(match.group(2)) <= 16384:
        raise argparse.ArgumentTypeError("screen must be WIDTHxHEIGHT within 640x480..16384x16384")
    return value

def ensure_private_screen(requested, private_display, host_display, *, run=subprocess.run, sleep=time.sleep):
    """Restore Cinnamon's private Xephyr framebuffer to the requested review size."""
    requested = validate_screen(requested)
    if (not private_display or not host_display
            or private_display.split(".")[0] == host_display.split(".")[0]):
        raise RuntimeError("Refusing framebuffer resize without a distinct private display")
    expected = tuple(int(part) for part in requested.split("x"))

    def command(args):
        result = run(args, capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError("Private framebuffer command failed: " + result.stderr[-1000:])
        return result.stdout

    def geometry(query):
        match = re.search(r"current\s+(\d+)\s+x\s+(\d+)", query)
        if not match:
            raise RuntimeError("Private RandR did not report framebuffer geometry")
        return tuple(int(value) for value in match.groups())

    before = command(["xrandr", "--query"])
    result = {"requested": requested, "before": list(geometry(before)),
              "query_before": before.splitlines()[:10], "resized": False}
    if tuple(result["before"]) != expected:
        command(["xrandr", "--output", "default", "--mode", requested])
        result["resized"] = True
        sleep(1)
    after = command(["xrandr", "--query"])
    result.update(after=list(geometry(after)), query_after=after.splitlines()[:10])
    if tuple(result["after"]) != expected:
        raise RuntimeError("Private framebuffer differs from requested review size: " + json.dumps(result))
    return result

WORKSPACE_RUNTIME = ROOT.parent.parent / 'refinements/cinnamon-current-collection/runtime/workspace-switcher@cinnamon.org'

def workspace_runtime_manifest(path=WORKSPACE_RUNTIME):
    files={str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob('*')) if p.is_file()}
    if set(files)!={'applet.js','metadata.json','settings-schema.json'}:raise RuntimeError('Unexpected native workspace runtime files')
    digest=hashlib.sha256()
    for key,value in sorted(files.items()):digest.update(key.encode());digest.update(bytes.fromhex(value))
    return {'source':str(path),'files':files,'tree_sha256':digest.hexdigest()}

def owned_workspace_probe():
    import importlib.util
    spec=importlib.util.spec_from_file_location('owned_workspace_probe', ROOT/'desktop/workspace_probe.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def fingerprint():
    h = hashlib.sha256()
    for p in sorted(SOURCE.rglob('*')):
        if p.is_file():
            h.update(str(p.relative_to(SOURCE)).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def instances(entries):
    """[(uuid, id)] from Cinnamon applet entries; only the optional orient override is accepted."""
    pattern = re.compile(r'panel([0-9]+):(left|center|right|top|bottom):([0-9]+):([A-Za-z0-9][A-Za-z0-9@._+-]*):([0-9]+)(?::(orient))?')
    out = []
    for entry in entries:
        match = pattern.fullmatch(entry)
        if not match: raise RuntimeError('Unsupported enabled-applets entry: ' + entry)
        out.append((match.group(4), int(match.group(5))))
    return out


def eval_json(result):
    """Decode a gdbus-printed org.Cinnamon.Eval answer '(true, <JSON.stringify(value)>)' into the JS value.

    The JS expressions below return plain values (never pre-stringified), so exactly one JSON layer exists."""
    from gi.repository import GLib
    ok, text = GLib.Variant.parse(None, result, None, None).unpack()
    if not ok: raise RuntimeError('Eval failed: ' + text)
    return json.loads(text)


def taskbar_problems(panels):
    """Right rail excludes the bottom-owned corner; all content fits both64px actors."""
    if len(panels) != 2 or {p.get('id') for p in panels} != {1, 2}: return ['expected exactly panels1/2']
    out = []
    for p in panels:
        pid = p['id']; mw = p.get('monitor_width'); mh = p.get('monitor_height')
        if not isinstance(mw, (int,float)) or not isinstance(mh, (int,float)): return ['monitor dimensions missing']
        if p.get('pos') != (3 if pid == 1 else 1): out.append('wrong panel edge')
        if pid == 1:
            if mh - 64 < LAUNCHER_COUNT * 64: out.append('available right rail height cannot fit canonical launcher tiles')
            if (p.get('width'),p.get('height'),p.get('x'),p.get('y')) != (64,mh-64,mw-64,0): out.append('right rail/corner geometry differs')
            z=p.get('zone_heights',[])
            if len(z)!=3 or sum(z)>mh-64: out.append('right rail content overflows')
        else:
            if (p.get('width'),p.get('height'),p.get('x'),p.get('y')) != (mw,64,0,mh-64): out.append('bottom row/corner geometry differs')
            z=p.get('zone_heights',[])
            if len(z)!=3 or max(z)>64: out.append('bottom row content overflows')
        for tile in p.get('launcher_tiles',[]):
            if tile.get('width') != 64 or tile.get('height') != 64: out.append('launcher tile differs from64px square')
        if pid == 1 and len(p.get('launcher_tiles',[])) != LAUNCHER_COUNT: out.append('visible launcher tile count differs from canonical launcher list')
    return out


def monitor_samples_valid(first, second):
    if not (second.get('sequence',0)>first.get('sequence',0) and second.get('timestamp',0)>first.get('timestamp',0)
            and second.get('disposed') is False and second.get('sourceId',0)>0): return False
    for key in ('cpuPercent','memoryPercent'):
        value=second.get(key)
        if not isinstance(value,(int,float)) or not 0<=value<=100: return False
    for key in ('rxBytesPerSec','txBytesPerSec'):
        value=second.get(key)
        if value is not None and (not isinstance(value,(int,float)) or value<0): return False
    return True


def monitor_cleanup_valid(first, second):
    return (first.get('disposed') is True and second.get('disposed') is True and first.get('sourceId') == second.get('sourceId') == 0
            and first.get('sequence') == second.get('sequence'))


def maximized_ready(state):
    return (state.get('maximized') == 3 and state.get('visible') is True and state.get('mapped') is True
            and state.get('opacity') == 255 and state.get('frame') == state.get('workarea'))


def preview_windows_ready(actors, preview_pid):
    mapped = [a for a in actors if a.get('visible') is True and a.get('mapped') is True
              and a.get('opacity') == 255 and a.get('width', 0) > 100 and a.get('height', 0) > 100]
    return (any(a.get('pid') == preview_pid for a in mapped)
            and any(str(a.get('wm_class', '')).lower() == 'nemo' and 'Files' in str(a.get('title', '')) for a in mapped))


def popup_ready(state):
    return (state.get('isOpen') is True and state.get('visible') is True and state.get('mapped') is True
            and state.get('opacity') == 255 and all(isinstance(state.get(k), (int, float)) and state[k] > 20
                                                 for k in ('width', 'height')))


def xserver(fd):
    """Prefer headless Xvfb; fall back to a nested Xephyr window. Env GNU_DARWIN_WORKSTATION_XSERVER overrides."""
    choice = os.environ.get('GNU_DARWIN_WORKSTATION_XSERVER') or ('Xvfb' if shutil.which('Xvfb') else 'Xephyr')
    size = str(SCREEN[0]) + 'x' + str(SCREEN[1])
    if choice == 'Xvfb':
        return ['Xvfb', '-displayfd', str(fd), '-screen', '0', size + 'x24', '-nolisten', 'tcp', '-noreset']
    return ['Xephyr', '-displayfd', str(fd), '-screen', size, '-nolisten', 'tcp', '-noreset', '-no-host-grab',
            '-title', NAME + ' — isolated preview']


def copy_live_layout():
    """READ-ONLY on the live side: the user's panel keys and the listed applets' code/settings, copied into RUN."""
    from gi.repository import Gio, GLib
    saved = os.environ.get('GNU_DARWIN_WORKSTATION_LAYOUT_FIXTURE')
    if saved:
        fixture=Path(saved).resolve()
        if fixture.parent != (ROOT/'verification').resolve():
            raise RuntimeError('Saved layout must be a retained preset verification run')
        previous=json.loads((fixture/'report.json').read_text())
        if previous['status']!='passed':raise RuntimeError('Saved layout has no passing restoration record')
        layout=json.loads((fixture/'standin-layout.json').read_text())
        (RUN/'standin-layout.json').write_text(json.dumps(layout,indent=2)+'\n')
        shutil.copytree(fixture/'config/cinnamon/spices',RUN/'config/cinnamon/spices')
        shutil.copytree(fixture/'data/cinnamon/applets',RUN/'data/cinnamon/applets')
        (RUN/'layout-fixture.json').write_text(json.dumps({'source':str(fixture),'scope':'retained original layout, restored by previous passing test'},indent=2)+'\n')
        return layout
    cinnamon = Gio.Settings.new('org.cinnamon')
    listed = cinnamon.props.settings_schema.list_keys()
    layout = {key: cinnamon.get_value(key).print_(True) for key in LAYOUT_KEYS if key in listed}
    (RUN / 'standin-layout.json').write_text(json.dumps(layout, indent=2) + '\n')
    live_config = Path(GLib.get_user_config_dir()) / 'cinnamon/spices'
    live_applets = Path(GLib.get_user_data_dir()) / 'cinnamon/applets'
    entries = GLib.Variant.parse(None, layout['enabled-applets'], None, None).unpack()
    for applet, ident in instances(entries):
        source = live_config / applet / (str(ident) + '.json')
        if source.is_file() and not source.is_symlink():
            target = RUN / 'config/cinnamon/spices' / applet / source.name
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        code = live_applets / applet
        if code.is_dir() and not (RUN / 'data/cinnamon/applets' / applet).exists():
            shutil.copytree(code, RUN / 'data/cinnamon/applets' / applet, symlinks=True)
    return layout


def parent():
    RUN.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env['GNU_DARWIN_WORKSTATION_TEST_RUN'] = str(RUN)
    for key, directory in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                           ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
        p = RUN / directory; p.mkdir(mode=0o700); env[key] = str(p)
    runtime = tempfile.TemporaryDirectory(prefix='gnu_darwin_workstation-preview-')
    env['XDG_RUNTIME_DIR'] = runtime.name
    shutil.copytree(SOURCE, RUN / 'data/themes' / NAME)
    for suffix in (' icons', ' cursors'):
        shutil.copytree(ROOT / 'desktop' / (NAME + suffix), RUN / 'data/icons' / (NAME + suffix), symlinks=True)
    copy_live_layout()
    # Include the actual user-overridden native pager even when currently disabled.
    from gi.repository import GLib
    workspace_code=Path(GLib.get_user_data_dir())/'cinnamon/applets/workspace-switcher@cinnamon.org'
    workspace_copy=RUN/'data/cinnamon/applets/workspace-switcher@cinnamon.org'
    if workspace_code.is_dir() and not workspace_copy.exists():
        shutil.copytree(workspace_code,workspace_copy,symlinks=True)
    shutil.copytree(WORKSPACE_RUNTIME,workspace_copy,dirs_exist_ok=True)
    # Match the menu stage's result while deriving the user's existing menu instance from the copied layout.
    standin = json.loads((RUN / 'standin-layout.json').read_text())
    from gi.repository import GLib
    menu_entries = GLib.Variant.parse(None, standin['enabled-applets'], None, None).unpack()
    menu_id = next((int(e.split(':')[4]) for e in menu_entries if len(e.split(':')) == 5 and e.split(':')[3] == 'menu@cinnamon.org'), None)
    if menu_id is None: raise RuntimeError('Copied Cinnamon layout has no menu@cinnamon.org instance')
    menu_target = RUN / 'config/cinnamon/spices/menu@cinnamon.org' / (str(menu_id) + '.json')
    if menu_target.is_file():
        menu_settings = json.loads(menu_target.read_text())
        menu_settings['menu-icon']['value'] = str(ROOT / 'artwork/menu-logo.png')
        menu_target.write_text(json.dumps(menu_settings, indent=4, ensure_ascii=False))
    gtk_config = RUN / 'config/gtk-3.0/settings.ini'
    gtk_config.parent.mkdir(parents=True, exist_ok=True)
    gtk_config.write_text('[Settings]\ngtk-theme-name=' + NAME + '\ngtk-icon-theme-name=' + NAME + ' icons\ngtk-cursor-theme-name=' + NAME + ' cursors\ngtk-cursor-theme-size=' + str(APPEARANCE['cursor_size']) + '\ngtk-font-name=' + APPEARANCE['ui_font'] + '\ngtk-application-prefer-dark-theme=0\ngtk-decoration-layout=' + APPEARANCE['button_layout'] + '\n')
    (RUN / 'home/.config/autostart').mkdir(parents=True)
    env.update(LIBGL_ALWAYS_SOFTWARE='1', LP_NUM_THREADS='1', OMP_NUM_THREADS='1',
               CLUTTER_DEFAULT_FPS='15', GSETTINGS_BACKEND='dconf',
               XDG_CURRENT_DESKTOP='X-Cinnamon', XDG_SESSION_TYPE='x11',
               GNU_DARWIN_WORKSTATION_PREVIEW='1', GNU_DARWIN_WORKSTATION_HOST_DISPLAY=os.environ.get('DISPLAY', ''),
               GNU_DARWIN_WORKSTATION_HOST_BUS=os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''),
               GNU_DARWIN_WORKSTATION_HOST_HOME=str(Path.home()))
    for key in ('SESSION_MANAGER', 'GTK_THEME', 'DCONF_PROFILE', 'CINNAMON_VERSION', 'GNOME_TERMINAL_SERVICE', 'GNOME_TERMINAL_SCREEN', 'DBUS_STARTER_ADDRESS', 'DBUS_STARTER_BUS_TYPE', 'ENV', 'BASH_ENV'):
        env.pop(key, None)
    # Use the same bounded software Mesa renderer as the accepted Cryostat preview.
    mesa_vendor = Path('/usr/share/glvnd/egl_vendor.d/50_mesa.json')
    if mesa_vendor.is_file():
        env.update(__EGL_VENDOR_LIBRARY_FILENAMES=str(mesa_vendor), __GLX_VENDOR_LIBRARY_NAME='mesa')
    r, w = os.pipe()
    with (RUN / 'xserver.log').open('w') as log:
        server = subprocess.Popen(xserver(w), pass_fds=(w,), stdout=log, stderr=log, env=env)
        os.close(w)
        try:
            if not select.select([r], [], [], 15)[0]:
                raise RuntimeError('Private display startup timed out')
            number = os.read(r, 32).decode().strip()
            if not number.isdigit():
                raise RuntimeError('Private display startup failed')
            env['DISPLAY'] = ':' + number
            result = subprocess.run(['dbus-run-session', '--', sys.executable, '-B', __file__, 'worker'], env=env, timeout=240)
            return result.returncode
        finally:
            os.close(r); server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            runtime.cleanup()


def worker():
    from gi.repository import Gio, GLib
    assert os.environ['DISPLAY'] != os.environ['GNU_DARWIN_WORKSTATION_HOST_DISPLAY']
    assert os.environ['DBUS_SESSION_BUS_ADDRESS'] != os.environ['GNU_DARWIN_WORKSTATION_HOST_BUS']
    assert Path.home() != Path(os.environ['GNU_DARWIN_WORKSTATION_HOST_HOME']) and str(Path.home()).startswith(str(RUN))
    assert os.environ['XDG_CONFIG_HOME'].startswith(str(RUN))
    def setting(schema, key, value):
        s = Gio.Settings.new(schema)
        s.set_value(key, GLib.Variant(s.get_value(key).get_type_string(), value)); Gio.Settings.sync()
    def bus(method, *args):
        p = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.Cinnamon', '--object-path',
                            '/org/Cinnamon', '--method', method, *args], capture_output=True, text=True, timeout=6)
        p.check_returncode(); return p.stdout.strip()
    def evaluate(code):
        result = bus('org.Cinnamon.Eval', code)
        if not result.startswith('(true,'):
            raise RuntimeError(result)
        return result
    def panels():
        return eval_json(evaluate('imports.ui.main.panelManager.panels.filter(p=>p).map(p=>({id:p.panelId,pos:p.panelPosition,height:p.actor.height,width:p.actor.width,'
                                  'monitor_width:global.display.get_monitor_geometry(p.monitorIndex).width,monitor_height:global.display.get_monitor_geometry(p.monitorIndex).height,'
                                  'x:p.actor.x-global.display.get_monitor_geometry(p.monitorIndex).x,y:p.actor.y-global.display.get_monitor_geometry(p.monitorIndex).y,'
                                  'launcher_tiles:p.panelId===1 ? imports.ui.appletManager.filterDefinitionsByUUID("panel-launchers@cinnamon.org").filter(d=>d.applet).reduce((all,d)=>all.concat(d.applet._launchers),[]).filter(l=>l.actor.visible).map(l=>({width:l.actor.width,height:l.actor.height})) : [],'
                                  'background_alpha:p.actor.get_theme_node().get_background_color().alpha,'
                                  'dock:{x:p._centerBox.x,width:p._centerBox.width,background_alpha:p._centerBox.get_theme_node().get_background_color().alpha},'
                                  'zone_heights:[p._leftBox,p._centerBox,p._rightBox].map(z=>z.get_preferred_height(-1)[1])}))'))
    def loaded():
        return eval_json(evaluate("imports.ui.appletManager.definitions.filter(d=>d.applet).map(d=>d.uuid+':'+d.applet_id)"))
    measurements = {'xserver': xserver(0)[0], 'samples': []}
    def measure(label):
        """Diagnostic that never raises: taskbar/zone/applet sizes into taskbar-measurements.json and a screenshot taskbar.png."""
        sample = {'label': label, 'time': time.strftime('%H:%M:%S')}
        try: sample.update(eval_json(evaluate(MEASURE_JS)))
        except Exception as error: sample['error'] = str(error)
        measurements['samples'].append(sample)
        try: (RUN / 'taskbar-measurements.json').write_text(json.dumps(measurements, indent=2) + '\n')
        except Exception: pass
        try: subprocess.run(['scrot', '--overwrite', str(RUN / ('taskbar.png' if len(measurements['samples']) == 1 else 'taskbar-' + label + '.png'))], timeout=8, capture_output=True)
        except Exception: pass
    # The private dconf starts as a copy of the user's current layout (read by the parent), so the
    # controller's strict instance checks and Cinnamon's own add/remove behaviour run as they will live.
    standin = json.loads((RUN / 'standin-layout.json').read_text())
    cinnamon = Gio.Settings.new('org.cinnamon')
    for key, printed in standin.items(): cinnamon.set_value(key, GLib.Variant.parse(None, printed, None, None))
    setting('org.cinnamon', 'enabled-extensions', [])
    Gio.Settings.sync()
    import desktop_control as control
    appearance = control.design_appearance()
    for schema in ('org.cinnamon.desktop.interface', 'org.gnome.desktop.interface'):
        obj = Gio.Settings.new(schema)
        available = set(obj.props.settings_schema.list_keys())
        values = {'gtk-theme': NAME, 'icon-theme': NAME + ' icons', 'cursor-theme': NAME + ' cursors',
                  'cursor-size': appearance['cursor_size'], 'font-name': appearance['ui_font'],
                  'document-font-name': appearance['document_font'],
                  'monospace-font-name': appearance['monospace_font'],
                  'gtk-application-prefer-dark-theme': appearance['prefer_dark']}
        if 'color-scheme' in available:
            kind, choices = obj.props.settings_schema.get_key('color-scheme').get_range().unpack()
            if kind == 'enum':
                scheme = control.color_scheme_preference(choices)
                if scheme is not None: values['color-scheme'] = scheme
        for key, value in values.items():
            if key in available: setting(schema, key, value)
    setting('org.cinnamon.desktop.wm.preferences', 'titlebar-font', appearance['title_font'])
    for schema in ('org.cinnamon.desktop.wm.preferences', 'org.gnome.desktop.wm.preferences'):
        setting(schema, 'button-layout', appearance['button_layout'])
    setting('org.cinnamon.theme', 'name', NAME)
    setting('org.cinnamon.desktop.wm.preferences', 'theme', NAME)
    setting('org.cinnamon.desktop.background', 'picture-uri', (ROOT / 'artwork/wallpaper.png').as_uri())
    setting('org.gnome.desktop.background', 'picture-uri', (ROOT / 'artwork/wallpaper.png').as_uri())
    log = (RUN / 'cinnamon.log').open('w')
    procs = []
    report = {'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'workspace_probe_sha256':hashlib.sha256((ROOT/'desktop/workspace_probe.py').read_bytes()).hexdigest(), 'workspace_runtime':workspace_runtime_manifest(), 'status': 'running', 'theme': NAME, 'source_sha256': fingerprint(), 'run': str(RUN), 'checks': [],
              'wallpaper_method': 'Private X root backdrop painted by feh --bg-fill; not native Cinnamon wallpaper verification',
              'layout': {'panel_heights': PANEL_HEIGHTS, 'standin_panels': standin.get('panels-enabled')}}
    try:
        import desktop_control as control
        # Gate coverage: the taskbar code and data this run exercises (compared by desktop_control.validate_source).
        report['taskbar_sha256'] = control.taskbar_fingerprint()
        control.SETTLE_DELAY = 1.0  # a full Cinnamon answers each panel write; give it time in software rendering
        shell = subprocess.Popen(['cinnamon'], stdout=log, stderr=log); procs.append(shell)
        procs.append(subprocess.Popen(['/usr/libexec/csd-background', '--exit-time=150'], stdout=log, stderr=log))
        deadline = time.monotonic() + 45
        while True:
            try: bus('org.freedesktop.DBus.Peer.Ping'); break
            except Exception:
                if shell.poll() is not None or time.monotonic() > deadline: raise
                time.sleep(1)
        time.sleep(3)
        report['private_framebuffer']=ensure_private_screen('2880x1800',os.environ['DISPLAY'],os.environ['GNU_DARWIN_WORKSTATION_HOST_DISPLAY'])
        report['checks'].append('private shell started on the copied layout ' + str(standin.get('panels-enabled')))
        # The taskbar, written by the controller's own panel code path (plan -> journal -> apply) into the private dconf.
        receipt = RUN / 'panel-rehearsal.json'
        panel = control.panel_targets()
        data = {'id': 'isolated', 'status': 'pending', 'guard_armed': True,  # no timer: the private session is disposable
                'settings': [], 'files': {}, 'assets': [], 'opacity_active': False, 'panel': {**panel, 'phase': 'journaled'}}
        # Exercise the normal asset transaction in private XDG, including its eventual removal.
        monitor_destination = Path(GLib.get_user_data_dir()) / 'cinnamon/applets' / MONITOR_UUID
        original_assets = control.asset_locations
        try:
            control.asset_locations = lambda: [(control.MONITOR_SOURCE, monitor_destination)]
            data['assets'] = control.prepare_assets(receipt, 'monitor-private')
        finally: control.asset_locations = original_assets
        control.save(receipt, data)
        control.swap_assets(receipt, data)
        control.apply_panel(receipt, data)
        time.sleep(3)
        active = Gio.Settings.new('org.cinnamon')
        if active.get_strv('panels-enabled') != DESIGN['panel_layout']['panels-enabled'] or active.get_strv('panels-height') != DESIGN['panel_layout']['panels-height']:
            raise RuntimeError('Active panel keys differ from the exact bottom-taskbar fixture')
        if active.get_strv('enabled-applets') != panel['taskbar']:
            raise RuntimeError('Active applet order/zones differ from the exact taskbar fixture')
        measure('after-apply+3s')  # diagnostic only
        calendar = Path(panel['calendar']['path'])
        report['layout'].update(taskbar=panel['taskbar'], calendar_id=panel['calendar']['id'])
        for schema in ('org.cinnamon.desktop.wm.preferences', 'org.gnome.desktop.wm.preferences'):
            if Gio.Settings.new(schema).get_string('button-layout') != appearance['button_layout']:
                raise RuntimeError('Private WM button layout differs from canonical appearance')
        report['appearance'] = appearance
        rendered = panels()
        report['layout']['rendered_panels'] = rendered
        if rendered:
            report['layout'].update(taskbar_height=rendered[0].get('height'), taskbar_width=rendered[0].get('width'),
                                    monitor_width=rendered[0].get('monitor_width'), zone_heights=rendered[0].get('zone_heights'))
        problems = taskbar_problems(rendered)
        if problems: raise RuntimeError('Taskbar geometry: ' + '; '.join(problems))
        report['checks'].append('exact right64/bottom64 actors; bottom owns corner; canonical square launcher tiles')
        want = [a + ':' + str(i) for a, i in instances(panel['taskbar'])]
        have = loaded()
        missing = [x for x in want if x not in have]
        report['layout']['loaded_applets'] = have
        if any(x.split(':')[0] in PRIMARY for x in missing): raise RuntimeError('Taskbar applets not loaded: ' + ', '.join(missing))
        if missing: report.setdefault('warnings', []).append('service-bound taskbar applets not loaded in the private session: ' + ', '.join(missing))
        report['checks'].append('taskbar applets loaded in order: ' + str(len(want) - len(missing)) + '/' + str(len(want)))
        monitor_definition = 'imports.ui.appletManager.filterDefinitionsByUUID(' + json.dumps(MONITOR_UUID) + ').find(d=>d.applet)'
        evaluate('global.__workstationMonitorProbe=' + monitor_definition + '.applet; true')
        def monitor_snapshot():
            return eval_json(evaluate('global.__workstationMonitorProbe.getMetricsSnapshot()'))
        geometry = eval_json(evaluate('(() => {const a=global.__workstationMonitorProbe.actor;return {width:a.width,height:a.height};})()'))
        if geometry != {'width':256,'height':64}: raise RuntimeError('Monitor geometry must be256x64: ' + json.dumps(geometry))
        first = monitor_snapshot(); deadline_monitor = time.monotonic() + 5
        while time.monotonic() < deadline_monitor:
            time.sleep(.3); second = monitor_snapshot()
            if monitor_samples_valid(first, second): break
        else: raise RuntimeError('Real monitor samples did not advance: ' + json.dumps([first,second]))
        report['monitor'] = {'geometry':geometry,'samples':[first,second]}
        report['checks'].append('monitor256x64 and advancing realCPU/MEM/RX/TX snapshots')
        label = eval_json(evaluate('imports.ui.appletManager.filterDefinitionsByUUID("calendar@cinnamon.org").filter(d=>d.applet_id==' +
                                   str(panel['calendar']['id']) + ')[0].applet._applet_label.get_text()'))
        report['layout']['calendar_label'] = label
        if not re.fullmatch(r'[0-9]{2}:[0-9]{2}', label): raise RuntimeError('Calendar label is not HH:MM: ' + repr(label))
        report['checks'].append('calendar clock renders HH:MM (%H:%M)')
        for instance in panel['new_instances']:
            if control.record(Path(instance['path'])) != panel['files'][instance['path']]['after']:
                raise RuntimeError('Cinnamon rewrote new instance settings: ' + instance['uuid'])
        report['layout']['new_instances'] = panel['new_instances']
        report['checks'].append('all three new instance settings files accepted byte-exact')
        deleted = [raw for raw, snap in panel['snapshots'].items() if snap['record']['kind'] == 'file' and not Path(raw).exists()]
        report['layout']['cinnamon_deleted_on_removal'] = deleted
        report['checks'].append('removed multi-instance settings files deleted by Cinnamon: ' + str(len(deleted)))
        # Exercise the optional native pager through its real scoped transaction.
        workspace_script=ROOT/'desktop/workspace_adapter.py';workspace_receipt=RUN/'workspace-preview.json'
        def stage(script,command,state):
            args=[sys.executable,'-B',str(script),command,'--state',str(state)]
            if command in ('apply','restore'):args.append('--commit')
            done=subprocess.run(args,capture_output=True,text=True,timeout=30)
            (RUN/(state.stem+'-'+command+'.log')).write_text(done.stdout+done.stderr)
            done.check_returncode()
        stage(workspace_script,'apply',workspace_receipt)
        picker_id=json.loads(workspace_receipt.read_text())['instance']
        picker_expr='imports.ui.appletManager.filterDefinitionsByUUID("workspace-switcher@cinnamon.org").find(d=>d.applet&&Number(d.applet_id)==='+str(picker_id)+').applet'
        deadline_picker=time.monotonic()+15
        while time.monotonic()<deadline_picker:
            try:
                picker=eval_json(evaluate('(() => {const a='+picker_expr+';return {count:a.buttons.length,active:global.workspace_manager.get_active_workspace_index(),buttons:a.buttons.map(b=>({label:b.actor.get_child().get_text(),reactive:b.actor.reactive,position:b.actor.get_transformed_position(),size:b.actor.get_transformed_size(),color:b.actor.get_theme_node().get_foreground_color().to_string(),background:b.actor.get_theme_node().get_background_color().to_string()}))};})()'))
                if picker['count']==4 and all(b['reactive'] and b['size'][0]>10 for b in picker['buttons']):break
            except Exception: pass
            time.sleep(.3)
        else:raise RuntimeError('Native workspace buttons did not map')
        assert [b['label'] for b in picker['buttons']]==['1','2','3','4']
        probe=owned_workspace_probe()
        report['workspace_native_horizontal']=probe.run_probe(lambda code:eval_json(evaluate(code)),
            lambda path:subprocess.run(['scrot','--overwrite',str(path)],check=True,timeout=8),RUN,probe.EXPECTED_STATE_COLORS,
            expression=picker_expr,shape='circle',click=False)
        # Real private XTest clicks; never send input to the host display.
        import ctypes,ctypes.util
        Xpick=ctypes.CDLL(ctypes.util.find_library('X11'));Tpick=ctypes.CDLL(ctypes.util.find_library('Xtst'))
        Xpick.XOpenDisplay.argtypes=[ctypes.c_char_p];Xpick.XOpenDisplay.restype=ctypes.c_void_p
        Xpick.XFlush.argtypes=[ctypes.c_void_p];Xpick.XCloseDisplay.argtypes=[ctypes.c_void_p]
        Tpick.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
        Tpick.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
        pointer=Xpick.XOpenDisplay(os.environ['DISPLAY'].encode());assert pointer
        try:
            transitions=[]
            for selected in (1,2,3,0):
                previous_button=None;stable_button=0
                for settle in range(30):
                    b=eval_json(evaluate('(() => {const a='+picker_expr+'.buttons['+str(selected)+'].actor;return {position:a.get_transformed_position(),size:a.get_transformed_size()};})()'))
                    stable_button=stable_button+1 if b==previous_button else 0
                    if stable_button>=5:break
                    previous_button=b;time.sleep(.2)
                assert stable_button>=5,'Workspace button geometry did not settle'
                x,y=b['position'];w,h=b['size']
                Tpick.XTestFakeMotionEvent(pointer,-1,int(x+w/2),int(y+h/2),0);Xpick.XFlush(pointer);time.sleep(.15)
                Tpick.XTestFakeButtonEvent(pointer,1,1,0);Xpick.XFlush(pointer);time.sleep(.1)
                Tpick.XTestFakeButtonEvent(pointer,1,0,0);Xpick.XFlush(pointer)
                deadline_click=time.monotonic()+3
                while time.monotonic()<deadline_click:
                    time.sleep(.2)
                    actual=eval_json(evaluate('global.workspace_manager.get_active_workspace_index()'))
                    if actual==selected:break
                time.sleep(.5)
                assert actual==selected, ('workspace click',selected,actual)
                transitions.append(actual+1)
        finally:Xpick.XCloseDisplay(pointer)
        stage(workspace_script,'check',workspace_receipt)
        report['workspace_picker']={'receipt':str(workspace_receipt),'geometry':picker,'clicked_workspaces':transitions,'restore':'pending'}
        report['checks'].append('Native numbered workspace picker: actual pointer clicks 2,3,4,1 switched workspaces')
        original_entries=Gio.Settings.new('org.cinnamon').get_strv('enabled-applets')
        original_picker_entry=json.loads(workspace_receipt.read_text())['entry']
        vertical_entry=original_picker_entry.replace('panel2:center:0:','panel1:center:0:',1)
        if vertical_entry==original_picker_entry:raise RuntimeError('Private picker entry is not the expected bottom slot')
        try:
            setting('org.cinnamon','enabled-applets',[vertical_entry if e==original_picker_entry else e for e in original_entries])
            report['workspace_native_vertical']=probe.run_probe(lambda code:eval_json(evaluate(code)),
                lambda path:subprocess.run(['scrot','--overwrite',str(path)],check=True,timeout=8),RUN,probe.EXPECTED_STATE_COLORS,
                expression=picker_expr,shape='circle',orientation='vertical')
        finally:
            setting('org.cinnamon','enabled-applets',original_entries)
        report['workspace_native_restored_bottom']=probe.run_probe(lambda code:eval_json(evaluate(code)),
            lambda path:subprocess.run(['scrot','--overwrite',str(path)],check=True,timeout=8),RUN,probe.EXPECTED_STATE_COLORS,
            expression=picker_expr,shape='circle',click=False)
        stage(workspace_script,'check',workspace_receipt)
        report['checks'].append('Native64px workspace circles in both orientations, exact colors, labels, pixels, panel/strip containment; private bottom entry restored')
        preview = subprocess.Popen([sys.executable, '-B', __file__, 'gtk3'], stdout=log, stderr=log); procs.append(preview)
        # A real Nemo window over the private wallpaper, beside the taskbar.
        nemo_dir = RUN / (NAME + ' Files')
        for folder in ('Documents', 'Music', 'Pictures'): (nemo_dir / folder).mkdir(parents=True)
        (nemo_dir / 'Read me.txt').write_text(NAME + ' actual Nemo appearance preview.\n')
        procs.append(subprocess.Popen(['nemo', '--no-desktop', str(nemo_dir)], env={**os.environ, 'GTK_THEME': NAME}, stdout=log, stderr=log))
        deadline_nemo = time.monotonic() + 40
        while time.monotonic() < deadline_nemo:
            listing = subprocess.run(['wmctrl', '-lx'], capture_output=True, text=True).stdout
            if 'nemo.nemo' in listing.lower(): break
            time.sleep(.5)
        else: raise RuntimeError('Private Nemo window failed to appear')
        for line in subprocess.check_output(['wmctrl', '-lx'], text=True).splitlines():
            if 'nemo.nemo' in line.lower():
                subprocess.run(['wmctrl', '-ir', line.split()[0], '-e', '0,' + str(24) + ',40,780,620'], check=True)
        if preview.poll() is not None: raise RuntimeError('GTK3 preview failed; inspect log')
        # Wait for BOTH preview windows; a late mapped window can steal the menu grab.
        deadline_windows = time.monotonic() + 20
        previous = None; stable = 0
        while time.monotonic() < deadline_windows:
            actors = eval_json(evaluate('global.get_window_actors().map(a=>({pid:a.meta_window.get_pid(),wm_class:a.meta_window.get_wm_class(),title:a.meta_window.get_title(),visible:a.visible,mapped:a.mapped,x:a.x,y:a.y,width:a.width,height:a.height,opacity:a.opacity}))'))
            report['preview_window_readiness'] = {'expected_preview_pid': preview.pid, 'actors': actors}
            signature = json.dumps(actors, sort_keys=True)
            if preview_windows_ready(actors, preview.pid):
                stable = stable + 1 if signature == previous else 0
                if stable >= 3:
                    report['mapped_preview_windows'] = actors
                    break
            else: stable = 0
            previous = signature
            time.sleep(.3)
        else: raise RuntimeError('Private preview windows did not settle before menu capture')
        def paint_wallpaper():
            subprocess.run(['feh', '--no-fehbg', '--bg-fill', str(ROOT / 'artwork/wallpaper.png')],
                           check=True, capture_output=True, timeout=8)
        def popup_state(name):
            return eval_json(evaluate('(() => {const a=imports.ui.appletManager.filterDefinitionsByUUID(' + json.dumps(name) +
                ').filter(d=>d.applet)[0].applet; const m=a.menu; const p=m.actor.get_transformed_position(); const z=m.actor.get_transformed_size();' +
                'return {isOpen:m.isOpen,visible:m.actor.visible,mapped:m.actor.mapped,opacity:m.actor.opacity,x:p[0],y:p[1],width:z[0],height:z[1],' +
                'label:a._applet_label ? a._applet_label.get_text() : null};})()'))
        def wallpaper_visible(path):
            # Workstation intentionally has a black wallpaper. Require actual painted Nemo content,
            # not a globally lit canvas; mapped-window gates separately assert compositor state.
            from PIL import Image
            with Image.open(path) as shot:
                pixels = list(shot.convert('RGB').crop((100,160,600,400)).getdata())
                lit = sum(max(rgb) > 40 for rgb in pixels) / len(pixels)
            return {'nemo_content_lit_fraction': lit, 'passed': lit >= .90, 'black_wallpaper_expected': True}
        paint_wallpaper()
        deadline_background = time.monotonic() + 15
        while time.monotonic() < deadline_background:
            background_capture = RUN / 'settled-background.png'
            subprocess.run(['scrot', '--overwrite', str(background_capture)], check=True, timeout=8)
            background = wallpaper_visible(background_capture)
            if background['passed']:
                report['settled_background'] = background
                break
            paint_wallpaper(); time.sleep(.4)
        else: raise RuntimeError('Mapped Nemo content did not paint before popup capture')
        for name in ('menu@cinnamon.org', 'calendar@cinnamon.org', 'notifications@cinnamon.org', 'sound@cinnamon.org'):
            base = 'imports.ui.appletManager.filterDefinitionsByUUID(' + json.dumps(name) + ').filter(d=>d.applet)[0].applet.menu'
            try:
                deadline_popup = time.monotonic() + 15
                previous = None; stable = 0; captures = []
                while time.monotonic() < deadline_popup:
                    state = popup_state(name)
                    if not popup_ready(state):
                        evaluate(base + '.open(); true'); stable = 0; time.sleep(.3); continue
                    shape = [state[k] for k in ('x', 'y', 'width', 'height')]
                    stable = stable + 1 if shape == previous else 0
                    previous = shape
                    if stable < 3: time.sleep(.3); continue
                    capture = RUN / ({'menu@cinnamon.org': 'menu.png', 'calendar@cinnamon.org': 'calendar.png'}.get(name, name.split('@')[0] + '.png'))
                    subprocess.run(['scrot', '--overwrite', str(capture)], check=True, timeout=8)
                    after = popup_state(name); wallpaper = wallpaper_visible(capture)
                    captures.append({'before': state, 'after': after, 'painted_content': wallpaper})
                    if popup_ready(after) and shape == [after[k] for k in ('x', 'y', 'width', 'height')] and wallpaper['passed']:
                        if name == 'menu@cinnamon.org':
                            expected_label = json.loads((RUN / 'config/cinnamon/spices/menu@cinnamon.org' / (str(next(i for a, i in instances(panel['taskbar']) if a == 'menu@cinnamon.org')) + '.json')).read_text())['menu-label']['value']
                            if after['label'] != expected_label: raise RuntimeError('Rendered menu label differs from exact copied label')
                        report.setdefault('popup_evidence', {})[name] = {'state': after, 'capture': str(capture), 'attempts': captures}
                        break
                    if not wallpaper['passed']: paint_wallpaper()
                    stable = 0; time.sleep(.3)
                else: raise RuntimeError('Popup did not remain visibly open for a stable non-black capture: ' + name)
            except RuntimeError:
                if name in PRIMARY: raise
                report.setdefault('warnings', []).append(name + ' menu not available in the private session'); continue
            finally:
                try: evaluate(base + '.close(); true')
                except Exception: pass
            report['checks'].append(name + ' visibly open before and after capture; painted application region verified over intentional black backdrop')
        bus('org.Cinnamon.ReloadTheme'); time.sleep(1)
        (RUN / 'open-popover').touch(); time.sleep(.6)
        subprocess.run(['scrot', '--overwrite', str(RUN / 'desktop.png')], check=True, timeout=8)
        report['checks'].append('GTK model-button and list-row popover render')
        report['checks'].append('actual Nemo window render')
        # Exercise real Metacity maximize/unmaximize, then a private GTK modal.
        nemo_actor = "global.get_window_actors().find(a=>String(a.meta_window.get_wm_class()).toLowerCase()==='nemo')"
        def nemo_state():
            return eval_json(evaluate('(() => {const a=' + nemo_actor + ';const w=a.meta_window;const r=w.get_frame_rect();const k=w.get_work_area_current_monitor();' +
                'return {visible:a.visible,mapped:a.mapped,opacity:a.opacity,maximized:w.get_maximized(),frame:[r.x,r.y,r.width,r.height],workarea:[k.x,k.y,k.width,k.height]};})()'))
        def await_state(reader, predicate, description):
            deadline = time.monotonic() + 12
            previous = None; stable = 0
            while time.monotonic() < deadline:
                state = reader()
                if predicate(state):
                    stable = stable + 1 if state == previous else 0
                    if stable >= 2: return state
                else: stable = 0
                previous = state; time.sleep(.25)
            raise RuntimeError(description + ': ' + json.dumps(previous))
        original_nemo = nemo_state()
        evaluate(nemo_actor + '.meta_window.maximize(3); true')
        maximized = await_state(nemo_state, maximized_ready, 'Nemo maximize geometry did not match work area')
        subprocess.run(['scrot', '--overwrite', str(RUN / 'maximized.png')], check=True, timeout=8)
        if not maximized_ready(nemo_state()): raise RuntimeError('Nemo changed during maximized capture')
        evaluate(nemo_actor + '.meta_window.unmaximize(3); true')
        restored_nemo = await_state(nemo_state, lambda state: state['maximized'] == 0 and state['frame'] == original_nemo['frame'], 'Nemo did not restore its frame')
        report['window_states'] = {'nemo_before': original_nemo, 'nemo_maximized': maximized, 'nemo_restored': restored_nemo}
        (RUN / 'open-modal').touch()
        def modal_state():
            return eval_json(evaluate('(() => {const a=global.get_window_actors().find(a=>a.meta_window.get_pid()===' + str(preview.pid) +
                ' && a.meta_window.get_transient_for());if(!a)return null;const w=a.meta_window;const p=w.get_transient_for();const r=w.get_frame_rect();' +
                'return {visible:a.visible,mapped:a.mapped,opacity:a.opacity,parent_pid:p.get_pid(),type:w.get_window_type(),frame:[r.x,r.y,r.width,r.height]};})()'))
        modal = await_state(modal_state, lambda state: state is not None and state['visible'] and state['mapped'] and state['opacity'] == 255 and state['parent_pid'] == preview.pid and state['type'] == 4, 'GTK modal did not map as a transient modal dialog')
        subprocess.run(['scrot', '--overwrite', str(RUN / 'modal.png')], check=True, timeout=8)
        if modal_state() != modal: raise RuntimeError('Modal changed during capture')
        (RUN / 'close-modal').touch()
        await_state(modal_state, lambda state: state is None, 'GTK modal did not close')
        report['window_states']['modal'] = modal
        report['checks'].append('Nemo maximized to exact work area and restored frame; private transient GTK modal mapped, captured and closed')
        gtk4 = subprocess.run([sys.executable, '-B', __file__, 'gtk4'], capture_output=True, text=True, timeout=10)
        if gtk4.returncode: raise RuntimeError('GTK4 CSS: ' + gtk4.stderr + gtk4.stdout)
        report['checks'].append('GTK4 CSS parse')
        # Real terminal application on this private bus/profile, never the user's login shell.
        terminal_profile = json.loads((ROOT / 'applications/generated/gnome-terminal/profile.json').read_text())
        terminal_uuid = terminal_profile['uuid']
        expected_terminal = {'background-color': DESIGN['palette']['terminal'], 'foreground-color': DESIGN['palette']['terminal_foreground'],
                             'font': appearance['monospace_font'], 'palette': DESIGN['terminal_ansi']}
        for key, expected in expected_terminal.items():
            if GLib.Variant.parse(None, terminal_profile['keys'][key], None, None).unpack() != expected:
                raise RuntimeError('Generated terminal profile differs from gated design: ' + key)
        terminal_settings = Gio.Settings.new_with_path('org.gnome.Terminal.Legacy.Profile', '/org/gnome/terminal/legacy/profiles:/:' + terminal_uuid + '/')
        terminal_keys = terminal_settings.props.settings_schema.list_keys()
        for key, printed in terminal_profile['keys'].items():
            if key in terminal_keys:
                terminal_settings.set_value(key, GLib.Variant.parse(None, printed, None, None))
        for key, value in {'use-custom-command': False, 'login-shell': False}.items():
            if key in terminal_keys: terminal_settings.set_boolean(key, value)
        setting('org.gnome.Terminal.ProfilesList', 'list', [terminal_uuid])
        setting('org.gnome.Terminal.ProfilesList', 'default', terminal_uuid)
        Gio.Settings.sync()
        terminal_title = NAME + ' terminal preview'
        terminal_close = RUN / 'close-terminal'
        # Fixed text clearly identifies a theme sample, with no fabricated uname/OS output.
        terminal_script = 'printf \'%s\\n\' \'GNU-Darwin Workstation | appearance preview\' \'\' \'White on black terminal - Liberation Mono 10\' \'Scientific desktop / source-inspired visual sample\' \'\' \'$ make workspace\' \'Theme assets: staged appearance sample\' \'Linux Mint host: unchanged\' \'\' \'$ _\'; n=0; while [ ! -f "$1" ] && [ "$n" -lt 300 ]; do sleep 0.2; n=$((n+1)); done'
        terminal = subprocess.Popen(['gnome-terminal', '--wait', '--profile=' + terminal_uuid, '--title=' + terminal_title,
                                     '--geometry=90x24+380+250', '--', '/bin/sh', '-c', terminal_script, 'preview', str(terminal_close)],
                                    stdout=log, stderr=log)
        procs.append(terminal)
        def terminal_state():
            return eval_json(evaluate('(() => {const a=global.get_window_actors().find(a=>a.meta_window.get_title()===' + json.dumps(terminal_title) +
                ');if(!a)return null;const w=a.meta_window;const r=w.get_frame_rect();return {visible:a.visible,mapped:a.mapped,opacity:a.opacity,' +
                'title:w.get_title(),wm_class:w.get_wm_class(),pid:w.get_pid(),frame:[r.x,r.y,r.width,r.height]};})()'))
        terminal_view = await_state(terminal_state, lambda state: state is not None and state['visible'] and state['mapped'] and state['opacity'] == 255
                                   and 'terminal' in str(state['wm_class']).lower(), 'Private GNOME Terminal did not map')
        subprocess.run(['scrot', '--overwrite', str(RUN / 'terminal.png')], check=True, timeout=8)
        if terminal_state() != terminal_view: raise RuntimeError('Terminal changed during capture')
        terminal_close.touch()
        terminal.wait(timeout=10)
        await_state(terminal_state, lambda state: state is None, 'Private terminal did not close')
        report['terminal_preview'] = {'window': terminal_view, 'profile_uuid': terminal_uuid, 'appearance': expected_terminal,
                                      'capture': str(RUN / 'terminal.png'), 'closed': True, 'shell': '/bin/sh -c fixed sample; no startup files'}
        report['checks'].append('actual private GNOME Terminal profile rendered and exact preview window closed')
        # Only native installed counterparts; never execute archived payloads.
        import signal, ctypes, ctypes.util
        source_image = ROOT.parent.parent / 'proposals/gnu-darwin-archive/today-2002.png'
        sample_image = RUN / 'historical-reference.png'
        shutil.copyfile(source_image, sample_image)
        # Seed unrelated private preferences, then use the real complete application stage.
        gimp_dir=Path.home()/'.config/GIMP/2.10';gimp_dir.mkdir(parents=True,exist_ok=True)
        gimp_rc=gimp_dir/'gimprc';gimp_personal=gimp_dir/'gtkrc'
        original_gimprc='# preserved private preference\n(theme "Dark")\n(icon-theme "Symbolic")\n(undo-levels 7)\n'
        original_gtkrc='# preserved personal GTK settings\n'
        gimp_rc.write_text(original_gimprc);gimp_personal.write_text(original_gtkrc)
        installed_theme=Path.home()/'.themes'/NAME
        installed_theme.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(SOURCE,installed_theme)
        application_receipt=RUN/'applications-preview.json'
        adapter=ROOT/'applications/adapter.py'
        # Match the existing host's palette registry without copying personal
        # preferences: the actual application stage registers its own key.
        private_registry=Path.home()/'.config/fastfetch/logos/hues/palettes.json'
        private_registry.parent.mkdir(parents=True,exist_ok=True)
        private_registry.write_text('{}\n')
        application_apply=subprocess.run([sys.executable,'-B',str(adapter),'apply','--state',str(application_receipt),'--commit'],capture_output=True,text=True,timeout=60)
        (RUN/'applications-apply.log').write_text(application_apply.stdout+application_apply.stderr)
        application_apply.check_returncode()
        assert '(theme "System")' in gimp_rc.read_text() and '(icon-theme "Legacy")' in gimp_rc.read_text()
        assert str(installed_theme/'gtk-2.0/gtkrc') in gimp_personal.read_text()
        logo_script=ROOT/'desktop/logo_adapter.py';logo_receipt=RUN/'logo-preview.json'
        ff_dir=Path.home()/'.config/fastfetch';ff_dir.mkdir(parents=True,exist_ok=True)
        ff_config=ff_dir/'config.jsonc'
        ff_seed={'logo':{'type':'chafa','source':'original.png','width':18,'height':9,'chafa':{'symbols':'braille','fgOnly':True,'canvasMode':'TRUECOLOR'}},'modules':['os','cpu',{'type':'memory','keyColor':'38;2;208;208;208'},{'type':'localip'}]}
        ff_bands=json.loads((ff_dir/'logos/hues/palettes.json').read_text())['gnu-darwin-workstation']
        ff_seed['display']={'color':{'title':'38;2;'+';'.join(map(str,ff_bands[0])),'keys':'38;2;'+';'.join(map(str,ff_bands[2]))}}
        ff_seed['modules'][-1]['keyColor']='38;2;'+';'.join(map(str,ff_bands[1]))
        ff_config.write_text(json.dumps(ff_seed,indent=2)+'\n')
        stage(logo_script,'apply',logo_receipt);stage(logo_script,'check',logo_receipt)
        logo_config=json.loads(ff_config.read_text());logo_installed=Path(logo_config['logo']['source'])
        assert logo_installed.read_bytes()==(ROOT/'artwork/fastfetch-lm-apple.png').read_bytes()
        assert [m.get('type') if isinstance(m,dict) else m for m in logo_config['modules']]==[m.get('type') if isinstance(m,dict) else m for m in ff_seed['modules']]
        assert logo_config['modules'][2]['keyColor']=='38;2;'+';'.join(map(str,json.loads((ff_dir/'logos/hues/palettes.json').read_text())['gnu-darwin-workstation'][5]))
        # Run the actual existing hue helper with its three path constants
        # redirected to this private home; its on-disk script is unchanged.
        import importlib.util
        helper_path=Path(os.environ['GNU_DARWIN_WORKSTATION_HOST_HOME'])/'.config/fastfetch/apply-hue-colors.py'
        hs=importlib.util.spec_from_file_location('private_hue_helper',helper_path);hm=importlib.util.module_from_spec(hs);hs.loader.exec_module(hm)
        hm.CONFIG_PATH=str(ff_config);hm.PALETTES_PATH=str(ff_dir/'logos/hues/palettes.json');hm.HUES_DIR=str(ff_dir/'logos/hues')
        argv_before=sys.argv;sys.argv=[str(helper_path),'gnu-darwin-workstation']
        try:hm.main()
        finally:sys.argv=argv_before
        assert json.loads(ff_config.read_text())==logo_config,'Existing hue helper drifted the final logo configuration'
        stage(logo_script,'check',logo_receipt)
        report['apple_logo']={'receipt':str(logo_receipt),'installed_sha256':hashlib.sha256(logo_installed.read_bytes()).hexdigest(),'modes_and_modules_preserved':True}
        logo_title=NAME+' Apple logo preview';logo_close=RUN/'close-logo-terminal'
        logo_helper=Path(os.environ['GNU_DARWIN_WORKSTATION_HOST_HOME'])/'.config/fastfetch/ansi-logo.sh'
        logo_raw=subprocess.run([str(logo_helper),str(ff_config)],capture_output=True,text=True,check=True,timeout=15).stdout.strip()
        assert Path(logo_raw).is_file() and str(Path(logo_raw)).startswith(str(Path.home()))
        report['apple_logo']['terminal_renderer']='existing ansi-logo.sh + fastfetch file-raw, matching the user shell wrapper'
        logo_command='"$1" --config "$2" --logo-type file-raw --logo "$4"; while [ ! -e "$3" ]; do sleep 0.1; done'
        logo_terminal=subprocess.Popen(['gnome-terminal','--wait','--window','--profile='+terminal_uuid,'--title='+logo_title,
            '--','/bin/sh','-c',logo_command,'logo-preview',shutil.which('fastfetch'),str(ff_config),str(logo_close),logo_raw],stdout=log,stderr=log)
        procs.append(logo_terminal)
        def logo_window():
            return eval_json(evaluate('(() => {const a=global.get_window_actors().find(a=>a.meta_window.get_title()==='+json.dumps(logo_title)+');return a?{visible:a.visible,mapped:a.mapped,opacity:a.opacity}:null;})()'))
        await_state(logo_window,lambda state:state and state['visible'] and state['mapped'] and state['opacity']==255,'Logo terminal did not map')
        time.sleep(1.5)
        subprocess.run(['scrot','--overwrite',str(RUN/'fastfetch.png')],check=True,timeout=8)
        logo_close.touch();logo_terminal.wait(timeout=10)
        report['apple_logo']['terminal_capture']=str(RUN/'fastfetch.png')
        app_env=os.environ.copy();app_env.pop('GTK2_RC_FILES',None)
        app_env['XDG_CONFIG_HOME']=str(Path.home()/'.config')
        report['application_tests'] = []
        for name, command in [('gimp', ['gimp','--new-instance','--no-splash','--console-messages',str(sample_image)])]:
            entry = {'name':name,'command':command,'archived_binary':False,'status':'starting'}
            app_log=(RUN/(name+'.log')).open('w')
            app=subprocess.Popen(command,env=app_env,stdout=app_log,stderr=app_log,start_new_session=True)
            procs.append(app)
            try:
                def app_windows():
                    return eval_json(evaluate('global.get_window_actors().filter(a=>a.meta_window.get_pid()==='+str(app.pid)+').map(a=>{const w=a.meta_window,r=w.get_frame_rect();return {title:w.get_title(),visible:a.visible,mapped:a.mapped,opacity:a.opacity,type:w.get_window_type(),frame:[r.x,r.y,r.width,r.height]};})'))
                deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    windows=app_windows()
                    if any(w['visible'] and w['mapped'] and w['opacity']==255 and w['frame'][2]>200 for w in windows):break
                    if app.poll() is not None:raise RuntimeError('Application exited before mapping')
                    time.sleep(.4)
                else:raise RuntimeError('Application window did not map within40 seconds')
                evaluate('(() => {const a=global.get_window_actors().find(a=>a.meta_window.get_pid()==='+str(app.pid)+'&&a.visible);a.meta_window.activate(global.get_current_time());return true;})()')
                time.sleep(2)
                subprocess.run(['scrot','--overwrite',str(RUN/(name+'.png'))],check=True,timeout=8)
                entry.update(status='mapped-captured',windows=app_windows(),capture=str(RUN/(name+'.png')),monitor=monitor_snapshot())
                # Private-display XTest exercises an ordinary chooser/menu; no input reaches the host X server.
                assert os.environ['DISPLAY'] != os.environ['GNU_DARWIN_WORKSTATION_HOST_DISPLAY']
                X=ctypes.CDLL(ctypes.util.find_library('X11'));T=ctypes.CDLL(ctypes.util.find_library('Xtst'))
                X.XOpenDisplay.argtypes=[ctypes.c_char_p];X.XOpenDisplay.restype=ctypes.c_void_p
                X.XStringToKeysym.argtypes=[ctypes.c_char_p];X.XStringToKeysym.restype=ctypes.c_ulong
                X.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];X.XKeysymToKeycode.restype=ctypes.c_uint
                X.XFlush.argtypes=[ctypes.c_void_p];X.XCloseDisplay.argtypes=[ctypes.c_void_p]
                T.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
                T.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
                T.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
                xd=X.XOpenDisplay(os.environ['DISPLAY'].encode());assert xd
                def key(k,pressed):T.XTestFakeKeyEvent(xd,X.XKeysymToKeycode(xd,X.XStringToKeysym(k.encode())),pressed,0)
                try:
                    if name=='gimp':
                        key('Control_L',1);key('o',1);key('o',0);key('Control_L',0)
                    else:
                        f=entry['windows'][0]['frame'];T.XTestFakeMotionEvent(xd,-1,f[0]+80,f[1]+80,0);T.XTestFakeButtonEvent(xd,1,1,0);T.XTestFakeButtonEvent(xd,1,0,0)
                    X.XFlush(xd);time.sleep(2)
                    entry['interaction_windows']=app_windows()
                    entry['chooser_open']=any(w['title']=='Open Image' and w['visible'] and w['mapped'] for w in entry['interaction_windows'])
                    if not entry['chooser_open']:raise RuntimeError('GIMP Open Image chooser did not map')
                    subprocess.run(['scrot','--overwrite',str(RUN/(name+'-interaction.png'))],check=True,timeout=8)
                    key('Escape',1);key('Escape',0);X.XFlush(xd);time.sleep(.5)
                    key('Control_L',1);key('q',1);key('q',0);key('Control_L',0);X.XFlush(xd)
                    app.wait(timeout=12);entry['graceful_close']=True
                finally:X.XCloseDisplay(xd)
                report['checks'].append('Installed '+name+' counterpart mapped and captured; interaction capture requires visual review')
            except Exception as error:
                entry.update(status='failed',error=str(error))
            finally:
                # Exact process group created above; never a name-based kill.
                if app.poll() is None:
                    os.killpg(app.pid,signal.SIGTERM)
                    try:app.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(app.pid,signal.SIGKILL);app.wait(timeout=5)
                app_log.close()
                entry['closed']=app.poll() is not None
                report['application_tests'].append(entry)
                time.sleep(.5)

        # Restore with the actual journal and retain an unrelated post-apply edit.
        unrelated='\n# unrelated edit made after application preview\n'
        with gimp_personal.open('a') as stream:stream.write(unrelated)
        application_restore=subprocess.run([sys.executable,'-B',str(adapter),'restore','--state',str(application_receipt),'--commit'],capture_output=True,text=True,timeout=60)
        (RUN/'applications-restore.log').write_text(application_restore.stdout+application_restore.stderr)
        application_restore.check_returncode()
        assert gimp_rc.read_text()==original_gimprc, 'GIMP preferences not restored byte-exact'
        assert gimp_personal.read_text()==original_gtkrc+unrelated, 'GIMP personal rc unrelated edit lost'
        if any(entry['status']!='mapped-captured' or not entry.get('graceful_close') for entry in report['application_tests']):
            raise RuntimeError('GIMP application preview or graceful close failed: '+json.dumps(report['application_tests']))
        report['gimp_adapter_preview']={'receipt':str(application_receipt),'status':'restored','normal_startup':True,'forced_GTK2_RC_FILES':False,'unrelated_preferences_preserved':True,'graceful_close':True}
        report['checks'].append('GIMP normal startup through actual application adapter; chooser captured; graceful close; full application stage restored and unrelated preference retained')

        stage(logo_script,'restore',logo_receipt)
        assert json.loads(ff_config.read_text())==ff_seed and not logo_installed.exists()
        report['apple_logo']['restore']='passed'
        stage(workspace_script,'restore',workspace_receipt)
        assert active.get_strv('enabled-applets')==panel['taskbar']
        counter_after_picker=cinnamon.get_int('next-applet-id')
        report['workspace_picker']['restore']='passed'
        report['checks'].append('Workspace picker and Apple logo stages restore independently; unrelated preferences retained')

        # Rehearse the controller's manual restore (conflict-checked) back to the copied layout, in the private session.
        original_run = control.run
        control.run = lambda args, check=True: '' if args[0] == 'systemctl' else original_run(args, check)
        result = control.restore(receipt, automatic=False)
        time.sleep(3)
        if result['status'] != 'restored': raise RuntimeError('Panel restore rehearsal: ' + json.dumps(result))
        drift = [row['key'] for row in panel['settings'] if not control.same_setting(row, control.live_value(row), row['before'])]
        if drift: raise RuntimeError('Restored private layout differs from the copy: ' + ', '.join(drift))
        for raw, snap in panel['snapshots'].items():
            if snap['record']['kind'] == 'file' and control.record(Path(raw)) != snap['record']:
                raise RuntimeError('Removed applet settings file not restored byte-exact after Cinnamon re-added it: ' + raw)
        for instance in panel['new_instances']:
            if Path(instance['path']).exists(): raise RuntimeError('New instance file left after restore: ' + instance['path'])
        if cinnamon.get_int('next-applet-id') != counter_after_picker: raise RuntimeError('next-applet-id changed by restore')
        removed_monitor = monitor_snapshot()
        time.sleep(1.3)
        settled_monitor = monitor_snapshot()
        if not monitor_cleanup_valid(removed_monitor, settled_monitor):
            raise RuntimeError('Removed monitor timer remained active: ' + json.dumps([removed_monitor,settled_monitor]))
        if monitor_destination.exists(): raise RuntimeError('Private monitor asset directory remained after restore')
        report['monitor']['removed'] = removed_monitor
        report['monitor']['settled_after_removal'] = settled_monitor
        report['monitor']['asset_removed'] = True
        evaluate('delete global.__workstationMonitorProbe; true')
        report['checks'].append('monitor disposed, timer0, sequence frozen; private applet asset removed')
        restored = panels()
        expected_panels = GLib.Variant.parse(None, standin['panels-enabled'], None, None).unpack()
        if len(restored) != len(expected_panels):
            raise RuntimeError('Restored panel count differs: ' + json.dumps(restored))
        if cinnamon.get_strv('panels-enabled') != expected_panels:
            raise RuntimeError('Restored panels-enabled differs from the copied original layout')
        report['layout']['restored_panels'] = restored
        report['checks'].append('controller restore rehearsal: layout, zone arrays, desklets exact; settings files byte-exact; all three new files gone; next-applet-id kept')
        # Rehearse settings restoration without touching the real desktop or stopping its shell.
        fixture = RUN / 'recovery-fixture.txt'
        fixture.write_text('original')
        before_file = control.record(fixture)
        fixture.write_text('trial')
        receipt = RUN / 'recovery-rehearsal.json'
        control.save(receipt, {'status': 'pending', 'files': {str(fixture): {'before': before_file, 'after': control.record(fixture)}},
                              'settings': [{'schema': 'org.cinnamon.theme', 'key': 'name', 'before': repr(NAME), 'user': repr(NAME), 'after': "'Mint-Y'"}],
                              'opacity_active': False})
        setting('org.cinnamon.theme', 'name', 'Mint-Y'); time.sleep(.3)
        control.restore(receipt, automatic=True)
        assert fixture.read_text() == 'original'
        time.sleep(.3)
        assert Gio.Settings.new('org.cinnamon.theme').get_string('name') == NAME
        report['checks'].append('isolated shell selection restore')
        if shell.poll() is not None: raise RuntimeError('Nested shell exited')
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'; report['error'] = str(error)
        import traceback
        report['traceback'] = traceback.format_exc()
        # Failure diagnostics from the still-running private shell: a later sample shows whether sizes settle.
        if 'shell' in locals() and shell.poll() is None:
            time.sleep(5); measure('after-failure+5s')
            report['diagnostics'] = [str(RUN / 'taskbar-measurements.json'), str(RUN / 'taskbar.png')]
    finally:
        for p in reversed(procs):
            if p.poll() is None: p.terminate()
        for p in reversed(procs):
            try: p.wait(timeout=4)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
        log.close()
        (RUN / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        (ROOT / 'verification').mkdir(exist_ok=True)
        (ROOT / 'verification/isolated-latest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'passed' else 1


def gtk(version):
    import gi
    gi.require_version('Gtk', version + '.0')
    from gi.repository import Gtk, GLib
    provider = Gtk.CssProvider(); errors = []
    provider.connect('parsing-error', lambda p, section, error: errors.append(str(error)))
    provider.load_from_path(str(SOURCE / ('gtk-' + version + '.0/gtk.css')))
    if errors: raise RuntimeError('\n'.join(errors))
    gtk_settings = Gtk.Settings.get_default()
    gtk_settings.set_property('gtk-theme-name', NAME)
    gtk_settings.set_property('gtk-font-name', APPEARANCE['ui_font'])
    gtk_settings.set_property('gtk-cursor-theme-size', APPEARANCE['cursor_size'])
    gtk_settings.set_property('gtk-application-prefer-dark-theme', APPEARANCE['prefer_dark'])
    gtk_settings.set_property('gtk-decoration-layout', APPEARANCE['button_layout'])
    if version == '4': return 0
    window = Gtk.Window(title=NAME + ' — controls')
    window.set_default_size(560, 380); window.move(880, 140)
    header = Gtk.HeaderBar(title=NAME); header.set_show_close_button(True); header.set_decoration_layout(APPEARANCE['button_layout']); window.set_titlebar(header)  # Square minimize-left/close-right controls follow canonical design
    library = Gtk.MenuButton(label='Library'); header.pack_start(library)
    popover = Gtk.Popover.new(library); popover.set_modal(False)
    popbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5); popbox.set_border_width(10)
    model = Gtk.ModelButton(); model.props.text = 'Appearance'; popbox.add(model)
    listing = Gtk.ListBox(); listing.add(Gtk.Label(label='Audio library')); popbox.add(listing)
    popover.add(popbox); library.set_popover(popover)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15); box.set_border_width(25)
    box.add(Gtk.Label(label=NAME + ' | classic GNU-Darwin Workstation desktop preview'))
    box.add(Gtk.Entry(placeholder_text='Search your desktop'))
    box.add(Gtk.Button(label='Open a quiet workspace'))
    check = Gtk.CheckButton(label='Checked control'); check.set_active(True); box.add(check)
    radio = Gtk.RadioButton.new_with_label_from_widget(None, 'Selected radio'); radio.set_active(True); box.add(radio)
    switch = Gtk.Switch(); switch.set_active(True); switch.set_halign(Gtk.Align.START); box.add(switch)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1); scale.set_value(65); box.add(scale)
    window.add(box); window.show_all()
    def show_popover():
        if (RUN / 'open-popover').exists(): popover.show_all(); popover.popup(); return False
        return True
    dialog = None
    def modal_markers():
        nonlocal dialog
        if (RUN / 'open-modal').exists() and dialog is None and not (RUN / 'close-modal').exists():
            popover.popdown()
            dialog = Gtk.MessageDialog(transient_for=window, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.OK_CANCEL, text='Keep this appearance preview?')
            dialog.format_secondary_text('Private visual test only. Your desktop settings are unchanged.')
            dialog.show_all()
        if dialog is not None and (RUN / 'close-modal').exists():
            dialog.destroy(); dialog = None
            return False
        return True
    GLib.timeout_add(200, modal_markers)
    GLib.timeout_add(200, show_popover)
    GLib.timeout_add_seconds(150, Gtk.main_quit); Gtk.main()
    return 0


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'parent'
    sys.exit(worker() if mode == 'worker' else gtk(mode[-1]) if mode in ('gtk3', 'gtk4') else parent())
