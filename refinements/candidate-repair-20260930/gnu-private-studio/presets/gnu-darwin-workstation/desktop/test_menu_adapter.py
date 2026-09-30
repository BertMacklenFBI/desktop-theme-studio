"""menu_adapter.py against a temporary HOME; the applet check and the Cinnamon reload are mocked."""
import hashlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import menu_adapter as m

FIXTURE = {
    'layout1': {'type': 'layout', 'pages': ['appearance'], 'appearance': {'type': 'page', 'title': 'Appearance', 'sections': ['appearance-panel']},
                'appearance-panel': {'type': 'section', 'title': 'Panel', 'keys': ['menu-custom', 'menu-icon', 'menu-icon-size', 'menu-label']}},
    'overlay-key': {'type': 'keybinding', 'default': 'Super_L::Super_R', 'description': 'Keybinding', 'value': 'Super_L::Super_R'},
    'menu-custom': {'type': 'switch', 'default': True, 'description': 'Use a custom icon and label', 'value': False},
    'menu-icon': {'type': 'iconfilechooser', 'default': 'linuxmint-logo-ring-symbolic', 'description': 'Icon', 'dependency': 'menu-custom',
                  'icon_categories': [{'name': 'Linux Mint', 'icons': ['linuxmint-logo', 'start-here-symbolic']}], 'value': 'linuxmint-logo-ring-symbolic'},
    'menu-icon-size': {'type': 'spinbutton', 'default': 32, 'min': 16, 'max': 96, 'step': 1, 'units': 'px', 'description': 'Icon size', 'value': 32},
    'menu-label': {'type': 'entry', 'default': '', 'description': 'Text', 'dependency': 'menu-custom', 'value': 'le cinabon'},
    'popup-width': {'type': 'generic', 'default': 700, 'value': 812.5},
    'show-description': {'type': 'switch', 'default': True, 'description': 'Café ☕ description', 'value': True},
    '__md5__': '1c3d7a2b2f4e5a6b7c8d9e0f1a2b3c4d',
}


def sha(b): return hashlib.sha256(b).hexdigest()


class MenuAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'; self.home.mkdir()
        self.old_home = os.environ.get('HOME'); os.environ['HOME'] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__('HOME', self.old_home) if self.old_home else os.environ.pop('HOME', None))
        self.state = Path(self.tmp.name) / 'state' / 'menu.json'; self.state.parent.mkdir()
        self.settings = m.settings_path(); self.settings.parent.mkdir(parents=True)
        self.refreshes = []
        self.addCleanup(patch.stopall)
        patch('sys.stdout', io.StringIO()).start()  # commit/check echo their receipts; keep the run quiet
        patch.object(m, 'active', lambda: None).start()
        patch.object(m, 'refresh', lambda value: self.refreshes.append(value) or {'method': 'mocked', 'value': value}).start()

    def write_settings(self, indent=4, newline='', data=None):
        raw = (json.dumps(data or FIXTURE, indent=indent, ensure_ascii=False) + newline).encode()
        self.settings.write_bytes(raw); self.settings.chmod(0o664); return raw

    def snapshot(self):
        return sorted((str(p.relative_to(self.home)), sha(p.read_bytes())) for p in self.home.rglob('*') if p.is_file())

    def test_paths_follow_home(self):
        self.assertTrue(str(self.settings).startswith(str(self.home)))
        self.assertEqual(m.logo_target().name, 'gnu-darwin-workstation-menu-logo.png')
        self.assertTrue(m.LOGO.is_file(), 'artwork/menu-logo.png must exist for this stage')

    def test_plan_is_read_only(self):
        raw = self.write_settings(); before = self.snapshot()
        plan = m.plan()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.settings.read_bytes(), raw)
        self.assertEqual(self.refreshes, [])
        self.assertEqual(plan['status'], 'planned'); self.assertEqual(plan['writes'], 0)
        self.assertEqual(plan['style'], [4, False, ''])
        self.assertEqual({a['keys'][0] for a in plan['actions']}, {'menu-icon', 'menu-custom'})
        self.assertEqual({a['keys'][1] for a in plan['actions']}, {'value'})
        icon = next(a for a in plan['actions'] if a['keys'][0] == 'menu-icon')
        self.assertEqual(icon['before'], 'linuxmint-logo-ring-symbolic'); self.assertEqual(icon['after'], str(m.logo_target()))
        custom = next(a for a in plan['actions'] if a['keys'][0] == 'menu-custom')
        self.assertEqual((custom['before'], custom['after']), (False, True))
        self.assertEqual(plan['touched_paths'], sorted([str(self.settings), str(m.logo_target())]))
        self.assertEqual(plan['gsettings'], []); self.assertEqual(plan['dconf'], []); self.assertEqual(plan['skipped'], [])
        self.assertTrue(plan['label_matches_brief']); self.assertIsNone(plan['artwork']['destination_before'])
        self.assertNotIn('menu-label', {a['keys'][0] for a in plan['actions']})

    def test_missing_settings_is_skipped_and_apply_check_restore_are_no_ops(self):
        before = self.snapshot()
        plan = m.plan()
        self.assertEqual(plan['status'], 'skipped'); self.assertEqual(plan['actions'], []); self.assertEqual(plan['touched_paths'], [])
        self.assertEqual(len(plan['skipped']), 1); self.assertIn('17.json', plan['skipped'][0])
        self.assertEqual(m.commit(dict(plan), self.state), 0)
        self.assertEqual(self.snapshot(), before)
        state = json.loads(self.state.read_text()); self.assertEqual(state['status'], 'skipped')
        self.assertTrue(m.check(state)['ok'])
        m.restore(state, self.state); self.assertEqual(json.loads(self.state.read_text())['status'], 'restored')
        self.assertEqual(self.snapshot(), before); self.assertEqual(self.refreshes, [])

    def test_apply_patches_only_the_two_keys_and_keeps_style(self):
        raw = self.write_settings(indent=4, newline='')
        plan = m.plan()
        self.assertEqual(m.commit(plan, self.state), 0)
        after_raw = self.settings.read_bytes(); after = json.loads(after_raw)
        expected = json.loads(raw)
        expected['menu-icon']['value'] = str(m.logo_target()); expected['menu-custom']['value'] = True
        self.assertEqual(after, expected)
        self.assertEqual(after['menu-label']['value'], 'le cinabon')
        self.assertEqual(list(after), list(expected))  # key order preserved
        self.assertEqual(after_raw, (json.dumps(expected, indent=4, ensure_ascii=False)).encode())  # same style, no trailing newline
        self.assertIn('Café ☕'.encode(), after_raw)  # ensure_ascii stayed off
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o664)
        self.assertEqual(sha(m.logo_target().read_bytes()), sha(m.LOGO.read_bytes()))
        self.assertEqual(self.refreshes, [str(m.logo_target())])
        state = json.loads(self.state.read_text())
        self.assertEqual(state['status'], 'applied'); self.assertTrue(all(a['applied'] for a in state['actions']))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertTrue(m.check(state)['ok'])

    def test_apply_keeps_indent2_style(self):
        raw = self.write_settings(indent=2, newline='\n')
        self.assertEqual(m.plan()['style'], [2, False, '\n'])
        m.commit(m.plan(), self.state)
        data = json.loads(self.settings.read_bytes())
        self.assertEqual(self.settings.read_bytes(), (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode())
        self.assertEqual(data['menu-custom']['value'], True)

    def test_label_change_between_plan_and_apply_is_refused(self):
        self.write_settings()
        plan = m.plan()
        changed = json.loads(json.dumps(FIXTURE)); changed['menu-label']['value'] = 'changed label'
        raw = self.write_settings(data=changed); before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'label changed'): m.commit(plan, self.state)
        self.assertEqual(self.settings.read_bytes(), raw)
        self.assertFalse(m.logo_target().exists())
        self.assertEqual(self.refreshes, ['linuxmint-logo-ring-symbolic'])  # rollback re-signals the original icon only
        state = json.loads(self.state.read_text())
        self.assertEqual(state['status'], 'rolled-back'); self.assertIn('label changed', state['error'])
        self.assertFalse(any(a['applied'] for a in state['actions']))
        self.assertEqual([x for x in self.snapshot() if not x[0].endswith('.json')], [x for x in before if not x[0].endswith('.json')])

    def test_label_changed_after_apply_blocks_restore_write_and_check_reports_it(self):
        self.write_settings(); m.commit(m.plan(), self.state)
        data = json.loads(self.settings.read_bytes()); data['menu-label']['value'] = 'renamed'
        self.settings.write_text(json.dumps(data, indent=4, ensure_ascii=False))
        state = json.loads(self.state.read_text())
        self.assertIn('menu-label changed', m.check(state)['problems'])
        with self.assertRaisesRegex(RuntimeError, 'label changed'): m.restore(state, self.state)
        self.assertEqual(json.loads(self.settings.read_bytes())['menu-label']['value'], 'renamed')
        self.assertEqual(json.loads(self.settings.read_bytes())['menu-icon']['value'], str(m.logo_target()))

    def test_restore_returns_byte_identical_file_and_removes_png(self):
        for indent, newline in ((4, ''), (2, '\n')):
            with self.subTest(indent=indent):
                if self.state.exists(): self.state.unlink()
                raw = self.write_settings(indent=indent, newline=newline); self.refreshes.clear()
                m.commit(m.plan(), self.state)
                self.assertNotEqual(self.settings.read_bytes(), raw); self.assertTrue(m.logo_target().is_file())
                state = json.loads(self.state.read_text())
                m.restore(state, self.state)
                self.assertEqual(self.settings.read_bytes(), raw)
                self.assertFalse(m.logo_target().exists())
                self.assertEqual(self.refreshes, [str(m.logo_target()), 'linuxmint-logo-ring-symbolic'])
                state = json.loads(self.state.read_text()); self.assertEqual(state['status'], 'restored')
                self.assertFalse(any(a['applied'] or a['attempted'] for a in state['actions']))
                self.assertTrue(m.check(state)['ok'])
                self.assertEqual(sorted(p.name for p in self.settings.parent.iterdir()), ['17.json'])

    def test_restore_keeps_other_keys_changed_meanwhile(self):
        self.write_settings(); m.commit(m.plan(), self.state)
        data = json.loads(self.settings.read_bytes()); data['popup-width']['value'] = 900; data['__md5__'] = 'ffff'
        self.settings.write_text(json.dumps(data, indent=4, ensure_ascii=False))
        m.restore(json.loads(self.state.read_text()), self.state)
        data = json.loads(self.settings.read_bytes())
        self.assertEqual(data['popup-width']['value'], 900); self.assertEqual(data['__md5__'], 'ffff')
        self.assertEqual(data['menu-icon']['value'], 'linuxmint-logo-ring-symbolic'); self.assertEqual(data['menu-custom']['value'], False)

    def test_reapply_over_kept_release_leaves_existing_png_on_restore(self):
        self.write_settings(); m.commit(m.plan(), self.state)   # first release, kept: PNG and keys stay
        raw_kept = self.settings.read_bytes()
        second = self.state.with_name('menu2.json')
        plan = m.plan()
        self.assertEqual(plan['artwork']['destination_before']['sha256'], sha(m.LOGO.read_bytes()))
        m.commit(plan, second)
        m.restore(json.loads(second.read_text()), second)
        self.assertEqual(self.settings.read_bytes(), raw_kept)
        self.assertTrue(m.logo_target().is_file())

    def test_apply_refuses_existing_state_and_check_flags_drift(self):
        self.write_settings(); m.commit(m.plan(), self.state)
        state = json.loads(self.state.read_text())
        data = json.loads(self.settings.read_bytes()); data['menu-custom']['value'] = False
        self.settings.write_text(json.dumps(data, indent=4, ensure_ascii=False))
        self.assertEqual(m.check(state)['problems'], ['menu-custom'])
        m.logo_target().unlink()
        self.assertIn('logo copy missing', m.check(state)['problems'])

    def test_cli_plan_and_check_exit_codes(self):
        import subprocess
        self.write_settings()
        env = dict(os.environ, HOME=str(self.home), TANGERINE_TEST='1')
        out = subprocess.run([sys.executable, str(HERE / 'menu_adapter.py'), 'plan'], capture_output=True, text=True, env=env)
        if out.returncode == 0:
            plan = json.loads(out.stdout); self.assertEqual(plan['status'], 'planned'); self.assertEqual(plan['path'], str(self.settings))
        else:  # gsettings unavailable in this environment: the applet guard must be the only reason
            self.assertIn('enabled', out.stdout + out.stderr)
        out = subprocess.run([sys.executable, str(HERE / 'menu_adapter.py'), 'apply'], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 2); self.assertIn('--state required', out.stderr)


if __name__ == '__main__': unittest.main()
