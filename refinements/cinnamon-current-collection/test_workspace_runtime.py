"""Real file/tree transaction fixtures; session boundaries are injected only."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
runtime = load('runtime_test_workspace', HERE / 'workspace_runtime.py')
live = load('runtime_test_live', HERE / 'live.py')
engine = live.base


class Transitions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'collection'
        self.home = Path(self.temp.name) / 'home'
        (self.root / 'state').mkdir(parents=True)
        for source in (runtime.UUID, 'workspace-switcher-rounded'):
            shutil.copytree(HERE / 'runtime' / source, self.root / 'runtime' / source)
        self.target = self.home / '.local/share/cinnamon/applets' / runtime.UUID
        shutil.copytree(self.root / 'runtime' / runtime.UUID, self.target)
        self.original = engine.tree_fingerprint(self.target)
        self.journal_patch = patch.object(engine, 'journal', engine.E.journal)
        self.journal_patch.start()
        self.addCleanup(self.journal_patch.stop)
        self.sequence = 0

    def plan(self, opted=True):
        self.sequence += 1
        path = self.root / 'state' / str(self.sequence) / 'receipt.json'
        path.parent.mkdir()
        section, row = runtime.plan('fixture', {'workspace_geometry': {'shape': 'circle'}} if opted else {},
                                    self.root, self.home, engine)
        data = {'id': str(self.sequence), 'status': 'applying', 'guard_armed': True,
                'actions': [], 'trees': [row], 'workspace_runtime': section}
        section['tree_index'] = 0
        engine.prepare_tree_record(row)
        engine.tree_paths(path, data)
        engine.E.journal(path, data)
        return path, data

    def reload(self, data, *, wrong=False, recreated=True, changed=False, absent=False):
        calls = []
        def evaluate(code):
            if '__dtsWorkspaceBefore=' in code:
                return {'ready': True}
            return {'path': 'foreign' if wrong else str(self.target),
                    'instances': [] if absent else [{'id': 902, 'ready': True, 'recreated': recreated}]}
        counter = [0]
        def snapshot(home):
            counter[0] += 1
            return {'settings': counter[0] if changed else 1, 'expected_native_ids': [] if absent else [902]}
        elapsed = iter([0, 0, 9])
        runtime.reload_checked(data, lambda *args: calls.append(args), self.home,
                               evaluator=evaluate, snapshot=snapshot,
                               monotonic=lambda: next(elapsed), sleep=lambda value: None)
        return calls

    def install(self, path, data):
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        engine.install_trees(path, data)
        self.reload(data)
        runtime.finish(path, data, self.root, engine, 'pending', engine.E.journal)
        data['status'] = 'pending'
        engine.E.journal(path, data)

    def keep(self, path, data):
        runtime.finish(path, data, self.root, engine, 'kept', engine.E.journal)
        data['status'] = 'kept'
        engine.E.journal(path, data)

    def restore(self, path, data):
        runtime.assert_owner(path, data, self.root, engine)
        engine.restore_trees(path, data)
        data['workspace_restoring'] = True
        self.reload(data)
        runtime.restore_owner(path, data, self.root, engine)
        data['status'] = 'restored'
        engine.E.journal(path, data)

    def test_first_entry_restore_exact_metadata_and_pointer(self):
        path, data = self.plan()
        self.install(path, data)
        self.assertEqual(runtime.owner(self.root)['phase'], 'pending')
        self.restore(path, data)
        self.assertEqual(engine.tree_fingerprint(self.target), self.original)
        self.assertIsNone(runtime.owner(self.root))

    def test_repeated_opted_and_leave_share_actual_original_backup(self):
        p1, d1 = self.plan(); self.install(p1, d1); self.keep(p1, d1)
        original_backup = copy.deepcopy(d1['workspace_runtime']['baseline'])
        p2, d2 = self.plan(); self.install(p2, d2); self.keep(p2, d2)
        self.assertEqual(d2['workspace_runtime']['baseline'], original_backup)
        p3, d3 = self.plan(False); self.install(p3, d3); self.keep(p3, d3)
        self.assertEqual(engine.tree_fingerprint(self.target), self.original)
        self.assertEqual(d3['workspace_runtime']['baseline'], original_backup)
        self.restore(p3, d3)
        self.assertEqual(runtime.owner(self.root)['receipt'], str(p2))
        self.restore(p2, d2)
        self.assertEqual(runtime.owner(self.root)['receipt'], str(p1))
        self.restore(p1, d1)
        self.assertEqual(engine.tree_fingerprint(self.target), self.original)

    def test_pending_owner_blocks_next_plan(self):
        path, data = self.plan(); self.install(path, data)
        with self.assertRaisesRegex(RuntimeError, 'prior workspace runtime trial'):
            self.plan()

    def test_old_receipt_cannot_restore_new_owner(self):
        p1, d1 = self.plan(); self.install(p1, d1); self.keep(p1, d1)
        p2, d2 = self.plan(); self.install(p2, d2)
        with self.assertRaisesRegex(RuntimeError, 'owner conflict'):
            runtime.assert_owner(p1, d1, self.root)
        self.assertEqual(runtime.owner(self.root)['receipt'], str(p2))

    def test_absent_and_foreign_runtime_refused_without_namespace_creation(self):
        shutil.rmtree(self.target)
        with self.assertRaisesRegex(RuntimeError, 'existing regular'):
            runtime.plan('fixture', {'workspace_geometry': {}}, self.root, self.home, engine)
        self.assertFalse(self.target.exists())
        shutil.copytree(self.root / 'runtime' / runtime.UUID, self.target)
        (self.target / 'applet.js').write_text('foreign runtime')
        with self.assertRaisesRegex(RuntimeError, 'Unowned workspace'):
            runtime.plan('fixture', {'workspace_geometry': {}}, self.root, self.home, engine)
        self.assertEqual((self.target / 'applet.js').read_text(), 'foreign runtime')

    def test_symlink_target_refused(self):
        moved = self.target.with_name('other')
        self.target.rename(moved); self.target.symlink_to(moved)
        with self.assertRaisesRegex(RuntimeError, 'existing regular'):
            self.plan()

    def test_external_owned_edit_blocks_next_plan(self):
        path, data = self.plan(); self.install(path, data); self.keep(path, data)
        (self.target / 'applet.js').write_text('external')
        with self.assertRaisesRegex(RuntimeError, 'changed externally'):
            self.plan()

    def test_corrupt_baseline_blocks_next_plan(self):
        path, data = self.plan(); self.install(path, data); self.keep(path, data)
        source = Path(data['workspace_runtime']['baseline']['source'])
        (source / 'applet.js').write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'baseline backup'):
            self.plan()

    def test_no_write_without_armed_guard(self):
        path, data = self.plan(); data['guard_armed'] = False
        with self.assertRaisesRegex(RuntimeError, 'armed recovery'):
            runtime.begin(path, data, self.root, engine, engine.E.journal)
        self.assertEqual(engine.tree_fingerprint(self.target), self.original)
        self.assertIsNone(runtime.owner(self.root))

    def test_wrong_source_or_stale_actor_fails_and_removes_prior_reload_pass(self):
        for options in ({'wrong': True}, {'recreated': False}):
            with self.subTest(options=options):
                path, data = self.plan()
                runtime.begin(path, data, self.root, engine, engine.E.journal)
                engine.install_trees(path, data)
                data['workspace_runtime']['reload'] = {'status': 'passed'}
                with self.assertRaisesRegex(RuntimeError, 'did not settle'):
                    self.reload(data, **options)
                self.assertNotIn('reload', data['workspace_runtime'])
                with self.assertRaisesRegex(RuntimeError, 'verified native reload'):
                    runtime.finish(path, data, self.root, engine, 'kept', engine.E.journal)
                self.restore(path, data)

    def test_reload_spice_settings_change_refused(self):
        path, data = self.plan()
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        engine.install_trees(path, data)
        with self.assertRaisesRegex(RuntimeError, 'spice files/settings'):
            self.reload(data, changed=True)
        self.restore(path, data)

    def test_intentionally_absent_native_layout_is_verified_explicitly(self):
        path, data = self.plan()
        runtime.begin(path, data, self.root, engine, engine.E.journal)
        engine.install_trees(path, data)
        self.reload(data, absent=True)
        self.assertEqual(data['workspace_runtime']['reload']['activation'], 'intentionally-absent')
        self.assertEqual(data['workspace_runtime']['reload']['expected_native_ids'], [])
        runtime.finish(path, data, self.root, engine, 'pending', engine.E.journal)
        self.restore(path, data)

    def test_interrupted_tree_install_recovers_before_and_after_each_rename(self):
        real_move = engine.move_tree
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                path, data = self.plan()
                runtime.begin(path, data, self.root, engine, engine.E.journal)
                counter = [0]
                def failing_move(source, target):
                    counter[0] += 1
                    real_move(source, target)
                    if counter[0] == fail_at:
                        raise OSError('injected interruption')
                with patch.object(engine, 'move_tree', failing_move):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        engine.install_trees(path, data)
                self.restore(path, data)
                self.assertEqual(engine.tree_fingerprint(self.target), self.original)

    def test_pointer_write_failure_before_tree_mutation_restores_safely(self):
        path, data = self.plan()
        original_journal = engine.E.journal
        def fail_pointer(target, value):
            if Path(target).name == runtime.POINTER:
                raise OSError('pointer write')
            return original_journal(target, value)
        with patch.object(engine.E, 'journal', fail_pointer):
            with self.assertRaisesRegex(OSError, 'pointer write'):
                runtime.begin(path, data, self.root, engine, original_journal)
        self.restore(path, data)
        self.assertEqual(engine.tree_fingerprint(self.target), self.original)

    def test_pointer_release_crash_recovery_first_and_previous_owner(self):
        for previous in (False, True):
            with self.subTest(previous=previous):
                prior = None
                if previous:
                    prior = self.plan(); self.install(*prior); self.keep(*prior)
                path, data = self.plan(); self.install(path, data)
                engine.restore_trees(path, data)
                data['workspace_restoring'] = True
                self.reload(data)
                real_journal = engine.E.journal
                def fail_after_pointer(target, value):
                    real_journal(target, value)
                    if Path(target).name == runtime.POINTER:
                        raise OSError('crash after owner handoff')
                boundary = (patch.object(engine.E, 'journal', fail_after_pointer) if previous else
                            patch.object(engine, 'sync_directory', side_effect=OSError('crash after owner handoff')))
                with boundary:
                    with self.assertRaisesRegex(OSError, 'crash after owner handoff'):
                        runtime.restore_owner(path, data, self.root, engine)
                restarted = json.loads(path.read_text())
                self.assertEqual(restarted['workspace_runtime']['phase'], 'releasing')
                self.restore(path, restarted)
                if prior:
                    self.restore(*prior)
                self.assertIsNone(runtime.owner(self.root))
                self.assertEqual(engine.tree_fingerprint(self.target), self.original)


if __name__ == '__main__':
    unittest.main()
