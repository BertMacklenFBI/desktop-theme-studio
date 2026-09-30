#!/usr/bin/python3
"""Scoped current-collection apply/check/restore. A trial auto-restores after 300s."""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent
STUDIO = ROOT.parents[1]
HOME = Path.home()
OWNER = 'cinnamon-current-collection'
DEFAULT_ISLAND_UUID = 'nothing-island@desktop-theme-studio'

def applet_uuid(profile):
    """Return a validated profile-specific Island UUID or the legacy default."""
    value = profile.get('island_uuid', DEFAULT_ISLAND_UUID)
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value):
        raise RuntimeError('Unsafe Cinnamon Island UUID in profile')
    return value

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

# Reuse the tested appearance engine, symlink adapter, tree recovery, and lock.
sys.path.insert(0, str(ROOT.parent/'ocean-silk-current'))
base = module('collection_ocean_base', ROOT.parent/'ocean-silk-current/theme.py')
E = base.E
guard = base.guard
native_library_palette = module(
    'collection_native_library_palette', ROOT/'runtime-widgets/native-library-palette.py'
)
NATIVE_LIBRARY_UI = HOME/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/ui'
NATIVE_LIBRARY_APPEARANCE = NATIVE_LIBRARY_UI.parent/'appearance.json'
NATIVE_LIBRARY_CSS = NATIVE_LIBRARY_UI/'cinnamon-current-library.css'
watcher = base.watcher
syncer = base.syncer
eww_state = module('collection_eww_state', ROOT/'eww_state.py')
_raw_current = E.current

def current(action):
    # The watcher rewrites this JSON file with its own key order. Match the
    # complete object to the receipt for both check and restore, while all
    # other files retain the engine's original byte-strict comparison.
    return eww_state.normalise(action, _raw_current(action), syncer.THEME_STATE)

E.current = current
eww_reload = module('collection_eww_reload', ROOT/'eww_reload.py')
SPEC = E.load(ROOT/'profiles.json')['profiles']

# Optional rail panel layout (repairs/panel-rail-20260927/DESIGN.md). Only a
# profile with a profiles.json panel_layout, or a collection that already has a
# panel owner pointer, loads panel_layout.py; everything else runs as before.
PANEL_STATE = ROOT/'state'
PANEL_POINTER = PANEL_STATE/'panel-epoch.json'
PANEL_GATES = ROOT/'verification/isolated-latest'
_panel_layout = None
_panel_warned = set()
_workspace_runtime = None

def workspace_runtime():
    global _workspace_runtime
    if _workspace_runtime is None:
        _workspace_runtime = module('collection_workspace_runtime', ROOT/'workspace_runtime.py')
    return _workspace_runtime

def panel_layout():
    global _panel_layout
    if _panel_layout is None:
        _panel_layout = module('collection_panel_layout', ROOT/'panel_layout.py')
    return _panel_layout

def panel_pointer_exists():
    return os.path.lexists(PANEL_POINTER)

def panel_bus(method, *args):
    return base.run('gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method',method,*args)

def tidal_desktop():
    return module('collection_tidal_desktop', ROOT/'contributions/tidal-integration/desktop/tidal_switch.py')

def plan_panel(slug, profile):
    """The receipt panel section (to-rail/to-home/none), or None for normal->normal."""
    if 'panel_layout' not in profile and not panel_pointer_exists() and slug != 'tidal-observatory':
        return None
    engine = panel_layout()
    tidal_owner = False
    if panel_pointer_exists():
        owner = engine.derive_state(PANEL_STATE)
        if owner['state'] != 'home':
            _, active = engine._owner_blocks(owner)
            tidal_owner = active.get('profile') == 'tidal-observatory'
    if slug == 'tidal-observatory' or tidal_owner:
        panel = tidal_desktop().plan_transition(engine, slug, ROOT/'generated'/slug, PANEL_STATE, PANEL_GATES)
    else:
        panel = engine.plan_transition(slug, profile, PANEL_STATE, PANEL_GATES)
    gate = (panel or {}).get('gate') or {}
    if panel is not None and not gate.get('required') and gate.get('status') not in (None, 'passed'):
        warning = ('theme: warning: the isolated rail rehearsal of '+str(panel.get('rail_profile'))+' is '
                   +str(gate.get('status'))+' ('+'; '.join(gate.get('reasons') or [])+'); leaving the rail is still allowed')
        if warning not in _panel_warned:
            _panel_warned.add(warning)
            print(warning, file=sys.stderr, flush=True)
    return panel

