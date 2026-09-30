#!/usr/bin/python3
"""One bounded Cinnamon + GTK preview of GNU-Darwin Aqua on a private X display and settings bus.

The real desktop, its D-Bus session and its dconf database are never touched: a private HOME,
XDG directories, a dbus-run-session bus (with its own dconf service writing under the private
XDG_CONFIG_HOME) and a private X server (Xvfb when installed, otherwise a nested Xephyr window) host
the render. The parent only READS the user's current panel layout and copies the listed applets'
code and settings files into the private tree. The private shell starts on that copy of the layout;
desktop_control.py's own panel writer then turns it into the top34 menu bar and dock68 bottom panel (with the new
calendar@cinnamon.org instance and its custom '%H:%M' format), the taskbar is checked and captured, and
the controller's own restore is rehearsed back to the copied layout. The report is the source gate
desktop_control.py requires before a live trial (verification/isolated-latest.json).
"""
from pathlib import Path
import argparse, hashlib, json, os, re, select, shutil, subprocess, sys, tempfile, time

ROOT = Path(__file__).resolve().parent
NAME = 'GNU-Darwin Aqua'
SOURCE = ROOT / 'desktop' / NAME
DESIGN = json.loads((ROOT / 'design.json').read_text())
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
RUN = Path(os.environ.get('GNU_DARWIN_AQUA_TEST_RUN', ROOT / 'verification' / time.strftime('isolated-%Y%m%d-%H%M%S')))
# Keys the parent copies from the live session into the private one (read-only on the live side).
LAYOUT_KEYS = ('enabled-applets', 'panels-enabled', 'panels-height', 'panels-autohide', 'panels-show-delay', 'panels-hide-delay',
               'panel-zone-icon-sizes', 'panel-zone-symbolic-icon-sizes', 'panel-zone-text-sizes', 'enabled-desklets', 'next-applet-id')
PRIMARY = ('menu@cinnamon.org', 'grouped-window-list@cinnamon.org', 'calendar@cinnamon.org')
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
    """Measure both actual actors and the painted centered dock body, not only settings."""
    if len(panels) != 2 or {p.get('id') for p in panels} != {1, 2}:
        return ['expected exactly panels 1 and 2']
    out = []
    for p in panels:
        pid = p['id']; height = PANEL_HEIGHTS[pid]
        if p.get('pos') != (0 if pid == 1 else 1): out.append('wrong panel edge: ' + str(pid))
        if p.get('height') != height: out.append('wrong panel height: ' + str(pid))
        if not isinstance(p.get('monitor_width'), (int, float)) or p.get('width') != p['monitor_width']:
            out.append('panel actor must span monitor: ' + str(pid))
        zones = p.get('zone_heights')
        if not (isinstance(zones, list) and len(zones) == 3 and all(isinstance(z, (int, float)) for z in zones)) or max(zones) > height:
            out.append('zone height unavailable or overflowing: ' + str(pid))
        if pid == 1 and p.get('background_alpha', 0) <= 0: out.append('top bar must paint a continuous background')
        if pid == 2:
            body = p.get('dock', {})
            width, x = body.get('width'), body.get('x')
            if not isinstance(width, (int, float)) or not 0 < width < p.get('width', 0): out.append('dock body must be compact and visible')
            elif not isinstance(x, (int, float)) or abs(x + width / 2 - p['width'] / 2) > 2: out.append('dock body is not centered')
            if body.get('background_alpha', 0) <= 0: out.append('dock body has no painted background')
            if p.get('background_alpha') != 0: out.append('bottom actor must stay transparent')
    return out


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
    """Prefer headless Xvfb; fall back to a nested Xephyr window. Env GNU_DARWIN_AQUA_XSERVER overrides."""
    choice = os.environ.get('GNU_DARWIN_AQUA_XSERVER') or ('Xvfb' if shutil.which('Xvfb') else 'Xephyr')
    size = str(SCREEN[0]) + 'x' + str(SCREEN[1])
    if choice == 'Xvfb':
        return ['Xvfb', '-displayfd', str(fd), '-screen', '0', size + 'x24', '-nolisten', 'tcp', '-noreset']
    return ['Xephyr', '-displayfd', str(fd), '-screen', size, '-nolisten', 'tcp', '-noreset', '-no-host-grab',
            '-title', NAME + ' — isolated preview']


