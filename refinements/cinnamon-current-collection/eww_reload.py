"""Handle the installed Eww build's 100ms IPC acknowledgement race."""
from pathlib import Path
import subprocess
import time


def reload_checked(binary, config, log_path, *, execute=None, sleep=time.sleep):
    execute = execute or subprocess.run
    log_path = Path(log_path)
    attempts = []
    command = [str(binary), '-c', str(config), '--no-daemonize']
    for index in range(2):
        offset = log_path.stat().st_size if log_path.exists() else 0
        result = execute([*command, 'reload'], text=True, capture_output=True, timeout=15)
        record = {'attempt': index + 1, 'returncode': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr}
        attempts.append(record)
        if result.returncode == 0:
            # This synchronous read is processed after the reload in Eww's
            # application queue. Never restart or kill a functioning daemon.
            ready = execute([*command, 'active-windows'], text=True, capture_output=True, timeout=10)
            record['readiness'] = {'returncode': ready.returncode, 'stdout': ready.stdout, 'stderr': ready.stderr}
            if ready.returncode:
                raise RuntimeError('Eww did not respond after reload: '+str(attempts))
            return attempts
        sleep(0.2)
        fresh = ''
        if log_path.exists():
            with log_path.open('rb') as handle:
                handle.seek(offset)
                fresh = handle.read().decode(errors='replace')
        record['fresh_log'] = fresh[-4000:]
        response = result.stdout + result.stderr
        known_race = ('Error reading response from server' in response or
                      'channel closed' in response or 'channel closed' in fresh)
        config_error = any(word in response.lower() for word in
                           ('parse error', 'error parsing', 'invalid css', 'failed to parse', 'syntax error'))
        if index or not known_race or config_error:
            raise RuntimeError('Eww reload failed: '+str(attempts))
        sleep(0.8)
    raise AssertionError('unreachable')
