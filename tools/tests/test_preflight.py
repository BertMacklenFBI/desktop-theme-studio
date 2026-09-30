"""Tests for tools/preflight.py. Temporary trees, fake /proc and fake plymouthd.conf only.

Run: /usr/bin/python3 -B -m unittest discover -s tools/tests -p 'test_preflight.py' -v
"""
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parents[1]
REAL_STUDIO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import preflight  # noqa: E402


def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def load_real_guard():
    spec = importlib.util.spec_from_file_location('guard_under_test', REAL_STUDIO / preflight.GUARD_REL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tree(unittest.TestCase):
    """A fake Studio + home + proc + system state under one temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.studio = self.root / 'studio'
        self.home = self.root / 'home'
        self.proc = self.root / 'proc'
        self.system = self.root / 'var-lib'
        for folder in (self.studio / 'presets', self.studio / 'refinements', self.studio / 'state',
                       self.home, self.proc, self.system):
            folder.mkdir(parents=True)
        write(self.proc / 'locks', '')
        # Keep the real guard reachable so guard_busy() exercises the import path.
        guard = self.studio / preflight.GUARD_REL
        guard.parent.mkdir(parents=True)
        guard.write_text((REAL_STUDIO / preflight.GUARD_REL).read_text())
        self.conf = self.root / 'plymouthd.conf'

    def tearDown(self):
        self._tmp.cleanup()

    def ctx(self, **kw):
        base = dict(studio=self.studio, home=self.home, proc=self.proc, plymouthd_conf=self.conf,
                    system_state=self.system, env={}, self_pid=999999)
        base.update(kw)
        return preflight.Context(**base)

    def preset(self, ident, status='kept', receipt='20260101-000000-abcdef', recovery=None, plymouth=None, geometry=None):
        folder = self.studio / 'presets' / ident
        write(folder / 'state/latest.json', {'id': receipt})
        write(folder / 'state' / receipt / 'desktop.json', {'status': status})
        if recovery:
            write(folder / 'state' / receipt / 'recovery.json', {'status': recovery})
        if plymouth:
            write(folder / 'system/plymouth' / plymouth, '')
        if geometry is not None:
            write(folder / 'design.json', {'geometry': geometry})
        return folder

    def collection(self, slugs, stage_ids=None):
        base = self.studio / preflight.COLLECTION_REL
        write(base / 'profiles.json', {'profiles': {s: {'name': s} for s in slugs}})
        for slug, stage in (stage_ids or {}).items():
            write(base / 'runtime-quality/system-stage/profiles' / f'{slug}.json', {'stage_id': stage})
        return base

    def process(self, pid, argv, ppid=1):
        folder = self.proc / str(pid)
        folder.mkdir(parents=True)
        (folder / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        (folder / 'stat').write_text(f'{pid} ({Path(argv[0]).name[:15]}) S {ppid} {pid} {pid} 0 -1\n')

    def levels(self, findings, check=None):
        return [f['level'] for f in findings if check is None or f['check'] == check]


class TargetTests(Tree):
    def test_resolution_kinds_and_primary(self):
        self.preset('tangerine-graphite')
        self.collection(['tangerine-graphite', 'clay-relief'])
        (self.studio / 'refinements/ocean-silk-current').mkdir()
        both = preflight.resolve_target(self.studio, 'tangerine-graphite')
        self.assertEqual(both.kinds, ['preset', 'collection'])
        self.assertEqual(both.primary, 'preset')
        self.assertTrue(both.collection)
        self.assertEqual(preflight.resolve_target(self.studio, 'tangerine-graphite', 'collection').primary, 'collection')
        self.assertEqual(preflight.resolve_target(self.studio, 'clay-relief').kinds, ['collection'])
        self.assertEqual(preflight.resolve_target(self.studio, 'ocean-silk-current').primary, 'refinement')

    def test_invalid_ids(self):
        (self.studio / 'presets/_template').mkdir()
        for bad in ('../etc', 'Upper', '', '_template', 'missing'):
            with self.assertRaises(ValueError, msg=bad):
                preflight.resolve_target(self.studio, bad)
        self.preset('plain')
        with self.assertRaises(ValueError):
            preflight.resolve_target(self.studio, 'plain', 'collection')


class PendingTests(Tree):
    def test_all_terminal_is_ok(self):
        self.preset('a', 'kept')
        self.preset('b', 'restored', recovery='restored')
        out = preflight.check_pending(self.ctx(), None, 'user')
        self.assertEqual(self.levels(out), ['ok'])
        self.assertIn('kept, restored', out[0]['message'])

    def test_busy_unknown_and_recovery_block(self):
        self.preset('a', 'pending')
        self.preset('b', 'kept', recovery='recovery-required')
        self.preset('c', 'mystery')
        out = preflight.check_pending(self.ctx(), None, 'user')
        self.assertEqual(self.levels(out), ['block'] * 3)
        messages = ' '.join(f['message'] for f in out)
        self.assertIn("status='pending'", messages)
        self.assertIn("status='recovery-required'", messages)
        self.assertIn('non-terminal/unknown', messages)

    def test_only_latest_receipt_counts(self):
        folder = self.preset('a', 'kept', receipt='20260102-000000-bbbbbb')
        write(folder / 'state/20260101-000000-aaaaaa/desktop.json', {'status': 'recovery-required'})
        self.assertEqual(self.levels(preflight.check_pending(self.ctx(), None, 'user')), ['ok'])

    def test_extended_stores(self):
        receipt = write(self.studio / 'refinements/r/state/x/receipt.json', {'status': 'applying'})
        write(self.studio / 'refinements/r/state/latest.json', {'receipt': str(receipt)})
        write(self.studio / 'refinements/s/state/latest.json', {'id': 'y'})
        write(self.studio / 'refinements/s/state/y/receipt.json', {'status': 'kept'})
        write(self.studio / 'state/activation-trials/t.json', {'preset': 'p', 'receipt': '/x', 'status': 'pending'})
        write(self.studio / preflight.COLLECTION_REL / 'state/shortcuts/1/receipt.json', {'status': 'active', 'profile': 'q'})
        write(self.studio / preflight.COLLECTION_REL / 'state/shortcuts/2/receipt.json', {'status': 'switching', 'profile': 'q'})
        write(self.studio / 'state/nim-nitch-repair/1/receipt.json', {'status': 'installing'})
        write(self.home / '.local/state/ocean-silk/snapshots/transaction.json', {'status': 'rollback_failed'})
        write(self.studio / 'state/transactions/t1/transaction.json', {'status': 'applied'})
        rows, errors = preflight.collect_receipts(self.ctx())
        self.assertEqual(errors, [])
        state = {(r['store'], r['status']): r['state'] for r in rows}
        self.assertEqual(state, {('refinement', 'applying'): 'busy', ('refinement', 'kept'): 'terminal',
                                 ('trial', 'pending'): 'busy', ('shortcut', 'active'): 'terminal',
                                 ('shortcut', 'switching'): 'busy', ('nim-repair', 'installing'): 'busy',
                                 ('legacy-snapshot', 'rollback_failed'): 'busy',
                                 ('studio-transaction', 'applied'): 'terminal'})
        self.assertEqual(self.levels(preflight.check_pending(self.ctx(), None, 'user')), ['block'] * 5)

    def test_xdg_state_home_is_honoured(self):
        write(self.root / 'xdg/ocean-silk/snapshots/transaction.json', {'status': 'pending'})
        rows, _ = preflight.collect_receipts(self.ctx(env={'XDG_STATE_HOME': str(self.root / 'xdg')}))
        self.assertEqual([r['store'] for r in rows], ['legacy-snapshot'])

    def test_unreadable_receipt_blocks(self):
        write(self.studio / 'presets/a/state/latest.json', '{broken')
        write(self.studio / 'presets/b/state/latest.json', {'id': '../escape'})
        out = preflight.check_pending(self.ctx(), None, 'user')
        self.assertEqual(self.levels(out), ['block', 'block'])

    def test_parity_with_guard_pending(self):
        """For guard's own scope, preflight blocks exactly what guard.pending() reports."""
        guard = load_real_guard()
        self.preset('a', 'pending')
        self.preset('b', 'kept', recovery='recovery-required')
        self.preset('c', 'kept')
        self.preset('d', 'restoring')
        write(self.studio / 'state/activation-trials/x.json', {'preset': 'c', 'receipt': str(self.root / 'r.json'), 'status': 'applying'})
        write(self.studio / 'state/activation-trials/y.json', {'preset': 'c', 'receipt': str(self.root / 'r.json'), 'status': 'kept'})
        expected = sorted(item['source'] for item in guard.pending(self.studio))
        rows, _ = preflight.collect_receipts(self.ctx())
        mine = sorted(str(Path(r['path']).with_name('desktop.json')) if r['path'].endswith('recovery.json') else r['path']
                      for r in rows if r['state'] != 'terminal')
        self.assertEqual(mine, expected)

    def test_busy_vocabulary_tracks_real_guard(self):
        self.assertEqual(preflight.guard_busy(REAL_STUDIO), frozenset(load_real_guard().BUSY))
        self.assertEqual(preflight.GUARD_BUSY, frozenset(load_real_guard().BUSY))


class LockTests(Tree):
    def lockfile(self, rel='state/activation.lock'):
        return write(self.studio / rel, '')

    def test_free_absent_and_never_created(self):
        self.lockfile()
        out = preflight.check_locks(self.ctx(), None, 'user')
        self.assertEqual(self.levels(out), ['ok'])
        self.assertFalse((self.studio / 'state/lock').exists())
        self.assertFalse((self.system / 'system.lock').exists())
        self.assertFalse((self.home / '.local/state').exists())

    def test_exclusive_and_shared_holders_block(self):
        for mode, label in ((fcntl.LOCK_EX, 'held-exclusive'), (fcntl.LOCK_SH, 'held-shared')):
            path = self.lockfile('presets/p/state/.lock')
            with open(path, 'a') as holder:
                fcntl.flock(holder, mode | fcntl.LOCK_NB)
                self.assertEqual(preflight.probe_lock(path), label)
                out = preflight.check_locks(self.ctx(), None, 'user')
                self.assertEqual(self.levels(out), ['block'])
                self.assertEqual(out[0]['detail']['owner'], 'desktop_control.py locked()')
            self.assertEqual(preflight.probe_lock(path), 'free')

    def test_proc_locks_names_holder(self):
        path = self.lockfile()
        st = os.stat(path)
        dev = f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}'
        write(self.proc / 'locks', f'1: FLOCK  ADVISORY  WRITE 4242 {dev} 0 EOF\n'
                                   f'1: -> FLOCK  ADVISORY  WRITE 4343 {dev} 0 EOF\n'
                                   '2: POSIX  ADVISORY  READ 1 08:02:1 0 EOF\n')
        out = preflight.check_locks(self.ctx(), None, 'user')
        self.assertEqual(self.levels(out), ['block'])
        self.assertEqual([h['pid'] for h in out[0]['detail']['holders']], [4242, 4343])

    @unittest.skipIf(os.geteuid() == 0, 'root ignores file modes')
    def test_unreadable_lock_warns(self):
        path = self.lockfile('state/lock')
        path.chmod(0)
        try:
            out = preflight.check_locks(self.ctx(), None, 'user')
        finally:
            path.chmod(0o600)
        self.assertIn('warn', self.levels(out))
        self.assertNotIn('block', self.levels(out))

    def test_symlinked_lock_is_not_followed(self):
        target = write(self.root / 'elsewhere.lock', '')
        (self.studio / 'state/activation.lock').symlink_to(target)
        self.assertTrue(preflight.probe_lock(self.studio / 'state/activation.lock').startswith('unreadable'))


