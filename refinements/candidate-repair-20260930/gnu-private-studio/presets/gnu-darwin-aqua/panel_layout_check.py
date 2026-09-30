#!/usr/bin/python3
"""Read-only fixture checks for the GNU-Darwin Aqua taskbar contract and restore journal."""
import copy
import json
from pathlib import Path
import importlib.util
import tempfile
import shutil

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('gnu_darwin_aqua_desktop_control_fixture', ROOT / 'desktop_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
lineage = json.loads((ROOT / 'lineage.json').read_text())['desktop_control']['panel']
design = json.loads((ROOT / 'design.json').read_text())

def require(condition, message):
    if not condition:
        raise AssertionError(message)

require(control.panel_problem(lineage) is None, 'lineage panel shape rejected')
require(control.design_panel_problems(lineage, design) == [], 'lineage and design panel_layout differ')
require(lineage['panels_enabled'] == ['1:0:top', '2:0:bottom'], 'must use top and bottom panels')
require(lineage['panels_height'] == ['1:34', '2:68'], 'panels must be 34 and 68 px')
entries = lineage['enabled_applets']
require(entries[:2] == ['panel1:left:0:menu@cinnamon.org:17',
                        'panel2:center:0:grouped-window-list@cinnamon.org:16'],
        'menu must be top left and grouped windows dock centered')
require(all(e.startswith('panel1:right:') for e in entries[2:]), 'tray and clock applets must occupy the right zone')
require(lineage['new_instance']['uuid'] == 'calendar@cinnamon.org', 'clock must use a new calendar instance')
require(lineage['new_instance']['settings']['custom-format'] == '%H:%M', 'clock must use a compact HH:MM format')

bad = copy.deepcopy(lineage)
bad['panels_enabled'] = ['1:0:top']
require(control.design_panel_problems(bad, design), 'top panel fixture should fail design cross-check')
bad = copy.deepcopy(lineage)
bad['enabled_applets'][1] = 'panel1:center:0:grouped-window-list@cinnamon.org:16'
require(control.design_panel_problems(bad, design), 'misplaced window list should fail cross-check')

restore = ' '.join(control.RESTORE_ORDER).lower()
for phrase in ('removed multi-instance applets settings files', 'panels-height', 'panels-enabled',
               'enabled-applets', 'panel-zone-icon-sizes', 'enabled-desklets', 'next-applet-id'):
    require(phrase in restore, 'restore plan omits ' + phrase)
require('never decremented' in restore, 'restore must preserve monotonic next-applet-id')
require(control.RESTORE_STEPS[0] == ('panels-height', 'panels-enabled', 'enabled-applets'),
        'panel layout keys must restore as one coherent group')
require(control.RESTORE_STEPS[1] == control.ZONE_KEYS and control.RESTORE_STEPS[2] == ('enabled-desklets',),
        'zone arrays and desklets must be restored after panel recreation')
require({'c-eyes@anaximeno:41', 'c-eyes@anaximeno:44'} <= set(lineage['remove']),
        'both removed c-eyes instances must be covered by byte-exact settings snapshots')

# Cinnamon's current nothing-island entry carries the supported optional orient override.
orient_entry = 'panel1:center:1:nothing-island@desktop-theme-studio:42:orient'
require(control.parse_live([orient_entry])[42] == ('nothing-island@desktop-theme-studio', orient_entry),
        'controller must preserve the original six-field orient entry exactly')
for bad_entry in (orient_entry + ':unknown', orient_entry.replace(':orient', ':vertical')):
    try:
        control.parse_live([bad_entry])
    except RuntimeError:
        pass
    else:
        require(False, 'controller accepted an unsupported applet suffix: ' + bad_entry)

isolate_spec = importlib.util.spec_from_file_location('gnu_darwin_aqua_isolate_fixture', ROOT / 'isolate.py')
isolate = importlib.util.module_from_spec(isolate_spec)
isolate_spec.loader.exec_module(isolate)
require(isolate.instances([orient_entry]) == [('nothing-island@desktop-theme-studio', 42)],
        'isolated applet copier must include the orient-override instance')
try:
    isolate.instances([orient_entry + ':unknown'])
except RuntimeError:
    pass
else:
    require(False, 'isolated applet copier accepted an unsupported suffix')

appearance = control.design_appearance()
require(appearance == {'ui_font': 'Liberation Sans 11', 'document_font': 'Liberation Sans 11',
                       'monospace_font': 'Liberation Mono 11', 'title_font': 'Liberation Sans Bold 11',
                       'cursor_size': 24, 'prefer_dark': False, 'button_layout': 'close,minimize,maximize:'},
        'planned appearance must match the design typography, cursor size, and light preference')
require(isolate.APPEARANCE == appearance, 'private GTK appearance values drifted from the controller')
require(control.color_scheme_preference(['default', 'prefer-light']) == 'prefer-light',
        'supported schemas should use the light color scheme')
require(control.color_scheme_preference(['default', 'prefer-dark']) == 'default',
        'schemas without prefer-light should fall back to default')
require(control.color_scheme_preference(['prefer-dark']) is None,
        'unsupported color-scheme values should not be written')

# A structurally valid but stale report must also fail the exact-source gate.
with tempfile.TemporaryDirectory(prefix='gnu-darwin-aqua-stale-fixture-') as temporary:
    fixture_root = Path(temporary) / 'gnu-darwin-aqua'
    (fixture_root / 'desktop' / design['name']).mkdir(parents=True)
    (fixture_root / 'verification').mkdir()
    shutil.copyfile(ROOT / 'isolate.py', fixture_root / 'isolate.py')
    shutil.copyfile(ROOT / 'desktop_control.py', fixture_root / 'desktop_control.py')
    (fixture_root / 'design.json').write_text((ROOT / 'design.json').read_text())
    (fixture_root / 'lineage.json').write_text((ROOT / 'lineage.json').read_text())
    report = {'status': 'passed', 'source_sha256': control.fingerprint(fixture_root / 'desktop' / design['name']),
              'taskbar_sha256': {'desktop_control.py': 'stale'}}
    (fixture_root / 'verification' / 'isolated-latest.json').write_text(json.dumps(report))
    old_root, old_lineage = control.ROOT, control.LINEAGE_FILE
    try:
        control.ROOT = fixture_root
        control.LINEAGE_FILE = fixture_root / 'lineage.json'
        try:
            control.validate_source()
        except Exception as error:
            require('isolated test of the exact taskbar code' in str(error), 'stale report rejected for an unexpected reason: ' + str(error))
        else:
            require(False, 'source gate accepted stale taskbar evidence')
    finally:
        control.ROOT, control.LINEAGE_FILE = old_root, old_lineage

# Canonical appearance changes invalidate the source gate even without stylesheet edits.
with tempfile.TemporaryDirectory(prefix='aqua-appearance-') as temporary:
    fixture_root = Path(temporary)
    for name in ('design.json', 'lineage.json', 'isolate.py'):
        shutil.copyfile(ROOT / name, fixture_root / name)
    old_root, old_lineage = control.ROOT, control.LINEAGE_FILE
    try:
        control.ROOT, control.LINEAGE_FILE = fixture_root, fixture_root / 'lineage.json'
        before = control.taskbar_fingerprint()
        changed = copy.deepcopy(design); changed['typography']['ui_size_pt'] += 1
        (fixture_root / 'design.json').write_text(json.dumps(changed))
        after = control.taskbar_fingerprint()
        require(before['design.json#appearance'] != after['design.json#appearance'], 'appearance mutation must invalidate gate')
    finally:
        control.ROOT, control.LINEAGE_FILE = old_root, old_lineage

for mutate in (lambda p: p['zone_sizes'].pop('2'),
               lambda p: p['zone_sizes']['2']['panel-zone-icon-sizes'].update(center=True),
               lambda p: p['zone_sizes']['2']['panel-zone-icon-sizes'].update(center=129)):
    bad = copy.deepcopy(lineage); mutate(bad)
    require(control.panel_problem(bad), 'malformed per-panel icon map accepted')
bad = copy.deepcopy(lineage); bad['zone_sizes']['2']['panel-zone-icon-sizes']['center'] = 20
require(control.design_panel_problems(bad, design), 'icon-size design drift accepted')
fixture = [dict(id=1, pos=0, height=34, width=1600, monitor_width=1600, zone_heights=[24,0,24], background_alpha=255),
           dict(id=2, pos=1, height=68, width=1600, monitor_width=1600, zone_heights=[0,60,0], background_alpha=0,
                dock=dict(x=600,width=400,background_alpha=255))]
require(not isolate.taskbar_problems(fixture), 'valid two-panel render rejected')
for mutate in (lambda p: p.pop(), lambda p: p[0].update(height=35), lambda p: p[1].update(pos=0),
               lambda p: p[1].update(zone_heights=[0,69,0]), lambda p: p[1]['dock'].update(x=500),
               lambda p: p[1]['dock'].update(width=1600), lambda p: p[1]['dock'].update(background_alpha=0),
               lambda p: p[1].update(background_alpha=255), lambda p: p[0].update(background_alpha=0)):
    bad = copy.deepcopy(fixture); mutate(bad)
    require(isolate.taskbar_problems(bad), 'bad two-panel rendered geometry accepted')

ready = dict(isOpen=True, visible=True, mapped=True, opacity=255, width=400, height=600)
require(isolate.popup_ready(ready), 'visible expanded popup rejected')
for key, value in (('isOpen', False), ('visible', False), ('mapped', False), ('opacity', 0), ('width', 0), ('height', 0)):
    invalid = {**ready, key: value}
    require(not isolate.popup_ready(invalid), 'closed or unmapped popup accepted: ' + key)

# Headerbar titles may replace Gtk.Window titles; process identity remains stable.
windows = [dict(pid=123, wm_class='Isolate.py', title='GNU-Darwin Aqua', visible=True, mapped=True, opacity=255, width=560, height=380),
           dict(pid=456, wm_class='Nemo', title='GNU-Darwin Aqua Files', visible=True, mapped=True, opacity=255, width=780, height=620)]
require(isolate.preview_windows_ready(windows, 123), 'PID-based preview identity rejected changed headerbar title')
require(not isolate.preview_windows_ready(windows, 999), 'unrelated process accepted as preview')
for index, key, value in ((0, 'mapped', False), (1, 'visible', False), (0, 'opacity', 0), (1, 'wm_class', 'Other'), (1, 'title', 'Other folder')):
    invalid = copy.deepcopy(windows); invalid[index][key] = value
    require(not isolate.preview_windows_ready(invalid, 123), 'unready preview windows accepted')

max_state = dict(maximized=3, visible=True, mapped=True, opacity=255, frame=[0,34,1600,1098], workarea=[0,34,1600,1098])
require(isolate.maximized_ready(max_state), 'exact maximized frame rejected')
for key, value in (('maximized', 1), ('mapped', False), ('opacity', 0), ('frame', [0,0,1600,1200])):
    require(not isolate.maximized_ready({**max_state, key: value}), 'invalid maximize state accepted')

print('GNU-Darwin Aqua panel layout fixtures passed; absent/stale isolated evidence remains fail-closed.')