def copy_live_layout():
    """READ-ONLY on the live side: the user's panel keys and the listed applets' code/settings, copied into RUN."""
    from gi.repository import Gio, GLib
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
    env['GNU_DARWIN_AQUA_TEST_RUN'] = str(RUN)
    for key, directory in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                           ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
        p = RUN / directory; p.mkdir(mode=0o700); env[key] = str(p)
    runtime = tempfile.TemporaryDirectory(prefix='gnu_darwin_aqua-preview-')
    env['XDG_RUNTIME_DIR'] = runtime.name
    shutil.copytree(SOURCE, RUN / 'data/themes' / NAME)
    for suffix in (' icons', ' cursors'):
        shutil.copytree(ROOT / 'desktop' / (NAME + suffix), RUN / 'data/icons' / (NAME + suffix), symlinks=True)
    copy_live_layout()
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
               GNU_DARWIN_AQUA_PREVIEW='1', GNU_DARWIN_AQUA_HOST_DISPLAY=os.environ.get('DISPLAY', ''),
               GNU_DARWIN_AQUA_HOST_BUS=os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''),
               GNU_DARWIN_AQUA_HOST_HOME=str(Path.home()))
    for key in ('SESSION_MANAGER', 'GTK_THEME', 'DCONF_PROFILE', 'CINNAMON_VERSION'):
        env.pop(key, None)
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
    assert os.environ['DISPLAY'] != os.environ['GNU_DARWIN_AQUA_HOST_DISPLAY']
    assert os.environ['DBUS_SESSION_BUS_ADDRESS'] != os.environ['GNU_DARWIN_AQUA_HOST_BUS']
    assert Path.home() != Path(os.environ['GNU_DARWIN_AQUA_HOST_HOME']) and str(Path.home()).startswith(str(RUN))
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
                                  'monitor_width:global.display.get_monitor_geometry(p.monitorIndex).width,'
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
    report = {'status': 'running', 'theme': NAME, 'source_sha256': fingerprint(), 'run': str(RUN), 'checks': [],
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
        report['harness_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report['private_framebuffer']=ensure_private_screen('2880x1800',os.environ['DISPLAY'],os.environ['GNU_DARWIN_AQUA_HOST_DISPLAY'])
        report['checks'].append('private shell started on the copied layout ' + str(standin.get('panels-enabled')))
        # The taskbar, written by the controller's own panel code path (plan -> journal -> apply) into the private dconf.
        receipt = RUN / 'panel-rehearsal.json'
        panel = control.panel_targets()
        data = {'id': 'isolated', 'status': 'pending', 'guard_armed': True,  # no timer: the private session is disposable
                'settings': [], 'files': {}, 'assets': [], 'opacity_active': False, 'panel': {**panel, 'phase': 'journaled'}}
        control.save(receipt, data)
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
        report['checks'].append('exact top34/dock68 actors; full-width painted top; transparent bottom with centered compact painted dock')
        want = [a + ':' + str(i) for a, i in instances(panel['taskbar'])]
        have = loaded()
        missing = [x for x in want if x not in have]
        report['layout']['loaded_applets'] = have
        if any(x.split(':')[0] in PRIMARY for x in missing): raise RuntimeError('Taskbar applets not loaded: ' + ', '.join(missing))
        if missing: report.setdefault('warnings', []).append('service-bound taskbar applets not loaded in the private session: ' + ', '.join(missing))
        report['checks'].append('taskbar applets loaded in order: ' + str(len(want) - len(missing)) + '/' + str(len(want)))
        label = eval_json(evaluate('imports.ui.appletManager.filterDefinitionsByUUID("calendar@cinnamon.org").filter(d=>d.applet_id==' +
                                   str(panel['calendar']['id']) + ')[0].applet._applet_label.get_text()'))
        report['layout']['calendar_label'] = label
        if not re.fullmatch(r'[0-9]{2}:[0-9]{2}', label): raise RuntimeError('Calendar label is not HH:MM: ' + repr(label))
        report['checks'].append('calendar clock renders HH:MM (%H:%M)')
        if control.record(calendar) != panel['files'][str(calendar)]['after']:
            raise RuntimeError('Cinnamon rewrote the new calendar settings file (md5/format mismatch)')
        report['checks'].append('calendar settings file accepted by Cinnamon byte-for-byte (md5 ' + panel['calendar']['md5'] + ')')
        deleted = [raw for raw, snap in panel['snapshots'].items() if snap['record']['kind'] == 'file' and not Path(raw).exists()]
        report['layout']['cinnamon_deleted_on_removal'] = deleted
        report['checks'].append('removed multi-instance settings files deleted by Cinnamon: ' + str(len(deleted)))
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
            # This uncovered right-edge strip lies outside both deliberately placed windows and all top menus.
            from PIL import Image
            with Image.open(path) as shot:
                width, height = shot.size
                crop = shot.convert('RGB').crop((int(width*.97), int(height*.40), int(width*.99), int(height*.65)))
                pixels = list(crop.getdata())
                lit = sum(max(rgb) > 40 for rgb in pixels) / len(pixels)
            return {'uncovered_strip_lit_fraction': lit, 'passed': lit >= .95}
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
        else: raise RuntimeError('Private wallpaper remained black before popup capture')
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
                    captures.append({'before': state, 'after': after, 'wallpaper': wallpaper})
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
            report['checks'].append(name + ' visibly open before and after capture; background strip verified')
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
        if calendar.exists(): raise RuntimeError('New calendar settings file left after restore: ' + str(calendar))
        if cinnamon.get_int('next-applet-id') != panel['next_applet_id']['after']: raise RuntimeError('next-applet-id changed by restore')
        restored = panels()
        expected_panels = GLib.Variant.parse(None, standin['panels-enabled'], None, None).unpack()
        if len(restored) != len(expected_panels):
            raise RuntimeError('Restored panel count differs: ' + json.dumps(restored))
        if cinnamon.get_strv('panels-enabled') != expected_panels:
            raise RuntimeError('Restored panels-enabled differs from the copied original layout')
        report['layout']['restored_panels'] = restored
        report['checks'].append('controller restore rehearsal: layout, zone arrays, desklets exact; settings files byte-exact; calendar file gone; next-applet-id kept')
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
    header = Gtk.HeaderBar(title=NAME); header.set_show_close_button(True); header.set_decoration_layout(APPEARANCE['button_layout']); window.set_titlebar(header)  # Left traffic lights follow the canonical design
    library = Gtk.MenuButton(label='Library'); header.pack_start(library)
    popover = Gtk.Popover.new(library); popover.set_modal(False)
    popbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5); popbox.set_border_width(10)
    model = Gtk.ModelButton(); model.props.text = 'Appearance'; popbox.add(model)
    listing = Gtk.ListBox(); listing.add(Gtk.Label(label='Audio library')); popbox.add(listing)
    popover.add(popbox); library.set_popover(popover)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15); box.set_border_width(25)
    box.add(Gtk.Label(label=NAME + ' | classic GNU-Darwin Aqua desktop preview'))
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