class ProcessTests(Tree):
    def test_classifier(self):
        c = preflight.classify_cmdline
        self.assertEqual(c(['/usr/bin/python3', '/s/presets/x/theme.py', 'apply']), 'theme.py')
        self.assertEqual(c(['/bin/sh', './theme.sh', 'keep']), 'theme.sh')
        self.assertEqual(c(['/usr/bin/python3', '-B', 'isolate.py']), 'isolate.py')
        self.assertEqual(c(['sudo', '-E', 'PYTHONPATH=x', '/usr/bin/python3', 'system/system.py', 'apply']), 'system.py')
        self.assertEqual(c(['/usr/bin/python3', '/home/u/.local/bin/theme', 'tg']), 'theme shortcut')
        self.assertEqual(c(['/usr/bin/python3', '/s/refinements/cinnamon-current-collection/live.py']), 'live.py')
        self.assertEqual(c(['/usr/bin/python3', '/s/presets/x/system/system.py', 'apply', '--commit']), 'system.py')
        self.assertEqual(c(['/usr/bin/python3', 'runtime-quality/system-stage/system.py']), 'system.py')
        self.assertEqual(c(['/usr/bin/python3', 'repair_installer.py', 'apply']), 'repair_installer.py')
        self.assertEqual(c(['publish_sources.py']), 'publish_sources.py')
        self.assertIn('Xephyr', c(['Xephyr', ':3', '-screen', '1280x800']))
        self.assertIsNotNone(c(['cinnamon', '--replace'], ['bash']))
        self.assertIsNone(c(['cinnamon', '--replace'], ['cinnamon-launcher']))
        for benign in (['vim', 'theme.py'], ['grep', 'collection.py'], ['/usr/bin/python3', '-c', 'import theme.py'],
                       ['/usr/bin/python3', '-m', 'unittest', 'theme.py'], ['/usr/bin/python3', '/other/live.py'],
                       ['/usr/bin/python3', 'other/system.py'], ['/usr/bin/python3', 'xsystem/system.py'], ['cinnamon'], [], ['bash']):
            self.assertIsNone(c(benign), benign)

    def test_scan_fake_proc(self):
        self.process(100, ['/usr/bin/python3', 'theme.py', 'apply'])
        self.process(200, ['cinnamon-launcher'])
        self.process(201, ['cinnamon', '--replace'], ppid=200)
        self.process(300, ['bash'])
        self.process(301, ['cinnamon', '--replace'], ppid=300)
        self.process(999999, ['/usr/bin/python3', 'shortcut.py'])  # this process: excluded
        (self.proc / '400').mkdir()  # kernel thread / raced exit: empty
        (self.proc / 'self').mkdir()
        out = preflight.check_processes(self.ctx(), None, 'user')
        blocks = [f for f in out if f['level'] == 'block']
        self.assertEqual([f['message'] for f in blocks], ['running: theme.py (pid 100)',
                                                           'running: cinnamon --replace (not the session launcher) (pid 301)'])
        self.assertEqual(self.levels(out, 'procs').count('info'), 1)

    def test_clean_proc(self):
        self.process(1, ['/sbin/init'])
        self.assertEqual(self.levels(preflight.check_processes(self.ctx(), None, 'user')), ['ok'])

    def test_missing_proc_blocks(self):
        out = preflight.check_processes(self.ctx(proc=self.root / 'nope'), None, 'user')
        self.assertEqual(self.levels(out), ['block'])


