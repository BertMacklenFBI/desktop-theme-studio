#!/usr/bin/python3
"""Scaffold presets/<id>/ with the lineage preset's *code* only.

The lineage is named in presets/_template/LINEAGE. Copies *.py / *.sh / README.md plus the
desktop/base/ snapshot. Never copies state, receipts, verification, generated or rendered trees,
logs, previews, artwork images, design.json, BRIEF.md or identity/ (those belong to the new preset
or to its designer). Existing files in the target are never overwritten.

Phase 6 lineage library (repairs/lineage-lib-20260923, repairs/lineage-lib-b-20260923): a lineage
file that is a *lineage shim* (it only loads lib/lineage/<module>) is copied byte for byte and
reported as a shim. Shims find the shared library from their own path, so the copy works as is and
must not be edited. When the lineage has a presets/<lineage>/lineage.json (the per-preset values the
shared modules read), the new preset gets its own lineage.json: the lineage's file with the identity
values replaced (name = presets/<id>/design.json "name", slug = <id>, temp_tag = <id> with '_' for
'-'). Other values (for example a panel layout) are inherited and listed so the builder can revisit
them. That needs presets/<id>/design.json to exist first (the designer writes it before scaffolding).
Refused: a design.json name that another preset already uses (both would install ~/.themes/<Name>).
lineage.json is never written into an existing preset that keeps its own full copies of the files the
lineage has as shims (for example macintosh-soft); red-panda-overtime* stays refused as before.

Usage: scaffold_preset.py <id> [--write]      (default is a dry run)
Exit codes: 0 ok, 1 nothing to do / conflicts, 2 invalid request.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / 'presets'
CODE_SUFFIXES = {'.py', '.sh'}
KEEP_NAMES = {'README.md'}
COPY_TREES = ('desktop/base',)          # upstream snapshot the desktop builder patches
NEVER_DIRS = {'state', 'verification', '__pycache__', '.build', 'generated', 'rendered',
              'identity', 'backups'}
RESERVED = {'_template', 'example', 'current-observed'}
SHIM_MARK = '"""Lineage shim ('
SHIM_LIB_RE = re.compile(r"^exec\(_lineage_code\('([A-Za-z0-9_]+\.py)'\)\)$", re.M)
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9 -]{0,63}')
SLUG_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
IDENTITY = ('name', 'slug', 'temp_tag')


def lineage() -> str:
    name = (PRESETS / '_template' / 'LINEAGE').read_text().split()[0]
    if not (PRESETS / name).is_dir():
        raise SystemExit(f'LINEAGE names a missing preset: {name}')
    return name


def plan(src: Path) -> list[Path]:
    picked = []
    for path in sorted(src.rglob('*')):
        rel = path.relative_to(src)
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in NEVER_DIRS for part in rel.parts):
            continue
        in_tree = any(rel.as_posix().startswith(t + '/') for t in COPY_TREES)
        if in_tree or path.suffix in CODE_SUFFIXES or (path.name in KEEP_NAMES and len(rel.parts) > 1):
            picked.append(rel)
    return picked


def shim_module(path: Path):
    """lib/lineage module name when `path` is a lineage shim, else None."""
    if path.suffix != '.py':
        return None
    head = path.read_bytes()[:4096].decode('utf-8', 'replace')
    lines = head.split('\n')
    if len(lines) < 2 or not lines[1].startswith(SHIM_MARK):
        return None
    found = SHIM_LIB_RE.findall(path.read_text())
    return found[0] if len(found) == 1 else None


def lineage_values(src: Path, dst: Path, preset_id: str):
    """(bytes, inherited keys) for the new presets/<id>/lineage.json, or (None, None) if the lineage has none.

    Raises ValueError when the new preset's identity cannot be derived safely."""
    base = src / 'lineage.json'
    if not base.is_file():
        return None, None
    doc = json.loads(base.read_text())
    section = doc.get('desktop_control') if isinstance(doc, dict) else None
    if doc.get('schema') != 1 or not isinstance(section, dict) or not set(IDENTITY) <= set(section):
        raise ValueError(f'{base} is not a schema-1 lineage.json with a desktop_control identity')
    design = dst / 'design.json'
    if not design.is_file():
        raise ValueError(f'{design} is required first: lineage.json takes the exact theme name from it')
    name = json.loads(design.read_text()).get('name')
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError(f'design.json name {name!r} is not a safe visible theme name')
    if not SLUG_RE.fullmatch(preset_id):
        raise ValueError(f'preset id {preset_id!r} is not a lowercase slug')
    values = dict(section, name=name, slug=preset_id, temp_tag=preset_id.replace('-', '_'))
    doc = dict(doc, desktop_control=values)
    return (json.dumps(doc, indent=2) + '\n').encode(), sorted(k for k in values if k not in IDENTITY)