def panel_restore_message(data, result):
    """Every deferred row and file with its live and expected value (API v1.3 B4), then the errors and the cause."""
    panel = data['panel']
    lines = [str(line) for line in result.get('explain') or []]
    if not lines:
        lines = [panel.get('restore_deferred') or 'incomplete']
        rest = [c for c in result.get('conflicts') or [] if not str(c).startswith('reload:')]
        if rest: lines += panel_layout().explain_problems(panel, rest, 'either')
    # Each row once: a preflight conflict is already embedded in restore_deferred (the first line).
    unique = []
    for line in lines:
        if line and not any(line in seen for seen in unique): unique.append(line)
    errors = []
    for error in [str(e) for e in panel.get('restore_errors') or []]:
        if error and not any(error in seen for seen in unique + errors): errors.append(error)
    if result.get('save_error'): errors.append('journal: '+str(result['save_error']))
    return ('Desktop restored; panel layout recovery '+str(result.get('status'))+': '+'; '.join(unique)
            +('; errors: '+'; '.join(errors) if errors else '')
            +('; cause: '+str(data['error']) if data.get('error') else ''))

def journal(path, data):
    E.journal(path, data)
    marker = STUDIO/'state/activation-trials'/('collection-'+data['id']+'.json')
    E.journal(marker, {'preset': OWNER, 'receipt': str(path), 'status': data['status']})

base.journal = lambda path, data: journal(path, data)

def ensure_closed(data):
    for name in sorted({a['requires_closed'] for a in data['actions'] if a.get('requires_closed')}):
        base.apps.ensure_closed(name)

E.preflight = ensure_closed

def add(data, action, value):
    if 'path' in action and action['kind'] != 'symlink' and Path(action['path']).is_symlink():
        raise RuntimeError('Expected regular appearance file: '+action['path'])
    action['after'] = value
    action.setdefault('before', E.current(action))
    if action['before'] != value:
        data['actions'].append(action)

def eww_managed_block(path, raw, marker, body):
    """Return a parser-valid Eww managed-block replacement.

    Yuck uses ``;;`` comments and SCSS uses ``//``. Also recognize the
    earlier invalid ``#`` marker so an interrupted legacy trial is repaired
    instead of leaving an unparseable block beside the corrected one.
    """
    comment = ';;' if path.suffix == '.yuck' else '//'
    begin, end = f'{comment} BEGIN {marker}', f'{comment} END {marker}'
    replacement = begin+'\n'+body+'\n'+end
    matches = []
    for old_begin, old_end in ((begin,end),(f'# BEGIN {marker}',f'# END {marker}')):
        matches.extend(re.finditer(re.escape(old_begin)+r'.*?'+re.escape(old_end),raw,re.S))
    if len(matches) > 1:
        raise RuntimeError(f'Duplicate managed Eww block in {path}: {marker}')
    before = matches[0].group() if matches else ''
    return before, replacement if matches else '\n'+replacement+'\n'

def settings_action(data, schema, key, value):
    settings = Gio.Settings.new(schema)
    if key in settings.props.settings_schema.list_keys():
        add(data, {'kind':'gsetting','schema':schema,'key':key},
            GLib.Variant(settings.get_value(key).get_type_string(), value).print_(True))

def file_action(data, path, raw, mode=None):
    action={'kind':'file','path':str(path)}
    if Path(path).is_file():action['before_mode']=Path(path).stat().st_mode & 0o777
    if mode is not None:action['after_mode']=mode
    add(data, action, base64.b64encode(raw).decode())

