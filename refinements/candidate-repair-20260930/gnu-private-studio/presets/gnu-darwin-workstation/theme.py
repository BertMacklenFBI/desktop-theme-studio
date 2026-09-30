#!/usr/bin/python3
"""Coordinate the complete GNU-Darwin Workstation user theme; the independent recovery timer restores every stage.

Stages, in apply order: desktop_control.py trial (GTK/Cinnamon/icons/cursors/fonts/wallpaper plus the three
asset trees, and the approved right dock and bottom application row), then applications/adapter.py (terminals, editors,
Fastfetch, prompt, KDE, Flatpak), then any optional desktop-owned stage present under desktop/. Eww, the music
widget, mpv and cava are protected and never touched. The panel layout is journaled and restored by the core
(design.json panel_layout.restore); the menu stage is restored right after the core (restore step 5).
`plan` is read-only.
"""
from pathlib import Path
import argparse, importlib.util, json, os, signal, subprocess, sys, time

ROOT = Path(__file__).resolve().parent
CORE = ROOT / 'desktop_control.py'
APPS = ROOT / 'applications/adapter.py'
RUNTIME = ROOT / 'applications/runtime'
BASE_STAGES = ((APPS, 'applications.json'),)
# Desktop-owned stages are applied only when their script exists. Each must implement
# plan / apply --state P --commit / check --state P / restore --state P --commit.
OPTIONAL_STAGES = ((ROOT / 'desktop/widget_adapter.py', 'widgets.json'), (ROOT / 'desktop/menu_adapter.py', 'menu.json'),
                   (ROOT / 'desktop/workspace_adapter.py', 'workspace.json'), (ROOT / 'desktop/logo_adapter.py', 'logo.json'))
# design.json panel_layout.restore step 5: menu-icon/menu-custom come back after the core's panel steps 1-4.
AFTER_CORE = ('menu.json',)
REFINEMENTS = ()
GTILE_CSS = Path.home() / '.local/share/cinnamon/extensions/gTile@shuairan/stylesheet.css'


def save_json(path, data):
    from desktop_control import save
    save(path, data)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def present_optional():
    return tuple((script, name) for script, name in OPTIONAL_STAGES if script.is_file())


def init_manifest(path, existing=False):
    manifest = path.parent / 'stages.json'
    if manifest.exists(): raise RuntimeError('Stage manifest already exists')
    required = [name for _, name in BASE_STAGES]
    if not existing: required += [name for _, name in REFINEMENTS]
    if existing:
        required += [name for _, name in OPTIONAL_STAGES if (path.parent / name).exists()]
        missing = [name for name in required if not (path.parent / name).is_file()]
        if missing: raise RuntimeError('Cannot register incomplete release: ' + ', '.join(missing))
    save_json(manifest, {'version': 1, 'required': required, 'started': required[:] if existing else []})


def start_stage(path, name):
    manifest = path.parent / 'stages.json'
    data = json.loads(manifest.read_text())
    if name not in data['required']: data['required'].append(name)
    if name not in data['started']: data['started'].append(name)
    save_json(manifest, data)


def missing_receipts(path, recovery=False):
    manifest = path.parent / 'stages.json'
    if manifest.exists():
        data = json.loads(manifest.read_text())
        names = data['started' if recovery else 'required']
    else:
        # Older completed releases still require their original mandatory stages.
        state = json.loads(path.read_text())
        names = ['applications.json'] if state['status'] in ('kept', 'pending', 'restored') else []
    return [name for name in names if not (path.parent / name).is_file()]


def unfinished_recovery(path):
    p = path.parent / 'recovery.json'
    return p.exists() and json.loads(p.read_text()).get('status') == 'recovery-required'


