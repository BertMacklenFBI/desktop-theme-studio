"""Tests for tools/scaffold_preset.py. Every studio lives in a temp dir; the real one is only read.

Run: /usr/bin/python3 -B -m unittest discover -s tools/tests -p 'test_scaffold_preset.py' -v
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parents[1]
REAL_STUDIO = TOOLS.parent
SHIM = ('#!/usr/bin/python3\n"""Lineage shim (Phase 6): runs the shared lib/lineage/desktop_control.py for this preset."""\n'
        "LINEAGE_PRESET = None\nexec(_lineage_code('desktop_control.py'))\n")


def load():
    spec = importlib.util.spec_from_file_location('scaffold_under_test', TOOLS / 'scaffold_preset.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def write(path, text):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text); return path


class Scaffold(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / 'studio'
        self.presets = self.root / 'presets'
        write(self.presets / '_template/LINEAGE', 'lineage-x\n')
        self.src = self.presets / 'lineage-x'
        write(self.src / 'desktop_control.py', SHIM)
        write(self.src / 'theme.py', 'NAME = "Lineage X"\n')
        write(self.src / 'state/latest.json', '{}')  # never copied
        self.layout = {'$comment': 'c', 'schema': 1,
                       'desktop_control': {'name': 'Lineage X', 'slug': 'lineage-x', 'temp_tag': 'lineage_x'}}
        write(self.src / 'lineage.json', json.dumps(self.layout, indent=2) + '\n')
        self.m = load()
        self.m.ROOT, self.m.PRESETS = self.root, self.presets

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.m.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_writes_nothing_and_names_the_shim(self):
        write(self.presets / 'new-one/design.json', json.dumps({'name': 'New One'}))
        rc, out, _ = self.run_main('new-one')
        self.assertEqual(rc, 0)
        self.assertIn('would copy desktop_control.py (lineage shim for lib/lineage/desktop_control.py; copy as is, do not edit)', out)
        self.assertIn('would write lineage.json (identity from design.json)', out)
        self.assertEqual(sorted(p.name for p in (self.presets / 'new-one').iterdir()), ['design.json'])

    def test_write_copies_shim_bytes_and_derives_lineage_json(self):
        write(self.presets / 'new-one/design.json', json.dumps({'name': 'New One'}))
        rc, out, _ = self.run_main('new-one', '--write')
        self.assertEqual(rc, 0)
        dst = self.presets / 'new-one'
        self.assertEqual((dst / 'desktop_control.py').read_bytes(), (self.src / 'desktop_control.py').read_bytes())
        self.assertFalse((dst / 'state').exists())
        doc = json.loads((dst / 'lineage.json').read_text())
        self.assertEqual(doc, dict(self.layout, desktop_control={'name': 'New One', 'slug': 'new-one', 'temp_tag': 'new_one'}))
        self.assertIn('shims=1 lineage.json=new', out)
        rc, out, _ = self.run_main('new-one', '--write')  # second run: nothing new, nothing overwritten
        self.assertEqual(rc, 1); self.assertIn('exists, kept lineage.json', out)

    def test_inherited_values_are_listed(self):
        self.layout['desktop_control']['panel'] = {'enabled': ['1:0:top'], 'height': ['1:40'], 'placement': {}}
        write(self.src / 'lineage.json', json.dumps(self.layout, indent=2) + '\n')
        write(self.presets / 'new-one/design.json', json.dumps({'name': 'New One'}))
        rc, out, _ = self.run_main('new-one', '--write')
        self.assertIn('inherited from lineage-x: panel', out)
        self.assertEqual(json.loads((self.presets / 'new-one/lineage.json').read_text())['desktop_control']['panel'],
                         self.layout['desktop_control']['panel'])

    def test_refuses_without_design_json_or_with_unsafe_name(self):
        rc, out, err = self.run_main('new-one', '--write')
        self.assertEqual(rc, 2); self.assertIn('design.json is required first', err)
        self.assertFalse((self.presets / 'new-one').exists())
        write(self.presets / 'new-one/design.json', json.dumps({'name': 'Bad/Name'}))
        rc, out, err = self.run_main('new-one', '--write')
        self.assertEqual(rc, 2); self.assertIn('not a safe visible theme name', err)
        write(self.presets / 'New_One/design.json', json.dumps({'name': 'New One'}))
        rc, out, err = self.run_main('New_One')
        self.assertEqual(rc, 2); self.assertIn('not a lowercase slug', err)

    def test_lineage_without_lineage_json_keeps_old_behaviour(self):
        (self.src / 'lineage.json').unlink()
        rc, out, _ = self.run_main('new-one', '--write')  # no design.json needed
        self.assertEqual(rc, 0)
        self.assertFalse((self.presets / 'new-one/lineage.json').exists())
        self.assertIn('lineage=lineage-x target=new-one new=2 kept=0 shims=1', out)

    def test_refuses_a_name_another_preset_uses(self):
        write(self.presets / 'lineage-x/design.json', json.dumps({'name': 'Lineage X'}))
        write(self.presets / 'new-one/design.json', json.dumps({'name': 'Lineage X'}))
        rc, out, err = self.run_main('new-one', '--write')
        self.assertEqual(rc, 2); self.assertIn('already used by presets/lineage-x', err)
        self.assertEqual(sorted(p.name for p in (self.presets / 'new-one').iterdir()), ['design.json'])

    def test_existing_preset_with_full_copies_gets_no_lineage_json(self):
        write(self.presets / 'old-one/design.json', json.dumps({'name': 'Old One'}))
        write(self.presets / 'old-one/desktop_control.py', '#!/usr/bin/python3\n"""Own full controller."""\nNAME = "Old One"\n')
        rc, out, _ = self.run_main('old-one', '--write')
        self.assertIn('not writing lineage.json: this preset keeps its own copies of desktop_control.py', out)
        self.assertFalse((self.presets / 'old-one/lineage.json').exists())
        self.assertEqual((self.presets / 'old-one/desktop_control.py').read_text().count('Own full controller'), 1)

    def test_reserved_ids_refused(self):
        for bad in ('_template', 'red-panda-overtime-2', 'a/b'):
            self.assertEqual(self.run_main(bad)[0], 2)

    def test_real_lineage_matches_its_own_derivation(self):
        """The real lineage's lineage.json is exactly what the scaffold would derive for it (read-only)."""
        real = load()
        src = real.PRESETS / real.lineage()
        if not (src / 'lineage.json').is_file():
            self.skipTest('lineage has no lineage.json yet (Phase 6 wave B part 2 not published)')
        data, inherited = real.lineage_values(src, src, src.name)
        self.assertEqual(data, (src / 'lineage.json').read_bytes())
        self.assertEqual(inherited, [])
        self.assertTrue(real.shim_module(src / 'desktop_control.py'))


if __name__ == '__main__':
    unittest.main()