class PlymouthTests(Tree):
    def test_parse(self):
        write(self.conf, '# c\n[Other]\nTheme=wrong\n[Daemon]\n; note\nTheme = tangerine-graphite \nDeviceScale=1\n')
        self.assertEqual(preflight.plymouthd_theme(self.conf), ('tangerine-graphite', None))
        self.assertEqual(preflight.plymouthd_theme(self.root / 'absent'), (None, None))
        write(self.conf, '[Daemon]\nShowDelay=0\n')
        self.assertEqual(preflight.plymouthd_theme(self.conf), (None, None))

    def test_target_names(self):
        a = self.preset('a', plymouth='a-boot.plymouth')
        b = self.preset('b', plymouth='b.script')
        write(self.studio / 'presets/b/system/plymouth/b.script.in', '')
        c = self.preset('c', plymouth='plymouthd.conf')
        write(c / 'system/plymouth/plymouthd.conf', '[Daemon]\nTheme=cee\n')
        d = self.preset('d', plymouth='d.script.in')
        write(d / 'system/plymouth/theme.plymouth.in', '')
        e = self.preset('e')
        self.assertEqual([preflight.preset_plymouth_name(f) for f in (a, b, c, d, e)], ['a-boot', 'b', 'cee', 'd', None])
        self.collection(['k'], {'k': 'cinnamon-current-k'})
        self.assertEqual(preflight.collection_plymouth_name(self.studio, 'k'), 'cinnamon-current-k')

    def test_mismatch_blocks_with_restore_message(self):
        write(self.conf, '[Daemon]\nTheme=tangerine-graphite\n')
        self.preset('quiet', plymouth='quiet.script')
        target = preflight.resolve_target(self.studio, 'quiet')
        out = preflight.check_plymouth(self.ctx(), target, 'root')
        self.assertEqual(self.levels(out), ['block'])
        self.assertIn("Tangerine Graphite's", out[0]['message'])
        self.assertIn('must be restored first', out[0]['message'])

    def test_collection_profile_blocks_when_overridden(self):
        write(self.conf, '[Daemon]\nTheme=tangerine-graphite\n')
        self.collection(['clay'], {'clay': 'cinnamon-current-clay'})
        out = preflight.check_plymouth(self.ctx(), preflight.resolve_target(self.studio, 'clay'), 'root')
        self.assertEqual(self.levels(out), ['block'])

    def test_match_and_dual_kind_warning(self):
        write(self.conf, '[Daemon]\nTheme=tangerine-graphite\n')
        self.preset('tangerine-graphite', plymouth='tangerine-graphite.script')
        self.collection(['tangerine-graphite'], {'tangerine-graphite': 'cinnamon-current-tangerine-graphite'})
        out = preflight.check_plymouth(self.ctx(), preflight.resolve_target(self.studio, 'tangerine-graphite'), 'root')
        self.assertEqual(self.levels(out), ['ok', 'warn'])
        forced = preflight.resolve_target(self.studio, 'tangerine-graphite', 'collection')
        self.assertEqual(self.levels(preflight.check_plymouth(self.ctx(), forced, 'root')), ['block'])

    def test_unset_and_unstaged(self):
        self.preset('x')
        target = preflight.resolve_target(self.studio, 'x')
        self.assertEqual(self.levels(preflight.check_plymouth(self.ctx(), target, 'root')), ['ok'])
        write(self.conf, '[Daemon]\nTheme=other\n')
        self.assertEqual(self.levels(preflight.check_plymouth(self.ctx(), target, 'root')), ['warn'])

    def test_askpass(self):
        self.preset('p')
        self.collection(['k', 'p'])
        k = preflight.resolve_target(self.studio, 'k')
        p = preflight.resolve_target(self.studio, 'p')
        # Unset is fine since the sudo-ticket fix (plain `theme NAME` works).
        self.assertEqual(self.levels(preflight.check_askpass(self.ctx(), k, 'root')), ['info'])
        self.assertEqual(self.levels(preflight.check_askpass(self.ctx(), p, 'root')), ['info'])
        tool = write(self.root / 'askpass', '#!/bin/sh\n')
        tool.chmod(0o755)
        self.assertEqual(self.levels(preflight.check_askpass(self.ctx(env={'SUDO_ASKPASS': str(tool)}), k, 'root')), ['ok'])
        self.assertEqual(self.levels(preflight.check_askpass(self.ctx(env={'SUDO_ASKPASS': '/nonexistent'}), k, 'root')), ['warn'])
        self.preset('solo')
        solo = preflight.resolve_target(self.studio, 'solo')
        self.assertEqual(self.levels(preflight.check_askpass(self.ctx(), solo, 'root')), ['skip'])


