import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SPEC = importlib.util.spec_from_file_location('activation_guard', Path(__file__).with_name('guard.py'))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def receipt(self, preset='plum-afterglow', status='pending'):
        base = self.root / 'presets' / preset / 'state'
        p = base / 'test/desktop.json'
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'status': status}))
        (base / 'latest.json').write_text(json.dumps({'id': 'test'}))
        return p

    def test_nested_same_process_does_not_deadlock(self):
        with guard.locked(self.root):
            with guard.locked(self.root):
                self.assertEqual(guard._held[str(self.root)][1], 2)
        self.assertFalse(guard._held)

    def test_other_owner_blocked_and_owner_recovery_allowed(self):
        p = self.receipt()
        with self.assertRaises(RuntimeError):
            with guard.operation(self.root, 'quiet-sage', 'apply'): pass
        with guard.operation(self.root, 'plum-afterglow', 'recover', p): pass
        with self.assertRaises(RuntimeError):
            with guard.operation(self.root, 'plum-afterglow', 'restore', p.with_name('wrong.json')): pass

    def test_aggregate_failure_blocks_after_core_restored(self):
        p = self.receipt(status='restored')
        p.with_name('recovery.json').write_text('{"status":"recovery-required"}')
        with self.assertRaises(RuntimeError):
            with guard.operation(self.root, 'plum-afterglow', 'apply'): pass
        with guard.operation(self.root, 'plum-afterglow', 'recover', p): pass

    def test_supplement_marker_blocks_new_trial(self):
        p = self.receipt(status='kept')
        markers = self.root / 'state/activation-trials'
        markers.mkdir(parents=True)
        (markers / 'test.json').write_text(json.dumps({'preset':'plum-afterglow','receipt':str(p),'status':'pending'}))
        with self.assertRaises(RuntimeError):
            with guard.operation(self.root, 'plum-afterglow', 'refine', p): pass
        with guard.operation(self.root, 'plum-afterglow', 'rollback', p): pass

    def test_timer_waits_for_command_then_proceeds(self):
        code = "import sys; sys.path.insert(0,sys.argv[1]); import guard;\nwith guard.locked(sys.argv[2],wait=True): print('recovered',flush=True)"
        with guard.locked(self.root):
            child = subprocess.Popen([sys.executable, '-c', code, str(Path(__file__).parent), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(.08)
            self.assertIsNone(child.poll())
        stdout, stderr = child.communicate(timeout=3)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), 'recovered')

    def test_interactive_conflict_fails_without_waiting(self):
        code = "import sys; sys.path.insert(0,sys.argv[1]); import guard;\nwith guard.locked(sys.argv[2]): pass"
        with guard.locked(self.root):
            child = subprocess.run([sys.executable, '-c', code, str(Path(__file__).parent), str(self.root)], capture_output=True, text=True, timeout=3)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('Another Studio appearance command', child.stderr)


if __name__ == '__main__': unittest.main()
