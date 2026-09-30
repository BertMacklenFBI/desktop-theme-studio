"""Refresh only the invoking Konsole tab's colors without touching its shell."""
import configparser
import os
from pathlib import Path
import re
import subprocess
import sys


def refresh(home=None, environ=None, stream=None):
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    stream = sys.stderr if stream is None else stream
    if not environ.get('KONSOLE_VERSION') or not stream.isatty():
        return {'status': 'not-applicable'}
    try:
        settings = configparser.ConfigParser(interpolation=None, strict=False)
        settings.read(home / '.config/konsolerc', encoding='utf-8')
        name = settings.get('Desktop Entry', 'DefaultProfile', fallback='')
        if not name or Path(name).name != name or not name.endswith('.profile'):
            return {'status': 'deferred', 'reason': 'Konsole default profile is unavailable'}
        profile = configparser.ConfigParser(interpolation=None, strict=False)
        profile.read(home / '.local/share/konsole' / name, encoding='utf-8')
        scheme = profile.get('Appearance', 'ColorScheme', fallback='')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', scheme):
            return {'status': 'deferred', 'reason': 'Konsole color scheme is unavailable or unsupported'}
        subprocess.run(['/usr/bin/konsoleprofile', 'ColorScheme=' + scheme],
                       stdout=stream, stderr=subprocess.PIPE, text=True,
                       timeout=3, check=True)
        return {'status': 'updated', 'scheme': scheme, 'scope': 'current-Konsole-tab'}
    except (OSError, subprocess.SubprocessError, configparser.Error) as error:
        return {'status': 'deferred', 'reason': str(error)}