def name_clash(dst: Path):
    """The other preset whose design.json has the same visible name as dst's, if any."""
    design = dst / 'design.json'
    if not design.is_file():
        return None
    try:
        name = json.loads(design.read_text()).get('name')
    except (ValueError, AttributeError):
        return None  # lineage_values() refuses an unreadable name
    for other in sorted(PRESETS.iterdir()):
        if other == dst or other.name in RESERVED or not (other / 'design.json').is_file():
            continue
        try:
            if json.loads((other / 'design.json').read_text()).get('name') == name:
                return other.name
        except (ValueError, AttributeError):
            continue
    return None


def keeps_own_copies(src: Path, dst: Path, files) -> list[str]:
    """Files the lineage has as shims but dst already has as full (non-shim) copies."""
    return [rel.as_posix() for rel in files if shim_module(src / rel) and (dst / rel).is_file() and not shim_module(dst / rel)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('id')
    ap.add_argument('--write', action='store_true', help='copy files (default: dry run)')
    args = ap.parse_args(argv)
    # red-panda-overtime* is pinned by source_fingerprint in the collection's profiles.json.
    if args.id in RESERVED or args.id.startswith('red-panda-overtime') or '/' in args.id:
        print(f'refusing reserved or invalid id: {args.id}', file=sys.stderr)
        return 2
    src = PRESETS / lineage()
    dst = PRESETS / args.id
    if src == dst:
        print('target is the lineage itself', file=sys.stderr)
        return 2
    clash = name_clash(dst)
    if clash:
        print(f'refusing: design.json name is already used by presets/{clash} (both would install ~/.themes/<Name>)',
              file=sys.stderr)
        return 2
    files = plan(src)
    target_lineage = dst / 'lineage.json'
    layout, inherited = None, None
    own = keeps_own_copies(src, dst, files)
    if own:
        print('not writing lineage.json: this preset keeps its own copies of ' + ', '.join(own))
    elif not target_lineage.exists():
        try:
            layout, inherited = lineage_values(src, dst, args.id)
        except ValueError as error:
            print(f'refusing: {error}', file=sys.stderr)
            return 2
    new = [r for r in files if not (dst / r).exists()]
    skipped = [r for r in files if (dst / r).exists()]
    shims = []
    for rel in new:
        module = shim_module(src / rel)
        note = f' (lineage shim for lib/lineage/{module}; copy as is, do not edit)' if module else ''
        if module:
            shims.append(rel.as_posix())
        print(('copy ' if args.write else 'would copy ') + rel.as_posix() + note)
        if args.write:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst / rel)
    for rel in skipped:
        print('exists, kept ' + rel.as_posix())
    if layout is not None:
        print(('write ' if args.write else 'would write ') + 'lineage.json (identity from design.json'
              + (', inherited from ' + src.name + ': ' + ', '.join(inherited) if inherited else '') + ')')
        if args.write:
            dst.mkdir(parents=True, exist_ok=True)
            with open(target_lineage, 'xb') as handle:  # never overwrite
                handle.write(layout)
    elif target_lineage.exists():
        print('exists, kept lineage.json')
    print(f'lineage={src.name} target={dst.name} new={len(new)} kept={len(skipped)} shims={len(shims)}'
          + (' lineage.json=new' if layout is not None else '')
          + ('' if args.write else ' (dry run; pass --write)'))
    if args.write and (new or layout is not None):
        print('Next: the builders replace hard-coded lineage names/paths in the copied code. '
              'Lineage shims are shared code: leave them unchanged and adjust presets/<id>/lineage.json instead.')
    return 0 if new or layout is not None else 1


if __name__ == '__main__':
    sys.exit(main())