GOOD_BUILDER = '''
import argparse, os, tempfile
EXPECTED_IDS = frozenset({"k", "p"})
CURRENT_IDS = frozenset({"k"})
def atomic_write(path, content):
    fd, tmp = tempfile.mkstemp()
    os.replace(tmp, path)
def build():
    return b"x"
def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    content = build()
    if args.check:
        assert open(args.output, "rb").read() == content
    else:
        atomic_write(args.output, content)
'''


class CatalogTests(Tree):
    def builder(self, source):
        return write(self.studio / preflight.CATALOG_REL, source)

    def test_real_builder_check_is_readonly(self):
        source = (REAL_STUDIO / preflight.CATALOG_REL).read_text()
        ok, reason = preflight.verify_check_is_readonly(source)
        self.assertTrue(ok, reason)

    def test_verifier_rejects_writers(self):
        v = preflight.verify_check_is_readonly
        self.assertTrue(v(GOOD_BUILDER)[0])
        self.assertFalse(v(GOOD_BUILDER.replace('"--check"', '"--dry"'))[0])
        self.assertFalse(v(GOOD_BUILDER.replace('assert open(args.output, "rb")', 'assert open(args.output, "w")'))[0])
        self.assertFalse(v(GOOD_BUILDER.replace('    content = build()', '    content = build(); atomic_write("x", b"")'))[0])
        self.assertFalse(v(GOOD_BUILDER.replace('    return b"x"', '    Path("x").write_text("y"); return b"x"'))[0])
        self.assertFalse(v(GOOD_BUILDER.replace('    return b"x"', '    import subprocess; return b"x"'))[0])
        self.assertFalse(v('def main(:')[0])
        self.assertFalse(v('x = 1')[0])

    def test_runner_results(self):
        self.builder(GOOD_BUILDER)
        self.preset('p')
        self.collection(['k'])
        k = preflight.resolve_target(self.studio, 'k')
        ok = self.ctx(run_catalog=lambda s: (0, '{"status": "checked", "sha256": "ab"}\n', ''))
        out = preflight.check_catalog(ok, k, 'catalog')
        self.assertEqual(self.levels(out), ['ok'])
        self.assertEqual(out[0]['detail']['status'], 'checked')
        stale = self.ctx(run_catalog=lambda s: (1, '', 'catalog error: generated registry is missing or stale: x\n'))
        out = preflight.check_catalog(stale, k, 'catalog')
        self.assertEqual(self.levels(out), ['block'])
        self.assertIn('stale', out[0]['message'])
        def boom(script):
            raise TimeoutError('slow')
        self.assertEqual(self.levels(preflight.check_catalog(self.ctx(run_catalog=boom), k, 'catalog')), ['block'])
        p = preflight.resolve_target(self.studio, 'p')
        self.assertEqual(self.levels(preflight.check_catalog(ok, p, 'catalog')), ['ok'])

    def test_membership_warnings(self):
        self.builder(GOOD_BUILDER)
        self.preset('zz')
        self.collection(['p'])
        ok = self.ctx(run_catalog=lambda s: (0, '{}', ''))
        self.assertEqual(self.levels(preflight.check_catalog(ok, preflight.resolve_target(self.studio, 'zz'), 'catalog')), ['warn', 'ok'])
        self.assertEqual(self.levels(preflight.check_catalog(ok, preflight.resolve_target(self.studio, 'p'), 'catalog')), ['warn', 'ok'])

    def test_unverifiable_builder_is_skipped_not_run(self):
        self.builder(GOOD_BUILDER.replace('    content = build()', '    content = build(); atomic_write("x", b"")'))
        self.preset('p')
        called = []
        out = preflight.check_catalog(self.ctx(run_catalog=lambda s: called.append(s)), preflight.resolve_target(self.studio, 'p'), 'catalog')
        self.assertEqual(self.levels(out), ['warn'])
        self.assertEqual(called, [])
        self.assertEqual(self.levels(preflight.check_catalog(self.ctx(studio=self.root / 'none'), preflight.Target('p', ['preset'], 'preset'), 'catalog')), ['warn'])