def refresh_native_library_theme(data):
    """Ask only an existing native library process to apply its new CSS in place."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION,None)
    owned = bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                         'NameHasOwner',GLib.Variant('(s)',('com.bertmacklen.MintDashboard',)),
                         GLib.VariantType('(b)'),Gio.DBusCallFlags.NONE,3000,None).unpack()[0]
    if not owned:
        data.setdefault('native_library_theme_refreshes',[]).append({'status':'not-running','launched':False})
        return
    command = ['gdbus','call','--session','--dest','com.bertmacklen.MintDashboard',
               '--object-path','/com/bertmacklen/MintDashboard',
               '--method','org.gtk.Actions.Activate','plum-refresh-theme','[]','{}']
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=5)
        record = {'returncode':result.returncode,
                  'stdout':result.stdout[-1000:], 'stderr':result.stderr[-1000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A missing hidden library is expected. The next fresh UI launch reads
        # the same generated CSS and appearance file; never restart or kill it.
        record = {'returncode':None, 'stderr':str(exc)}
    data.setdefault('native_library_theme_refreshes',[]).append(record)

def ini_action(data, path, section, key, value):
    add(data, {'kind':'ini','path':str(path),'section':section,'key':key}, value)

def validate(data):
    E.validate_protected(data)
    if data.get('workspace_runtime') and data.get('id'):
        workspace_runtime().assert_owner(ROOT/'state'/data['id']/'receipt.json',data,ROOT,base)
    if 'panel' in data:
        panel = data['panel']
        problems = panel_layout().validate_problems(data)
        if problems:
            raise RuntimeError('Panel layout changed externally; reconcile before continuing: '
                               +'; '.join(panel_layout().explain_problems(panel, problems)))
        if panel.get('phase') == 'planned':
            refusal = panel_layout().gate_problem(panel)
            if refusal: raise RuntimeError(refusal)
        return
    if base.layout() != data['panel_before']:
        if not panel_pointer_exists():
            raise RuntimeError('Panel layout changed externally; reconcile before continuing')
        # Once a panel owner pointer exists, a rail transaction may have
        # allocated applet ids since this receipt: next-applet-id may only grow.
        problems = panel_layout().legacy_layout_problems(data['panel_before'], PANEL_STATE)
        if problems:
            now = base.layout()
            raise RuntimeError('Panel layout changed externally; reconcile before continuing: '+'; '.join(
                'org.cinnamon/'+key+': live '+str(now.get(key))+', expected '+str(data['panel_before'][key]) for key in problems))

def plan(slug, with_apps=True):
    profile = SPEC[slug]
    island_id = applet_uuid(profile)
    generated = ROOT/'generated'/slug
    manifest = E.load(generated/'manifest.json')
    for relative, digest in manifest['generated_files'].items():
        if E.digest((generated/relative).read_bytes()) != digest:
            raise RuntimeError('Generated asset changed without rebuild: '+relative)
    data = {'profile':slug,'name':profile['name'],'actions':[], 'protected_hashes':{},
            'required_assets':{},'panel_before':base.layout(),'skipped':[], 'trees':[]}
    panel = plan_panel(slug, profile)
    if panel is not None: data['panel'] = panel
    protected = [HOME/'.bashrc']+[HOME/'.local/bin'/n for n in
        ('play','playalbum','pause','next','prev','nextalbum','prevalbum','mpv-vis','fastfetch')]
    protected += [HOME/'.config/fastfetch'/n for n in ('modes.py','long.jsonc')]
    island = HOME/'.local/share/cinnamon/applets'/island_id
    if slug != 'tidal-observatory':
        protected += [p for p in island.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                      and p.name not in ('applet.js','stylesheet.css')]
    for path in protected:
        if path.is_file(): data['protected_hashes'][str(path)] = E.digest(path.read_bytes())
    # Some applications ask for the historical Xcursor diagonal names.
    # Select the existing custom diagonal pixels rather than Oxygen fallback.
    cursors=Path(profile['sources']['cursor'])/'cursors'
    if cursors.is_dir():
        for alias,target_name in {'fd_double_arrow':'size_fdiag','bd_double_arrow':'size_bdiag'}.items():
            dest=cursors/alias;custom=cursors/target_name
            if not custom.is_file():raise RuntimeError('Missing custom resize cursor '+str(custom))
            data['protected_hashes'][str(custom)]=E.digest(custom.read_bytes())
            if not dest.exists() and not dest.is_symlink():
                add(data,{'kind':'symlink','path':str(dest)},target_name)
            elif not dest.is_symlink() or dest.readlink()!=Path(target_name):
                data['skipped'].append('Existing cursor alias preserved: '+str(dest))
    else:
        data['skipped'].append('Inherited cursor family has no custom bitmap aliases; inspect cursor states after selection')
    source = generated/'desktop'/profile['theme']
    target = HOME/'.themes'/profile['theme']
    data['trees'] = [{'source':str(source),'target':str(target),'sha':base.tree(source),
                      'before_sha':base.tree_state(target)}]
    for kind, name in (('icons', profile['icons']), ('cursors', profile['cursor'])):
        dependency = generated/kind/name
        if dependency.is_dir():
            destination = HOME/'.icons'/name
            data['trees'].append({'source':str(dependency),'target':str(destination),
                                  'sha':base.tree(dependency),'before_sha':base.tree_state(destination)})
    runtime, runtime_tree = workspace_runtime().plan(slug,profile,ROOT,HOME,base)
    if runtime is not None:
        runtime['tree_index'] = len(data['trees'])
        data['workspace_runtime'] = runtime
        data['trees'].append(runtime_tree)
        for source_file in Path(runtime_tree['source']).rglob('*'):
            if source_file.is_file():
                data['required_assets'][str(source_file)] = E.digest(source_file.read_bytes())
    for tree_item in data['trees']:
        base.prepare_tree_record(tree_item)
    if slug == 'tidal-observatory':
        tidal_desktop().add_island_tree(data, generated, HOME, base)
    else:
        for name in ('applet.js','stylesheet.css'):
            src = generated/'island'/island_id/name
            file_action(data, island/name, src.read_bytes())
            data['required_assets'][str(src)] = E.digest(src.read_bytes())
    for schema in ('org.cinnamon.desktop.interface','org.gnome.desktop.interface'):
        for key, value in {'gtk-theme':profile['theme'],'icon-theme':profile['icons'],
                           'cursor-theme':profile['cursor'],'color-scheme':'prefer-light' if profile['light'] else 'prefer-dark',
                           'gtk-application-prefer-dark-theme':not profile['light']}.items():
            settings_action(data, schema, key, value)
        if 'cursor_size' in profile:
            size = profile['cursor_size']
            if type(size) is not int or not 16 <= size <= 96:
                raise RuntimeError('Invalid cursor size')
            settings_action(data, schema, 'cursor-size', size)
        typography = profile.get('typography', {})
        for key, role in {'font-name':'interface', 'document-font-name':'document',
                          'monospace-font-name':'monospace'}.items():
            if role in typography:
                settings_action(data, schema, key, typography[role])
    if 'titlebar' in profile.get('typography', {}):
        settings_action(data, 'org.cinnamon.desktop.wm.preferences', 'titlebar-font',
                        profile['typography']['titlebar'])
    for schema in ('org.cinnamon.desktop.background','org.gnome.desktop.background'):
        settings_action(data, schema, 'picture-uri', (generated/'artwork/wallpaper.png').as_uri())
    settings_action(data, 'org.cinnamon.desktop.wm.preferences', 'theme', profile['theme'])
    settings_action(data, 'org.cinnamon.theme', 'name', profile['theme'])
    for version in ('3.0','4.0'):
        for key, value in {'gtk-theme-name':profile['theme'],'gtk-icon-theme-name':profile['icons'],
                           'gtk-cursor-theme-name':profile['cursor'],
                           'gtk-application-prefer-dark-theme':str(int(not profile['light']))}.items():
            ini_action(data, HOME/f'.config/gtk-{version}/settings.ini', 'Settings', key, value)
        if 'cursor_size' in profile:
            ini_action(data, HOME/f'.config/gtk-{version}/settings.ini', 'Settings', 'gtk-cursor-theme-size', str(profile['cursor_size']))
    gtk4_names = ('gtk.css','gtk-dark.css','assets') if profile.get('gtk4_assets', True) else ('gtk.css','gtk-dark.css')
    for name in gtk4_names:
        dest = HOME/'.config/gtk-4.0'/name
        if not (source/'gtk-4.0'/name).exists():
            raise RuntimeError('Missing GTK4 source '+name)
        action = {'kind':'symlink','path':str(dest)}
        if isinstance(E.current(action), dict) and E.current(action) != E.MISSING:
            raise RuntimeError('Expected GTK4 symlink '+str(dest))
        add(data, action, str(target/'gtk-4.0'/name))
    gtk2 = HOME/'.gtkrc-2.0'
    if gtk2.exists():
        for key, value in {'gtk-theme-name':profile['theme'],'gtk-icon-theme-name':profile['icons']}.items():
            match = re.search(r'^'+key+r'\s*=.*$', gtk2.read_text(), re.M)
            if match: add(data, {'kind':'text','path':str(gtk2),'before':match.group(),'count':1}, key+'="'+value+'"')
    for relative in ('.icons/default/index.theme','.local/share/icons/default/index.theme'):
        ini_action(data, HOME/relative, 'Icon Theme', 'Inherits', profile['cursor'])
    entries = Gio.Settings.new('org.cinnamon').get_strv('enabled-applets')
    menu_ids = [str(ident) for ident, (uuid, _) in panel_layout().parse_live(list(entries)).items()
                if uuid == 'menu@cinnamon.org']
    if len(menu_ids) != 1: raise RuntimeError('Expected one current menu instance')
    data['menu_id'] = menu_ids[0]
    menu = HOME/'.config/cinnamon/spices/menu@cinnamon.org'/(menu_ids[0]+'.json')
    if data.get('panel', {}).get('layout_kind') != 'tidal-two-panel':
        add(data, {'kind':'json','path':str(menu),'keys':['menu-icon','value']}, str(generated/'artwork/menu-logo.png'))
    # Merge only generated Eww appearance variables; preserve widget behavior.
    pal = E.load(generated/'widgets/eww-palette.json')[profile['theme']]
    # Register every collection palette with the persistent watcher before it
    # resumes. Without this key a newly introduced theme name falls back to
    # Graphite & Brass immediately after an otherwise successful keep.
    add(data, {'kind':'json','path':str(syncer.PALETTES),'keys':[profile['theme']]}, pal)
    scss = syncer.STYLESHEET
    raw = scss.read_text()
    start = raw.index(syncer.START); end = raw.index(syncer.END,start)+len(syncer.END)
    add(data, {'kind':'text','path':str(scss),'before':raw[start:end],'count':1}, syncer.render_variables(pal))
    file_action(data, syncer.MUSIC_ARTWORK, syncer.render_music_artwork(pal).encode())
    file_action(data, syncer.THEME_STATE, (json.dumps({'cinnamon_theme':profile['theme'],'palette':profile['theme'],'selection_text':syncer.selection_text(pal),**pal},indent=2)+'\n').encode())
    if slug == 'red-panda-overtime':
        bundle = generated/'widgets/rpo'
        destinations = {
            'red-panda-overtime.scss': HOME/'Documents/eww-graphite-brass/config/themes/red-panda-overtime.scss',
            'red-panda-overtime.yuck': HOME/'Documents/eww-graphite-brass/config/red-panda-overtime.yuck',
            'rpo-flow.py': HOME/'Documents/eww-graphite-brass/config/scripts/rpo-flow.py',
            'rpo-widgets': HOME/'Documents/eww-graphite-brass/bin/rpo-widgets',
        }
        for name, destination in destinations.items():
            file_action(data, destination, (bundle/name).read_bytes(),
                        0o755 if name in ('rpo-flow.py','rpo-widgets') else None)
        for path, marker, body in (
            (HOME/'Documents/eww-graphite-brass/config/eww.scss', 'Red Panda Overtime', '@import "themes/red-panda-overtime";'),
            (HOME/'Documents/eww-graphite-brass/config/eww.yuck', 'Red Panda Overtime', '(include "red-panda-overtime.yuck")'),
        ):
            raw = path.read_text()
            before, after = eww_managed_block(path, raw, marker, body)
            add(data, {'kind':'text','path':str(path),'before':before,'count':1}, after)
    # Tidal cards are additive. Leaving the theme removes only its owned includes.
    tidal_eww = module('collection_tidal_eww', ROOT/'contributions/tidal-integration/apps/integration.py')
    from types import SimpleNamespace
    tidal_eww.plan_actions(SimpleNamespace(file_action=file_action, add=add), data, generated, HOME,
                           slug == 'tidal-observatory')
    # The native Music Library gets a final profile-token CSS layer. Its
    # permanent in-process hook applies this in-place without rebuilding its
    # index, changing its search, or touching player/MPRIS behavior.
    if NATIVE_LIBRARY_APPEARANCE.is_file():
        native_logo = generated/'artwork/menu-logo.png'
        for key, value in native_library_palette.appearance_tokens(profile, native_logo).items():
            add(data, {'kind':'json','path':str(NATIVE_LIBRARY_APPEARANCE),'keys':['theme',key]}, value)
        file_action(data, NATIVE_LIBRARY_CSS, native_library_palette.stylesheet(profile).encode())
    else:
        data['skipped'].append('Native Music Library appearance.json is absent; preserved its existing UI state')
    if with_apps:
        adapter_path = ROOT/'runtime-apps/activation.py'
        if adapter_path.exists():
            adapter = module('collection_app_activation', adapter_path)
            result = adapter.plan_actions(slug, profile, HOME, E)
            for action in result['actions']: action['stage']='applications'
            data['actions'].extend(result['actions'])
            data['skipped'].extend(result.get('skipped',[]))
            data['protected_hashes'].update(result.get('protected_hashes',{}))
        else: data['skipped'].append('Application activation adapter is not yet built')
    return data

def refresh(data):
    base.island_actions.reload(base.run)
    base.run('gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.ReloadTheme')
    if data.get('workspace_runtime'):
        workspace_runtime().reload_checked(data,panel_bus,HOME)
    menu = HOME/'.config/cinnamon/spices/menu@cinnamon.org'/(data['menu_id']+'.json')
    icon = E.load(menu)['menu-icon']['value']
    base.run('gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.updateSetting','menu@cinnamon.org',data['menu_id'],'menu-icon',json.dumps(icon))
    eww_root = HOME/'Documents/eww-graphite-brass'
    attempts = eww_reload.reload_checked(eww_root/'bin/eww', eww_root/'config', eww_root/'state/eww.log')
    data.setdefault('eww_reloads', []).append({'at':time.time(), 'attempts':attempts})
    refresh_native_library_theme(data)

def check(data):
    validate(data)
    bad = [str(a.get('path',a.get('key'))) for a in data['actions'] if a.get('attempted') and E.current(a)!=a['after']]
    for tree_item in data['trees']:
        try:
            version=tree_item.get('fingerprint_version')
            if version is not None:
                base.validate_ancestry(tree_item)
            if version is None:
                conflict=base.tree_state(tree_item['target'])!=base.tree_with_nested_actions(tree_item,data)
            elif version!=base.TREE_FINGERPRINT_VERSION:
                conflict=True
            else:
                conflict=base.observed_tree_fingerprint(tree_item,data)!=tree_item.get('installed_fingerprint')
            if conflict:bad.append(tree_item['target'])
        except Exception:
            bad.append(tree_item['target'])
    if 'panel' in data:
        bad.extend(panel_layout().panel_problems(data['panel'], 'after'))
    return bad

def restore(path, data):
    if data.get('workspace_runtime') and data['status']!='restored':
        workspace_runtime().assert_owner(path,data,ROOT,base)
    if 'panel' in data and data['status']!='restored':
        refusal=panel_layout().restore_order_problem(PANEL_STATE,path)
        if refusal: raise RuntimeError(refusal)
    try:
        if data['status']=='restored': return
        validate(data)
        desktop=[a for a in data['actions'] if a.get('stage')!='applications' and not a.get('requires_closed')]
        applications=[a for a in data['actions'] if a not in desktop]
        # Application writers may reopen during a trial. They cannot delay
        # recovery of the shell, wallpaper, GTK theme, and widget palette.
        for action in desktop:
            if action.get('attempted') and E.current(action) not in (action['before'],action['after']):
                raise RuntimeError('Restore conflict: '+str(action.get('path',action.get('key'))))
        panel_result=None
        if 'panel' in data:
            # Settled receipt: external panel edits refuse (nothing written yet).
            # Transitional receipt: the off rows Cinnamon or a kill left are deferred.
            deferred_rows=panel_layout().restore_preflight(path,data,PANEL_STATE)
        base.preflight_trees(path,data)
        watcher.pause(path.parent/'watcher.json')
        data['status']='restoring';journal(path,data)
        if 'panel' in data:
            # The reverse panel transition goes first (Indigo order). It never raises;
            # the appearance and tree restore below always continue.
            panel_result=panel_layout().restore_panel(path,data,journal,panel_bus,conflicts=deferred_rows,automatic=True)
        scope={**data,'actions':desktop}
        E.restore(scope,path.parent/'desktop-restore.json')
        journal(path,data)
        base.restore_trees(path,data)
        if data.get('workspace_runtime'): data['workspace_restoring']=True
        refresh(data)
        if data.get('workspace_runtime'): workspace_runtime().restore_owner(path,data,ROOT,base,journal)
        data['desktop_restored']=True;journal(path,data)
        deferred=[]
        for action in reversed(applications):
            if not action.get('attempted'):continue
            try:
                scope={**data,'actions':[action]}
                ensure_closed(scope)
                E.restore(scope,path.parent/'application-restore.json')
                journal(path,data)
            except Exception as exc:
                deferred.append({'path':action.get('path',action.get('key')),'reason':str(exc)})
        data['deferred_application_restore']=deferred
        # Extension color fragments are application-stage actions. Refresh
        # after restoring those files as well as after shell recovery above.
        base.run('gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.ReloadTheme')
        if panel_result is not None and panel_result['status']!='restored':
            data['status']='recovery-required';journal(path,data)
            raise RuntimeError(panel_restore_message(data,panel_result))
        validate(data)
        if deferred:
            data['status']='recovery-required';journal(path,data)
            raise RuntimeError('Desktop restored; application recovery deferred: '+str(deferred))
        data['status']='restored';journal(path,data)
    except Exception as exc:
        if 'panel' in data: panel_layout().mark_restore_attempted(data['panel'])
        data.update(status='recovery-required',recovery_error=str(exc));journal(path,data);raise
    finally:
        try:watcher.resume(path.parent/'watcher.json')
        except Exception as exc:
            data.update(status='recovery-required',watcher_resume_error=str(exc))
            journal(path,data)
            raise

def apply(slug, transaction_id=None):
    guard.assert_allowed(STUDIO,OWNER,'apply')
    workspace_acceptance=workspace_runtime().require_activation(slug,SPEC[slug],ROOT,module)
    data=plan(slug);ensure_closed(data);validate(data)
    if workspace_acceptance is not None: data['workspace_acceptance']=workspace_acceptance
    isolated=E.load(ROOT/'verification/isolated-latest'/f'{slug}.json')
    if isolated.get('status')!='passed': raise RuntimeError('Passing isolated Cinnamon preview required')
    preview=module('collection_preview_hashes',ROOT/'isolate.py')
    generated=ROOT/'generated'/slug
    island_id = applet_uuid(SPEC[slug])
    for key,source in [('theme_sha256',generated/'desktop'/SPEC[slug]['theme']),
                       ('island_sha256',generated/'island'/island_id)]:
        if isolated.get(key)!=preview.sha256_tree(source):
            raise RuntimeError('The exact generated assets require an updated isolated preview: '+key)
    stamp=transaction_id or time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6,32}', stamp):
        raise ValueError('Invalid desktop transaction id')
    path=ROOT/'state'/stamp/'receipt.json'
    data.update(id=stamp,status='applying',deadline=time.time()+300,unit='collection-recovery-'+stamp,
                environment={key:os.environ[key] for key in ('DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS') if key in os.environ})
    path.parent.mkdir(parents=True);base.tree_paths(path,data)
    if 'panel' not in data:
        journal(path,data)
        E.journal(ROOT/'state/latest.json',{'receipt':str(path)})
    try:
        if 'panel' in data:
            panel_layout().prepare_receipt(path,data['panel']);journal(path,data)
            E.journal(ROOT/'state/latest.json',{'receipt':str(path)})
            panel_layout().write_pointer(PANEL_STATE,path)
        base.run('systemd-run','--user','--unit',data['unit'],'--on-active=300s','--timer-property=AccuracySec=1s',
                 '/usr/bin/python3',__file__,'restore','--receipt',path,'--automatic')
        if 'panel' in data or data.get('workspace_runtime'): data['guard_armed']=True;journal(path,data)
        # Acceptance and planning must describe the same reviewed runtime.
        # Source changes after planning are also refused by the tree engine.
        current_acceptance=workspace_runtime().require_activation(slug,SPEC[slug],ROOT,module)
        if current_acceptance != workspace_acceptance:
            raise RuntimeError('Workspace acceptance changed between preflight and guarded mutation')
        if data.get('workspace_runtime'): workspace_runtime().begin(path,data,ROOT,base,journal)
        watcher.pause(path.parent/'watcher.json')
        # Recheck every plan snapshot before the shared installer begins its
        # staged copies; it repeats this check immediately before each rename.
        for tree_item in data['trees']:
            base.assert_planned_tree_current(tree_item)
        base.install_trees(path,data)
        if 'panel' in data: panel_layout().apply_panel(path,data,journal,panel_bus)
        for action in data['actions']:
            observed=E.current(action)
            if observed not in (action['before'],action['after']): raise RuntimeError('Appearance changed while applying '+str(action.get('path',action.get('key'))))
            if observed==action['after']: action['converged_during_apply']=True
            action['attempted']=True;journal(path,data)
            E.write(action,action['after']);action['applied']=True;journal(path,data)
        refresh(data)
        bad=check(data)
        if bad: raise RuntimeError('Applied state mismatch: '+str(bad))
        if 'panel' in data: panel_layout().mark_pending(data['panel'])
        if data.get('workspace_runtime'): workspace_runtime().finish(path,data,ROOT,base,'pending',journal)
        data['status']='pending';journal(path,data)
    except Exception as exc:
        data['error']=str(exc);journal(path,data)
        if 'panel' not in data: restore(path,data)
        else:
            try: restore(path,data)
            except Exception as recovery:
                raise RuntimeError(str(exc)+'; recovery: '+str(recovery)) from exc
        raise
    return path,data

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','apply','check','keep','restore'))
    parser.add_argument('profile',nargs='?',choices=list(SPEC))
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--automatic',action='store_true')
    args=parser.parse_args()
    with guard.locked(STUDIO,wait=args.action=='restore'):
        if args.action in ('plan','apply'):
            if not args.profile: parser.error('profile is required')
            if args.action=='plan':
                data=plan(args.profile)
                summary={'profile':args.profile,'actions':len(data['actions']),'trees':data['trees'],'skipped':data['skipped']}
                if 'panel' in data: summary['panel']=panel_layout().plan_summary(data['panel'])
                print(json.dumps(summary,indent=2));return
            path,data=apply(args.profile)
        else:
            path=(args.receipt or Path(E.load(ROOT/'state/latest.json')['receipt'])).resolve()
            if path.parent.parent!=ROOT/'state' or path.name!='receipt.json':raise RuntimeError('Foreign receipt')
            data=E.load(path)
            os.environ.update(data['environment'])
            if args.action=='restore' and args.automatic and data['status'] in ('kept','restored'):return
            guard.assert_allowed(STUDIO,OWNER,'rollback' if args.action=='restore' else 'refine-keep' if args.action=='keep' else 'check',path)
            if args.action=='restore':restore(path,data)
            else:
                bad=check(data)
                if bad:raise RuntimeError('Check failed: '+str(bad))
                if args.action=='keep':
                    if data['status']!='pending' or time.time()>=data['deadline']:raise RuntimeError('Trial is not pending or expired')
                    keep(path,data)
        print(json.dumps({'profile':data['profile'],'receipt':str(path),'status':data['status'],'skipped':data['skipped']},indent=2))

def keep(path,data):
    # Leave the pending receipt and automatic recovery intact until the exact
    # watcher pause obligation has been discharged. A leftover timer is then
    # harmless: automatic restore checks the durable kept status under lock.
    watcher.resume(path.parent/'watcher.json')
    if data.get('workspace_runtime'): workspace_runtime().finish(path,data,ROOT,base,'kept',journal)
    if 'panel' in data: panel_layout().mark_kept(data['panel'])
    data['status']='kept';journal(path,data)
    try:base.run('systemctl','--user','stop',data['unit']+'.timer')
    except Exception as exc:
        data['timer_cleanup_warning']=str(exc);journal(path,data)

if __name__=='__main__':main()
