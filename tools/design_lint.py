#!/usr/bin/python3 -B
"""Lint a Desktop Theme Studio preset design.json.

Usage:
    tools/design_lint.py <id-or-path> [--write] [--json]
                         [--root DIR] [--themes-dir DIR]

<id-or-path> is a preset id (resolved to presets/<id>/design.json under the
studio root, the parent of tools/), a preset directory, or a design.json path.

Checks: schema (tools/design.schema.json), required palette tokens (read from
presets/_template/design.json at run time), leftover TODO strings, ANSI and
rainbow colours, installed fonts (fc-list), WCAG 2.x contrast, and an accent
hue census against the other presets and ~/Documents/themes.

Exit status: 0 no errors (warnings allowed), 1 lint errors, 2 bad invocation
or unreadable file. Nothing is written unless --write is given, in which case
the result goes to <preset>/identity/contrast.json.

Standard library only. Read-only apart from --write.
"""

import argparse
import colorsys
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(TOOLS_DIR)
SCHEMA_PATH = os.path.join(TOOLS_DIR, "design.schema.json")
DEFAULT_THEMES_DIR = os.path.expanduser("~/Documents/themes")

# Missing any of these is an error; other template tokens missing is a warning.
CORE_TOKENS = (
    "foreground", "background", "surface", "elevated", "muted", "accent",
    "selection", "selection_foreground", "focus", "control_border",
)
SURFACES = ("background", "surface", "elevated")
TEXT_MIN = 4.5
NONTEXT_MIN = 3.0
HUE_MIN_DEG = 20.0
ACHROMATIC_SAT = 0.08  # HSL saturation below this has no meaningful hue
CENSUS_SKIP = {"_template", "example", "current-observed"}
CENSUS_SKIP_SUBSTR = ("-review-", "-preflight-")
THEMES_MAX_BYTES = 1_000_000
THEMES_SKIP_DIRS = {"icons", "cursors", "cursors_scalable", "__pycache__", ".git"}
HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
TYPO_FAMILIES = ("ui_family", "title_family", "clock_family", "monospace_family")


class InvocationError(Exception):
    """Bad arguments or unreadable input: exit 2."""


# --------------------------------------------------------------------------
# Colour maths
# --------------------------------------------------------------------------

def is_hex(value):
    return isinstance(value, str) and bool(HEX_RE.match(value))


def hex_to_rgb(value):
    h = value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def norm_hex(value):
    return "#" + value.lstrip("#").upper()


def relative_luminance(value):
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def hue_sat(value):
    r, g, b = (c / 255.0 for c in hex_to_rgb(value))
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0, s