class PanelTests(Tree):
    def gs(self, heights="['1:46', '2:66']", enabled="['1:0:top', '2:0:bottom']"):
        values = {'panels-height': heights, 'panels-enabled': enabled}
        calls = []
        def get(schema, key):
            calls.append((schema, key))
            return values[key]
        return get, calls

    def test_drift_warns(self):
        self.preset('t', geometry={'panel_height': 44, 'bottom_panel_height': 54})
        get, calls = self.gs()
        out = preflight.check_panels(self.ctx(gsettings=get), preflight.resolve_target(self.studio, 't'), 'user')
        self.assertEqual(self.levels(out), ['warn', 'warn'])
        self.assertIn('design 54px, live 66px', out[0]['message'])
        self.assertEqual({k for _, k in calls}, {'panels-height', 'panels-enabled'})

    def test_match_and_variants(self):
        self.preset('t', geometry={'top_panel_px': 46})
        get, _ = self.gs(heights="@as ['1:46']", enabled="['1:0:top']")
        self.assertEqual(self.levels(preflight.check_panels(self.ctx(gsettings=get), preflight.resolve_target(self.studio, 't'), 'user')), ['ok'])
        self.preset('b', geometry={'bottom_panel_height': 54})
        get, _ = self.gs(heights="['1:46']", enabled="['1:0:top']")
        self.assertEqual(self.levels(preflight.check_panels(self.ctx(gsettings=get), preflight.resolve_target(self.studio, 'b'), 'user')), ['warn'])

    def test_skips_and_failures(self):
        self.preset('keep', geometry={'preserve_panel_layout': True, 'panel_height': 40})
        self.preset('none', geometry={})
        self.preset('nodesign')
        self.collection(['k'])
        for ident in ('keep', 'none', 'nodesign', 'k'):
            out = preflight.check_panels(self.ctx(gsettings=lambda *a: self.fail('gsettings called')),
                                         preflight.resolve_target(self.studio, ident), 'user')
            self.assertEqual(self.levels(out), ['skip'], ident)
        self.preset('t', geometry={'panel_height': 44})
        def broken(schema, key):
            raise RuntimeError('no session bus')
        self.assertEqual(self.levels(preflight.check_panels(self.ctx(gsettings=broken), preflight.resolve_target(self.studio, 't'), 'user')), ['warn'])


