#!/usr/bin/python3
"""Claude Code hooks for the coordinator session (wired in .claude/settings.json).

pre  (PreToolUse, Bash): before a live apply, run tools/preflight.py and deny on a blocking finding.
       presets/<id>/theme.sh apply       -> preflight <id> --stage user
       system/system.py <slug> apply --commit (inside presets/<id>/) -> preflight <id> --stage root
       theme <profile> / ~/.local/bin/theme <profile>  -> preflight <profile> --kind collection --stage user
     `keep`, `restore` and `theme restore` are never gated: a pending receipt is expected then, and
     blocking would push the 180 s keep window into an automatic rollback.
post (PostToolUse, Write|Edit|MultiEdit): after edits to collection profiles/contributions/generated
     trees, remind that catalog pins are stale.

Advisory for the coordinator: a preflight crash (exit 2) or unparsable input never blocks.
"""
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT = ROOT / 'tools' / 'preflight.py'
COLLECTION = ROOT / 'refinements' / 'cinnamon-current-collection'
PIN_TRIGGERS = ('profiles.json', 'contributions/', 'generated/')
NOT_PROFILES = {'restore', 'status', 'list', 'help', '--help', '-h', 'current', 'doctor'}


def preset_from(path: str, cwd: Path) -> str | None:
    p = (cwd / path).resolve() if not path.startswith('/') else Path(path).resolve()
    try:
        rel = p.relative_to(ROOT / 'presets')
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def targets(command: str, cwd: Path) -> list[tuple[str, list[str]]]:
    found = []
    for segment in re.split(r'&&|\|\||;|\|', command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        words = [w for w in words if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', w)]
        if not words:
            continue
        for i, w in enumerate(words):
            name = Path(w).name
            rest = words[i + 1:]
            if name == 'theme.sh' and rest[:1] == ['apply']:
                pid = preset_from(w, cwd)
                if pid:
                    found.append((pid, ['--stage', 'user']))
            elif name == 'system.py' and 'apply' in rest and '--commit' in rest:
                pid = preset_from(w, cwd)
                if pid:
                    found.append((pid, ['--stage', 'root']))
            elif i == 0 and (w == 'theme' or w.endswith('/.local/bin/theme')) and rest:
                if rest[0] not in NOT_PROFILES and not rest[0].startswith('-'):
                    found.append((rest[0], ['--kind', 'collection', '--stage', 'user']))
    return found


def pre(event: dict) -> int:
    command = (event.get('tool_input') or {}).get('command') or ''
    cwd = Path(event.get('cwd') or ROOT)
    for pid, args in targets(command, cwd):
        run = subprocess.run(['/usr/bin/python3', '-B', str(PREFLIGHT), pid, *args],
                             capture_output=True, text=True, timeout=60)
        if run.returncode == 1:
            reason = f'preflight blocked {pid} {" ".join(args)}:\n{run.stdout.strip()[-1500:]}'
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                                     'permissionDecision': 'deny',
                                                     'permissionDecisionReason': reason}}))
            return 0
        if run.returncode != 0:
            print(f'hook_coordinator: preflight exited {run.returncode} for {pid}; not blocking: '
                  f'{run.stderr.strip()[-300:]}', file=sys.stderr)
    return 0


def post(event: dict) -> int:
    tool_input = event.get('tool_input') or {}
    path = tool_input.get('file_path') or tool_input.get('notebook_path') or ''
    try:
        rel = Path(path).resolve().relative_to(COLLECTION).as_posix()
    except ValueError:
        return 0
    if rel == 'profiles.json' or rel.startswith(PIN_TRIGGERS[1:]):
        note = ('Collection sources changed: catalog pins are now stale. Before any republish run '
                'repairs/nim-nitch/catalog/build_theme_registry.py --check and refresh the pins.')
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                                                 'additionalContext': note}}))
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if mode == 'pre':
        return pre(event)
    if mode == 'post':
        return post(event)
    print('usage: hook_coordinator.py pre|post < hook-json', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