def call(script, *args, timeout=60):
    p = subprocess.run(['/usr/bin/python3', str(script), *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if p.returncode: raise RuntimeError(p.stdout + p.stderr)
    print(p.stdout.strip(), flush=True)
    return p.stdout


def receipt():
    ident = json.loads((ROOT / 'state/latest.json').read_text())['id']
    return ROOT / 'state' / ident / 'desktop.json'


def refresh():
    """Reload the shell theme and the gTile stylesheet; Eww is deliberately left alone."""
    import desktop_control
    desktop_control.bus('org.Cinnamon.ReloadTheme')
    if GTILE_CSS.is_file():
        css = str(GTILE_CSS)
        code = '(()=>{let t=imports.gi.St.ThemeContext.get_for_stage(global.stage).get_theme(); t.unload_stylesheet(' + json.dumps(css) + ');t.load_stylesheet(' + json.dumps(css) + ');return true;})()'
        answer = desktop_control.bus('org.Cinnamon.Eval', code)
        if not answer.startswith('(true,'): raise RuntimeError('gTile stylesheet refresh: ' + answer)


def recover(path, automatic=False):
    state = json.loads(path.read_text())
    # Aggregate gate: an incomplete supplemental recovery keeps the timer retrying even when the
    # core receipt already says restored (the defect the older pastel/moonstone controllers kept).
    if automatic and not unfinished_recovery(path) and state['status'] not in ('applying', 'pending', 'restoring', 'recovery-required'):
        return {'status': state['status'], 'timer': 'ignored'}
    errors = ['Missing attempted stage receipt: ' + name for name in missing_receipts(path, recovery=True)]
    # Application appearance restores first, then the desktop core, then the menu stage (AFTER_CORE).
    for script, filename in (*reversed(REFINEMENTS), *reversed(OPTIONAL_STAGES), *reversed(BASE_STAGES)):
        p = path.parent / filename
        if p.exists() and filename not in AFTER_CORE:
            try: call(script, 'restore', '--state', p, '--commit', timeout=45)
            except Exception as error: errors.append(str(error))
    try:
        if state['status'] != 'restored':
            call(CORE, 'recover' if automatic else 'restore', '--receipt', path, timeout=60)
    except Exception as error: errors.append(str(error))
    for script, filename in reversed(OPTIONAL_STAGES):
        p = path.parent / filename
        if p.exists() and filename in AFTER_CORE:
            try: call(script, 'restore', '--state', p, '--commit', timeout=45)
            except Exception as error: errors.append(str(error))
    try: refresh()
    except Exception as error: errors.append('refresh: ' + str(error))
    status = 'recovery-required' if errors else 'restored'
    save_json(path.parent / 'recovery.json', {'status': status, 'errors': errors})
    return {'status': status, 'errors': errors}


def check(path):
    missing = missing_receipts(path)
    if missing: raise RuntimeError('Missing required stage receipts: ' + ', '.join(missing))
    if unfinished_recovery(path): raise RuntimeError('A previous recovery is incomplete; inspect recovery.json')
    composed = module('gnu_darwin_workstation_composed_checks', RUNTIME / 'composed.py')
    # Compose in apply order. Checks use temporary expectations; restore and
    # keep always retain the original receipts and their exact before values.
    stages = (*BASE_STAGES, *OPTIONAL_STAGES, *REFINEMENTS)
    existing = [(script, path.parent / name) for script, name in stages if (path.parent / name).exists()]
    allowed_ui = ()  # No shell or protected music files are appearance targets.
    with composed.receipts([path, *(journal for _, journal in existing)], allowed_protected=allowed_ui) as checks:
        logo=path.parent/'logo.json'
        overlay=('--overlay-state',logo) if logo.exists() and any(a.get('applied') and a.get('kind')=='suffix' for a in json.loads(logo.read_text()).get('actions',[])) else ()
        call(CORE, 'check', '--receipt', checks[path], *overlay)
        for script, journal in existing:
            call(script, 'check', '--state', checks[journal])


def plan():
    """Read-only: current enhanced release, or the original prospective stage plans."""
    latest = ROOT / 'state/latest.json'
    if latest.is_file():
        path = receipt()
        workspace = path.parent / 'workspace.json'
        manifest_path = path.parent / 'stages.json'
        if workspace.is_file() and manifest_path.is_file():
            workspace_state = json.loads(workspace.read_text())
            manifest = json.loads(manifest_path.read_text())
            if ('workspace.json' in manifest.get('required', []) and
                    workspace_state.get('status') == 'applied' and
                    any(action.get('applied') for action in workspace_state.get('actions', []))):
                # A kept release with an additive picker has a deliberately different
                # panel target. Describe it without reallocating instances or calling
                # prospective stage planners that would attempt duplicate additions.
                import desktop_control as control
                composed = module('gnu_darwin_workstation_plan_projection', RUNTIME / 'composed.py')
                state = json.loads(path.read_text())
                existing = [(script, name, json.loads((path.parent / name).read_text()))
                            for script, name in (*BASE_STAGES, *OPTIONAL_STAGES, *REFINEMENTS)
                            if (path.parent / name).is_file()]
                projected = composed.project_state(state, [data for _, _, data in existing])
                rows = []
                for row in [*projected.get('settings', []), *projected.get('panel', {}).get('settings', [])]:
                    actual = control.live_value(row)
                    rows.append({'schema': row['schema'], 'key': row['key'], 'actual': actual,
                                 'expected': row['after'], 'matches': control.same_setting(row, actual, row['after'])})
                drift = [row['schema'] + '/' + row['key'] for row in rows if not row['matches']]
                try: control.validate_source(); source = 'ready'
                except Exception as error: source = str(error)
                enabled = next(row for row in rows if (row['schema'], row['key']) == ('org.cinnamon', 'enabled-applets'))
                current_applets = control.GLib.Variant.parse(None, enabled['actual'], None, None).unpack()
                expected_applets = control.GLib.Variant.parse(None, enabled['expected'], None, None).unpack()
                return {'theme': 'GNU-Darwin Workstation', 'status': 'current-enhanced-release',
                        'receipt': str(path), 'receipt_status': state['status'], 'writes': 0,
                        'prospective_action': 'none', 'full_reapply_requires_restore': True,
                        'note': 'This is the registered release with its additive picker. Restore before a full reapply.',
                        'verification_scope': 'Read-only receipt projection and current core/panel settings; use check for full file and stage verification.',
                        'core': {'settings': 0, 'files': 0, 'assets': [], 'source_validation': source,
                                 'settings_drift': drift, 'settings_evidence': rows,
                                 'wallpaper_present': (ROOT / 'artwork/wallpaper.png').is_file(),
                                 'panel_layout': {'already_applied': not drift, 'panel_rows_changing': [],
                                                  'taskbar': current_applets, 'expected_taskbar': expected_applets,
                                                  'next_applet_id': control.Gio.Settings.new('org.cinnamon').get_int('next-applet-id')}},
                        'stages': {name: {'script': str(script), 'status': data.get('status'),
                                          'registered': name in manifest.get('required', []),
                                          'applied_actions': sum(bool(a.get('applied')) for a in data.get('actions', [])),
                                          'proposed_actions': 0} for script, name, data in existing},
                        'optional_stages_present': [name for _, name in present_optional()]}
    core = json.loads(subprocess.run(['/usr/bin/python3', str(CORE), 'plan'], capture_output=True, text=True, check=True, timeout=60).stdout)
    stages = {}
    for script, name in (*BASE_STAGES, *present_optional()):
        out = subprocess.run(['/usr/bin/python3', str(script), 'plan'], capture_output=True, text=True, timeout=60)
        if out.returncode: stages[name] = {'error': out.stdout + out.stderr}; continue
        data = json.loads(out.stdout)
        stages[name] = {'script': str(script), 'actions': len(data.get('actions', [])), 'touched_paths': len(data.get('touched_paths', [])),
                        'gsettings': len(data.get('gsettings', [])), 'dconf': len(data.get('dconf', [])), 'skipped': data.get('skipped', [])}
    panel = core['panel_layout']
    return {'theme': 'GNU-Darwin Workstation', 'core': {'settings': len(core['settings']), 'files': sum(1 for f in core['files'].values() if f['changes']),
            'assets': [a['destination'] for a in core['assets'] if a['changes']], 'source_validation': core['source_validation'],
            'wallpaper_present': core['wallpaper_present'],
            'panel_layout': {'already_applied': panel['already_applied'], 'panel_rows_changing': [r['key'] for r in panel['settings'] if r['changes']], 'taskbar': panel['taskbar'],
                             'calendar': panel['calendar'], 'next_applet_id': panel['next_applet_id'],
                             'snapshots': panel['snapshots'], 'restore_order': panel['restore_order']}},
            'stages': stages, 'optional_stages_present': [name for _, name in present_optional()], 'writes': 0}


def _main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['plan', 'apply', 'keep', 'check', 'restore', 'recover', 'refresh'])
    p.add_argument('--receipt', type=Path)
    a = p.parse_args()
    if os.geteuid() == 0: raise RuntimeError('Run the user theme as your desktop user; system installer is separate')
    if a.command == 'plan':
        print(json.dumps(plan(), indent=2)); return 0
    if a.command == 'apply':
        if (ROOT / 'state/latest.json').exists() and unfinished_recovery(receipt()):
            raise RuntimeError('Resolve the previous incomplete recovery before starting another trial')
        def timeout(signum, frame): raise TimeoutError('Theme controller exceeded its 120-second budget')
        signal.signal(signal.SIGALRM, timeout); signal.alarm(120)
        call(CORE, 'trial', timeout=95)
        path = receipt()
        try:
            init_manifest(path)
            for script, filename in (*BASE_STAGES, *present_optional()):
                start_stage(path, filename)
                call(script, 'apply', '--state', path.parent / filename, '--commit', timeout=60)
            for script, filename in REFINEMENTS:
                start_stage(path, filename)
                call(script, 'apply', '--state', path.parent / filename, '--commit', timeout=20)
            refresh(); check(path)
        except Exception:
            signal.alarm(0)
            recover(path); raise
        print('Trial ready. Inspect the desktop and run ./theme.sh keep before the 180-second deadline. Otherwise all stages recover automatically.')
    elif a.command in ('restore', 'recover'):
        result = recover(a.receipt or receipt(), a.command == 'recover'); print(json.dumps(result, indent=2))
        if result.get('errors'): return 2
    elif a.command == 'refresh': refresh()
    else:
        path = a.receipt or receipt(); check(path)
        if a.command == 'keep': call(CORE, 'keep', '--receipt', path)
    return 0


def main():
    # Shared Studio lock protects the whole composed operation, not only core settings.
    guard = module('studio_activation_guard', RUNTIME / 'guard.py')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('command', nargs='?')
    parser.add_argument('--receipt', type=Path)
    args, _ = parser.parse_known_args()
    if args.command not in ('apply', 'keep', 'check', 'restore', 'recover', 'refresh'):
        return _main()
    target = args.receipt
    if target is None and (ROOT / 'state/latest.json').exists(): target = receipt()
    with guard.operation(ROOT.parents[1], ROOT.name, args.command, target):
        return _main()


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr); sys.exit(2)