class DriverTests(Tree):
    def cli(self, *args):
        base = ['--studio', str(self.studio), '--home', str(self.home), '--proc', str(self.proc),
                '--plymouthd-conf', str(self.conf), '--system-state', str(self.system)]
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k not in ('XDG_STATE_HOME', 'SUDO_ASKPASS')}
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            try:
                code = preflight.main([*args, *base])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_plan_per_stage(self):
        names = lambda t, s: [c.__name__ for c in preflight.plan(t, s)]
        preset = preflight.Target('p', ['preset'], 'preset')
        both = preflight.Target('p', ['preset', 'collection'], 'preset')
        common = ['check_pending', 'check_locks', 'check_processes']
        self.assertEqual(names(preset, 'user'), common + ['check_panels', 'check_panel_owner'])
        self.assertEqual(names(both, 'user'), common + ['check_askpass', 'check_catalog', 'check_panels', 'check_panel_owner'])
        self.assertEqual(names(preset, 'root'), common + ['check_plymouth', 'check_askpass', 'check_panel_owner'])
        self.assertEqual(names(preset, 'catalog'), common + ['check_catalog'])

    def test_exit_codes(self):
        self.preset('p', plymouth='p.script')
        code, out, _ = self.cli('p', '--stage', 'root')
        self.assertEqual(code, 0, out)
        self.assertIn('CLEAR: 0 blocking', out)
        write(self.conf, '[Daemon]\nTheme=tangerine-graphite\n')
        code, out, _ = self.cli('p', '--stage', 'root', '--json')
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual((report['blocking'], report['exit']), (1, 1))
        self.assertEqual(self.cli('missing', '--stage', 'root')[0], 2)
        self.assertEqual(self.cli('p', '--stage', 'bogus')[0], 2)
        self.assertEqual(self.cli('p')[0], 2)

    def test_crashing_check_blocks(self):
        self.preset('p')
        def explode(ctx, target, stage):
            raise KeyError('boom')
        with mock.patch.object(preflight, 'plan', lambda t, s: [explode]):
            out = preflight.run(self.ctx(), preflight.resolve_target(self.studio, 'p'), 'user')
        self.assertEqual(self.levels(out), ['block'])

    def test_render_table(self):
        report = {'id': 'p', 'stage': 'user', 'kinds': ['preset'], 'primary': 'preset', 'blocking': 1, 'warnings': 0,
                  'findings': [preflight.finding('locks', 'block', 'lock held: x', owner='o')]}
        text = preflight.render(report)
        self.assertIn('CHECK', text)
        self.assertIn('BLOCKED: 1 blocking', text)
        self.assertIn('"owner": "o"', text)


