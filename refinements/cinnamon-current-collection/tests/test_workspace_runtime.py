"""Real tree-writer recovery fixtures; only Cinnamon/session calls are mocked."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

COLLECTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COLLECTION.parent / 'ocean-silk-current'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runtime = load('tested_workspace_runtime', COLLECTION / 'workspace_runtime.py')
engine = load('tested_workspace_tree_engine', COLLECTION.parent / 'ocean-silk-current/theme.py')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'collection'
        self.home = Path(self.temp.name) / 'home'
        self.target = self.home / '.local/share/cinnamon/applets' / runtime.UUID
        for name in (runtime.UUID, 'workspace-switcher-rounded'):
            shutil.copytree(COLLECTION / 'runtime' / name, self.root / 'runtime' / name)
        shutil.copytree(self.root / 'runtime' / runtime.UUID, self.target)
        (self.root / 'state').mkdir()
        self.before = engine.tree_fingerprint(self.target)
        self.counter = 0
        self.opted = {'workspace_geometry': {'shape': 'rounded', 'minimum_gap': 4}}
        self.journal_patch = patch.object(engine, 'journal', engine.E.journal)
        self.journal_patch.start()
        self.addCleanup(self.journal_patch.stop)

    def prepare(self, profile=None):
        section, row = runtime.plan('fixture', self.opted if profile is None else profile,
                                    self.root, self.home, engine)
        self.counter += 1
        ident = '20260930-100000-' + ('%06x' % self.counter)
        path = self.root / 'state' / ident / 'receipt.json'
        path.parent.mkdir()
        engine.prepare_tree_record(row)
        section['tree_index'] = 0
        data = {'id': ident, 'profile': 'fixture', 'status': 'applying', 'guard_armed': True,
                'trees': [row], 'actions': [], 'workspace_runtime': section}
        engine.tree_paths(path, data)
        engine.E.journal(path, data)
        return path, data

    def native(self, data, **kwargs):
        def evaluator(code):
            if 'global.__dtsWorkspaceBefore=' in code:
                return {'ready': True}
            return {'path': str(self.target), 'instances': [{'id': 36, 'ready': True, 'recreated': True}]}
        runtime.reload_checked(data, lambda *args: None, self.home,
                               evaluator=evaluator, snapshot=lambda home: {
                                   'files': {}, 'settings': {}, 'expected_native_ids': [36]}, **kwargs)

    def apply(self, profile=None):
        path, data = self.prepare(profile)
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        engine.install_trees(path, data)
        self.native(data)
        runtime.finish(path, data, self.root, engine, 'pending', engine.E.journal)
        data['status'] = 'pending'
        engine.E.journal(path, data)
        runtime.finish(path, data, self.root, engine, 'kept', engine.E.journal)
        data['status'] = 'kept'
        engine.E.journal(path, data)
        return path, data

    def restore(self, path, data):
        runtime.assert_owner(path, data, self.root)
        engine.restore_trees(path, data)
        data['workspace_restoring'] = True
        self.native(data)
        runtime.restore_owner(path, data, self.root, engine)
        data['status'] = 'restored'
        engine.E.journal(path, data)

    def test_opted_repeat_leave_and_newest_first_exact_restore(self):
        a, da = self.apply()
        baseline = da['workspace_runtime']['baseline']
        b, db = self.apply()
        self.assertEqual(db['workspace_runtime']['baseline'], baseline)
        c, dc = self.apply({})
        self.assertEqual(engine.tree_fingerprint(self.target), self.before)
        with self.assertRaisesRegex(RuntimeError, 'owner conflict'):
            self.restore(a, da)
        self.restore(c, dc)
        self.assertEqual(engine.tree(self.target), engine.tree(self.root / 'runtime/workspace-switcher-rounded'))
        self.restore(b, db)
        self.restore(a, da)
        self.assertEqual(engine.tree_fingerprint(self.target), self.before)
        self.assertFalse((self.root / 'state' / runtime.POINTER).exists())

    def test_plan_does_not_write_or_reload(self):
        files = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob('*') if p.is_file()}
        runtime.plan('fixture', self.opted, self.root, self.home, engine)
        self.assertEqual(files, {str(p): p.read_bytes() for p in Path(self.temp.name).rglob('*') if p.is_file()})

    def test_nonopted_unowned_has_no_transition(self):
        self.assertEqual(runtime.plan('carbon', {}, self.root, self.home, engine), (None, None))

    def test_absent_and_foreign_baseline_refuse(self):
        (self.target / 'applet.js').write_text('foreign code')
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)
        shutil.rmtree(self.target)
        with self.assertRaisesRegex(RuntimeError, 'existing regular'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)

    def test_symlink_runtime_refuses(self):
        shutil.rmtree(self.target)
        self.target.symlink_to(self.root / 'runtime' / runtime.UUID)
        with self.assertRaisesRegex(RuntimeError, 'regular'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)

    def test_external_installed_edit_refuses_preserving_edit(self):
        self.apply()
        (self.target / 'applet.js').write_text('external edit')
        with self.assertRaisesRegex(RuntimeError, 'changed externally'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)
        self.assertEqual((self.target / 'applet.js').read_text(), 'external edit')

    def test_missing_or_metadata_changed_baseline_refuses(self):
        _, data = self.apply()
        source = Path(data['workspace_runtime']['baseline']['source'])
        (source / 'applet.js').chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'backup changed'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)
        shutil.rmtree(source)
        with self.assertRaisesRegex(RuntimeError, 'backup changed'):
            runtime.plan('fixture', self.opted, self.root, self.home, engine)

    def test_guard_required_before_pointer(self):
        path, data = self.prepare()
        data['guard_armed'] = False
        with self.assertRaisesRegex(RuntimeError, 'armed recovery'):
            runtime.begin(path, data, self.root, engine, engine.E.journal)
        self.assertFalse((self.root / 'state' / runtime.POINTER).exists())

    def test_failures_at_both_actual_rename_boundaries_restore(self):
        for fail_at in (1, 2):
            with self.subTest(rename=fail_at):
                path, data = self.prepare()
                runtime.begin(path, data, self.root, engine, engine.E.journal)
                actual = engine.move_tree
                calls = [0]
                def fail(source, target):
                    calls[0] += 1
                    if calls[0] == fail_at:
                        raise OSError('injected rename failure')
                    return actual(source, target)
                with patch.object(engine, 'move_tree', fail):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        engine.install_trees(path, data)
                self.restore(path, data)
                self.assertEqual(engine.tree_fingerprint(self.target), self.before)

    def test_pointer_before_install_interrupt_restores_without_reload(self):
        path, data = self.prepare()
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        self.restore(path, data)
        self.assertEqual(data['workspace_runtime']['reload']['status'], 'not-mutated')
        self.assertEqual(engine.tree_fingerprint(self.target), self.before)

    def test_keep_requires_native_settlement(self):
        path, data = self.prepare()
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        engine.install_trees(path, data)
        with self.assertRaisesRegex(RuntimeError, 'verified native reload'):
            runtime.finish(path, data, self.root, engine, 'kept', engine.E.journal)

    def test_native_wrong_path_or_old_instances_times_out(self):
        _, data = self.apply()
        ticks = iter([0, 0, 9])
        with self.assertRaisesRegex(RuntimeError, 'did not settle'):
            runtime.reload_checked(data, lambda *args: None, self.home,
                evaluator=lambda code: {'path': '/wrong', 'instances': []},
                snapshot=lambda home: {'expected_native_ids': [36]},
                monotonic=lambda: next(ticks), sleep=lambda _: None)

    def test_native_setting_drift_refuses(self):
        _, data = self.apply()
        samples = iter([{'expected_native_ids': [36]},
                        {'expected_native_ids': [36], 'changed': True}])
        with self.assertRaisesRegex(RuntimeError, 'changed workspace'):
            runtime.reload_checked(data, lambda *args: None, self.home,
                evaluator=lambda code: {'path': str(self.target), 'instances': [
                    {'id': 36, 'ready': True, 'recreated': True}]},
                snapshot=lambda home: next(samples))

    def test_missing_expected_native_identity_contract_refuses_before_reload(self):
        _, data = self.apply()
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'expected instance identities are absent'):
            runtime.reload_checked(data, lambda *args: calls.append(args), self.home,
                evaluator=lambda code: {'ready': True}, snapshot=lambda home: {})
        self.assertEqual(calls, [])

    def test_partial_or_duplicate_native_instance_sets_refuse(self):
        for observed in ([36], [36, 36]):
            with self.subTest(observed=observed):
                _, data = self.apply()
                ticks = iter([0, 0, 9])
                with self.assertRaisesRegex(RuntimeError, 'did not settle'):
                    runtime.reload_checked(data, lambda *args: None, self.home,
                        evaluator=lambda code: {'path': str(self.target), 'instances': [
                            {'id': ident, 'ready': True, 'recreated': True} for ident in observed]},
                        snapshot=lambda home: {'expected_native_ids': [36, 37]},
                        monotonic=lambda: next(ticks), sleep=lambda _: None)

    def test_missing_current_receipt_rejects_before_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'regular file'):
            runtime.require_receipt('fixture', self.opted, self.root, lambda *args: object())
        self.assertEqual(engine.tree_fingerprint(self.target), self.before)


if __name__ == '__main__':
    unittest.main()
