import subprocess
import tempfile
import unittest
from pathlib import Path
from eww_reload import reload_checked


class Reload(unittest.TestCase):
    def run_sequence(self, sequence):
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv[-1])
            code, error = sequence.pop(0)
            return subprocess.CompletedProcess(argv, code, '', error)
        with tempfile.TemporaryDirectory() as temp:
            result = reload_checked('/tmp/eww', '/tmp/config', Path(temp)/'log', execute=execute, sleep=lambda seconds:None)
        return result, calls

    def test_lost_acknowledgement_retries_then_confirms_ready(self):
        result, calls = self.run_sequence([(1, 'Error reading response from server'), (0, ''), (0, '')])
        self.assertEqual(calls, ['reload', 'reload', 'active-windows'])
        self.assertEqual(len(result), 2)

    def test_invalid_configuration_is_not_hidden(self):
        with self.assertRaisesRegex(RuntimeError, 'Eww reload failed'):
            self.run_sequence([(1, 'Failed to parse stylesheet')])

    def test_unresponsive_daemon_after_reload_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'did not respond'):
            self.run_sequence([(0, ''), (1, 'connection refused')])

    def test_repeated_response_failure_stops(self):
        with self.assertRaisesRegex(RuntimeError, 'Eww reload failed'):
            self.run_sequence([(1, 'Error reading response from server'), (1, 'Error reading response from server')])

if __name__ == '__main__':unittest.main()