if __name__ == '__main__':
    unittest.main()


class PanelOwnerTests(unittest.TestCase):
    """DESIGN.md (repairs/panel-rail-20260927) section 4, amended by coordinator review items
    1/3/4/8 (2026-09-27): the BLOCKING panel-owner check now scans every preset (not only the
    collection target), fails closed on an undeterminable collection panel state (panel_seq
    scan, matching publication A's restore_preconditions), and also blocks on a still-installed
    Plymouth theme directory. Plus design_panels()/check_panels() learning a rail profile's
    left/right panel from profiles.json panel_layout instead of unconditionally preserving the
    live layout."""

    def studio(self, tmp):
        studio = Path(tmp) / 'studio'
        (studio / 'refinements/cinnamon-current-collection/state').mkdir(parents=True)
        (studio / 'presets/indigo-lunchbox/state').mkdir(parents=True)
        profiles = {'profiles': {'indigo-lunchbox': {'panel_layout': {
            'schema': 1, 'kind': 'rail',
            'provenance': {'lineage': 'presets/indigo-lunchbox/lineage.json',
                           'lineage_panel_sha256': 'a' * 64, 'design_panel_layout_sha256': 'b' * 64},
            'panel': {'panels_enabled': ['1:0:left'], 'panels_height': ['1:56'],
                      'enabled_applets': ['panel1:left:0:menu@cinnamon.org:17',
                                          'panel1:right:0:calendar@cinnamon.org:new'],
                      'remove': [], 'new_instance': {'uuid': 'calendar@cinnamon.org', 'settings': {'a': True}},
                      'zone_sizes': {}, 'enabled_desklets': []}}},
            'clay-relief': {}}}
        write(studio / 'refinements/cinnamon-current-collection/profiles.json', profiles)
        return studio

    def ctx(self, studio, **overrides):
        kwargs = dict(studio=studio, home=studio, proc=Path('/proc'), plymouthd_conf=Path('/dev/null'),
                     plymouth_themes_dir=studio / 'no-plymouth-themes', system_state=Path('/dev/null'), env={})
        kwargs.update(overrides)
        return preflight.Context(**kwargs)

    def test_design_panels_reads_left_from_profiles_json_panel_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            wanted, note = preflight.design_panels(self.ctx(studio), target)
            self.assertEqual(wanted, {'left': 56})
            self.assertIsNone(note)

    def test_design_panels_still_preserves_live_layout_for_a_normal_collection_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            target = preflight.Target('clay-relief', ['collection'], 'collection')
            wanted, note = preflight.design_panels(self.ctx(studio), target)
            self.assertIsNone(wanted)
            self.assertEqual(note, 'collection profiles preserve the live panel layout')

    def test_panel_owner_blocks_theme_profile_while_the_preset_rail_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            state = studio / 'presets/indigo-lunchbox/state'
            write(state / 'latest.json', {'id': 'r1'})
            (state / 'r1').mkdir()
            write(state / 'r1/desktop.json', {'status': 'kept', 'panel': {'rail': {'profile': 'indigo-lunchbox'}}})
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(any(f['level'] == 'block' for f in findings))

    def test_panel_owner_blocks_theme_slate_orbit_while_a_different_preset_rail_is_kept(self):
        """Coordinator review item 1: ANY collection target is blocked, not only indigo-lunchbox."""
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            state = studio / 'presets/indigo-lunchbox/state'
            write(state / 'latest.json', {'id': 'r1'})
            (state / 'r1').mkdir()
            write(state / 'r1/desktop.json', {'status': 'kept', 'panel': {'rail': {'profile': 'indigo-lunchbox'}}})
            target = preflight.Target('slate-orbit', ['collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            blocking = [f for f in findings if f['level'] == 'block']
            self.assertTrue(blocking)
            self.assertIn('indigo-lunchbox', blocking[0]['message'])

    def test_panel_owner_allows_theme_profile_once_the_preset_rail_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            state = studio / 'presets/indigo-lunchbox/state'
            write(state / 'latest.json', {'id': 'r1'})
            (state / 'r1').mkdir()
            write(state / 'r1/desktop.json', {'status': 'restored', 'panel': {'rail': {'profile': 'indigo-lunchbox'}}})
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(all(f['level'] != 'block' for f in findings))

    def test_panel_owner_warns_but_does_not_block_on_an_unreadable_preset_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            state = studio / 'presets/indigo-lunchbox/state'
            write(state / 'latest.json', {'id': 'r1'})
            # 'r1' directory is missing on purpose: latest.json names a receipt that cannot be read.
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(all(f['level'] != 'block' for f in findings))
            self.assertTrue(any(f['level'] == 'warn' and 'unreadable' in f['message'] for f in findings))

    def test_panel_owner_blocks_theme_profile_while_its_own_plymouth_theme_is_installed(self):
        """Coordinator review item 3: the preset root stage's own installed marker."""
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            themes_dir = studio / 'plymouth-themes'
            (themes_dir / 'indigo-lunchbox').mkdir(parents=True)
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio, plymouth_themes_dir=themes_dir), target, 'user')
            blocking = [f for f in findings if f['level'] == 'block']
            self.assertTrue(blocking)
            self.assertIn('plymouth', blocking[0]['message'].lower())

    def test_panel_owner_never_blocks_a_non_rail_profile_on_its_installed_plymouth_theme(self):
        """Coordinator review item R1 (regression): a normal (non-rail) profile's own preset
        root stage commonly stays installed under /usr/share/plymouth/themes permanently (e.g.
        the real live .../themes/tangerine-graphite); the Plymouth block must be scoped to rail
        profiles only (those with a profiles.json panel_layout), or every other current profile
        would be blocked forever."""
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            themes_dir = studio / 'plymouth-themes'
            (themes_dir / 'clay-relief').mkdir(parents=True)
            target = preflight.Target('clay-relief', ['collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio, plymouth_themes_dir=themes_dir), target, 'user')
            self.assertTrue(all(f['level'] != 'block' for f in findings))

    def test_panel_owner_blocks_the_preset_apply_while_the_collection_rail_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            cstate = studio / 'refinements/cinnamon-current-collection/state'
            write(cstate / 'panel-epoch.json', {'receipt': str(cstate / 'r1/receipt.json'), 'panel_seq': 1})
            (cstate / 'r1').mkdir()
            write(cstate / 'r1/receipt.json',
                      {'status': 'kept', 'panel': {'state_after': 'rail', 'panel_seq': 1,
                                                    'rail': {'profile': 'indigo-lunchbox'}}})
            ctx = self.ctx(studio)
            for stage, kinds, primary in (('user', ['preset'], 'preset'), ('root', ['preset'], 'preset')):
                with self.subTest(stage=stage):
                    target = preflight.Target('indigo-lunchbox', kinds, primary)
                    findings = preflight.check_panel_owner(ctx, target, stage)
                    self.assertTrue(any(f['level'] == 'block' for f in findings))

    def test_panel_owner_never_blocks_an_unrelated_preset_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            (studio / 'presets/tangerine-graphite/state').mkdir(parents=True)
            cstate = studio / 'refinements/cinnamon-current-collection/state'
            write(cstate / 'panel-epoch.json', {'receipt': str(cstate / 'r1/receipt.json'), 'panel_seq': 1})
            (cstate / 'r1').mkdir()
            write(cstate / 'r1/receipt.json',
                      {'status': 'kept', 'panel': {'state_after': 'rail', 'panel_seq': 1,
                                                    'rail': {'profile': 'indigo-lunchbox'}}})
            target = preflight.Target('tangerine-graphite', ['preset'], 'preset')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(all(f['level'] != 'block' for f in findings))

    def test_panel_owner_fails_closed_when_the_collection_panel_state_is_undeterminable(self):
        """Coordinator review item 8: a panel receipt missing panel_seq (or any other reason
        collection_panel_state cannot resolve a state) blocks the preset apply rather than
        silently assuming home."""
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            cstate = studio / 'refinements/cinnamon-current-collection/state'
            write(cstate / 'panel-epoch.json', {'receipt': str(cstate / 'r1/receipt.json')})
            (cstate / 'r1').mkdir()
            write(cstate / 'r1/receipt.json',
                      {'status': 'kept', 'panel': {'state_after': 'rail', 'rail': {'profile': 'indigo-lunchbox'}}})
            target = preflight.Target('indigo-lunchbox', ['preset'], 'preset')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(any(f['level'] == 'block' and 'fail' in f['message'] for f in findings))

    def test_collection_panel_state_home_with_no_receipts_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            self.assertEqual(preflight.collection_panel_state(self.ctx(studio)), ('home', None))

    def test_panel_owner_clear_with_no_pointer_and_no_preset_rail(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = self.studio(tmp)
            target = preflight.Target('indigo-lunchbox', ['preset', 'collection'], 'collection')
            findings = preflight.check_panel_owner(self.ctx(studio), target, 'user')
            self.assertTrue(all(f['level'] != 'block' for f in findings))
