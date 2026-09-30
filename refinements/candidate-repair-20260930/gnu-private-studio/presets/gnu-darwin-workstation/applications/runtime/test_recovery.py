"""Controller recovery gates on temporary receipts; every desktop/process call is mocked."""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import theme, desktop_control


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'desktop.json'
        self.path.write_text(json.dumps({'status': 'pending'}))
        for _, name in theme.BASE_STAGES:
            self.path.with_name(name).write_text('{}')
        theme.init_manifest(self.path, existing=True)

    def test_missing_required_stage_fails_before_checks(self):
        self.path.with_name('applications.json').unlink()
        with patch.object(theme, 'call') as call:
            with self.assertRaisesRegex(RuntimeError, 'applications.json'): theme.check(self.path)
            call.assert_not_called()

    def test_supplement_failure_survives_core_success_and_retries(self):
        failures = [True]; calls = []
        def call(script, *args, **kwargs):
            calls.append(script)
            if script == theme.APPS and failures[0]: raise RuntimeError('simulated restore conflict')
            if script == theme.CORE: self.path.write_text('{"status":"restored"}')
        with patch.object(theme, 'call', side_effect=call), patch.object(theme, 'refresh'):
            self.assertEqual(theme.recover(self.path, True)['status'], 'recovery-required')
            self.assertEqual(json.loads(self.path.read_text())['status'], 'restored')
            # The aggregate gate keeps the automatic timer retrying although the core says restored.
            failures[0] = False; calls.clear()
            self.assertEqual(theme.recover(self.path, True)['status'], 'restored')
            self.assertIn(theme.APPS, calls); self.assertNotIn(theme.CORE, calls)

    def test_settled_release_ignores_stray_timer(self):
        self.path.write_text(json.dumps({'status': 'kept'}))
        with patch.object(theme, 'call') as call:
            self.assertEqual(theme.recover(self.path, True), {'status': 'kept', 'timer': 'ignored'})
            call.assert_not_called()

    def test_unattempted_stage_is_not_missing_during_partial_apply(self):
        self.path.with_name('stages.json').unlink(); theme.init_manifest(self.path)
        self.path.with_name('applications.json').unlink()
        self.assertEqual(theme.missing_receipts(self.path, recovery=True), [])
        self.assertIn('applications.json', theme.missing_receipts(self.path))

    def test_attempted_missing_receipt_remains_recovery_required(self):
        self.path.with_name('applications.json').unlink()
        with patch.object(theme, 'call'), patch.object(theme, 'refresh'):
            self.assertEqual(theme.recover(self.path)['status'], 'recovery-required')

    def test_check_composes_core_then_stages(self):
        order = []
        with patch.object(theme, 'call', side_effect=lambda script, *a, **k: order.append((script, a[0]))):
            theme.check(self.path)
        self.assertEqual(order[0], (theme.CORE, 'check'))
        self.assertEqual(order[1], (theme.APPS, 'check'))
        self.assertTrue(self.path.with_name('applications.json').read_text() == '{}')

    def test_overlay_projection_still_detects_unrelated_edits(self):
        folder = Path(self.tmp.name) / 'tree'; folder.mkdir(); f = folder / 'style.css'; f.write_bytes(b'base')
        original = desktop_control.tree_record(folder)
        f.write_bytes(b'base\nrefinement')
        self.assertEqual(original, desktop_control.tree_record(folder, {str(f): b'base'}))
        (folder / 'unrelated').write_text('drift')
        self.assertNotEqual(original, desktop_control.tree_record(folder, {str(f): b'base'}))

    def test_panel_layout_is_never_a_written_setting(self):
        # Exercise target composition with a stable panel fixture; host applet inventory is unrelated
        # to this invariant and may contain Cinnamon entries unsupported by the current parser.
        panel = {'taskbar': ['panel1:left:0:menu@cinnamon.org:1'], 'enabled-applets': []}
        with patch.object(desktop_control, 'panel_targets', return_value=panel):
            settings, files, layout = desktop_control.targets()
        self.assertFalse([row for row in settings if row['schema'] == 'org.cinnamon'])
        self.assertIn('enabled-applets', layout)
        self.assertIsInstance(layout['enabled-applets'], list)
        self.assertEqual({row['key'] for row in settings if row['schema'] == 'org.cinnamon.desktop.background'}, {'picture-uri', 'picture-options'})
        self.assertNotIn(str(Path.home() / '.config/autostart/mint-dashboard.desktop'), files)


if __name__ == '__main__': unittest.main()
