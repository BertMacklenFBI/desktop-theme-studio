"""Authored candidate panels and real panel transaction helpers, without a session.

Run with /usr/bin/python3 -B -m unittest discover -s
refinements/cinnamon-current-collection/tests -p test_candidate_layout.py -v.
GSettings is an in-memory injected object; every settings file and journal is in
a TemporaryDirectory. GLib is used only for its real Variant parser/printer.
No Cinnamon process, user bus, home directory, or actual settings is accessed.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from gi.repository import GLib

COLLECTION = Path(__file__).resolve().parents[1]
STUDIO = COLLECTION.parents[1]
DESKTOP = STUDIO / 'refinements/candidate-repair-20260930/desktop'
AUTHORED = {
    'macintosh-soft': {'kind': 'two-panel', 'top_height': 44, 'bottom_height': 54},
    'moonstone-stereo': {'kind': 'two-panel', 'top_height': 78, 'bottom_height': 76},
    'ocean-silk': {'kind': 'two-panel', 'top_height': 44, 'bottom_height': 54},
    'plum-afterglow': {'kind': 'single-bottom', 'bottom_height': 42},
}
ROLES = ('menu@cinnamon.org', 'workspace-switcher@cinnamon.org',
         'nothing-island@desktop-theme-studio', 'notifications@cinnamon.org',
         'sound@cinnamon.org', 'grouped-window-list@cinnamon.org')


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, COLLECTION / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate = load('candidate_layout_under_independent_test', 'candidate_layout.py')
panel = load('panel_layout_under_independent_test', 'panel_layout.py')


def profile(slug):
    return {'candidate_layout': copy.deepcopy(AUTHORED[slug]), 'island_uuid': ROLES[2]}


def native_rows(slug):
    layout = AUTHORED[slug]
    heights = ([layout['top_height'], layout['bottom_height']]
               if layout['kind'] == 'two-panel' else [layout['bottom_height']])
    return {'panels': [{'id': i, 'height': height, 'mapped': True}
                       for i, height in enumerate(heights, 1)]}


class CandidateDataTests(unittest.TestCase):
    def assert_authored(self, slug):
        authored = AUTHORED[slug]
        contract = json.loads((DESKTOP / slug / 'contract.json').read_text())
        self.assertEqual(contract['candidate_layout'], authored,
                         'The frozen desktop contract must retain the authored panel composition')
        actual = candidate.settings(profile(slug))
        two = authored['kind'] == 'two-panel'
        self.assertEqual(actual['panels-enabled'], ['1:0:top', '2:0:bottom'] if two else ['1:0:bottom'])
        self.assertEqual(actual['panels-height'],
                         [f"1:{authored['top_height']}", f"2:{authored['bottom_height']}"]
                         if two else [f"1:{authored['bottom_height']}"])
        self.assertEqual(actual['panels-autohide'], ['1:false', '2:false'] if two else ['1:false'])
        parsed = panel.parse_live(actual['enabled-applets'])
        self.assertEqual({ident: value[0] for ident, value in parsed.items()},
                         dict(zip(range(901, 907), ROLES)))
        self.assertEqual(actual['next-applet-id'], 907)
        self.assertEqual(actual['enabled-extensions'], [])
        self.assertEqual(actual['enabled-desklets'], [])
        island = parsed[903][1].split(':')
        dock = parsed[906][1].split(':')
        self.assertEqual(island[:3], ['panel1', 'center' if two else 'right', '0'])
        self.assertEqual(dock[:3], ['panel2' if two else 'panel1', 'center', '0'])
        self.assertEqual(candidate.assert_native(native_rows(slug), profile(slug)), actual)

    def test_macintosh_authored_44_top_54_bottom(self): self.assert_authored('macintosh-soft')
    def test_moonstone_authored_78_top_76_bottom(self): self.assert_authored('moonstone-stereo')
    def test_ocean_authored_44_top_54_bottom(self): self.assert_authored('ocean-silk')
    def test_plum_authored_single_42_bottom(self): self.assert_authored('plum-afterglow')

    def test_missing_or_unknown_layout_fields_refused(self):
        for bad in (None, [], {}, {'kind': 'unknown'},
                    {'kind': 'two-panel', 'top_height': 44},
                    {**AUTHORED['macintosh-soft'], 'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                candidate.settings({'candidate_layout': bad})

    def test_height_bounds_and_types_refused(self):
        for bad in (15, 201, 44.0, True, '44', None, float('nan'), float('inf')):
            data = profile('macintosh-soft'); data['candidate_layout']['top_height'] = bad
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError): candidate.settings(data)

    def test_unsafe_island_identity_refused(self):
        for bad in ('../escape', 'hello world@bad', 'a@b;command', '', None, 42):
            data = profile('macintosh-soft'); data['island_uuid'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError): candidate.settings(data)

    def test_wrong_native_count_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'].pop()
        with self.assertRaises(RuntimeError): candidate.assert_native(bad, profile('macintosh-soft'))

    def test_wrong_native_id_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'][1]['id'] = 99
        with self.assertRaises(RuntimeError): candidate.assert_native(bad, profile('macintosh-soft'))

    def test_duplicate_native_ids_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'][1]['id'] = 1
        with self.assertRaises(RuntimeError): candidate.assert_native(bad, profile('macintosh-soft'))

    def test_unmapped_native_panel_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'][0]['mapped'] = False
        with self.assertRaises(RuntimeError): candidate.assert_native(bad, profile('macintosh-soft'))

    def test_wrong_native_height_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'][0]['height'] = 54
        with self.assertRaises(RuntimeError): candidate.assert_native(bad, profile('macintosh-soft'))

    def test_nonfinite_native_heights_refused(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            bad = native_rows('macintosh-soft'); bad['panels'][0]['height'] = value
            with self.subTest(value=repr(value)), self.assertRaises((RuntimeError, ValueError)):
                candidate.assert_native(bad, profile('macintosh-soft'))

    def test_truthy_nonboolean_mapped_refused(self):
        bad = native_rows('macintosh-soft'); bad['panels'][0]['mapped'] = 'false'
        with self.assertRaises((RuntimeError, ValueError)):
            candidate.assert_native(bad, profile('macintosh-soft'))


class MemorySettings:
    """A tiny injected settings store that records real Variant writes and resets."""
    def __init__(self):
        self.defaults = {
            'enabled-applets': GLib.Variant('as', [
                f'panel7:left:{order}:{applet}:{ident}'
                for order, (ident, applet) in enumerate(zip((17, 36, 93, 20, 11, 16), ROLES))]),
            'panels-enabled': GLib.Variant('as', ['7:0:top', '8:0:bottom']),
            'panels-height': GLib.Variant('as', ['7:37', '8:63']),
            'enabled-desklets': GLib.Variant('as', []),
            'next-applet-id': GLib.Variant('i', 118),
        }
        for key in panel.ZONE_KEYS:
            self.defaults[key] = GLib.Variant('s', json.dumps([
                {'panelId': 7, 'left': 23, 'center': 26, 'right': 29},
                {'panelId': 8, 'left': 31, 'center': 32, 'right': 33}]))
        self.values = dict(self.defaults)
        self.users = {key: value for key, value in self.values.items() if key != 'panel-zone-text-sizes'}
        self.writes = []

    def get_value(self, key): return self.values[key]
    def get_user_value(self, key): return self.users.get(key)
    def get_strv(self, key): return list(self.values[key].unpack())
    def get_int(self, key): return self.values[key].unpack()
    def get_string(self, key): return self.values[key].unpack()
    def set_value(self, key, value):
        self.writes.append(('set', key, value.print_(True)))
        self.values[key] = self.users[key] = value
        return True
    def set_int(self, key, value): return self.set_value(key, GLib.Variant('i', value))
    def reset(self, key):
        self.writes.append(('reset', key, None))
        self.users.pop(key, None)
        self.values[key] = self.defaults[key]


class PanelTransactionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='candidate-panel-fixtures-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mem = MemorySettings()
        def new(schema):
            if schema != panel.PANEL: raise AssertionError('Unexpected settings schema: ' + schema)
            return self.mem
        fake_gio = types.SimpleNamespace(Settings=types.SimpleNamespace(new=new, sync=lambda: None))
        for target, value in (('_gi', lambda: (fake_gio, GLib)),
                              ('_panel_settings', lambda: self.mem), ('SETTLE_DELAY', 0),
                              ('config_dir', lambda: self.root / 'config'),
                              ('spices_paths', self.spices_paths)):
            item = patch.object(panel, target, value); item.start(); self.addCleanup(item.stop)
        self.bus_calls = []

    def spices_paths(self, applet, ident):
        return [self.root / place / applet / f'{ident}.json' for place in ('config', 'legacy')]

    def layout(self, slug='macintosh-soft'):
        authored = AUTHORED[slug]; two = authored['kind'] == 'two-panel'
        main, dock = 1, 2 if two else 1
        entries = [f'panel{main}:left:0:{ROLES[0]}:17',
                   f'panel{main}:left:1:{ROLES[1]}:36',
                   f'panel{main}:{"center" if two else "right"}:0:{ROLES[2]}:93',
                   f'panel{main}:right:{0 if two else 1}:{ROLES[3]}:20',
                   f'panel{main}:right:{1 if two else 2}:{ROLES[4]}:11',
                   f'panel{dock}:center:0:{ROLES[5]}:16']
        # The coherent in-memory home uses IDs 7/8 to exercise ownership's
        # unowned-layout guard even when home and target have the same edges.
        return {'schema': 1, 'kind': 'horizontal',
                'provenance': {'lineage': 'independent authored four-candidate fixture',
                               'lineage_panel_sha256': 'a' * 64, 'design_panel_layout_sha256': 'b' * 64},
                'panel': {'panels_enabled': ['1:0:top', '2:0:bottom'] if two else ['1:0:bottom'],
                          'panels_height': [f"1:{authored['top_height']}", f"2:{authored['bottom_height']}"]
                                          if two else [f"1:{authored['bottom_height']}"],
                          'enabled_applets': entries, 'remove': [], 'new_instance': None,
                          'zone_sizes': {}, 'enabled_desklets': []}}

    def plan(self, slug='macintosh-soft'):
        return panel.plan_to_rail(slug, self.layout(slug))

    def snapshot(self, values):
        # Zone settings are JSON text. The transaction compares complete parsed
        # arrays and may normalize their insignificant JSON whitespace.
        return {key: json.loads(value.unpack()) if key in panel.ZONE_KEYS else value.print_(True)
                for key, value in values.items()}

    def saved(self): return self.snapshot(self.mem.values)

    def apply(self, slug):
        before = self.saved()
        plan = self.plan(slug)
        path = self.root / 'state' / slug / 'receipt.json'
        path.parent.mkdir(parents=True)
        data = {'profile': slug, 'status': 'applying', 'guard_armed': True, 'panel': plan}
        panel.prepare_receipt(path, plan)
        panel.apply_panel(path, data, panel.atomic_save, bus=lambda *args: self.bus_calls.append(args))
        return path, data, before

    def roundtrip(self, slug):
        layout = self.layout(slug)
        self.assertIsNone(panel.validate_layout({'panel_layout': layout}))
        before = self.saved()
        users = self.snapshot(self.mem.users)
        custom = self.spices_paths(ROLES[2], 93)[0]
        custom.parent.mkdir(parents=True); custom.write_bytes(b'{"clock-format":"%H:%M"}\r\n')
        path, data, _ = self.apply(slug)
        self.assertEqual(self.mem.get_strv('panels-height'), layout['panel']['panels_height'])
        self.assertEqual(self.mem.get_strv('panels-enabled'), layout['panel']['panels_enabled'])
        self.assertEqual(set(panel.parse_live(self.mem.get_strv('enabled-applets'))), {17, 36, 93, 20, 11, 16})
        self.assertEqual(self.mem.get_int('next-applet-id'), 118)
        self.assertEqual(data['panel']['next_applet_id']['allocate'], False)
        self.assertEqual(data['panel']['files'], {})
        self.assertEqual(data['panel']['calendar'], {})
        custom.unlink()  # Exercise the actual settings-file insurance restore.
        self.mem.writes.clear()
        restored = panel.restore_panel(path, data, panel.atomic_save,
                                       bus=lambda *args: self.bus_calls.append(args), automatic=False)
        self.assertEqual(restored['status'], 'restored')
        self.assertEqual(data['panel']['phase'], 'restored')
        self.assertEqual(self.saved(), before)
        self.assertEqual(self.snapshot(self.mem.users), users)
        self.assertEqual(custom.read_bytes(), b'{"clock-format":"%H:%M"}\r\n')
        self.assertEqual(self.mem.get_int('next-applet-id'), 118)
        self.assertNotIn('next-applet-id', [event[1] for event in self.mem.writes])
        keys = [event[1] for event in self.mem.writes]
        self.assertLess(keys.index('panels-height'), keys.index('panels-enabled'))
        self.assertLess(keys.index('panels-enabled'), keys.index('enabled-applets'))
        self.assertIn(('reset', 'panel-zone-text-sizes', None), self.mem.writes)
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())['panel']['phase'], 'restored')

    def test_macintosh_transaction_restore(self): self.roundtrip('macintosh-soft')
    def test_moonstone_transaction_restore(self): self.roundtrip('moonstone-stereo')
    def test_ocean_transaction_restore(self): self.roundtrip('ocean-silk')
    def test_plum_transaction_restore(self): self.roundtrip('plum-afterglow')

    def test_duplicate_target_instance_id_refused(self):
        layout = self.layout(); layout['panel']['enabled_applets'][1] = 'panel1:left:1:workspace-switcher@cinnamon.org:17'
        self.assertIn('duplicate instance id', panel.validate_layout({'panel_layout': layout}))
        with self.assertRaisesRegex(RuntimeError, 'duplicate instance id'): panel.plan_to_rail('fixture', layout)
        self.assertEqual(self.mem.writes, [])

    def test_duplicate_zone_order_refused(self):
        layout = self.layout(); layout['panel']['enabled_applets'][1] = 'panel1:left:0:workspace-switcher@cinnamon.org:36'
        self.assertIn('duplicate zone order', panel.validate_layout({'panel_layout': layout}))

    def test_missing_live_instance_refused(self):
        layout = self.layout(); layout['panel']['enabled_applets'][1] = 'panel1:left:1:workspace-switcher@cinnamon.org:999'
        with self.assertRaisesRegex(RuntimeError, 'Expected applet instance missing'): panel.plan_to_rail('fixture', layout)
        self.assertEqual(self.mem.writes, [])

    def test_reused_id_with_different_uuid_refused(self):
        layout = self.layout(); layout['panel']['enabled_applets'][1] = 'panel1:left:1:foreign@fixture:36'
        with self.assertRaisesRegex(RuntimeError, 'Expected applet instance missing'): panel.plan_to_rail('fixture', layout)

    def test_unplaced_unknown_live_applet_refused(self):
        layout = self.layout()
        self.mem.values['enabled-applets'] = GLib.Variant('as',
            self.mem.get_strv('enabled-applets') + ['panel1:right:99:foreign@fixture:777'])
        with self.assertRaisesRegex(RuntimeError, 'neither places nor removes'): panel.plan_to_rail('fixture', layout)
        self.assertEqual(self.mem.writes, [])

    def test_duplicate_live_ids_refused(self):
        layout = self.layout()
        self.mem.values['enabled-applets'] = GLib.Variant('as',
            self.mem.get_strv('enabled-applets') + ['panel1:right:99:foreign@fixture:36'])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate applet instance id'): panel.plan_to_rail('fixture', layout)

    def test_unknown_live_entry_shape_refused(self):
        layout = self.layout()
        self.mem.values['enabled-applets'] = GLib.Variant('as', ['malformed-app-entry'])
        with self.assertRaisesRegex(RuntimeError, 'Unsupported enabled-applets'): panel.plan_to_rail('fixture', layout)

    def test_target_missing_panel_height_refused(self):
        layout = self.layout(); layout['panel']['panels_height'].pop()
        self.assertIn('name exactly', panel.validate_layout({'panel_layout': layout}))

    def test_horizontal_cannot_allocate_new_instance(self):
        layout = self.layout(); layout['panel']['new_instance'] = {'uuid': 'calendar@cinnamon.org', 'settings': {'format': '%H'}}
        layout['panel']['enabled_applets'].append('panel1:right:2:calendar@cinnamon.org:new')
        self.assertIn('preserve existing applet instance IDs', panel.validate_layout({'panel_layout': layout}))

    def test_horizontal_cannot_use_side_rail(self):
        layout = self.layout(); layout['panel']['panels_enabled'][0] = '1:0:left'
        self.assertIn('horizontal layout', panel.validate_layout({'panel_layout': layout}))

    def test_existing_rail_requires_single_side_panel_and_new_instance(self):
        layout = self.layout(); layout['kind'] = 'rail'
        self.assertIn('new_instance', panel.validate_layout({'panel_layout': layout}))
        spec = layout['panel']; spec['new_instance'] = {'uuid': 'calendar@cinnamon.org', 'settings': {'format': '%H'}}
        spec['enabled_applets'].append('panel1:right:2:calendar@cinnamon.org:new')
        self.assertIn('exactly one panel', panel.validate_layout({'panel_layout': layout}))
        spec['panels_enabled'] = ['1:0:top']; spec['panels_height'] = ['1:65']
        spec['enabled_applets'][5] = 'panel1:right:3:grouped-window-list@cinnamon.org:16'
        self.assertIn('left or right edge', panel.validate_layout({'panel_layout': layout}))
        spec['panels_enabled'] = ['1:0:left']
        self.assertIsNone(panel.validate_layout({'panel_layout': layout}))

    def test_apply_requires_recovery_guard(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'recovery must be armed'):
            panel.apply_panel(self.root / 'receipt.json', {'panel': plan}, panel.atomic_save)
        self.assertEqual(self.mem.writes, [])

    def test_increased_counter_never_decrements_on_restore(self):
        path, data, _ = self.apply('macintosh-soft')
        self.mem.set_int('next-applet-id', 129)
        self.mem.writes.clear()
        result = panel.restore_panel(path, data, panel.atomic_save, bus=lambda *args: None, automatic=False)
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(self.mem.get_int('next-applet-id'), 129)
        self.assertNotIn('next-applet-id', [event[1] for event in self.mem.writes])


if __name__ == '__main__': unittest.main(verbosity=2)
