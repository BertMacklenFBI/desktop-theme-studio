#!/usr/bin/env python3
"""Static self-check for the GNU-Darwin Workstation Plymouth script.

Plymouth's script language is C-like and has no offline interpreter, so this
checks what can be checked without plymouthd:

  * braces, parentheses and brackets balance (outside strings/comments),
  * every `fun` opens a body and every body closes,
  * `return` only appears inside a function body,
  * no trailing comma before `)` or `}`,
  * every image filename referenced via Image("...") or stem + "-cap-l.png" etc.
    exists next to the script (wallpaper.png / logo.png are installer-provided),
  * every Plymouth.Set*Function name is one the installed script.so exports,
  * no unresolved @token@ in the rendered script, and the template only uses
    tokens the installer knows (@<palette>_rgb@, @theme_name@, @slug@).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESET = HERE.parent.parent
SLUG = json.loads((PRESET / "design.json").read_text())["id"]
PALETTE = json.loads((PRESET / "design.json").read_text())["palette"]
INSTALLER_PROVIDED = {"wallpaper.png", "logo.png"}


def strip(text: str) -> str:
    """Remove comments and string bodies (keeping quotes) so punctuation counts are clean."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append('""')
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check(path: Path, template: bool) -> list[str]:
    errors: list[str] = []
    raw = path.read_text()
    code = strip(raw)

    depth = {"{": 0, "(": 0, "[": 0}
    pairs = {"}": "{", ")": "(", "]": "["}
    fun_depths: list[int] = []
    line = 1
    pending_fun = False
    tokens = re.finditer(r"\bfun\b|\breturn\b|[{}()\[\]]|\n", code)
    for m in tokens:
        t = m.group(0)
        if t == "\n":
            line += 1
        elif t == "fun":
            pending_fun = True
        elif t in depth:
            depth[t] += 1
            if t == "{" and pending_fun:
                fun_depths.append(depth["{"])
                pending_fun = False
        elif t in pairs:
            depth[pairs[t]] -= 1
            if depth[pairs[t]] < 0:
                errors.append(f"{path.name}:{line}: unmatched '{t}'")
                depth[pairs[t]] = 0
            if t == "}" and fun_depths and depth["{"] < fun_depths[-1]:
                fun_depths.pop()
        elif t == "return" and not fun_depths:
            errors.append(f"{path.name}:{line}: 'return' outside a function")
    for opener, d in depth.items():
        if d != 0:
            errors.append(f"{path.name}: unbalanced '{opener}' (depth {d} at end of file)")
    if pending_fun:
        errors.append(f"{path.name}: 'fun' without a body")
    if re.search(r",\s*[)}]", code):
        errors.append(f"{path.name}: trailing comma before ')' or '}}'")

    # Every referenced pixmap must exist next to the script.
    referenced = set(re.findall(r'Image\("([^"]+)"\)', raw))
    stems = set(re.findall(r'band_new\("([^"]+)"', raw))
    for stem in stems:
        referenced.update({f"{stem}.png", f"{stem}-cap-l.png", f"{stem}-cap-r.png"})
    for name in sorted(referenced):
        if name in INSTALLER_PROVIDED:
            continue
        if not (HERE / name).is_file():
            errors.append(f"{path.name}: references missing pixmap {name}")

    # Callback registrations must be names script.so knows.
    exported = set()
    for so in Path("/usr/lib").glob("*/plymouth/script.so"):
        out = subprocess.run(["strings", str(so)], capture_output=True, text=True).stdout
        exported.update(re.findall(r"\bSet[A-Za-z]+Function\b", out))
    if exported:
        for name in set(re.findall(r"Plymouth\.(Set[A-Za-z]+Function)", raw)):
            if name not in exported:
                errors.append(f"{path.name}: Plymouth.{name} is not exported by script.so")

    # Template tokens.
    allowed = {f"{k}_rgb" for k in PALETTE} | {"theme_name", "slug"}
    for token in set(re.findall(r"@([a-z][a-z_]*)@", raw)):
        if template and token not in allowed:
            errors.append(f"{path.name}: unknown template token @{token}@")
        if not template:
            errors.append(f"{path.name}: unresolved token @{token}@ in rendered script")
    if "// @OPTIONAL_LOGO@" not in raw:
        errors.append(f"{path.name}: missing '// @OPTIONAL_LOGO@' marker line")
    return errors


def main() -> int:
    problems = check(HERE / f"{SLUG}.script.in", template=True)
    rendered = HERE / f"{SLUG}.script"
    if rendered.is_file():
        problems += check(rendered, template=False)
    else:
        problems.append(f"{rendered.name} missing: run generate.py")
    for p in problems:
        print("FAIL", p)
    if not problems:
        print("ok: braces/parens balanced, fun bodies closed, no stray return, pixmaps present, callbacks exported, tokens resolved")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