def hue_distance(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# --------------------------------------------------------------------------
# Minimal JSON-schema subset validator
# --------------------------------------------------------------------------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve_ref(ref, root_schema):
    if not ref.startswith("#/"):
        raise ValueError("only local $ref supported: %s" % ref)
    node = root_schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(value, schema, root_schema=None, path="$"):
    """Return a list of (path, message) for every violation."""
    root_schema = root_schema if root_schema is not None else schema
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], root_schema)
    errs = []
    want = schema.get("type")
    if want is not None:
        types = want if isinstance(want, list) else [want]
        if not any(_TYPES[t](value) for t in types):
            errs.append((path, "expected %s, got %s" % ("/".join(types), _jtype(value))))
            return errs
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append((path, "missing required key '%s'" % key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key, sub in value.items():
            if key in props:
                errs.extend(validate(sub, props[key], root_schema, "%s.%s" % (path, key)))
            elif isinstance(extra, dict):
                errs.extend(validate(sub, extra, root_schema, "%s.%s" % (path, key)))
            elif extra is False:
                errs.append(("%s.%s" % (path, key), "unexpected key"))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append((path, "has %d items, needs at least %d" % (len(value), schema["minItems"])))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append((path, "has %d items, allows at most %d" % (len(value), schema["maxItems"])))
        if "items" in schema:
            for i, item in enumerate(value):
                errs.extend(validate(item, schema["items"], root_schema, "%s[%d]" % (path, i)))
    if isinstance(value, str) and "pattern" in schema:
        if not re.search(schema["pattern"], value):
            errs.append((path, "%r does not match %s" % (_short(value), schema["pattern"])))
    if _TYPES["number"](value) and "minimum" in schema and value < schema["minimum"]:
        errs.append((path, "%r is below minimum %r" % (value, schema["minimum"])))
    return errs


def _jtype(v):
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](v):
            return name
    return type(v).__name__


def _short(s, n=40):
    return s if len(s) <= n else s[:n - 1] + "…"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_target(arg, root):
    """Return (design_path, preset_dir, preset_id)."""
    if not arg or not arg.strip():
        raise InvocationError("empty preset id or path")
    if os.path.isfile(arg):
        path = os.path.abspath(arg)
    elif os.path.isdir(arg):
        path = os.path.join(os.path.abspath(arg), "design.json")
    elif "/" in arg or arg.endswith(".json"):
        raise InvocationError("no such file: %s" % arg)
    else:
        if arg in (".", "..") or arg.startswith("."):
            raise InvocationError("invalid preset id: %s" % arg)
        path = os.path.join(root, "presets", arg, "design.json")
    if not os.path.isfile(path):
        raise InvocationError("design.json not found: %s" % path)
    preset_dir = os.path.dirname(path)
    return path, preset_dir, os.path.basename(preset_dir)


def palette_of(design):
    """Return (palette_dict, source_key). Falls back to legacy 'tokens'."""
    pal = design.get("palette")
    if isinstance(pal, dict):
        return pal, "palette"
    tok = design.get("tokens")
    if isinstance(tok, dict):
        return tok, "tokens"
    return {}, None


def walk_strings(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield "%s.<key>" % path, k
            for item in walk_strings(v, "%s.%s" % (path, k)):
                yield item
    elif isinstance(node, list):
        for i, v in enumerate(node):
            for item in walk_strings(v, "%s[%d]" % (path, i)):
                yield item
    elif isinstance(node, str):
        yield path, node


def installed_families():
    """Return a set of lower-cased family names, or None if fc-list is missing."""
    exe = shutil.which("fc-list")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, ":", "family"], capture_output=True, text=True,
                             timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fams = set()
    for line in out.splitlines():
        for name in line.split(","):
            name = name.replace("\\", "").strip()
            if name:
                fams.add(name.lower())
    return fams


def _json_accent(data):
    """Find an accent hex in a design/palette json; return (hex, key) or None."""
    if not isinstance(data, dict):
        return None
    for key in ("palette", "tokens"):
        sub = data.get(key)
        if isinstance(sub, dict) and is_hex(sub.get("accent")):
            return sub["accent"], "%s.accent" % key
    if is_hex(data.get("accent")):
        return data["accent"], "accent"
    return None


def census_sources(root, themes_dir, exclude_path):
    """Yield (label, accent_hex) from other presets and ~/Documents/themes."""
    found = []
    presets = os.path.join(root, "presets")
    excl = os.path.realpath(exclude_path) if exclude_path else None
    if os.path.isdir(presets):
        for name in sorted(os.listdir(presets)):
            if name in CENSUS_SKIP or any(s in name for s in CENSUS_SKIP_SUBSTR):
                continue
            path = os.path.join(presets, name, "design.json")
            if not os.path.isfile(path) or os.path.realpath(path) == excl:
                continue
            try:
                hit = _json_accent(load_json(path))
            except (OSError, ValueError):
                continue
            if hit:
                found.append(("presets/%s" % name, hit[0]))
    if themes_dir and os.path.isdir(themes_dir):
        for theme in sorted(os.listdir(themes_dir)):
            tdir = os.path.join(themes_dir, theme)
            if not os.path.isdir(tdir) or theme.startswith("_"):
                continue
            for dirpath, dirnames, filenames in os.walk(tdir):
                dirnames[:] = sorted(d for d in dirnames if d not in THEMES_SKIP_DIRS)
                for fn in sorted(filenames):
                    if not fn.endswith(".json"):
                        continue
                    if fn != "design.json" and "palette" not in fn and "theme" not in fn:
                        continue
                    path = os.path.join(dirpath, fn)
                    try:
                        if os.path.getsize(path) > THEMES_MAX_BYTES:
                            continue
                        hit = _json_accent(load_json(path))
                    except (OSError, ValueError):
                        continue
                    if hit:
                        rel = os.path.relpath(path, themes_dir)
                        found.append(("themes/%s" % rel, hit[0]))
    return found


# --------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------

def lint(design_path, root=DEFAULT_ROOT, themes_dir=DEFAULT_THEMES_DIR,
         families="auto", schema_path=SCHEMA_PATH):
    """Lint one design.json. Returns a result dict.

    families: "auto" runs fc-list; None simulates fc-list missing; a set of
    names (any case) is used directly (tests).
    Raises InvocationError if the file or schema is unreadable.
    """
    try:
        with open(design_path, "rb") as fh:
            raw = fh.read()
        design = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InvocationError("cannot read %s: %s" % (design_path, exc))
    if not isinstance(design, dict):
        raise InvocationError("%s: top level is not a JSON object" % design_path)
    try:
        schema = load_json(schema_path)
    except (OSError, ValueError) as exc:
        raise InvocationError("cannot read schema %s: %s" % (schema_path, exc))

    findings = []

    def add(severity, check, message, path=None):
        findings.append({"severity": severity, "check": check,
                         "path": path, "message": message})

    # ---- schema --------------------------------------------------------
    for path, msg in validate(design, schema):
        top = path[2:].split(".")[0].split("[")[0] if path.startswith("$.") else ""
        check = {"terminal_ansi": "ansi", "rainbow": "rainbow", "palette": "palette",
                 "typography": "fonts"}.get(top, "schema")
        if path == "$" and "'" in msg:
            key = msg.split("'")[1]
            check = {"terminal_ansi": "ansi", "rainbow": "rainbow",
                     "palette": "palette", "typography": "fonts"}.get(key, "schema")
        add("error", check, msg, path)

    palette, pal_src = palette_of(design)
    if pal_src == "tokens":
        add("warning", "palette",
            "legacy schema: no 'palette'; using 'tokens' for contrast and census", "$.tokens")

    # ---- required palette tokens (from template) -----------------------
    template_path = os.path.join(root, "presets", "_template", "design.json")
    required = list(CORE_TOKENS)
    try:
        tpl = load_json(template_path)
        tpl_pal = tpl.get("palette") if isinstance(tpl, dict) else None
        if isinstance(tpl_pal, dict) and tpl_pal:
            required = list(tpl_pal.keys())
            for t in CORE_TOKENS:
                if t not in required:
                    required.append(t)
        else:
            add("warning", "palette", "template has no palette; using core token list",
                template_path)
    except (OSError, ValueError) as exc:
        add("warning", "palette", "template unreadable (%s); using core token list" % exc,
            template_path)
    missing_core, missing_other = [], []
    for tok in required:
        if tok not in palette:
            (missing_core if tok in CORE_TOKENS else missing_other).append(tok)
    if missing_core:
        add("error", "palette", "missing core tokens: %s" % ", ".join(missing_core),
            "$.palette")
    if missing_other:
        add("warning", "palette",
            "missing template tokens: %s" % ", ".join(missing_other), "$.palette")
    if pal_src == "tokens":
        for tok, val in palette.items():
            if not is_hex(val):
                add("error", "palette", "%r is not a hex colour" % _short(str(val)),
                    "$.tokens.%s" % tok)

    # ---- TODO ----------------------------------------------------------
    todo = [(p, s) for p, s in walk_strings(design) if "TODO" in s]
    for p, s in todo:
        add("error", "todo", "contains TODO: %r" % _short(s), p)

    # ---- fonts ---------------------------------------------------------
    typo = design.get("typography") if isinstance(design.get("typography"), dict) else {}
    fams = installed_families() if families == "auto" else families
    fonts = []
    wanted = [(k, typo[k]) for k in TYPO_FAMILIES if isinstance(typo.get(k), str)]
    if wanted and fams is None:
        add("warning", "fonts", "fc-list not available; font check skipped")
    elif fams is not None:
        lower = {f.lower() for f in fams}
        for key, fam in wanted:
            if "TODO" in fam:
                continue  # already an error
            ok = fam.strip().lower() in lower
            fonts.append({"key": key, "family": fam, "installed": ok})
            if not ok:
                add("error", "fonts", "font family %r not in fc-list output" % fam,
                    "$.typography.%s" % key)

    # ---- contrast ------------------------------------------------------
    rules = design.get("semantic_rules") if isinstance(design.get("semantic_rules"), dict) else {}
    accent_rule = str(rules.get("small_accent_text", ""))
    accent_fill_only = "fill-only" in accent_rule.lower()
    pairs = []
    for fg in ("foreground", "muted"):
        for bg in SURFACES:
            pairs.append((fg, bg, TEXT_MIN, "error"))
    pairs.append(("selection_foreground", "selection", TEXT_MIN, "error"))
    pairs.append(("terminal_foreground", "terminal", TEXT_MIN, "error"))
    for fg in ("control_border", "focus", "disabled"):
        for bg in SURFACES:
            pairs.append((fg, bg, NONTEXT_MIN, "error"))
    pairs.append(("accent", "background", TEXT_MIN, "info" if accent_fill_only else "error"))

    contrast = []
    for fg, bg, minimum, sev in pairs:
        row = {"fg": fg, "bg": bg, "min": minimum, "fg_hex": palette.get(fg),
               "bg_hex": palette.get(bg), "ratio": None, "pass": None, "severity": sev}
        fv, bv = palette.get(fg), palette.get(bg)
        if fv is None or bv is None:
            if fg == "disabled" and fv is None:
                continue  # optional token
            row["note"] = "skipped: token missing"
            contrast.append(row)
            continue
        if not (is_hex(fv) and is_hex(bv)):
            row["note"] = "skipped: not hex"
            contrast.append(row)
            continue
        ratio = contrast_ratio(fv, bv)
        row["ratio"] = round(ratio, 2)
        row["pass"] = ratio >= minimum
        if fg == "accent" and accent_fill_only:
            row["note"] = "fill-only accent: reported, not enforced"
        contrast.append(row)
        if not row["pass"]:
            add(sev, "contrast", "%s %s on %s %s = %.2f:1 (< %.1f)"
                % (fg, norm_hex(fv), bg, norm_hex(bv), ratio, minimum),
                "$.%s.%s" % (pal_src or "palette", fg))

    # ---- ANSI on terminal (warnings) -----------------------------------
    ansi = design.get("terminal_ansi")
    term = palette.get("terminal")
    ansi_rows = []
    if isinstance(ansi, list) and is_hex(term):
        for i, col in enumerate(ansi):
            if i in (0, 8) or not is_hex(col):
                continue
            ratio = contrast_ratio(col, term)
            ok = ratio >= NONTEXT_MIN
            ansi_rows.append({"slot": i, "hex": norm_hex(col), "ratio": round(ratio, 2),
                              "pass": ok})
            if not ok:
                add("warning", "ansi", "ANSI %d %s on terminal %s = %.2f:1 (< %.1f)"
                    % (i, norm_hex(col), norm_hex(term), ratio, NONTEXT_MIN),
                    "$.terminal_ansi[%d]" % i)

    # ---- hue census ----------------------------------------------------
    census = []
    accent = palette.get("accent")
    if is_hex(accent):
        my_hue, my_sat = hue_sat(accent)
        for label, other in census_sources(root, themes_dir, design_path):
            o_hue, o_sat = hue_sat(other)
            row = {"source": label, "accent": norm_hex(other), "hue": round(o_hue, 1)}
            if my_sat < ACHROMATIC_SAT or o_sat < ACHROMATIC_SAT:
                row["distance"] = None
                row["note"] = "achromatic; hue not compared"
            else:
                dist = hue_distance(my_hue, o_hue)
                row["distance"] = round(dist, 1)
                if dist < HUE_MIN_DEG:
                    add("warning", "hue", "accent %s (hue %.0f) is %.1f deg from %s %s"
                        % (norm_hex(accent), my_hue, dist, label, norm_hex(other)),
                        "$.%s.accent" % (pal_src or "palette"))
            census.append(row)
        census.sort(key=lambda r: (r["distance"] is None, r["distance"] or 0))
        accent_info = {"hex": norm_hex(accent), "hue": round(my_hue, 1),
                       "saturation": round(my_sat, 3)}
    else:
        accent_info = None

    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {
        "tool": "tools/design_lint.py",
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "design": design_path,
        "design_sha256": hashlib.sha256(raw).hexdigest(),
        "preset": os.path.basename(os.path.dirname(design_path)),
        "palette_source": pal_src,
        "required_tokens": required,
        "accent_fill_only": accent_fill_only,
        "errors": errors,
        "warnings": warnings,
        "exit": 1 if errors else 0,
        "findings": findings,
        "contrast": contrast,
        "ansi": ansi_rows,
        "fonts": fonts,
        "accent": accent_info,
        "hue_census": census,
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join("%%-%ds" % w for w in widths)
    out = [fmt % tuple(headers), fmt % tuple("-" * w for w in widths)]
    out.extend(fmt % tuple(str(c) for c in r) for r in rows)
    return "\n".join(line.rstrip() for line in out)


def render_text(res):
    lines = ["design_lint: %s" % res["design"], ""]
    if res["contrast"]:
        rows = []
        for c in res["contrast"]:
            if c["ratio"] is None:
                status = c.get("note", "skipped")
            elif c["pass"]:
                status = "ok"
            else:
                status = {"error": "FAIL", "info": "info"}.get(c["severity"], "FAIL")
            rows.append([c["fg"], c["bg"], c["fg_hex"] or "-", c["bg_hex"] or "-",
                         "%.2f" % c["ratio"] if c["ratio"] is not None else "-",
                         "%.1f" % c["min"], status])
        lines.append("Contrast (WCAG 2.x)")
        lines.append(_table(rows, ["fg", "bg", "fg hex", "bg hex", "ratio", "min", "status"]))
        lines.append("")
    if res["ansi"]:
        bad = [a for a in res["ansi"] if not a["pass"]]
        lines.append("ANSI on terminal: %d/%d slots >= %.1f:1 (slots 0 and 8 exempt)"
                     % (len(res["ansi"]) - len(bad), len(res["ansi"]), NONTEXT_MIN))
        lines.append("")
    if res["fonts"]:
        lines.append("Fonts: " + ", ".join("%s=%s (%s)" % (f["key"], f["family"],
                     "ok" if f["installed"] else "MISSING") for f in res["fonts"]))
        lines.append("")
    if res["accent"]:
        lines.append("Hue census: accent %s hue %.0f deg" % (res["accent"]["hex"],
                                                             res["accent"]["hue"]))
        if res["hue_census"]:
            rows = [[h["source"], h["accent"], "%.0f" % h["hue"],
                     "-" if h["distance"] is None else "%.1f" % h["distance"],
                     h.get("note") or ("CLOSE" if h["distance"] is not None
                                       and h["distance"] < HUE_MIN_DEG else "ok")]
                    for h in res["hue_census"]]
            lines.append(_table(rows, ["source", "accent", "hue", "dist", "status"]))
        else:
            lines.append("(no other accents found)")
        lines.append("")
    if res["findings"]:
        order = {"error": 0, "warning": 1, "info": 2}
        rows = [[f["severity"], f["check"], f["path"] or "", f["message"]]
                for f in sorted(res["findings"], key=lambda f: order.get(f["severity"], 3))]
        lines.append("Findings")
        lines.append(_table(rows, ["severity", "check", "path", "message"]))
        lines.append("")
    lines.append("Result: %d error(s), %d warning(s) -> exit %d"
                 % (res["errors"], res["warnings"], res["exit"]))
    return "\n".join(lines)


def write_result(res, preset_dir):
    ident = os.path.join(preset_dir, "identity")
    os.makedirs(ident, exist_ok=True)
    dest = os.path.join(ident, "contrast.json")
    fd, tmp = tempfile.mkstemp(prefix=".contrast.", suffix=".json", dir=ident)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return dest


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        sys.exit(2)


def main(argv=None):
    ap = _Parser(prog="design_lint.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="preset id, preset directory or design.json path")
    ap.add_argument("--write", action="store_true",
                    help="save the result to <preset>/identity/contrast.json")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="studio root (default: parent of tools/)")
    ap.add_argument("--themes-dir", default=DEFAULT_THEMES_DIR,
                    help="extra theme bundles for the hue census (default: ~/Documents/themes)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    try:
        design_path, preset_dir, _pid = resolve_target(args.target, root)
        res = lint(design_path, root=root, themes_dir=args.themes_dir)
    except InvocationError as exc:
        sys.stderr.write("design_lint: %s\n" % exc)
        return 2
    if args.write:
        try:
            res["written"] = write_result(res, preset_dir)
        except OSError as exc:
            sys.stderr.write("design_lint: cannot write result: %s\n" % exc)
            return 2
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(render_text(res))
        if args.write:
            print("Wrote %s" % res["written"])
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
