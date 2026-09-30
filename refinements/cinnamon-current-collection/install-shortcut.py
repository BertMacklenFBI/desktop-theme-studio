#!/usr/bin/python3
"""Install only the user terminal entry point and its Bash completion link."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run this installer as the desktop user, without sudo.')
    links = {
        Path.home() / '.local/bin/theme': ROOT / 'shortcut.py',
        Path.home() / '.local/share/bash-completion/completions/theme':
            ROOT / 'staged/completions/theme.bash',
    }
    for destination, source in links.items():
        if not source.is_file():
            raise SystemExit('Missing shortcut source: ' + str(source))
        if os.path.lexists(destination) and not (
                destination.is_symlink() and destination.readlink() == source):
            raise SystemExit('Refusing to replace an existing file: ' + str(destination))
    (ROOT / 'shortcut.py').chmod(0o755)
    for destination, source in links.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not os.path.lexists(destination):
            destination.symlink_to(source)
    spec = importlib.util.spec_from_file_location('theme_shortcut_install', ROOT / 'shortcut.py')
    shortcut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shortcut)
    receipt = {
        'installed_at': shortcut.timestamp(),
        'links': {str(dest): {'target': str(src), 'sha256': hashlib.sha256(src.read_bytes()).hexdigest()}
                  for dest, src in links.items()},
        'theme_selected': False,
        'shell_startup_edited': False,
    }
    shortcut.write_json(ROOT / 'verification/shortcut-links.json', receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
