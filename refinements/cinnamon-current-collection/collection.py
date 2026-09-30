#!/usr/bin/env python3
"""Build and statically verify the non-live Cinnamon Current Collection.

This program intentionally writes only under its own collection directory.  It
does not call gsettings, dconf, systemctl, Cinnamon, Eww, or a terminal app.

``build [SLUG ...] [--force]`` is incremental: a profile is skipped when its
input key (profile entry, this file, Eww palette entry, contributions/<slug>
and a stat-first fingerprint of every source it reads) and its generated tree
both match build-cache/<slug>.json.  Rebuilt profiles are built in a temp dir
beside generated/ and synced in; unchanged files are never rewritten.
``link-identical [--commit]`` hard-links identical large icon/cursor files
across generated/<slug>/ trees only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import filecmp
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator


ROOT = Path(__file__).resolve().parent
STUDIO_ROOT = ROOT.parent.parent
SPEC_PATH = ROOT / "profiles.json"
GENERATED = ROOT / "generated"
VERIFICATION = ROOT / "verification"
ISOLATED_LATEST = VERIFICATION / "isolated-latest"
OCEAN = ROOT.parent / "ocean-silk-current"
OCEAN_ISLAND = OCEAN / "glass" / "island" / "applet"
OCEAN_APPLET = OCEAN / "glass" / "staged" / "applet.js"
OCEAN_STYLESHEET = OCEAN / "glass" / "staged" / "stylesheet.css"
EWW_PALETTES = Path.home() / "Documents" / "eww-graphite-brass" / "config" / "themes" / "palettes.json"
EWW_CLOSE_SOURCE = Path.home() / "Documents" / "eww-graphite-brass" / "config" / "carbon.yuck"
EWW_CLOSE_STYLE = Path.home() / "Documents" / "eww-graphite-brass" / "config" / "eww.scss"
MARKER = "/* Desktop Theme Studio current-collection overlay: generated. */"
EWW_BUNDLE_FILES = ("red-panda-overtime.scss", "red-panda-overtime.yuck", "rpo-flow.py", "rpo-widgets")
# Output modes are fixed by this builder, not by the caller: 0664 files and
# 0775 directories, exactly as the 2026-09-22 trees were built.
BUILD_UMASK = 0o002
# Cursor trees keep their relative in-tree alias symlinks (hand2 -> pointer),
# because the root system stage pins cursor trees with symlink targets
# (runtime-quality/system-stage/system.py source_tree_hash) and the desktop
# stage installs generated/<slug>/cursors into ~/.icons with symlinks kept.
# Exception: Tangerine Graphite's root-stage pin (system-stage/profiles/
# tangerine-graphite.json, sources.cursors = its generated tree) was taken
# from the dereferenced tree on 2026-09-22; its cursors stay dereferenced until
# that profile and the catalog `tangerine-graphite-system` pin are re-pinned.
# Four completed legacy sources use relative links for the diagonal native
# cursors. Materialize their identical bytes in the generated package so the
# root-stage derived-alias guard can pin regular native targets.
CURSOR_TREES_DEREFERENCED = frozenset({"tangerine-graphite", "barbatos", "nocturne", "nocturne-air", "nocturne-studio"})
LEGACY_ISOLATED_CHECKS = (
    "menu@cinnamon.org opens and closes",
    "notifications@cinnamon.org opens and closes",
    "sound@cinnamon.org opens and closes",
    "Island actual × close",
    "Island actual Escape",
    "Island outside click",
    "Island ReloadXlet lifecycle",
    "Cinnamon theme reload",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(value)
    temp.replace(path)


def write_text(path: Path, value: str) -> None:
    write_bytes(path, value.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# Optional per-build memo of already-known source file digests, keyed by the
# path string as walked.  The incremental builder fills it from its stat-first
# input fingerprint so manifest source hashes never re-read a source file.
HashLookup = Callable[[Path], str]


def sha256_known(path: Path, lookup: HashLookup | None = None) -> str:
    return lookup(path) if lookup is not None else sha256(path)


def sha256_tree(path: Path, lookup: HashLookup | None = None) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(sha256_known(item, lookup).encode("ascii"))
    return digest.hexdigest()


def hash_with_stat(path: Path) -> tuple[str, tuple[int, int, int, int]]:
    """sha256 of a regular file plus the (ino, size, mtime_ns, ctime_ns) it kept while being read.

    Raises ValueError when the file changed during the read, so a digest is
    always bound to the exact inode state it describes.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Expected a regular file: {path}")
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(handle.fileno())
    key = (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if key != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError(f"File changed while it was hashed: {path}")
    return digest.hexdigest(), key


def inode_state(st: os.stat_result) -> tuple[int, int, int, int]:
    """The (ino, size, mtime_ns, ctime_ns) that hash_with_stat() binds a digest to."""
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def output_digests(path: Path) -> dict[str, tuple[str, tuple[int, int, int, int]]]:
    """Regular files only, each with the stat its digest was read at (the top-level
    manifest.json excluded; a NESTED manifest.json, e.g. a profile's own
    system-payload/manifest.json copied there by copy_system_payload(), is a distinct file
    and IS included)."""
    top_level_manifest = path / "manifest.json"
    return {item.relative_to(path).as_posix(): hash_with_stat(item)
            for item in sorted(path.rglob("*"))
            if not item.is_symlink() and item.is_file() and item != top_level_manifest}


def manifest_file_digests(path: Path, regular: dict[str, tuple[str, Any]]) -> dict[str, str]:
    """generated_files exactly as output_files() defines them: every rglob entry that
    is_file() (a file symlink included) mapped to the sha256 of the bytes it reads as.
    live.py plan() verifies these by read_bytes(), which follows symlinks.  Each
    symlink reuses the digest of the in-tree regular file it resolves to. Only the
    TOP-LEVEL manifest.json is excluded; a nested one (e.g. system-payload/manifest.json)
    is an ordinary regular file here."""
    resolved_root = path.resolve()
    top_level_manifest = path / "manifest.json"
    out: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file() or item == top_level_manifest:
            continue
        rel = item.relative_to(path).as_posix()
        if item.is_symlink():
            target = item.resolve()
            inner = target.relative_to(resolved_root).as_posix() if target.is_relative_to(resolved_root) else None
            out[rel] = regular[inner][0] if inner in regular else sha256(target)
        else:
            out[rel] = regular[rel][0]
    return out


def copy_fresh(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> str:
    """copy2 that never writes into an existing inode.

    Generated icon/cursor files may be hard-linked across profiles
    (``link-identical``); truncating one in place would change every link.
    """
    if os.path.lexists(target) and not os.path.isdir(target):
        os.unlink(target)
    return shutil.copy2(source, target)


def copytree_fresh(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True, copy_function=copy_fresh)


def tree_symlinks(root: Path) -> dict[str, str]:
    """Every symlink in a tree (not followed) as rel -> target.

    Refuses absolute targets and any link resolving outside the tree, the rule
    system.py _ensure_tree_symlinks_stay_inside() applies to installed trees.
    Cursor aliases must also be bare same-directory names (no "/" and no "."
    or ".."): the check above is made in the source tree, and a re-entering
    relative path could still point outside the copy, which root later reads.
    """
    root = Path(root)
    resolved_root = root.resolve()
    links: dict[str, str] = {}

    def fail(error: OSError) -> None:
        raise error

    for directory, dirnames, filenames in os.walk(root, followlinks=False, onerror=fail):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            item = base / name
            if not item.is_symlink():
                continue
            target = os.readlink(item)
            if (os.path.isabs(target) or "/" in target or target in ("", ".", "..")
                    or not item.resolve().is_relative_to(resolved_root)):
                raise ValueError(f"Cursor symlink escapes its tree: {item} -> {target}")
            links[item.relative_to(root).as_posix()] = target
    return links


def copytree_links(source: Path, target: Path) -> None:
    """copytree_fresh() that keeps relative in-tree symlinks as symlinks."""
    tree_symlinks(source)
    shutil.copytree(source, target, dirs_exist_ok=True, copy_function=copy_fresh, symlinks=True)


def isolated_contract(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the profile's isolated evidence contract without changing legacy defaults."""
    configured = profile.get("isolated_verification")
    if configured is None:
        return {"kind": "legacy-default", "required_checks": list(LEGACY_ISOLATED_CHECKS)}
    if not isinstance(configured, dict):
        raise ValueError("isolated_verification must be an object")
    required = configured.get("required_checks")
    if (not isinstance(required, list) or not required
            or any(not isinstance(item, str) or not item.strip() for item in required)
            or len(set(required)) != len(required)):
        raise ValueError("isolated_verification.required_checks must contain unique non-empty strings")
    contract: dict[str, Any] = {"kind": "profile", "required_checks": list(required)}
    fingerprint = configured.get("source_fingerprint")
    if fingerprint is not None:
        if not isinstance(fingerprint, dict):
            raise ValueError("isolated_verification.source_fingerprint must be an object")
        expected = fingerprint.get("sha256")
        base = fingerprint.get("base")
        paths = fingerprint.get("paths")
        receipt_field = fingerprint.get("receipt_field", "source_sha256")
        exclude_python_cache = fingerprint.get("exclude_python_cache", False)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("isolated source fingerprint must declare a lowercase SHA-256")
        if not isinstance(base, str) or not base:
            raise ValueError("isolated source fingerprint must declare its base directory")
        if (not isinstance(paths, list) or not paths
                or any(not isinstance(item, str) or not item for item in paths)):
            raise ValueError("isolated source fingerprint must declare source paths")
        if not isinstance(receipt_field, str) or not receipt_field:
            raise ValueError("isolated source fingerprint receipt field must be a string")
        if not isinstance(exclude_python_cache, bool):
            raise ValueError("exclude_python_cache must be a boolean")
        contract["source_fingerprint"] = {
            "sha256": expected,
            "base": base,
            "paths": list(paths),
            "receipt_field": receipt_field,
            "exclude_python_cache": exclude_python_cache,
        }
    return contract


# ---------------------------------------------------------------------------
# Optional rail panel layout (repairs/panel-rail-20260927/DESIGN.md §1, §3).
# Only a profile that declares "panel_layout" reaches this code; panel_layout.py
# is loaded lazily, so every other profile builds and plans exactly as before.
# ---------------------------------------------------------------------------
PANEL_LAYOUT_MODULE = ROOT / "panel_layout.py"
_PANEL_LAYOUT: Any = None


def panel_layout_module() -> Any:
    """Load panel_layout.py on first use (it imports gi only inside its functions)."""
    global _PANEL_LAYOUT
    if _PANEL_LAYOUT is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("collection_panel_layout", PANEL_LAYOUT_MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PANEL_LAYOUT = module
    return _PANEL_LAYOUT


def rail_layout(profile: dict[str, Any]) -> dict[str, Any] | None:
    """The validated panel_layout of a rail profile; None for a profile without one."""
    if "panel_layout" not in profile:
        return None
    error = panel_layout_module().validate_layout(profile)
    if error:
        raise ValueError(f"invalid panel_layout: {error}")
    return profile["panel_layout"]


def panel_spec_sha256(layout: dict[str, Any]) -> str:
    """sha256 of the canonical panel_layout (the isolated rail gate's panel_spec_sha256)."""
    return panel_layout_module().spec_sha256(layout)


def rail_activation_section(slug: str, layout: dict[str, Any]) -> dict[str, Any]:
    panel = layout["panel"]
    module = panel_layout_module()
    new = panel["new_instance"]
    snapshots = []
    for item in panel["remove"]:
        uuid, ident = item.rsplit(":", 1)
        snapshots += [f"~/.config/cinnamon/spices/{uuid}/{ident}.json", f"~/.cinnamon/configs/{uuid}/{ident}.json"]
    return {
        "kind": layout["kind"],
        "schema": layout["schema"],
        "spec_sha256": panel_spec_sha256(layout),
        "provenance": layout["provenance"],
        "writes": {
            "org.cinnamon": list(module.PANEL_APPLY_ORDER),
            "next-applet-id": f"compare-and-set: the current value becomes the new {new['uuid']} instance id, then +1; never decremented",
            "files": [f"~/.config/cinnamon/spices/{new['uuid']}/<next-applet-id>.json (new instance settings, written before enabled-applets)"],
        },
        "placements": list(panel["enabled_applets"]),
        "panels_enabled": list(panel["panels_enabled"]),
        "panels_height": list(panel["panels_height"]),
        "removed": list(panel["remove"]),
        "snapshots": snapshots,
        "restore_order": [" then ".join(step) for step in module.RESTORE_STEPS],
        "transitions": {
            "to-rail": "the first switch to this profile journals the current (home) layout, its removed applets' settings files byte-exact, and allocates the new instance",
            "none": "switching to this profile while its rail is live writes no panel key",
            "to-home": "the next switch to a profile without panel_layout restores home",
        },
        "isolated_gate": f"verification/isolated-latest/{slug}.panel.json from isolate_panel.py: panel_code_sha256 (panel_layout.py) and panel_spec_sha256 (this spec_sha256)",
    }


def rail_activation_plan(slug: str, layout: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    must = [item for item in plan["must_not_touch"] if item not in ("enabled-applets", "panel placement")]
    plan["must_not_touch"] = must + ["applets outside the declared placements/removals",
                                     "c-eyes settings content (byte-exact snapshot only)"]
    plan["requires"] = plan["requires"] + ["isolated rail rehearsal"]
    plan["panel_layout"] = rail_activation_section(slug, layout)
    return plan


def verify_isolated_panel(slug: str, layout: dict[str, Any], path: Path) -> dict[str, Any]:
    """The isolate_panel.py report of a rail profile, judged exactly as live.plan's to-rail gate."""
    gate = panel_layout_module().gate_status(path, slug, panel_spec_sha256(layout), True)
    return {**gate, "ok": gate["status"] == "passed"}


def fingerprint_sources(base: Path, roots: list[str], exclude_python_cache: bool = False) -> str:
    """Hash the declared current source roots using the preset acceptance algorithm."""
    base = Path(base)
    resolved_base = base.resolve()
    files: list[Path] = []
    for raw in roots:
        relative = Path(raw)
        source = base / relative
        if relative.is_absolute() or ".." in relative.parts or not source.resolve().is_relative_to(resolved_base):
            raise ValueError(f"Unsafe isolated fingerprint source: {raw}")
        if source.is_dir():
            files.extend(
                item for item in source.rglob("*")
                if item.is_file() and not (
                    exclude_python_cache
                    and ("__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"})
                )
            )
        elif source.is_file():
            files.append(source)
        else:
            raise FileNotFoundError(f"Missing isolated fingerprint source: {source}")
    digest = hashlib.sha256()
    for path in sorted(files, key=str):
        digest.update(str(path.relative_to(base)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def rgb(color: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError(f"Expected a six-digit color, got {color!r}")
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def rgba(color: str, alpha: float) -> str:
    red, green, blue = rgb(color)
    return f"rgba({red},{green},{blue},{alpha:.2f})"


def color_csv(color: str) -> str:
    return ",".join(str(value) for value in rgb(color))


def color_u16(color: str) -> str:
    return ",".join(str(value * 257) for value in rgb(color))


def relative_luminance(color: str) -> float:
    def linear(value: int) -> float:
        channel = value / 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    red, green, blue = rgb(color)
    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def palette(profile: dict[str, Any]) -> dict[str, str]:
    return profile["palette"]


def eww_palette_key(profile: dict[str, Any]) -> str:
    """Return the exact palette-registry key, which may differ from GTK's theme alias."""
    return profile.get("eww_palette_key", profile["theme"])


def panel_overlay(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    style = profile["style"]
    panel_alpha = style["panel_alpha"]
    popup_alpha = style["popup_alpha"]
    radius = style["panel_radius"]
    inset = "0.42" if profile["light"] else "0.12"
    return f'''{MARKER}
/* {profile["name"]}: {style["identity"]}. Background alpha never reduces label opacity. */
#panel.panel-top, #panel.panel-bottom {{
  background-color: transparent;
  background-gradient-direction: none;
  background-image: none;
  border-image: none;
  box-shadow: none;
}}
#panel.panel-top #panelLeft, #panel.panel-top #panelCenter, #panel.panel-top #panelRight {{
  background-color: transparent;
  background-gradient-direction: vertical;
  background-gradient-start: {rgba(colors["surface"], panel_alpha)};
  background-gradient-end: {rgba(colors["raised"], panel_alpha)};
  border-image: none;
  border-radius: {radius}px;
  box-shadow: inset 0 1px rgba(255,255,255,{inset});
}}
#panel.panel-bottom #panelCenter {{
  background-color: transparent;
  background-gradient-direction: vertical;
  background-gradient-start: {rgba(colors["surface"], panel_alpha)};
  background-gradient-end: {rgba(colors["raised"], panel_alpha)};
  border-image: none;
  border-radius: {radius}px;
  box-shadow: inset 0 1px rgba(255,255,255,{inset});
}}
#panel.panel-bottom #panelCenter .applet-box, #panel.panel-bottom .grouped-window-list-box {{
  background-color: transparent;
  background-gradient-direction: none;
  background-image: none;
  border-image: none;
}}
#panel .applet-label, #panel .system-status-icon, #panel .panel-button {{ color: {colors["foreground"]}; }}
#panel .grouped-window-list-item-box:focus, #panel .applet-box:focus {{ border-color: {colors["accent"]}; }}
.popup-menu-content, .appmenu-background .popup-menu-content, .modal-dialog, #notification, .notification, .osd-window {{
  background-color: {rgba(colors["surface"], popup_alpha)};
  color: {colors["foreground"]};
  border-color: {colors["border"]};
}}
.popup-menu-item:active, .popup-menu-item:selected, .popup-menu-item:hover {{
  background-color: {rgba(colors["raised"], min(popup_alpha, 0.82))};
  color: {colors["foreground"]};
}}
.popup-menu-item:focus, .modal-dialog:focus {{ border-color: {colors["accent"]}; }}
/* Cinnamon 6.6 uses appmenu-* classes for the actual Cinabon menu. */
.appmenu-sidebar, .menu-favorites-box {{
  background-color:{colors["raised"]}; color:{colors["foreground"]}; border-color:{colors["border"]};
}}
.appmenu-sidebar StLabel, .appmenu-sidebar StIcon, .appmenu-sidebar-button,
.appmenu-category-button, .appmenu-category-button StLabel,
.appmenu-application-button, .appmenu-application-button StLabel {{ color:{colors["foreground"]}; }}
.appmenu-category-button-selected, .appmenu-category-button-selected StLabel,
.appmenu-application-button-selected, .appmenu-application-button-selected StLabel,
.appmenu-sidebar-button:hover, .appmenu-sidebar-button:hover StLabel,
.appmenu-category-button:hover, .appmenu-category-button:hover StLabel {{
  background-color:{colors["selection"]}; color:{colors["selection_foreground"]};
}}
.appmenu-application-button-description, .appmenu-category-button-greyed {{ color:{colors["muted"]}; }}
.appmenu-sidebar .user-widget-label {{ color:{colors["foreground"]}; }}
.appmenu-sidebar .appmenu-system-button {{ background-color:{colors["surface"]}; color:{colors["foreground"]}; border-color:{colors["border"]}; }}
.appmenu-sidebar .appmenu-system-button:hover {{ background-color:{colors["selection"]}; color:{colors["selection_foreground"]}; }}
#appmenu-search-entry, #menu-search-entry {{
  background-color:{colors["terminal"]}; color:{colors["terminal_foreground"]};
  caret-color:{colors["terminal_foreground"]}; border-color:{colors["border"]};
  selection-background-color:{colors["selection"]}; selected-color:{colors["selection_foreground"]};
}}
#appmenu-search-entry:focus, #menu-search-entry:focus {{ border-color:{colors["accent"]}; }}
.appmenu-search-entry-icon, .menu-search-entry-icon {{ color:{colors["terminal_foreground"]}; }}
#panel.panel-top .workspace-switcher, #panel.panel-top .workspace-switcher .workspace-button {{
  background-color:transparent; background-gradient-direction:none; color:{colors["foreground"]};
}}
#panel.panel-top .workspace-switcher .workspace-button:outlined {{
  background-color:{colors["selection"]}; color:{colors["selection_foreground"]}; border-color:{colors["selection"]};
}}
#panel.panel-top .workspace-switcher .workspace-button:hover {{ background-color:{colors["raised"]}; color:{colors["foreground"]}; }}
'''


def recolor_ocean(text: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    substitutions = {
        "#DEE7E6": colors["surface"],
        "#E9EBE6": colors["background"],
        "#CBD7DA": colors["raised"],
        "#B9CDD3": colors["hover"],
        "#354D64": colors["accent"],
        "#4B667E": colors["hover"],
        "#304957": colors["foreground"],
        "#475F6C": colors["muted"],
        "#93AAB4": colors["border"],
        "#647F8E": colors["border"],
        "#42665C": colors["success"],
        "#755B30": colors["warning"],
        "#874B4B": colors["error"],
    }
    for before, after in substitutions.items():
        text = re.sub(re.escape(before), after, text, flags=re.IGNORECASE)
    return text


def recolor_legacy_shell(text: str, profile: dict[str, Any]) -> str:
    """Replace inherited Ocean paint in base selectors, retaining each geometry."""
    colors = palette(profile)
    text = recolor_ocean(text, profile)
    roles = {
        '#d3e0e1':'raised', '#becfd4':'hover', '#9eb5be':'border', '#a9bec6':'border',
        '#edf1e9':'surface', '#b6cdd6':'raised', '#a1bdc9':'raised', '#f4f5ee':'terminal',
        '#b8737a':'error', '#f26267':'error', '#6f94a4':'border',
    }
    for old, role in roles.items():
        text = re.sub(re.escape(old), colors[role], text, flags=re.IGNORECASE)
    rgb_roles = {(222,231,230):'surface',(203,215,218):'raised',(233,235,230):'background',
                 (233,238,232):'surface',(161,189,201):'raised',(48,73,87):'foreground',
                 (75,102,126):'accent',(53,77,100):'accent',(248,248,239):'foreground'}
    def replace_rgb(match):
        role = rgb_roles.get(tuple(int(match[i]) for i in range(1,4)))
        if role is None: return match.group()
        return rgba(colors[role], float(match[4]))
    return re.sub(r'rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)', replace_rgb, text)


def legacy_palette_shell_styles(profile: dict[str, Any]) -> str:
    """Paint imported panels without changing their alpha, geometry or images."""
    if not profile.get("legacy_completion"):
        return ""
    c = palette(profile)
    source = (Path(profile['sources']['theme']) / 'cinnamon/cinnamon.css').read_text()
    source = source.split(authored_shell_provenance(profile).rstrip('\n'), 1)[0]
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    rules = []
    def tint(value, role):
        if value.strip().lower() in ('transparent', 'none'):
            return value.strip()
        alpha = re.search(r'rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\s*\)', value)
        if alpha:
            return rgba(c[role], float(alpha[1]))
        return c[role]
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', source):
        selected = [s.strip() for s in selectors.split(',') if
                    re.search(r'#panel\b|\.panel-(?:top|bottom|left|right)\b|\.workspace-(?:button|graph)\b|\.popup-(?:menu-content|sub-menu)\b', s)]
        if not selected:
            continue
        declarations = []
        root = all(re.fullmatch(r'#panel(?:\.panel-(?:top|bottom|left|right))?', s) for s in selected)
        for prop, value in re.findall(r'([\w-]+)\s*:\s*([^;]+)', body):
            role = 'top_background' if root else 'surface'
            if prop == 'color':
                declarations.append('color:' + tint(value, 'top_foreground'))
            elif prop in ('background-color', 'background-gradient-start', 'background-gradient-end'):
                declarations.append(prop + ':' + tint(value, role))
            elif prop == 'border-color':
                declarations.append(prop + ':' + tint(value, 'control_border'))
            elif prop == 'border' and re.search(r'#[\da-fA-F]{3,8}|rgba?\(', value):
                paint = re.search(r'rgba?\([^)]*\)|#[\da-fA-F]{3,8}', value)[0]
                declarations.append('border-color:' + tint(paint, 'control_border'))
        if declarations:
            rules.append(', '.join(selected) + ' {' + ';'.join(declarations) + ';}')
    return '\n/* Canonical legacy panel paint; authored alpha, images and shapes retained. */\n' + '\n'.join(rules) + f'''
#panel .applet-box StLabel, #panel .applet-box StIcon {{ color:{c['top_foreground']}; }}
#panel.panel-top .workspace-button, #panel.panel-top .workspace-switcher .workspace-button,
#panel.panel-top .workspace-button:shaded {{ background-color:{c['surface']}; color:{c['foreground']}; border-color:{c['control_border']}; }}
#panel.panel-top .workspace-button:hover, #panel.panel-top .workspace-switcher .workspace-button:hover {{ background-color:{c['hover']}; color:{c['foreground']}; }}
#panel.panel-top .workspace-button:outlined, #panel.panel-top .workspace-button:outlined:hover,
#panel.panel-top .workspace-switcher .workspace-button:outlined, #panel.panel-top .workspace-switcher .workspace-button:outlined:hover,
#panel.panel-top .workspace-graph .workspace:active {{ background-color:{c['selection']}; color:{c['selection_foreground']}; border-color:{c['focus']}; }}
#panel.panel-top .workspace-button StLabel, #panel.panel-top .workspace-button StIcon {{ color:{c['foreground']}; }}
#panel.panel-top .workspace-button:outlined StLabel, #panel.panel-top .workspace-button:outlined StIcon,
#panel.panel-top .workspace-button:outlined:hover StLabel, #panel.panel-top .workspace-button:outlined:hover StIcon {{ color:{c['selection_foreground']}; }}
.workspace-graph .windows {{ -active-window-background:{c['focus']}; -active-window-border:{c['focus']}; -inactive-window-background:{c['control_border']}; -inactive-window-border:{c['control_border']}; }}
.appmenu-sidebar, .appmenu-sidebar-button, .appmenu-system-button {{ background-color:{c['surface']}; color:{c['foreground']}; border-color:{c['control_border']}; }}
.appmenu-sidebar .user-widget.vertical .user-widget-label, .appmenu-sidebar StLabel, .appmenu-sidebar StIcon,
.appmenu-application-button StLabel, .appmenu-category-button StLabel {{ color:{c['foreground']}; }}
.appmenu-sidebar-button:hover, .appmenu-system-button:hover {{ background-color:{c['hover']}; color:{c['foreground']}; }}
.appmenu-sidebar-button:hover StLabel, .appmenu-sidebar-button:hover StIcon,
.appmenu-system-button:hover StLabel, .appmenu-system-button:hover StIcon {{ color:{c['foreground']}; }}
.appmenu-application-button-selected, .appmenu-category-button-selected,
.appmenu-application-button-selected:hover, .appmenu-category-button-selected:hover {{ background-color:{c['selection']}; color:{c['selection_foreground']}; }}
.appmenu-application-button-selected StLabel, .appmenu-application-button-selected StIcon,
.appmenu-category-button-selected StLabel, .appmenu-category-button-selected StIcon,
.appmenu-application-button-selected:hover StLabel, .appmenu-application-button-selected:hover StIcon,
.appmenu-category-button-selected:hover StLabel, .appmenu-category-button-selected:hover StIcon {{ color:{c['selection_foreground']}; }}
#appmenu-search-entry, #menu-search-entry, #appmenu-search-entry:hover, #menu-search-entry:hover,
#appmenu-search-entry:focus, #menu-search-entry:focus {{ background-color:{c['background']}; color:{c['foreground']}; caret-color:{c['foreground']}; border-color:{c['control_border']}; selection-background-color:{c['selection']}; selected-color:{c['selection_foreground']}; }}
.appmenu-search-entry-icon, .menu-search-entry-icon {{ color:{c['foreground']}; }}
'''


def completion_shell_styles(profile: dict[str, Any]) -> str:
    """Opt-in semantic paint for preserved legacy app menus."""
    if not profile.get("legacy_completion"):
        return ""
    colors = palette(profile)
    return f'''/* Legacy Eucalyptus app-menu semantic ink; authored geometry retained. */
.appmenu-sidebar, .appmenu-system-button, .appmenu-sidebar-button,
.appmenu-sidebar .appmenu-system-button, .appmenu-sidebar .appmenu-sidebar-button {{
  background-color:{colors["raised"]}; color:{colors["foreground"]};
}}
.appmenu-sidebar StLabel, .appmenu-sidebar StIcon,
.appmenu-sidebar .user-widget.vertical .user-widget-label,
.user-widget.vertical .user-widget-label,
.appmenu-system-button StLabel, .appmenu-system-button StIcon,
.appmenu-sidebar-button StLabel, .appmenu-sidebar-button StIcon {{ color:{colors["foreground"]}; }}
.appmenu-system-button:hover, .appmenu-sidebar-button:hover,
.appmenu-sidebar .appmenu-system-button:hover, .appmenu-sidebar .appmenu-sidebar-button:hover,
.appmenu-application-button:hover, .appmenu-category-button:hover {{
  background-color:{colors["hover"]}; color:{colors["foreground"]};
}}
.appmenu-system-button:hover StLabel, .appmenu-system-button:hover StIcon,
.appmenu-sidebar-button:hover StLabel, .appmenu-sidebar-button:hover StIcon,
.appmenu-sidebar .appmenu-system-button:hover StLabel, .appmenu-sidebar .appmenu-system-button:hover StIcon,
.appmenu-sidebar .appmenu-sidebar-button:hover StLabel, .appmenu-sidebar .appmenu-sidebar-button:hover StIcon,
.appmenu-application-button:hover StLabel, .appmenu-application-button:hover StIcon,
.appmenu-category-button:hover StLabel, .appmenu-category-button:hover StIcon {{ color:{colors["foreground"]}; }}
.appmenu-application-button-selected, .appmenu-category-button-selected,
.appmenu-application-button-selected:hover, .appmenu-category-button-selected:hover {{
  background-color:{colors["selection"]}; color:{colors["selection_foreground"]};
}}
.appmenu-application-button-selected StLabel, .appmenu-application-button-selected StIcon,
.appmenu-category-button-selected StLabel, .appmenu-category-button-selected StIcon,
.appmenu-application-button-selected:hover StLabel, .appmenu-application-button-selected:hover StIcon,
.appmenu-category-button-selected:hover StLabel, .appmenu-category-button-selected:hover StIcon {{ color:{colors["selection_foreground"]}; }}
.appmenu-category-button-greyed, .appmenu-category-button-greyed:hover {{
  background-color:{colors["raised"]}; color:{colors["muted"]};
}}
.appmenu-category-button-greyed StLabel, .appmenu-category-button-greyed StIcon,
.appmenu-category-button-greyed:hover StLabel, .appmenu-category-button-greyed:hover StIcon {{
  color:{colors["muted"]}; opacity:1;
}}
'''


def island_styles(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    style = profile["style"]
    base = recolor_ocean(OCEAN_STYLESHEET.read_text(encoding="utf-8"), profile)
    rendered = base.rstrip() + f'''\n\n/* {profile["name"]} per-profile island geometry and glass. */
.ni-compact {{ border-radius:{style["radius"]}px; }}
.ni-popup .popup-menu-content {{
  background-color:{rgba(colors["surface"], style["island_alpha"])};
  border-color:{colors["border"]};
  border-radius:{style["radius"] + 8}px;
}}
.ni-media, .ni-card, .ni-meters, .ni-top-volume, .ni-calendar-grid {{
  background-color:{rgba(colors["raised"], style["card_alpha"])};
  border-radius:{style["radius"]}px;
}}
.ni-button, .ni-tab, .ni-close {{
  background-color:{rgba(colors["raised"], min(style["card_alpha"] + 0.12, 0.82))};
  border-radius:{max(8, style["radius"] - 4)}px;
}}
.ni-status, .ni-record-tile, .ni-tab:checked, .ni-button:active, .ni-transport-button:active {{
  background-color:{rgba(colors["accent"], 0.92)};
  color:{colors["selection_foreground"]};
}}
.ni-status StLabel, .ni-status StIcon, .ni-record-tile StLabel, .ni-record-tile StIcon {{ color:{colors["selection_foreground"]}; }}
'''
    if profile.get("legacy_completion"):
        # Explicit legacy opt-in: inherited accents are fill-only in some
        # palettes, and shell .menu selectors can otherwise win on load order.
        rendered += f'''
/* Legacy completion Island semantic ink; palette and geometry retained. */
.menu.ni-popup .popup-menu-content {{
  background-color:{rgba(colors["surface"], style["island_alpha"])};
  color:{colors["foreground"]}; border-color:{colors["border"]};
}}
.ni-compact, .ni-compact-clock, .ni-display, .ni-metric, .ni-error,
.ni-content, .ni-title, .ni-text, .ni-button-label, .ni-day,
.ni-cover, .ni-cover StIcon, .ni-top-volume {{ color:{colors["foreground"]}; }}
.ni-muted, .ni-caption, .ni-compact-side, .ni-status-label,
.ni-day-heading, .ni-status-error {{ color:{colors["muted"]}; }}
.ni-button:focus, .ni-transport-button:focus, .ni-tab:focus,
.ni-close:focus, .ni-record-tile:focus {{ border-color:{colors["focus"]}; }}
.slider.ni-slider {{
  color:{colors["focus"]};
  -slider-background-color:{colors["background"]};
  -slider-border-color:{colors["background"]};
  -slider-active-background-color:{colors["focus"]};
  -slider-active-border-color:{colors["focus"]};
}}
.ni-status, .ni-record-tile {{
  background-color:{colors["selection"]}; color:{colors["selection_foreground"]};
}}
.ni-status StLabel, .ni-status StIcon,
.ni-record-tile StLabel, .ni-record-tile StIcon {{ color:{colors["selection_foreground"]}; }}
.ni-button:hover, .ni-transport-button:hover, .ni-tab:hover,
.ni-close:hover, .ni-status:hover, .ni-record-tile:hover {{
  background-color:{colors["hover"]}; color:{colors["foreground"]};
}}
.ni-status:hover StLabel, .ni-status:hover StIcon,
.ni-record-tile:hover StLabel, .ni-record-tile:hover StIcon,
.ni-button:hover StLabel, .ni-button:hover StIcon,
.ni-transport-button:hover StLabel, .ni-transport-button:hover StIcon,
.ni-tab:hover StLabel, .ni-tab:hover StIcon,
.ni-close:hover StLabel, .ni-close:hover StIcon {{ color:{colors["foreground"]}; }}
.ni-weather-card, .ni-day.ni-today, .ni-tab:checked,
.ni-tab:checked:hover, .ni-button:active, .ni-button:active:hover,
.ni-transport-button:active, .ni-transport-button:active:hover {{
  background-color:{colors["selection"]}; color:{colors["selection_foreground"]};
}}
.ni-weather-card StLabel, .ni-weather-card StIcon,
.ni-tab:checked StLabel, .ni-tab:checked StIcon,
.ni-tab:checked:hover StLabel, .ni-tab:checked:hover StIcon,
.ni-button:active StLabel, .ni-button:active StIcon,
.ni-button:active:hover StLabel, .ni-button:active:hover StIcon,
.ni-transport-button:active StLabel, .ni-transport-button:active StIcon,
.ni-transport-button:active:hover StLabel, .ni-transport-button:active:hover StIcon {{ color:{colors["selection_foreground"]}; }}
'''
        # The existing disabled ink clears the text floor on surface in every
        # completed palette (BARBATOS does not clear it on raised). Include
        # combined states so checked/active child ink cannot defeat disabled.
        disabled = (".ni-button", ".ni-status", ".ni-record-tile",
                    ".ni-transport-button", ".ni-tab", ".ni-close")
        states = tuple(selector + suffix for selector in disabled
                       for suffix in (":disabled", ":disabled:hover",
                                      ":disabled:active", ":disabled:active:hover",
                                      ":disabled:checked", ":disabled:checked:hover"))
        rendered += ",\n".join(states) + f''' {{
  background-color:{colors["surface"]}; color:{colors["disabled"]};
}}
'''
        rendered += ",\n".join(selector + child for selector in states
                              for child in (" StLabel", " StIcon"))
        rendered += f''' {{ color:{colors["disabled"]}; }}
'''
    return rendered


def island_applet(profile: dict[str, Any]) -> str:
    source = OCEAN_APPLET.read_text(encoding="utf-8")
    if profile.get('island_inline_style_updates'):
        updates = profile['island_inline_style_updates']
        expected = {
            'actor': ('this.actor', 'background-color:transparent; color:#354D64; border-radius:18px; box-shadow:none; padding:0; margin:0;'),
            'compact': ('this._compact', 'background-color:transparent;'),
            'compact_weather': ('this._compactWeather', 'color:#304957;'),
            'compact_clock': ('this._compactClock', 'color:#354D64;'),
            'compact_status': ('this._compactStatus', 'color:#304957;'),
        }
        if set(updates) != set(expected):
            raise ValueError('Unknown or missing reviewed Island inline appearance fields')
        substitutions = {}
        for key, (actor, literal) in expected.items():
            value = updates[key]
            if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9:#;. %,-]+', value):
                raise ValueError('Unsafe Island inline appearance declaration')
            before = actor + ".set_style('" + literal + "');"
            placeholder = '__dts_candidate_' + key + '__'
            if source.count(before) != 1:
                raise ValueError('Island inline appearance source changed: ' + key)
            source = source.replace(before, actor + ".set_style('" + placeholder + "');")
            substitutions[placeholder] = value
        paints = profile['island_paints']
        for prefix, literal, key in (('', '.314,.365,.4,1', 'meter_track'),
                                     ('if(Number.isFinite(value)){', '.616,.776,.765,1', 'meter_active'),
                                     ('if(i/bars<progress)', '.616,.776,.765,1', 'wave_active'),
                                     ('else ', '.412,.467,.451,1', 'wave_idle')):
            before = prefix + 'cr.setSourceRGBA(' + literal + ')'
            if source.count(before) != 1 or not re.fullmatch(r'#[0-9a-fA-F]{6}', paints[key]):
                raise ValueError('Island reviewed drawing paint differs: ' + key)
            marker = '__dts_candidate_paint_' + key + '__'
            channels = ','.join(f'{value / 255:.6f}' for value in rgb(paints[key]))
            source = source.replace(before, prefix + 'cr.setSourceRGBA(' + marker + ')')
            substitutions[marker] = channels + ',1'
        source = recolor_ocean(source, profile)
        for marker, value in substitutions.items():
            source = source.replace(marker, value)
        return source
    if profile.get("legacy_completion"):
        colors = palette(profile)
        # Placeholders avoid feeding newly inserted palette colors through
        # recolor_ocean again when a canonical value equals an Ocean literal.
        roles = ("foreground", "island_background", "island_foreground",
                 "background", "focus")
        for role in roles:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", colors[role]):
                raise ValueError(f"Invalid legacy Island {role}: {colors[role]!r}")
        for before, after, count in (
            ("color:#354D64;", "color:__legacy_island_foreground__;", 2),
            ("this._compact.set_style('background-color:transparent;');",
             "this._compact.set_style('background-color:__legacy_island_island_background__;');", 1),
            ("color:#304957;", "color:__legacy_island_island_foreground__;", 2),
            ("color:#874B4B;", "color:__legacy_island_foreground__;", 1),
        ):
            if source.count(before) != count:
                raise ValueError("Legacy Island inline source changed; review its ink mapping")
            source = source.replace(before, after)
        # Drawing-area paints bypass CSS. A light control-border track can
        # hide the active fill, so meter and waveform inactive paint uses the
        # canonical background. Guard each site independently: the original
        # identical active literals both use focus for meter and waveform
        # progress. Keep all operations, coordinates and timers.
        for active in ("focus",):
            if min(contrast(colors[active], colors[backdrop])
                   for backdrop in ("background", "raised")) < 3:
                raise ValueError("Legacy Island drawing contrast changed; review its palette mapping")
        for prefix, literal, role in (
            ("", ".314,.365,.4,1", "background"),
            ("if(Number.isFinite(value)){", ".616,.776,.765,1", "focus"),
            ("if(i/bars<progress)", ".616,.776,.765,1", "focus"),
            ("else ", ".412,.467,.451,1", "background"),
        ):
            before = prefix + "cr.setSourceRGBA(" + literal + ")"
            if source.count(before) != 1:
                raise ValueError("Legacy Island paint source changed; review its palette mapping")
            channels = ",".join(f"{value / 255:.6f}" for value in rgb(colors[role]))
            source = source.replace(before, prefix + "cr.setSourceRGBA(" + channels + ",1)")
        source = recolor_ocean(source, profile)
        for role in roles:
            source = source.replace("__legacy_island_" + role + "__", colors[role])
        return source
    if "accent_text" in profile["palette"]:
        # These inline declarations are text colors and outrank the CSS layer.
        source = re.sub(r"(?<![-\w])color\s*:\s*#354D64", "color:" + profile["palette"]["accent_text"],
                        source, flags=re.IGNORECASE)
        # Cairo drawings bypass the stylesheet, so map their paint too.
        for literal, role, count in ((".314,.365,.4,1", "border", 1),
                                     (".616,.776,.765,1", "accent_text", 2),
                                     (".412,.467,.451,1", "muted", 1)):
            before = "cr.setSourceRGBA(" + literal + ")"
            if source.count(before) != count:
                raise ValueError("Island paint source changed; review its palette mapping")
            channels = ",".join(f"{value / 255:.6f}" for value in rgb(profile["palette"][role]))
            source = source.replace(before, "cr.setSourceRGBA(" + channels + ",1)")
    if "island_background" in profile["palette"]:
        # A gilt-set cabochon (design.json collection_dependencies.island (a)): the compact
        # box's own inline background outranks the CSS layer, exactly like the accent_text
        # case above; the applet actor itself is a different literal and is left untouched,
        # so it stays transparent. The clock's inline text color maps to accent (not
        # accent_text: this substitution is independent of the block above, and a profile
        # that set both keys would have accent_text's regex run first). The weather/status
        # inline text colors map to island_foreground.
        colors = profile["palette"]
        for role in ("island_background", "accent", "island_foreground"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", colors[role]):
                raise ValueError(f"Invalid hex color for island {role}: {colors[role]!r}")
        for before, after in (
            ("this._compact.set_style('background-color:transparent;');",
             "this._compact.set_style('background-color:" + colors["island_background"] + ";');"),
            ("this._compactClock.set_style('color:#354D64;');",
             "this._compactClock.set_style('color:" + colors["accent"] + ";');"),
            ("this._compactWeather.set_style('color:#304957;');",
             "this._compactWeather.set_style('color:" + colors["island_foreground"] + ";');"),
            ("this._compactStatus.set_style('color:#304957;');",
             "this._compactStatus.set_style('color:" + colors["island_foreground"] + ";');"),
        ):
            if source.count(before) != 1:
                raise ValueError("Island applet source changed; review its palette mapping")
            source = source.replace(before, after)
    return recolor_ocean(source, profile)


def render_kitty(slug: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    values: list[tuple[str, str]] = [
        ("font_family", "Hack"),
        ("font_size", f"{float(profile.get('terminal_font_size', 11)):.1f}"),
        ("background_opacity", "1.0"),
        ("background", colors["terminal"]),
        ("foreground", colors["terminal_foreground"]),
        ("cursor", colors["accent"]),
        ("cursor_text_color", colors.get("cursor_text", colors["terminal"])),
        ("selection_background", colors["selection"]),
        ("selection_foreground", colors["selection_foreground"]),
        ("active_tab_background", colors["surface"]),
        ("active_tab_foreground", colors["foreground"]),
        ("inactive_tab_background", colors["terminal"]),
        ("inactive_tab_foreground", profile["ansi"][8]),
        ("url_color", colors.get("accent_text", colors["accent"])),
    ]
    values.extend((f"color{index}", value) for index, value in enumerate(profile["ansi"]))
    return f"# {profile['name']} appearance only; custom shell and music commands are untouched.\n" + "\n".join(f"{key} {value}" for key, value in values) + "\n"


def render_konsole(slug: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    rows = ["[General]", f"Description={profile['name']}", "Opacity=1"]
    for section, value in [("Background", colors["terminal"]), ("Foreground", colors["terminal_foreground"])] + [
        (f"Color{index % 8}" + ("Intense" if index >= 8 else ""), color) for index, color in enumerate(profile["ansi"])
    ]:
        rows.extend(("", f"[{section}]", f"Color={color_csv(value)}"))
    return "\n".join(rows) + "\n"


def render_ptyxis(slug: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    rows = ["[Palette]", f"Name={profile['name']}"]
    for section in ("Light", "Dark"):
        rows.extend(("", f"[{section}]", f"Background={colors['terminal']}", f"Foreground={colors['terminal_foreground']}", f"Cursor={colors['accent']}"))
        rows.extend(f"Color{index}={value}" for index, value in enumerate(profile["ansi"]))
    return "\n".join(rows) + "\n"


def render_gtk(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    return f'''/* {profile["name"]} GTK application surface. */
window, dialog, .background {{ background-color: {colors["surface"]}; color: {colors["foreground"]}; }}
headerbar {{ background-color: {colors["raised"]}; color: {colors["foreground"]}; border-color: {colors["border"]}; }}
entry, textview, .view {{ background-color: {colors["terminal"]}; color: {colors["terminal_foreground"]}; caret-color: {colors["accent"]}; }}
button {{ background-color: {colors["raised"]}; color: {colors["foreground"]}; border-color: {colors["border"]}; }}
button:hover {{ background-color: {colors["hover"]}; }}
button:checked, button:active {{ background-color: {colors["selection"]}; color: {colors["selection_foreground"]}; }}
selection {{ background-color: {colors["selection"]}; color: {colors["selection_foreground"]}; }}
'''


def render_ptyxis_css(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    named_roles = {
        'theme_fg_color': 'foreground', 'theme_text_color': 'foreground',
        'theme_bg_color': 'surface', 'theme_base_color': 'terminal',
        'theme_selected_bg_color': 'selection', 'theme_selected_fg_color': 'selection_foreground',
        'window_fg_color': 'foreground', 'window_bg_color': 'surface',
        'view_fg_color': 'terminal_foreground', 'view_bg_color': 'terminal',
        'headerbar_fg_color': 'foreground', 'headerbar_bg_color': 'raised',
        'headerbar_backdrop_color': 'surface', 'headerbar_border_color': 'border',
        'accent_color': 'accent_text' if 'accent_text' in colors else 'accent',
        'accent_bg_color': 'selection', 'accent_fg_color': 'selection_foreground',
    }
    named = '\n'.join(f'@define-color {name} {colors[role]};' for name, role in named_roles.items())
    return f'''/* {profile["name"]} Ptyxis application chrome; load as a profile-specific append block. */
{named}
:root {{
  --accent-bg-color: {colors["selection"]};
  --accent-fg-color: {colors["selection_foreground"]};
  --accent-color: {colors.get("accent_text", colors["accent"])};
  --window-bg-color: {colors["surface"]};
  --window-fg-color: {colors["foreground"]};
  --view-bg-color: {colors["terminal"]};
  --view-fg-color: {colors["terminal_foreground"]};
  --headerbar-bg-color: {colors["raised"]};
  --headerbar-fg-color: {colors["foreground"]};
  --headerbar-backdrop-color: {colors["surface"]};
  --headerbar-border-color: {colors["border"]};
  --sidebar-bg-color: {colors["raised"]};
  --sidebar-fg-color: {colors["foreground"]};
  --sidebar-backdrop-color: {colors["surface"]};
  --card-bg-color: {colors["raised"]};
  --card-fg-color: {colors["foreground"]};
  --dialog-bg-color: {colors["surface"]};
  --dialog-fg-color: {colors["foreground"]};
  --popover-bg-color: {colors["surface"]};
  --popover-fg-color: {colors["foreground"]};
}}
window {{ background-color: {colors["surface"]}; color: {colors["foreground"]}; }}
headerbar {{ background-color: {colors["raised"]}; background-image: none; color: {colors["foreground"]}; border-color: {colors["border"]}; }}
headerbar label, headerbar image, headerbar button, .titlebar label, .titlebar image, .titlebar button {{ color: {colors["foreground"]}; }}
headerbar button:checked, headerbar button:checked label, headerbar button:checked image {{ color: {colors["selection_foreground"]}; }}
terminal, vte-terminal {{ background-color: {colors["terminal"]}; color: {colors["terminal_foreground"]}; }}
button:checked, button:active {{ background-color: {colors["selection"]}; color: {colors["selection_foreground"]}; }}
'''


def render_sourceview(slug: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    roles = {
        "text": (colors["foreground"], colors["surface"]),
        "selection": (colors["selection_foreground"], colors["selection"]),
        "current-line": (None, colors["raised"]),
        "line-numbers": (colors["muted"], colors["raised"]),
        "def:comment": (colors["muted"], None),
        "def:keyword": (colors.get("accent_text", colors["accent"]), None),
        "def:string": (colors["success"], None),
        "def:number": (colors["warning"], None),
        "def:error": (colors["error"], None),
    }
    rows = [f'<?xml version="1.0" encoding="UTF-8"?>', f'<style-scheme id="cinnamon-current-{slug}" name="{profile["name"]}" version="1.0"><author>Desktop Theme Studio</author>']
    for name, (foreground, background) in roles.items():
        attrs = [f'name="{name}"']
        if foreground:
            attrs.append(f'foreground="{foreground}"')
        if background:
            attrs.append(f'background="{background}"')
        rows.append("<style " + " ".join(attrs) + "/>")
    rows.append("</style-scheme>")
    return "\n".join(rows) + "\n"


def render_kde(slug: str, profile: dict[str, Any]) -> str:
    colors = palette(profile)
    rows = [f"# {profile['name']} interface-only color scheme", "[General]", f"Name={profile['name']}", f"ColorScheme=CinnamonCurrent{slug.title().replace('-', '')}"]
    for section, background, foreground in (
        ("View", colors["surface"], colors["foreground"]),
        ("Window", colors["surface"], colors["foreground"]),
        ("Button", colors["raised"], colors["foreground"]),
        ("Tooltip", colors["raised"], colors["foreground"]),
        ("Selection", colors["selection"], colors["selection_foreground"]),
        ("Header", colors["raised"], colors["foreground"]),
    ):
        selected = section == "Selection"
        def text_color(role: str) -> str:
            return color_csv(foreground if selected else colors[role])
        rows.extend(("", f"[Colors:{section}]", f"BackgroundNormal={color_csv(background)}", f"BackgroundAlternate={color_csv(background if selected else colors['raised'])}", f"ForegroundNormal={color_csv(foreground)}", f"ForegroundInactive={text_color('muted')}", f"ForegroundActive={text_color('accent_text' if 'accent_text' in colors else 'accent')}", f"ForegroundNegative={text_color('error')}", f"ForegroundNeutral={text_color('warning')}", f"ForegroundPositive={text_color('success')}", f"DecorationFocus={color_csv(colors['accent'])}", f"DecorationHover={color_csv(colors['hover'])}"))
    return "\n".join(rows) + "\n"


def render_nano(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    return f'''# {profile["name"]} Nano color fragment; do not replace non-color user preferences.
set titlecolor bold,{colors["terminal"]},{colors["success"]}
set statuscolor {colors["terminal_foreground"]},{colors["terminal"]}
set errorcolor {colors["terminal"]},{colors["error"]}
set promptcolor {colors["terminal_foreground"]},{colors["terminal"]}
set selectedcolor {colors["selection_foreground"]},{colors["selection"]}
set numbercolor {colors["muted"]}
set keycolor {colors.get("accent_text", colors["accent"])}
set functioncolor {colors["terminal_foreground"]}
'''


def render_tilda(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    return f'''# {profile["name"]} Tilda color fragment; it must be merged only while Tilda is closed.
enable_transparency=false
back_alpha=65535
back_red={color_u16(colors["terminal"]).split(",")[0]}
back_green={color_u16(colors["terminal"]).split(",")[1]}
back_blue={color_u16(colors["terminal"]).split(",")[2]}
text_red={color_u16(colors["terminal_foreground"]).split(",")[0]}
text_green={color_u16(colors["terminal_foreground"]).split(",")[1]}
text_blue={color_u16(colors["terminal_foreground"]).split(",")[2]}
cursor_red={color_u16(colors["accent"]).split(",")[0]}
cursor_green={color_u16(colors["accent"]).split(",")[1]}
cursor_blue={color_u16(colors["accent"]).split(",")[2]}
palette={{{", ".join(str(value * 257) for color in profile["ansi"] for value in rgb(color))}}}
'''


def render_cava(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    return f'''# {profile["name"]} appearance fragment for Cava; sampling and music behavior are untouched.
gradient = 1
gradient_count = 3
gradient_color_1 = '{colors.get("accent_text", colors["accent"])}'
gradient_color_2 = '{colors["success"]}'
gradient_color_3 = '{colors["warning"]}'
background = '{colors["terminal"]}'
foreground = '{colors["terminal_foreground"]}'
'''


def render_gtile(profile: dict[str, Any]) -> str:
    colors = palette(profile)
    return f'''/* {profile["name"]} gTile appearance; geometry and key bindings remain untouched. */
.grid-panel {{ background-color:{colors["surface"]}; border:1px solid {colors["border"]}; border-radius:{profile["style"]["radius"]}px; }}
.grid-title,.grid-panel .settings-label,.grid-panel .tile-label {{ color:{colors["foreground"]}; }}
.table-element,.settings-button {{ background-color:{colors["raised"]}; color:{colors["foreground"]}; border:1px solid {colors["border"]}; }}
.table-element:hover,.table-element:activate,.settings-button:hover,.settings-button:activate,.settings-button:activate:hover {{ background-color:{colors["selection"]}; color:{colors["selection_foreground"]}; border-color:{colors["accent"]}; }}
.table-element:hover .tile-label,.table-element:activate .tile-label,.settings-button:hover .settings-label,.settings-button:activate .settings-label,.settings-button:activate:hover .settings-label {{ color:{colors["selection_foreground"]}; }}
.grid-preview:activate {{ background-color:{rgba(colors["accent"], 0.30)}; border:2px solid {colors["accent"]}; }}
'''


def application_plan(slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    name = profile["name"]
    return {
        "profile": slug,
        "policy": "Staged only. Apply individual assets only after their host application is closed normally and the active profile is explicitly selected.",
        "assets": [
            {"name": "Kitty", "source": f"kitty/cinnamon-current-{slug}.conf", "target": f"~/.config/kitty/cinnamon-current-{slug}.conf", "action": "add an include; retain all other Kitty preferences"},
            {"name": "Konsole", "source": f"konsole/CinnamonCurrent{slug.title().replace('-', '')}.colorscheme", "target": f"~/.local/share/konsole/CinnamonCurrent{slug.title().replace('-', '')}.colorscheme", "action": "select only in the active Konsole profile"},
            {"name": "Ptyxis", "source": f"ptyxis/cinnamon-current-{slug}.palette", "target": f"~/.local/share/org.gnome.Ptyxis/palettes/cinnamon-current-{slug}.palette", "requires_closed": True},
            {"name": "Ptyxis chrome", "source": "ptyxis/gtk.css", "target": "profile-specific GTK4 append block", "requires_closed": True},
            {"name": "GtkSourceView/Xed", "source": f"sourceview/cinnamon-current-{slug}.xml", "target": "~/.local/share/gtksourceview-*/styles/", "action": "select scheme after installation"},
            {"name": "KDE/Krita", "source": f"kde/CinnamonCurrent{slug.title().replace('-', '')}.colors", "target": "~/.local/share/color-schemes/", "action": "select interface scheme only"},
            {"name": "Nano", "source": "nano/colors.rc", "target": "~/.nanorc", "action": "merge color lines only"},
            {"name": "Tilda", "source": "tilda/colors.ini", "target": "~/.config/tilda/config_0", "requires_closed": True, "action": "merge color keys only"},
            {"name": "Cava", "source": "cava/colors.conf", "target": "existing Cava color block", "action": "merge appearance keys only"},
            {"name": "gTile", "source": "gtile/stylesheet.css", "target": "gTile extension stylesheet", "action": "replace current collection appearance block only"},
            {"name": "Fastfetch short/long", "source": "fastfetch/identity.json", "target": "existing Fastfetch theme selector", "action": "preserve mode wrappers and required local-IP format"},
            *([{"name": "Eww profile style", "source": "widgets/eww-style.scss", "target": "shared Eww stylesheet managed import", "action": "keep shared Yuck behavior; replace only the owned collection-style import"}] if profile.get("sources", {}).get("eww_stylesheet") else []),
        ],
        "fresh_application_checks": ["Kitty", "GNOME Terminal", "Konsole if installed", "Ptyxis after normal closure", "Xed", "Krita if installed", "Tilda after normal closure", "Fastfetch short", "Fastfetch long"],
        "protected": ["~/.bashrc", "~/.local/bin/play", "~/.local/bin/playalbum", "~/.local/bin/pause", "~/.local/bin/next", "~/.local/bin/prev", "~/.local/bin/nextalbum", "~/.local/bin/prevalbum", "~/.local/bin/mpv-vis"],
        "notes": f"{name} has staged color assets only; no user application preferences are modified during collection build.",
    }


def activation_plan(slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    contract = isolated_contract(profile)
    island_id = profile.get("island_uuid", "nothing-island@desktop-theme-studio")
    narrow_assets = [
        {"from": f"generated/{slug}/desktop/{profile['theme']}", "to": f"~/.themes/{profile['theme']}", "operation": "replace only this profile tree after backup"},
        {"from": f"generated/{slug}/island/{island_id}/applet.js", "to": f"~/.local/share/cinnamon/applets/{island_id}/applet.js", "operation": "replace palette-bearing source only"},
        {"from": f"generated/{slug}/island/{island_id}/stylesheet.css", "to": f"~/.local/share/cinnamon/applets/{island_id}/stylesheet.css", "operation": "replace palette-bearing source only"},
        {"from": f"generated/{slug}/artwork/menu-logo.png", "to": "selected Cinabon menu-icon setting", "operation": "discover instance first; update only menu-icon value"},
        {"from": f"generated/{slug}/artwork/wallpaper.png", "to": "org.cinnamon.desktop.background picture-uri", "operation": "set only selected wallpaper URI"},
    ]
    if profile.get("island_source"):
        narrow_assets[1:3] = [{"from": f"generated/{slug}/island/{island_id}",
                              "to": f"~/.local/share/cinnamon/applets/{island_id}",
                              "operation": "install complete authored applet tree with conflict-checked recovery"}]
    if profile.get("sources", {}).get("eww_stylesheet"):
        narrow_assets.append({
            "from": f"generated/{slug}/widgets/eww-style.scss",
            "to": "Eww shared stylesheet managed import",
            "operation": "install a reversible profile class-rule layer; preserve shared Yuck and playback behavior",
        })
    plan = {
        "profile": slug,
        "policy": "Read-only plan. A coordinator must create a fresh receipt and recovery timer before applying this plan.",
        "narrow_assets": narrow_assets,
        "settings_intent": {
            "org.cinnamon.desktop.interface gtk-theme": profile["theme"],
            "org.cinnamon.desktop.interface icon-theme": profile["icons"],
            "org.cinnamon.desktop.interface cursor-theme": profile["cursor"],
            "org.cinnamon.theme name": profile["theme"],
            "org.cinnamon.desktop.wm.preferences theme": profile["theme"],
        },
        "must_not_touch": ["enabled-applets", "panel placement", "custom music commands", "shell configuration", "Eww playback and system-control behavior", "existing generic opacity service", "Ocean-Silk-Glass theme tree"],
        "runtime_controller": "live.py provides receipt-backed desktop, application, and Eww appearance actions; trial.py performs serial verification and restore",
        "isolated_gate": {
            "contract": contract["kind"],
            "required_checks": contract["required_checks"],
            "source_sha256": contract.get("source_fingerprint", {}).get("sha256"),
            "receipt_field": contract.get("source_fingerprint", {}).get("receipt_field"),
            "fingerprint_exclusions": (
                ["directories named __pycache__", "files ending .pyc", "files ending .pyo"]
                if contract.get("source_fingerprint", {}).get("exclude_python_cache") else []
            ),
        },
        "requires": ["fresh backup", "300-second recovery timer", "normal closure of Ptyxis and Tilda before related app preferences", "one serial Cinnamon reload", "fresh visual and application checks"],
        "status": "not executed by this collection builder",
    }
    if slug == "tidal-observatory":
        plan["must_not_touch"] = [item for item in plan["must_not_touch"] if item not in ("enabled-applets", "panel placement")]
        plan["panel_layout"] = {"kind": "tidal-two-panel", "top_height": 46, "bottom_height": 66,
            "behavior": "Journal existing applets and menu geometry, preserve favorites, add missing native dock roles, restore previous layout when leaving Tidal",
            "controller": "contributions/tidal-integration/desktop/tidal_switch.py"}
    layout = rail_layout(profile)
    return plan if layout is None else rail_activation_plan(slug, layout, plan)


def render_fastfetch_identity(slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": slug,
        "name": profile["name"],
        "logo": f"macklenmobile-logo-{slug}.png",
        "short": {"uses_existing_mode_wrapper": True, "palette": profile["ansi"]},
        "long": {
            "uses_existing_mode_wrapper": True,
            "required_modules": ["OS", "HOST", "KERNEL", "SHELL", "DE", "WM", "THEME", "ICONS", "TERMINAL", "TERMINAL FONT", "CPU", "GPU", "DISPLAY", "MEM", "SWAP", "DISK", "LOCAL IP"],
            "local_ip_format": "86.75-309",
        },
        "guard": "Do not replace modes.py, long.jsonc, or user-defined Fastfetch wrappers.",
    }


def output_files(path: Path) -> dict[str, str]:
    return {item.relative_to(path).as_posix(): sha256(item) for item in sorted(path.rglob("*")) if item.is_file() and item.name != "manifest.json"}


def copy_asset(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Missing required source asset: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    copy_fresh(source, target)


def render_eww_style(source: Path) -> str:
    """Package only reviewed Eww class rules; profile tokens stay centrally managed.

    The current runtime imports this file after its common stylesheet and its
    theme-variable block. Source assets/imports are rejected here because a
    theme package must not resolve relative or home-directory resources from
    outside its immutable generated tree.
    """
    raw = source.read_text(encoding="utf-8")
    start_marker = "// THEME_VARIABLES_START"
    end_marker = "// THEME_VARIABLES_END"
    if raw.count(start_marker) != 1 or raw.count(end_marker) != 1:
        raise ValueError(f"Eww stylesheet must contain one token block: {source}")
    start = raw.index(start_marker)
    finish = raw.index(end_marker, start) + len(end_marker)
    rules = (raw[:start] + raw[finish:]).strip()
    if not rules:
        raise ValueError(f"Eww stylesheet has no theme-specific class rules: {source}")
    if len(rules.encode("utf-8")) > 128 * 1024:
        raise ValueError(f"Eww stylesheet exceeds 128 KiB: {source}")
    if re.search(r"(?im)^\s*@(?:import|use|forward|include|mixin|function)\b", rules):
        raise ValueError(f"Eww stylesheet imports/includes are not allowed: {source}")
    if re.search(r"(?i)url\s*\(", rules):
        raise ValueError(f"Eww stylesheet has external asset references; package them explicitly first: {source}")
    # These theme files are declarative CSS/SCSS rules. A simple structural
    # count catches a truncated source before it is copied into an active import.
    if rules.count("{") != rules.count("}") or rules.count("{") == 0:
        raise ValueError(f"Unbalanced or empty Eww rules: {source}")
    return rules + "\n"


def profile_styles(profile: dict[str, Any], key: str) -> str:
    """Append an explicitly owned material layer after the shared controls."""
    source = profile.get("overrides", {}).get(key)
    if not source:
        return ""
    return "\n\n" + Path(source).read_text(encoding="utf-8").rstrip() + "\n"


def authored_bundle(profile: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    contract = profile.get('authored_eww_bundle')
    if contract is None:
        return None
    if not isinstance(contract, dict) or contract.get('schema') != 1:
        raise ValueError('Unsupported authored Eww bundle contract')
    root = Path(contract['source'])
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Authored Eww bundle needs a regular source directory')
    name = contract['manifest']
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('Unsafe authored Eww manifest path')
    manifest = load_json(root / name)
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported authored Eww file manifest')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if any(p.is_symlink() for p in root.rglob('*')) or actual != set(manifest['files']) | {name}:
        raise ValueError('Authored Eww source closure differs from its manifest')
    for relative, row in manifest['files'].items():
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts or not relative:
            raise ValueError('Unsafe authored Eww source path')
        target = root / rel
        if (hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']
                or stat.S_IMODE(target.stat().st_mode) != row['mode']):
            raise ValueError('Authored Eww source digest or mode differs: ' + relative)
    for field in ('launcher', 'private_launcher'):
        if contract[field] not in manifest['files']:
            raise ValueError('Authored Eww launcher is not manifest bound')
    if not contract.get('windows') or any(not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value) for value in contract['windows']):
        raise ValueError('Unsafe authored Eww window identity')
    return root, manifest


def apply_theme_patches(profile: dict[str, Any], theme: Path) -> None:
    """Apply pinned, exact text changes only within a generated theme tree."""
    source = profile.get("theme_patches")
    if source:
        for patch in load_json(Path(source)):
            relative = Path(patch["path"])
            target = theme / relative
            if (relative.is_absolute() or ".." in relative.parts or target.is_symlink()
                    or not target.resolve().is_relative_to(theme.resolve()) or not target.is_file()):
                raise ValueError(f"Unsafe theme patch target: {relative}")
            before, after = patch["before"], patch["after"]
            text = target.read_text(encoding="utf-8")
            count = patch["count"]
            if not before or not isinstance(count, int) or count < 1 or text.count(before) != count:
                raise ValueError(f"Theme patch no longer matches its source: {relative}")
            write_text(target, text.replace(before, after))
    gtk2 = profile.get("overrides", {}).get("gtk2_rc")
    if gtk2:
        directory = theme / "gtk-2.0"
        copy_asset(Path(gtk2), directory / "studio-controls.rc")
        target = directory / "gtkrc"
        write_text(target, target.read_text(encoding="utf-8").rstrip()
                   + '\n\ninclude "studio-controls.rc"\n')
    gtk = profile_styles(profile, "gtk_css")
    if gtk:
        for version in ("3.0", "4.0"):
            for name in ("gtk.css", "gtk-dark.css"):
                target = theme / ("gtk-" + version) / name
                if target.is_symlink() or not target.is_file():
                    raise ValueError(f"Expected a regular generated GTK stylesheet: {target}")
                write_text(target, target.read_text(encoding="utf-8").rstrip() + gtk)


def ensure_source_profile(profile: dict[str, Any]) -> None:
    for field in ("theme", "icon", "cursor"):
        path = Path(profile["sources"][field])
        if not path.is_dir():
            raise FileNotFoundError(f"Missing required {field} directory: {path}")
    for field in ("wallpaper", "menu_logo", "fastfetch_logo"):
        path = Path(profile["sources"][field])
        if not path.is_file():
            raise FileNotFoundError(f"Missing required {field} file: {path}")


def authored_shell_provenance(profile: dict[str, Any]) -> str:
    return f"/* Desktop Theme Studio: authored Cinnamon shell theme retained for {profile['name']}. */\n"


def copy_system_payload(slug: str, profile: dict[str, Any], output: Path) -> None:
    """Copy an optional, profile-scoped GRUB/Plymouth boot payload into
    generated/<slug>/system-payload/.

    ``profile["system_payload_source"]`` is a STUDIO-relative directory (e.g.
    "presets/<id>/system/collection-payload"); when absent, this is a no-op, so every
    profile without the key is completely untouched. When present, this fails closed:
    every entry under the source directory (including its own manifest.json) must be a
    plain regular file -- no symlinks anywhere in the tree; the manifest (schema 1,
    matching "stage_id", "files") must enumerate every OTHER file in the tree exactly
    once, with a lowercase sha256 matching its bytes, or nothing is written. Only a
    plain copy is used (copy_asset -> copy_fresh -> shutil.copy2; never a hard link or
    symlink). runtime-quality/system-stage/system.py's own _system_payload_files()
    remains the sole authority for the required boot-asset set, the GRUB/Plymouth
    content cross-references, and the live root stage; this step does not duplicate or
    pre-empt that policy -- it only stages the bytes it will later read.
    """
    source_rel = profile.get("system_payload_source")
    if not source_rel:
        return
    source = (STUDIO_ROOT / source_rel).resolve()
    if not source.is_relative_to(STUDIO_ROOT) or source.is_symlink() or not source.is_dir():
        raise FileNotFoundError(f"Missing system payload source directory: {source}")
    manifest_path = source / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError(f"Missing system payload manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "stage_id", "files"}:
        raise ValueError(f"System payload manifest fields are invalid: {manifest_path}")
    if manifest.get("schema") != 1:
        raise ValueError(f"Unsupported system payload manifest schema: {manifest_path}")
    expected_stage_id = f"cinnamon-current-{slug}"
    if manifest.get("stage_id") != expected_stage_id:
        raise ValueError(
            f"System payload stage_id mismatch: {manifest.get('stage_id')!r} != {expected_stage_id!r}")
    entries = manifest.get("files")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"System payload file map is empty or invalid: {manifest_path}")
    files: dict[str, str] = {}
    for relative, expected_hash in entries.items():
        if (not isinstance(relative, str) or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            raise ValueError(f"System payload contains an invalid path or checksum: {relative!r}")
        relative_path = Path(relative)
        if ("\\" in relative or relative_path.is_absolute()
                or relative_path.as_posix() != relative or ".." in relative_path.parts):
            raise ValueError(f"Unsafe system payload path: {relative}")
        if relative in files:
            raise ValueError(f"Duplicate system payload path: {relative}")
        files[relative] = expected_hash
    actual_names: set[str] = set()
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"System payload source cannot contain symlinks: {item}")
        if item.is_file() and item != manifest_path:
            actual_names.add(item.relative_to(source).as_posix())
    if actual_names != set(files):
        raise ValueError(f"System payload inventory differs from its manifest: {manifest_path}")
    for relative, expected_hash in files.items():
        item = source / relative
        if sha256(item) != expected_hash:
            raise ValueError(f"System payload checksum differs: {relative}")
    destination = output / "system-payload"
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"build_profile needs an absent system-payload directory: {destination}")
    for relative in files:
        copy_asset(source / relative, destination / relative)
    copy_asset(manifest_path, destination / "manifest.json")


def copy_completion_applications(slug: str, profile: dict[str, Any], applications: Path) -> None:
    """Use the reviewed legacy app fragments through existing narrow adapters.

    Profiles without the opt-in key retain their original generated bytes.
    Supplemental custom app assets remain in the separate component export.
    """
    relative = profile.get("completion_applications_source")
    if not relative:
        return
    source = (STUDIO_ROOT / relative).resolve()
    approved = STUDIO_ROOT / "refinements/legacy-completion-v1/themes" / slug / "applications"
    if source != approved.resolve() or source.is_symlink() or not source.is_dir():
        raise ValueError("Completion application source is outside this profile's staged tree")
    scheme = "CinnamonCurrent" + slug.title().replace("-", "")
    authored_scheme = "LegacyCompletion" + slug.title().replace("-", "")
    mapping = {
        f"kitty/legacy-{slug}.conf": f"kitty/cinnamon-current-{slug}.conf",
        f"konsole/{authored_scheme}.colorscheme": f"konsole/{scheme}.colorscheme",
        f"ptyxis/legacy-{slug}.palette": f"ptyxis/cinnamon-current-{slug}.palette",
        "gtk/gtk-3.css": "gtk/application.css",
        "gtk/gtk-4.css": "ptyxis/gtk.css",
        f"sourceview/legacy-{slug}.xml": f"sourceview/cinnamon-current-{slug}.xml",
        f"kde/{authored_scheme}.colors": f"kde/{scheme}.colors",
        "nano/colors.rc": "nano/colors.rc",
        "tilda/colors.ini": "tilda/colors.ini",
        "cava/colors.conf": "cava/colors.conf",
    }
    for source_name, target_name in mapping.items():
        item = source / source_name
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"Missing regular completion app fragment: {item}")
        text = item.read_text(encoding="utf-8")
        if source_name.startswith("sourceview/"):
            old = f'id="legacy-{slug}"'
            if text.count(old) != 1:
                raise ValueError("Completion syntax scheme identity is ambiguous")
            text = text.replace(old, f'id="cinnamon-current-{slug}"')
        if source_name.startswith("kde/"):
            text = text.replace(f"ColorScheme={authored_scheme}", f"ColorScheme={scheme}")
        write_text(applications / target_name, text)


def build_profile(slug: str, profile: dict[str, Any], eww: dict[str, Any],
                  output: Path | None = None, lookup: HashLookup | None = None,
                  digests_out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one profile package into a fresh directory (default GENERATED/slug).

    Every write starts from source bytes: the output must be absent or empty,
    so appended layers (overlay, provenance marker, GTK/GTK2 includes) can
    never accumulate across builds.  ``lookup`` supplies already-known source
    digests; it never changes output bytes.  ``digests_out`` receives each
    generated file's digest and the stat it was read at (hash once).
    """
    ensure_source_profile(profile)
    if profile.get("eww_palette_source"):
        eww = {**eww, **load_json(Path(profile["eww_palette_source"]))}
    key = eww_palette_key(profile)
    if key not in eww:
        raise KeyError(f"Eww does not have a palette for {key}")
    if not OCEAN_ISLAND.is_dir() or not OCEAN_APPLET.is_file() or not OCEAN_STYLESHEET.is_file():
        raise FileNotFoundError("The verified current Ocean island source is unavailable")

    output = GENERATED / slug if output is None else Path(output)
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError(f"build_profile needs an absent or empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    theme_source = Path(profile["sources"]["theme"])
    theme_output = output / "desktop" / profile["theme"]
    copytree_fresh(theme_source, theme_output)
    apply_theme_patches(profile, theme_output)
    cinnamon_css = theme_output / "cinnamon" / "cinnamon.css"
    if not cinnamon_css.is_file():
        raise FileNotFoundError(f"{profile['theme']} does not provide cinnamon/cinnamon.css")
    if not profile.get("preserve_authored_shell", False):
        current_css = cinnamon_css.read_text(encoding="utf-8")
        if MARKER in current_css:
            current_css = current_css.split(MARKER, 1)[0].rstrip() + "\n"
        current_css = recolor_legacy_shell(current_css, profile)
        write_text(cinnamon_css, current_css.rstrip() + "\n\n" + panel_overlay(profile)
                   + profile_styles(profile, "cinnamon_css"))
    else:
        # Imported theme rails/docks often have richer per-selector geometry
        # than the collection's scalar panel overlay can express. Keep those
        # authored rules byte-for-byte and add only an explicit provenance
        # marker plus any separately reviewed Cinnamon component stylesheet.
        current_css = cinnamon_css.read_text(encoding="utf-8")
        provenance = authored_shell_provenance(profile)
        if provenance.rstrip("\n") in current_css:
            # A source frozen from an earlier generated tree already carries
            # the marker (and any component layer after it); append it once.
            current_css = current_css.split(provenance.rstrip("\n"), 1)[0]
        write_text(cinnamon_css, current_css.rstrip() + "\n\n" + provenance
                   + legacy_palette_shell_styles(profile)
                   + completion_shell_styles(profile) + profile_styles(profile, "cinnamon_css"))

    island_id = profile.get("island_uuid", "nothing-island@desktop-theme-studio")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9][A-Za-z0-9._-]{0,63}", island_id):
        raise ValueError("Unsafe Island UUID")
    island_output = output / "island" / island_id
    if profile.get("island_source"):
        copytree_fresh(Path(profile["island_source"]), island_output)
        if load_json(island_output / "metadata.json").get("uuid") != island_id:
            raise ValueError("Authored Island metadata UUID mismatch")
    else:
        copytree_fresh(OCEAN_ISLAND, island_output)
        write_text(island_output / "applet.js", island_applet(profile))
        write_text(island_output / "stylesheet.css", island_styles(profile)
                   + profile_styles(profile, "island_css"))

    artwork = output / "artwork"
    copy_asset(Path(profile["sources"]["wallpaper"]), artwork / "wallpaper.png")
    copy_asset(Path(profile["sources"]["menu_logo"]), artwork / "menu-logo.png")
    copy_asset(Path(profile["sources"]["fastfetch_logo"]), artwork / f"macklenmobile-logo-{slug}.png")
    if "progress_fill" in profile["sources"]:
        copy_asset(Path(profile["sources"]["progress_fill"]), artwork / "progress-fill.png")

    applications = output / "applications"
    write_text(applications / "kitty" / f"cinnamon-current-{slug}.conf", render_kitty(slug, profile))
    write_text(applications / "konsole" / f"CinnamonCurrent{slug.title().replace('-', '')}.colorscheme", render_konsole(slug, profile))
    write_text(applications / "ptyxis" / f"cinnamon-current-{slug}.palette", render_ptyxis(slug, profile))
    write_text(applications / "ptyxis" / "gtk.css", render_ptyxis_css(profile))
    write_text(applications / "gtk" / "application.css", render_gtk(profile))
    write_text(applications / "sourceview" / f"cinnamon-current-{slug}.xml", render_sourceview(slug, profile))
    write_text(applications / "kde" / f"CinnamonCurrent{slug.title().replace('-', '')}.colors", render_kde(slug, profile))
    write_text(applications / "nano" / "colors.rc", render_nano(profile))
    write_text(applications / "tilda" / "colors.ini", render_tilda(profile))
    write_text(applications / "cava" / "colors.conf", render_cava(profile))
    write_text(applications / "gtile" / "stylesheet.css", render_gtile(profile))
    write_json(applications / "fastfetch" / "identity.json", render_fastfetch_identity(slug, profile))
    write_json(applications / "activation-plan.json", application_plan(slug, profile))
    copy_completion_applications(slug, profile, applications)

    write_json(output / "widgets" / "eww-palette.json", {key: eww[key]})
    eww_style_source = profile.get("sources", {}).get("eww_stylesheet")
    if eww_style_source:
        write_text(output / "widgets" / "eww-style.scss", render_eww_style(Path(eww_style_source)))
    if profile.get("eww_bundle"):
        bundle = Path(profile["eww_bundle"])
        for name in EWW_BUNDLE_FILES:
            copy_asset(bundle / name, output / "widgets" / "rpo" / name)
    authored = authored_bundle(profile)
    if authored is not None:
        copytree_fresh(authored[0], output / 'widgets/authored')
        write_json(output / 'widgets/authored-contract.json', profile['authored_eww_bundle'])
    write_json(output / "widgets" / "contract.json", {
        "palette_key": key,
        "behavior": "Preserve the shared Eww Yuck, playback and system-control behavior, and current top-right close affordance. Optional profile SCSS adds class rules only; the controller applies palette variables and the selected style import.",
        "style_bundle": "eww-style.scss" if eww_style_source else None,
        "verification_boundary": "This is a built palette/style contract. Fresh per-profile interaction evidence is recorded separately in runtime-widgets/runs/.",
    })
    write_json(output / "activation-plan.json", activation_plan(slug, profile))

    # Inherited icon/cursor themes are still concrete profile trees. Keep them
    # beside the generated desktop tree so first-time selection and recovery do
    # not depend on a pre-existing ~/.icons installation.
    copytree_fresh(Path(profile["sources"]["icon"]), output / "icons" / profile["icons"])
    if slug in CURSOR_TREES_DEREFERENCED:
        copytree_fresh(Path(profile["sources"]["cursor"]), output / "cursors" / profile["cursor"])
    else:
        copytree_links(Path(profile["sources"]["cursor"]), output / "cursors" / profile["cursor"])

    copy_system_payload(slug, profile, output)

    if slug == "tidal-observatory":
        helper_path = ROOT / "contributions/tidal-integration/apps/integration.py"
        spec = importlib.util.spec_from_file_location("tidal_eww_build", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.build_payload(output, Path(profile["tidal_candidate"]))
    for name, path in profile.get("extra_artwork", {}).items():
        if Path(name).name != name or not name.endswith(".png"):
            raise ValueError("Unsafe extra artwork name")
        copy_asset(Path(path), artwork / name)

    generated = output_digests(output)
    if digests_out is not None:
        digests_out.update(generated)
    source_hashes = {key: sha256_known(Path(value), lookup) for key, value in profile["sources"].items()
                     if key in ("wallpaper", "menu_logo", "fastfetch_logo", "progress_fill", "eww_stylesheet")}
    manifest = {
        "schema": 1,
        "profile": slug,
        "name": profile["name"],
        "theme": profile["theme"],
        "icons": profile["icons"],
        "cursor": profile["cursor"],
        "palette": profile["palette"],
        "ansi": profile["ansi"],
        "terminal_font_size": profile.get("terminal_font_size", 11),
        "style": profile["style"],
        "sources": profile["sources"],
        "source_asset_sha256": source_hashes,
        "source_theme_sha256": sha256_tree(theme_source, lookup),
        "source_icon_sha256": sha256_tree(Path(profile["sources"]["icon"]), lookup),
        "source_cursor_sha256": sha256_tree(Path(profile["sources"]["cursor"]), lookup),
        "source_override_sha256": {
            key: sha256_known(Path(value), lookup) for key, value in profile.get("overrides", {}).items()
        },
        "source_patch_sha256": sha256_known(Path(profile["theme_patches"]), lookup) if profile.get("theme_patches") else None,
        "eww_palette_key": key,
        "eww_stylesheet_sha256": (source_hashes.get("eww_stylesheet") if eww_style_source else None),
        "generated_files": manifest_file_digests(output, generated),
        "coverage": {
            "built": (["authored Cinnamon shell theme retained", "shared popup and Nothing Island skin", "exact artwork", "Eww palette contract", "terminal and application color assets", "activation plan"]
                      if profile.get("preserve_authored_shell", False)
                      else ["Cinnamon panel and popup overlay", "Nothing Island source", "exact artwork", "Eww palette/style contract", "terminal and application color assets", "activation plan"]),
            "not_live_selected": True,
            "requires_fresh_visual_checks": True,
            "requires_login_reboot_check": True,
        },
    }
    layout = rail_layout(profile)
    if layout is not None:
        manifest["panel_layout_sha256"] = panel_spec_sha256(layout)
        manifest["coverage"]["built"].append("rail panel layout contract (live.py applies it; isolated rail rehearsal required)")
    if profile.get('candidate_layout'):
        manifest['candidate_layout'] = profile['candidate_layout']
        manifest['coverage']['built'].append('authored private candidate composition; guarded live panel adapter pending')
    if profile.get('authored_eww_bundle'):
        manifest['authored_eww_bundle'] = profile['authored_eww_bundle']
    write_json(output / "manifest.json", manifest)
    return manifest


# ---------------------------------------------------------------------------
# Incremental build.  Input keys and output fingerprints are stored OUTSIDE the
# generated tree, in build-cache/<slug>.json beside generated/ (see
# cache_dir_for), so generated/ bytes and index.json are exactly those of a
# full build.  Skipping needs both: the recorded input key still matches, and
# generated/<slug> still matches its recorded output fingerprint.
# ---------------------------------------------------------------------------
CACHE_SCHEMA = 1
BUILD_CACHE = ROOT / "build-cache"
LINK_MIN_SIZE = 64 * 1024
LINK_TREES = ("icons", "cursors")
_DIGEST_MEMO: dict[tuple[int, int, int, int, int], str] = {}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_dir_for(generated_root: Path) -> Path:
    """build-cache/ for a tree named generated/, else build-cache-<name>/, beside it."""
    generated_root = Path(generated_root)
    name = "build-cache" if generated_root.name == "generated" else f"build-cache-{generated_root.name}"
    return generated_root.parent / name


def _stat_row(st: os.stat_result) -> list[int]:
    return [st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode), st.st_ino, st.st_dev]


def _input_row(st: os.stat_result, digest: str) -> list[Any]:
    # [size, mtime_ns, mode, ino, dev, sha256, ctime_ns]; ctime cannot be reset by utime.
    return _stat_row(st) + [digest, st.st_ctime_ns]


def _digest_for(path: Path, st: os.stat_result, prior: list[Any] | None, counter: dict[str, int]) -> str:
    if prior is not None and len(prior) == 7 and list(prior[:5]) == _stat_row(st) and prior[6] == st.st_ctime_ns:
        return prior[5]
    memo_key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    known = _DIGEST_MEMO.get(memo_key)
    if known is None:
        known = sha256(path)
        _DIGEST_MEMO[memo_key] = known
        counter["hashed"] = counter.get("hashed", 0) + 1
    return known


def fingerprint_input(path: Path, prior: dict[str, Any] | None, counter: dict[str, int],
                      record_links: bool = False) -> dict[str, Any]:
    """Stat-first fingerprint of a build input, read the way copytree/copy2 read it.

    Symlinks are followed (the builder copies their targets).  A file whose
    (size, mtime_ns, mode, ino, dev) equals the prior record reuses its digest;
    any other file is content-hashed.  Only digests and modes enter the key,
    plus, with record_links (cursor trees, whose links are kept), every
    symlink's target text.
    """
    path = Path(path)
    prior_files = (prior or {}).get("files", {})
    if path.is_file():
        st = path.stat()
        return {"kind": "file", "path": str(path),
                "files": {".": _input_row(st, _digest_for(path, st, prior_files.get("."), counter))}, "dirs": {}}
    if not path.is_dir():
        return {"kind": "missing", "path": str(path), "files": {}, "dirs": {}}
    files: dict[str, list[Any]] = {}
    dirs: dict[str, int] = {".": stat.S_IMODE(path.stat().st_mode)}

    def fail(error: OSError) -> None:
        raise error

    for directory, dirnames, filenames in os.walk(path, followlinks=True, onerror=fail):
        base = Path(directory)
        rel_dir = base.relative_to(path).as_posix()
        for name in dirnames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            dirs[rel] = stat.S_IMODE((base / name).stat().st_mode)
        for name in filenames:
            item = base / name
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            st = item.stat()  # raises for a dangling link, exactly like copy2
            if not stat.S_ISREG(st.st_mode):
                continue
            files[rel] = _input_row(st, _digest_for(item, st, prior_files.get(rel), counter))
    record = {"kind": "dir", "path": str(path), "files": files, "dirs": dirs}
    if record_links:
        record["links"] = tree_symlinks(path)
    return record


def input_digest(record: dict[str, Any]) -> str:
    logical = {"kind": record["kind"],
               "files": sorted([rel, row[2], row[5]] for rel, row in record["files"].items()),
               "dirs": sorted(record["dirs"].items())}
    if "links" in record:
        logical["links"] = sorted(record["links"].items())
    return hashlib.sha256(canonical_json(logical).encode("utf-8")).hexdigest()


def build_inputs(slug: str, profile: dict[str, Any]) -> list[tuple[str, Path]]:
    """Every file or tree whose bytes can reach generated/<slug>."""
    items = [(f"sources.{key}", Path(value)) for key, value in sorted(profile.get("sources", {}).items())]
    items += [(f"overrides.{key}", Path(value)) for key, value in sorted(profile.get("overrides", {}).items())]
    if profile.get("theme_patches"):
        items.append(("theme_patches", Path(profile["theme_patches"])))
    if profile.get("eww_palette_source"):
        items.append(("eww_palette_source", Path(profile["eww_palette_source"])))
    if profile.get("eww_bundle"):
        items += [(f"eww_bundle.{name}", Path(profile["eww_bundle"]) / name) for name in EWW_BUNDLE_FILES]
    if profile.get('authored_eww_bundle'):
        authored = authored_bundle(profile)
        items.append(('authored_eww_bundle', authored[0]))
    if profile.get("system_payload_source"):
        items.append(("system_payload_source", STUDIO_ROOT / profile["system_payload_source"]))
    if profile.get("completion_applications_source"):
        items.append(("completion_applications_source", STUDIO_ROOT / profile["completion_applications_source"]))
    if profile.get("island_source"):
        items.append(("island_source", Path(profile["island_source"])))
    items += [(f"extra_artwork.{name}", Path(path)) for name, path in profile.get("extra_artwork", {}).items()]
    if slug == "tidal-observatory":
        items += [("tidal_eww_source", Path(profile["tidal_candidate"]) / "apps/eww"),
                  ("tidal_eww_helper", ROOT / "contributions/tidal-integration/apps/integration.py"),
                  ("tidal_escape_helper", ROOT / "contributions/tidal-integration/apps/tidal_escape.py"),
                  ("tidal_dismiss_helper", ROOT / "contributions/tidal-integration/apps/dismiss-focused.py")]
    items += [("ocean.island", OCEAN_ISLAND), ("ocean.applet", OCEAN_APPLET), ("ocean.stylesheet", OCEAN_STYLESHEET)]
    items.append(("contributions", ROOT / "contributions" / slug))
    return items


def compute_input_key(slug: str, profile: dict[str, Any], eww: dict[str, Any],
                      prior_inputs: dict[str, Any] | None) -> tuple[str, dict[str, Any], dict[str, int]]:
    counter: dict[str, int] = {}
    prior_inputs = prior_inputs or {}
    inputs = {}
    for label, path in build_inputs(slug, profile):
        prior = prior_inputs.get(label)
        if prior is not None and prior.get("path") != str(path):
            prior = None
        inputs[label] = fingerprint_input(path, prior, counter, record_links=(label == "sources.cursor"))
    merged = {**eww, **load_json(Path(profile["eww_palette_source"]))} if profile.get("eww_palette_source") else eww
    palette_key = eww_palette_key(profile)
    document = {
        "schema": CACHE_SCHEMA,
        "slug": slug,
        "python": list(sys.version_info[:2]),
        "umask": BUILD_UMASK,
        "code_sha256": sha256(Path(__file__).resolve()),
        "profile": profile,
        "eww_entry": {palette_key: merged.get(palette_key)},
        "inputs": {label: {"path": value["path"], "digest": input_digest(value)} for label, value in inputs.items()},
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest(), inputs, counter


def input_lookup(inputs: dict[str, Any]) -> HashLookup:
    """Map walked source paths to the digests the fingerprint already holds."""
    known: dict[str, str] = {}
    for value in inputs.values():
        base = value["path"]
        for rel, row in value["files"].items():
            known[base if rel == "." else os.path.join(base, rel)] = row[5]

    def lookup(path: Path) -> str:
        found = known.get(str(path))
        return found if found is not None else sha256(path)
    return lookup


def scan_tree(root: Path, links: dict[str, str] | None = None
              ) -> tuple[dict[str, os.stat_result], dict[str, int], list[str]]:
    """lstat every entry of a generated tree without following links.

    Symlinks go to ``links`` (rel -> target) when a dict is given, otherwise
    to the foreign list with special files.
    """
    files: dict[str, os.stat_result] = {}
    dirs: dict[str, int] = {}
    foreign: list[str] = []

    def fail(error: OSError) -> None:
        raise error

    for directory, dirnames, filenames in os.walk(root, followlinks=False, onerror=fail):
        base = Path(directory)
        rel_dir = base.relative_to(root).as_posix()
        for name in list(dirnames):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            st = (base / name).lstat()
            if stat.S_ISLNK(st.st_mode):
                if links is None:
                    foreign.append(rel)
                else:
                    links[rel] = os.readlink(base / name)
                dirnames.remove(name)
            else:
                dirs[rel] = stat.S_IMODE(st.st_mode)
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            st = (base / name).lstat()
            if stat.S_ISREG(st.st_mode):
                files[rel] = st
            elif stat.S_ISLNK(st.st_mode) and links is not None:
                links[rel] = os.readlink(base / name)
            else:
                foreign.append(rel)
    return files, dirs, foreign


def output_row(st: os.stat_result, digest: str) -> list[Any]:
    """[size, mtime_ns, mode, ino, sha256, ctime_ns] of one generated file."""
    return [st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode), st.st_ino, digest, st.st_ctime_ns]


def stat_matches(st: os.stat_result, row: list[Any] | None) -> bool:
    """Cheap check: size, mtime_ns, mode, ino and ctime_ns all unchanged."""
    return (row is not None and len(row) == 6 and row[5] == st.st_ctime_ns
            and [st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode), st.st_ino] == list(row[:4]))


def verify_output(root: Path, recorded: dict[str, Any] | None) -> tuple[bool, dict[str, list[Any]], int]:
    """Cheap stat check of generated/<slug> against its recorded fingerprint.

    Entries whose (size, mtime_ns, mode, ino, ctime_ns) changed are
    content-hashed; the tree matches only if every file's digest and mode, and
    the file and directory sets, equal the record.
    """
    if recorded is None or not root.is_dir() or root.is_symlink():
        return False, {}, 0
    links: dict[str, str] = {}
    files, dirs, foreign = scan_tree(root, links)
    expected = recorded.get("files", {})
    if (foreign or set(files) != set(expected) or dirs != recorded.get("dirs", {})
            or links != recorded.get("links", {})):
        return False, {}, 0
    refreshed: dict[str, list[Any]] = {}
    hashed = 0
    for rel, st in files.items():
        row = expected[rel]
        if stat_matches(st, row):
            refreshed[rel] = list(row)
            continue
        hashed += 1
        if stat.S_IMODE(st.st_mode) != row[2] or st.st_size != row[0]:
            return False, {}, hashed
        try:
            digest, state = hash_with_stat(root / rel)
            now = (root / rel).lstat()
        except (OSError, ValueError):
            return False, {}, hashed
        # Record the new stat only if it is the very inode state that was hashed.
        if digest != row[4] or inode_state(now) != state or stat.S_IMODE(now.st_mode) != row[2]:
            return False, {}, hashed
        refreshed[rel] = output_row(now, digest)
    return True, refreshed, hashed


def _replace_file(source: Path, target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    os.replace(source, target)  # replaces a regular file or a symlink itself, never its target


def _place_symlink(path: Path, link_target: str) -> None:
    """Create or retarget path as a symlink atomically (temp name + os.replace)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    temporary = path.with_name(f".{path.name}.symlink-{uuid.uuid4().hex}")
    os.symlink(link_target, temporary)
    try:
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def sync_tree(built: Path, target: Path, built_digests: dict[str, tuple[str, tuple[int, int, int, int]]],
              known: dict[str, list[Any]] | None) -> tuple[dict[str, int], dict[str, Any]]:
    """Make target byte-, symlink- and mode-identical to built, rewriting only what differs.

    built_digests maps each built REGULAR file rel -> (sha256, (ino, size,
    mtime_ns, ctime_ns)) taken while it was hashed.  Byte-identical target
    files and same-target symlinks keep their inode.  Changed or new files are
    moved in with os.replace; symlinks are (re)created under a temp name and
    os.replace'd, whether the old entry was a file, another link or a dir;
    nothing is ever followed or written in place, so a hard link breaks only
    for that path.  Extras are removed; modes are fixed (by replacement when
    the inode is shared).  Every recorded row pairs a final lstat with a
    digest known to describe that exact inode state; any change meanwhile
    raises (the caller has already dropped the record).
    """
    stats = {"written": 0, "unchanged": 0, "removed": 0, "modes": 0, "hashed": 0}
    built_links: dict[str, str] = {}
    built_files, built_dirs, built_foreign = scan_tree(built, built_links)
    if built_foreign:
        raise ValueError(f"Built tree contains special entries: {built_foreign[:3]}")
    if set(built_files) != set(built_digests):
        raise ValueError("Built tree and its digests disagree")
    # rel -> (digest, ino, size, mtime_ns, ctime_ns) the final file must still have.
    expected: dict[str, tuple[str, int, int, int, int]] = {}

    def moved_in(rel: str, path: Path) -> None:
        digest, (ino, size, mtime, _) = built_digests[rel]
        st = path.lstat()
        if (st.st_ino, st.st_size, st.st_mtime_ns) != (ino, size, mtime):
            raise ValueError(f"Built file changed after it was hashed: {rel}")
        expected[rel] = (digest, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    if not target.exists() and not target.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(built, target)  # a directory rename leaves file inodes and links untouched
        for rel in built_files:
            moved_in(rel, target / rel)
        stats["written"] = len(built_files) + len(built_links)
    else:
        if target.is_symlink() or not target.is_dir():
            raise ValueError(f"Generated profile path is not a directory: {target}")
        links: dict[str, str] = {}
        files, dirs, foreign = scan_tree(target, links)
        for rel in foreign:
            (target / rel).unlink()
            stats["removed"] += 1
        for rel in sorted(built_dirs, key=lambda item: item.count("/")):
            path = target / rel
            if rel in files or rel in links:
                path.unlink()
                files.pop(rel, None)
                links.pop(rel, None)
                stats["removed"] += 1
            if rel not in dirs:
                path.mkdir()
        known = known or {}
        for rel, built_st in sorted(built_files.items()):
            source, path = built / rel, target / rel
            built_digest = built_digests[rel][0]
            current = files.get(rel)
            if current is None:  # absent, a symlink or a directory: replace it
                _replace_file(source, path)
                moved_in(rel, path)
                stats["written"] += 1
                continue
            prior = known.get(rel)
            if stat_matches(current, prior):
                digest = prior[4]
                state = (current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
            elif current.st_size != built_st.st_size:
                digest, state = None, None
            else:
                digest, state = hash_with_stat(path)
                stats["hashed"] += 1
            if digest != built_digest:
                _replace_file(source, path)
                moved_in(rel, path)
                stats["written"] += 1
            elif stat.S_IMODE(current.st_mode) != stat.S_IMODE(built_st.st_mode):
                if current.st_nlink > 1:
                    _replace_file(source, path)
                    moved_in(rel, path)
                    stats["written"] += 1
                else:
                    os.chmod(path, stat.S_IMODE(built_st.st_mode))
                    st = path.lstat()  # chmod moves ctime; content was verified just above
                    if (st.st_ino, st.st_size, st.st_mtime_ns) != state[:3]:
                        raise ValueError(f"Generated file changed during sync: {rel}")
                    expected[rel] = (digest, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                    stats["modes"] += 1
            else:
                expected[rel] = (digest, *state)
                stats["unchanged"] += 1
        for rel, link_target in sorted(built_links.items()):
            if links.get(rel) == link_target:
                stats["unchanged"] += 1
                continue
            _place_symlink(target / rel, link_target)
            stats["written"] += 1
        replaced = set(built_files) | set(built_links)
        for rel in sorted((set(files) | set(links)) - replaced - set(built_dirs)):
            ancestors = {"/".join(rel.split("/")[:depth]) for depth in range(1, rel.count("/") + 1)}
            if not ancestors & replaced:  # else it vanished with a dir replaced above
                path = target / rel
                if os.path.lexists(path):
                    path.unlink()
            stats["removed"] += 1
        for rel in sorted(set(dirs) - set(built_dirs) - replaced,
                          key=lambda item: item.count("/"), reverse=True):
            path = target / rel
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            stats["removed"] += 1
        for rel, mode in built_dirs.items():
            path = target / rel
            if stat.S_IMODE(path.lstat().st_mode) != mode:
                os.chmod(path, mode)
                stats["modes"] += 1
    final_links: dict[str, str] = {}
    files, dirs, foreign = scan_tree(target, final_links)
    if foreign or set(files) != set(expected) or final_links != built_links:
        raise ValueError(f"Generated tree changed during sync: {target}")
    rows: dict[str, list[Any]] = {}
    for rel, st in files.items():
        digest, ino, size, mtime, ctime = expected[rel]
        if (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns) != (ino, size, mtime, ctime):
            observed, state = hash_with_stat(target / rel)
            stats["hashed"] += 1
            st = (target / rel).lstat()
            if observed != digest or inode_state(st) != state:
                raise ValueError(f"Generated file changed during sync: {rel}")
        rows[rel] = output_row(st, digest)
    return stats, {"files": rows, "dirs": dirs, "links": final_links}


def load_record(cache_dir: Path, slug: str) -> dict[str, Any] | None:
    path = cache_dir / f"{slug}.json"
    try:
        value = load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA or value.get("profile") != slug:
        return None
    return value


def save_record(cache_dir: Path, slug: str, record: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    write_bytes(cache_dir / f"{slug}.json", (canonical_json(record) + "\n").encode("utf-8"))


@contextmanager
def build_lock(cache_dir: Path) -> Iterator[None]:
    """Exclusive, non-blocking flock on <cache_dir>/.lock for build, link-identical and integrate."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(cache_dir / ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another collection build holds {cache_dir / '.lock'}") from None
        yield
    finally:
        os.close(descriptor)


@contextmanager
def build_umask() -> Iterator[None]:
    previous = os.umask(BUILD_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


def build_temp_prefix(generated_root: Path) -> str:
    return f".{Path(generated_root).name}-build-"


def clean_stale_builds(generated_root: Path) -> None:
    """Remove temp trees left by an interrupted build (caller holds the lock)."""
    generated_root = Path(generated_root)
    for path in generated_root.parent.glob(build_temp_prefix(generated_root) + "*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def verbatim_copies(slug: str, profile: dict[str, Any]) -> list[tuple[str, str, set[str]]]:
    """(input label, output prefix, excluded source rels) for every byte-for-byte copy."""
    theme_rewritten = {"cinnamon/cinnamon.css"}
    if profile.get("theme_patches"):
        theme_rewritten |= {patch["path"] for patch in load_json(Path(profile["theme_patches"]))}
    overrides = profile.get("overrides", {})
    if overrides.get("gtk2_rc"):
        theme_rewritten |= {"gtk-2.0/gtkrc", "gtk-2.0/studio-controls.rc"}
    if overrides.get("gtk_css"):
        theme_rewritten |= {f"gtk-{version}/{name}" for version in ("3.0", "4.0") for name in ("gtk.css", "gtk-dark.css")}
    copies = [
        ("sources.theme", f"desktop/{profile['theme']}/", theme_rewritten),
        ("sources.icon", f"icons/{profile['icons']}/", set()),
        ("sources.cursor", f"cursors/{profile['cursor']}/", set()),
        ("ocean.island", "island/nothing-island@desktop-theme-studio/", {"applet.js", "stylesheet.css"}),
        ("sources.wallpaper", "artwork/wallpaper.png", set()),
        ("sources.menu_logo", "artwork/menu-logo.png", set()),
        ("sources.fastfetch_logo", f"artwork/macklenmobile-logo-{slug}.png", set()),
    ]
    if profile.get("island_source"):
        copies = [row for row in copies if row[0] != "ocean.island"]
        copies.append(("island_source", f"island/{profile['island_uuid']}/", set()))
    copies += [(f"extra_artwork.{name}", f"artwork/{name}", set()) for name in profile.get("extra_artwork", {})]
    if "progress_fill" in profile.get("sources", {}):
        copies.append(("sources.progress_fill", "artwork/progress-fill.png", set()))
    if overrides.get("gtk2_rc"):
        copies.append(("overrides.gtk2_rc", f"desktop/{profile['theme']}/gtk-2.0/studio-controls.rc", set()))
    if profile.get("eww_bundle"):
        copies += [(f"eww_bundle.{name}", f"widgets/rpo/{name}", set()) for name in EWW_BUNDLE_FILES]
    return copies


def copy_mismatches(slug: str, profile: dict[str, Any], inputs: dict[str, Any],
                    digests: dict[str, tuple[str, Any]], built: Path | None = None) -> list[str]:
    """Post-copy verification: each verbatim copy's bytes (and kept symlinks) equal its source."""
    problems = []
    for label, prefix, excluded in verbatim_copies(slug, profile):
        record = inputs.get(label, {})
        kept_links = record.get("links", {}) if label == "sources.cursor" and slug not in CURSOR_TREES_DEREFERENCED else {}
        if kept_links or (label == "sources.cursor" and slug not in CURSOR_TREES_DEREFERENCED):
            copied: dict[str, str] = {}
            if built is not None:
                scan_tree(built / prefix, copied)
            if built is not None and copied != kept_links:
                problems.append(prefix + "<symlinks>")
        for rel, row in record.get("files", {}).items():
            if rel in excluded:
                continue
            parts = rel.split("/")
            if any("/".join(parts[:depth]) in kept_links for depth in range(1, len(parts) + 1)):
                continue  # kept as (or under) a symlink; checked by target above
            output_rel = prefix if rel == "." else prefix + rel
            if output_rel not in digests or digests[output_rel][0] != row[5]:
                problems.append(output_rel)
    return problems


BUILD_ATTEMPTS = 2
_TEST_HOOKS: dict[str, Callable[..., None]] = {}


def build_one(slug: str, profile: dict[str, Any], eww: dict[str, Any], generated_root: Path,
              force: bool = False, cache_dir: Path | None = None) -> dict[str, Any]:
    """Skip, or rebuild into a temp dir beside generated_root and sync generated_root/slug.

    The caller holds build_lock(cache_dir).  Output modes come from BUILD_UMASK.
    After building, every verbatim copy is checked against its source digest
    and the inputs are re-fingerprinted; if a source changed during the build
    it is rebuilt once more, and a second change aborts before any sync.
    """
    generated_root = Path(generated_root)
    cache_dir = cache_dir_for(generated_root) if cache_dir is None else Path(cache_dir)
    target = generated_root / slug
    with build_umask():
        record = load_record(cache_dir, slug)
        key, inputs, counter = compute_input_key(slug, profile, eww, record.get("inputs") if record else None)
        result: dict[str, Any] = {"profile": slug, "source_files_hashed": counter.get("hashed", 0)}
        if not force and record is not None and record.get("input_key") == key:
            ok, refreshed, hashed = verify_output(target, record.get("output"))
            result["output_files_hashed"] = hashed
            if ok:
                if refreshed != record["output"]["files"] or inputs != record.get("inputs"):
                    save_record(cache_dir, slug, {**record, "inputs": inputs,
                                                  "output": {**record["output"], "files": refreshed}})
                result.update(action="skipped", written=0, removed=0, modes=0)
                return result
            result["reason"] = "generated tree differs from its recorded fingerprint"
        else:
            result["reason"] = "forced" if force else ("no build record" if record is None else "inputs changed")
        temporary = Path(tempfile.mkdtemp(prefix=build_temp_prefix(generated_root) + slug + "-",
                                          dir=generated_root.parent))
        try:
            for attempt in range(1, BUILD_ATTEMPTS + 1):
                built = temporary / f"attempt-{attempt}" / slug
                digests: dict[str, tuple[str, Any]] = {}
                build_profile(slug, profile, eww, output=built, lookup=input_lookup(inputs), digests_out=digests)
                digests["manifest.json"] = hash_with_stat(built / "manifest.json")
                _TEST_HOOKS.get("after_build_profile", lambda *args: None)(slug, built, attempt)
                problems = copy_mismatches(slug, profile, inputs, digests, built)
                key_after, inputs_after, _ = compute_input_key(slug, profile, eww, inputs)
                if key_after == key and not problems:
                    break
                result["retried"] = attempt
                key, inputs = key_after, inputs_after
                shutil.rmtree(built.parent)
            else:
                raise ValueError(f"Inputs of {slug} kept changing during the build; nothing was synced")
            # Invalidate first: an interrupted sync can never be mistaken for current.
            (cache_dir / f"{slug}.json").unlink(missing_ok=True)
            stats, output = sync_tree(built, target, digests, (record or {}).get("output", {}).get("files"))
            save_record(cache_dir, slug, {"schema": CACHE_SCHEMA, "profile": slug, "input_key": key,
                                          "generated_root": str(generated_root), "inputs": inputs, "output": output})
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    result.update(action="built", **stats)
    return result


def settle_records(generated_root: Path, cache_dir: Path, slugs: list[str]) -> None:
    """Re-check records after a build that synced at least one profile.

    A later profile's sync can replace a path that shared an inode (link-identical)
    with an earlier profile's file; the link-count change moves that inode's ctime,
    which would cost a re-hash on the next build.  Refresh such rows now, only
    after verify_output() re-hashed them against the recorded digest.
    """
    for slug in slugs:
        record = load_record(cache_dir, slug)
        if record is None:
            continue
        ok, refreshed, _ = verify_output(Path(generated_root) / slug, record.get("output"))
        if ok and refreshed != record["output"]["files"]:
            save_record(cache_dir, slug, {**record, "output": {**record["output"], "files": refreshed}})


def write_json_if_changed(path: Path, value: Any) -> bool:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        if path.read_bytes() == payload:
            return False
    except FileNotFoundError:
        pass
    write_bytes(path, payload)
    return True


def build(spec: dict[str, Any], slugs: list[str] | None = None, force: bool = False) -> int:
    selected = list(dict.fromkeys(slugs)) if slugs else list(spec["profiles"])
    unknown = [slug for slug in selected if slug not in spec["profiles"]]
    if unknown:
        raise KeyError(f"Unknown profile: {', '.join(unknown)}")
    eww = load_json(EWW_PALETTES)
    with build_lock(BUILD_CACHE), build_umask():
        clean_stale_builds(GENERATED)
        GENERATED.mkdir(parents=True, exist_ok=True)
        rebuilt: list[str] = []
        for slug in selected:
            result = build_one(slug, spec["profiles"][slug], eww, GENERATED, force=force, cache_dir=BUILD_CACHE)
            if result["action"] == "built":
                rebuilt.append(slug)
            if result["action"] == "skipped":
                print(f"skipped {slug} (inputs and generated tree unchanged)")
            else:
                print(f"built {slug} ({result['reason']}): {result['written']} written, "
                      f"{result['removed']} removed, {result['modes']} modes fixed, {result['unchanged']} unchanged")
        if rebuilt:  # inode peers may sit in any recorded profile, candidates included
            settle_records(GENERATED, BUILD_CACHE, sorted(path.stem for path in BUILD_CACHE.glob("*.json")))
        index = {"collection": spec["collection"], "profiles": {
            slug: {"name": value["name"], "manifest": f"{slug}/manifest.json"} for slug, value in spec["profiles"].items()}}
        write_json_if_changed(GENERATED / "index.json", index)
    return 0


def _same_bytes(first: Path, second: Path) -> bool:
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            a, b = left.read(1024 * 1024), right.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def _link_key(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_size, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_dev)


def link_identical(generated_root: Path | None = None, commit: bool = False,
                   min_size: int = LINK_MIN_SIZE) -> dict[str, Any]:
    """Hard-link byte-identical regular files within generated/<slug>/{icons,cursors}.

    Candidates are files of at least min_size bytes with equal size, mode,
    owner, group and device; both ends always lie inside generated_root.  The
    plan is made under build_lock.  With commit, every link is preceded by a
    fresh lstat (type, size, mode, uid, gid, dev) and a full byte comparison
    with the group's canonical file, whose hash is verified before and after
    the group is linked.  Build records are refreshed with that verified hash;
    a record whose stored digest differs loses that row, so the next build
    rebuilds the profile instead of trusting it.
    """
    root = Path(generated_root or GENERATED)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Generated root must be a real directory: {root}")
    root = root.resolve()
    cache_dir = cache_dir_for(root)
    lock = build_lock(cache_dir) if (commit or cache_dir.is_dir()) else nullcontext()
    with lock:
        candidates: dict[tuple[int, int, int, int, int], list[tuple[str, os.stat_result]]] = {}
        for profile_dir in sorted(root.iterdir()):
            if profile_dir.is_symlink() or not profile_dir.is_dir():
                continue
            for tree in LINK_TREES:
                base = profile_dir / tree
                if base.is_symlink() or not base.is_dir():
                    continue
                files, _, _ = scan_tree(base)
                for rel, st in files.items():
                    if st.st_size >= min_size:
                        candidates.setdefault(_link_key(st), []).append((f"{profile_dir.name}/{tree}/{rel}", st))
        records = ({path.stem: load_record(cache_dir, path.stem) for path in cache_dir.glob("*.json")}
                   if cache_dir.is_dir() else {})

        def digest(rel: str, st: os.stat_result) -> str:
            slug, inner = rel.split("/", 1)
            row = ((records.get(slug) or {}).get("output") or {}).get("files", {}).get(inner)
            if stat_matches(st, row):
                return row[4]
            return hash_with_stat(root / rel)[0]

        plan: list[tuple[tuple[int, int, int, int, int], str, list[str]]] = []
        savings = already = 0
        for key, members in sorted(candidates.items()):
            if len({st.st_ino for _, st in members}) < 2:
                if len(members) > 1:
                    already += (len(members) - 1) * key[0]
                continue
            by_digest: dict[str, list[tuple[str, os.stat_result]]] = {}
            seen_inodes: dict[int, str] = {}
            for rel, st in sorted(members):
                value = seen_inodes.get(st.st_ino) or digest(rel, st)
                seen_inodes[st.st_ino] = value
                by_digest.setdefault(value, []).append((rel, st))
            for value, group in by_digest.items():
                inodes = {st.st_ino for _, st in group}
                already += (len(group) - len(inodes)) * key[0]
                if len(inodes) > 1:
                    plan.append((key, value, [rel for rel, _ in group]))
                    savings += (len(inodes) - 1) * key[0]
        linked = skipped_metadata = skipped_content = skipped_groups = dropped_rows = 0
        saved = 0
        if commit:
            dirty: set[str] = set()
            for key, planned_digest, members in plan:
                canonical = root / members[0]
                before = canonical.lstat()
                if not stat.S_ISREG(before.st_mode) or _link_key(before) != key:
                    skipped_groups += 1
                    continue
                verified, state = hash_with_stat(canonical)
                if verified != planned_digest or state != inode_state(before):
                    skipped_groups += 1
                    continue
                for rel in members[1:]:
                    path = root / rel
                    if any((root / parent).is_symlink() for parent in path.relative_to(root).parents):
                        raise ValueError(f"Refusing to link through a symlinked directory: {rel}")
                    _TEST_HOOKS.get("before_link", lambda *args: None)(canonical, path)
                    st = path.lstat()
                    if not stat.S_ISREG(st.st_mode) or _link_key(st) != key:
                        skipped_metadata += 1
                        continue
                    if st.st_ino == before.st_ino:
                        continue
                    if not _same_bytes(canonical, path):
                        skipped_content += 1
                        continue
                    temporary = path.with_name(f".{path.name}.link-{uuid.uuid4().hex}")
                    os.link(canonical, temporary)
                    try:
                        os.replace(temporary, path)
                    finally:
                        if os.path.lexists(temporary):
                            os.unlink(temporary)
                    linked += 1
                    saved += key[0] if st.st_nlink == 1 else 0
                # The canonical must still hold the verified bytes after linking.
                final_digest, final_state = hash_with_stat(canonical)
                canonical_ino = final_state[0]
                for rel in members:
                    path = root / rel
                    st = path.lstat()
                    if st.st_ino != canonical_ino:
                        continue  # not linked; its row keeps describing its own inode
                    slug, inner = rel.split("/", 1)
                    record = records.get(slug)
                    files = (record or {}).get("output", {}).get("files", {})
                    if inner not in files:
                        continue
                    # Never pair a stat with a digest unless that exact inode state was hashed.
                    if files[inner][4] != verified or final_digest != verified or inode_state(st) != final_state:
                        del files[inner]  # stale digest: force a rebuild rather than trust it
                        dropped_rows += 1
                    else:
                        files[inner] = output_row(st, verified)
                    dirty.add(slug)
            for slug in sorted(dirty):
                save_record(cache_dir, slug, records[slug])
    return {"root": str(root), "commit": commit, "groups": len(plan),
            "links_planned": sum(len(members) - 1 for _, _, members in plan), "links_created": linked,
            "skipped_metadata": skipped_metadata, "skipped_content": skipped_content,
            "skipped_groups": skipped_groups, "record_rows_dropped": dropped_rows,
            "bytes_saved": saved if commit else 0, "bytes_saveable": savings,
            "bytes_already_shared": already, "min_size": min_size}


def gtk_parse(path: Path) -> tuple[bool, str]:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception as error:  # Optional capability on a build host.
        return False, f"GTK CSS parser unavailable: {error}"
    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(path.read_bytes())
        return True, "GTK 3 CssProvider accepted generated CSS"
    except Exception as error:
        return False, f"GTK CSS parse failed: {error}"


def verify_profile(slug: str, profile: dict[str, Any], eww: dict[str, Any]) -> dict[str, Any]:
    if profile.get("eww_palette_source"):
        eww = {**eww, **load_json(Path(profile["eww_palette_source"]))}
    output = GENERATED / slug
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(condition), "detail": detail})

    island_id = profile.get("island_uuid", "nothing-island@desktop-theme-studio")
    required = [output / "manifest.json", output / "desktop" / profile["theme"] / "index.theme", output / "desktop" / profile["theme"] / "cinnamon" / "cinnamon.css", output / "island" / island_id / "applet.js", output / "island" / island_id / "stylesheet.css", output / "widgets" / "eww-palette.json", output / "applications" / "activation-plan.json", output / "activation-plan.json"]
    source_eww_style = profile.get("sources", {}).get("eww_stylesheet")
    if source_eww_style:
        required.append(output / "widgets" / "eww-style.scss")
    for path in required:
        check(f"generated {path.relative_to(output)}", path.is_file(), str(path))
    if not all(item["ok"] for item in checks):
        return {"profile": slug, "ok": False, "checks": checks}

    colors = palette(profile)
    for name, first, second in (
        ("surface text", colors["foreground"], colors["surface"]),
        ("terminal text", colors["terminal_foreground"], colors["terminal"]),
        ("selection text", colors["selection_foreground"], colors["selection"]),
    ):
        ratio = contrast(first, second)
        check(f"contrast {name}", ratio >= 4.5, f"{ratio:.2f}:1 ({first} on {second})")

    for source_key, generated_name in (("wallpaper", "wallpaper.png"), ("menu_logo", "menu-logo.png"), ("fastfetch_logo", f"macklenmobile-logo-{slug}.png")):
        source = Path(profile["sources"][source_key])
        target = output / "artwork" / generated_name
        check(f"exact {source_key} copy", source.is_file() and target.is_file() and sha256(source) == sha256(target), f"{source} -> {target}")
    if "progress_fill" in profile["sources"]:
        source = Path(profile["sources"]["progress_fill"])
        target = output / "artwork/progress-fill.png"
        check("exact progress fill copy", target.is_file() and sha256(source) == sha256(target), str(source))
    for source_key in ("theme", "icon", "cursor"):
        source = Path(profile["sources"][source_key])
        check(f"installed {source_key} dependency", source.is_dir(), str(source))

    css = (output / "desktop" / profile["theme"] / "cinnamon" / "cinnamon.css").read_text(encoding="utf-8")
    authored = profile.get("preserve_authored_shell", False)
    if authored:
        source_css = (Path(profile["sources"]["theme"]) / "cinnamon/cinnamon.css").read_text(encoding="utf-8").rstrip()
        if profile.get("theme_patches"):
            for patch in load_json(Path(profile["theme_patches"])):
                if patch.get("path") == "cinnamon/cinnamon.css":
                    before, after, count = patch["before"], patch["after"], patch["count"]
                    if source_css.count(before) != count:
                        raise ValueError(f"Authored-shell verification patch no longer matches source: {profile['theme_patches']}")
                    source_css = source_css.replace(before, after)
        manifest = load_json(output / "manifest.json")
        provenance = authored_shell_provenance(profile)
        copied_css = css.removeprefix(provenance)
        check("authored shell source retained", copied_css.startswith(source_css), "the source Cinnamon stylesheet remains the exact generated prefix after the coordinator marker")
        check("authored shell fingerprint", manifest.get("source_theme_sha256") == sha256_tree(Path(profile["sources"]["theme"])), "theme source tree hash is recorded; panel palette appearance remains a visual-check item")
    else:
        check("panel ownership", MARKER in css, "generated overlay is present")
        check("panel transparency", "rgba(" in css and "#panel.panel-top" in css and "#panel.panel-bottom" in css, "generated top and bottom panel selectors use translucent surfaces")
        css_folded = css.casefold()
        check("profile panel colors", colors["surface"].casefold() in css_folded
              and colors["accent"].casefold() in css_folded, "surface and focus colors are present in generated CSS")

    applet = (output / "island" / island_id / "applet.js").read_text(encoding="utf-8")
    island_css = (output / "island" / island_id / "stylesheet.css").read_text(encoding="utf-8")
    if profile.get("island_source"):
        check("authored Island source retained", sha256_tree(output / "island" / island_id) == sha256_tree(Path(profile["island_source"])), "complete custom applet copied without generic reskin")
        check("authored Island close", "Close Tidal Observatory Island" in applet and "this.menu.close()" in applet, "named close control present; actual click verified separately")
        check("authored Island Escape", "Clutter.KEY_Escape" in applet, "keyboard handler present")
    else:
        check("island top-right close", "this._button('×'" in applet and "Close island" in applet, "current visible close button retained")
        check("island Escape dismissal", "Clutter.KEY_Escape" in applet and "this.menu.close()" in applet, "current Escape handler retained")
        check("island translucent cards", "rgba(" in island_css and ".ni-card" in island_css and ".ni-popup .popup-menu-content" in island_css, "profile alpha applies to painting surfaces")
        check("island profile colors", colors["accent"] in island_css and colors["surface"] in island_css, "profile palette substituted")

    saved_eww = load_json(output / "widgets" / "eww-palette.json")
    key = eww_palette_key(profile)
    check("Eww palette correspondence", saved_eww == {key: eww.get(key)}, f"registered key {key}")
    contract = load_json(output / "widgets" / "contract.json")
    if source_eww_style:
        source_style = Path(source_eww_style)
        generated_style = output / "widgets" / "eww-style.scss"
        check("Eww profile stylesheet is source-derived",
              generated_style.is_file() and generated_style.read_text(encoding="utf-8") == render_eww_style(source_style),
              f"generated shared-Yuck class layer from {source_style}")
        built_manifest = load_json(output / "manifest.json")
        check("Eww profile stylesheet fingerprint",
              built_manifest.get("eww_stylesheet_sha256") == sha256(source_style),
              "source stylesheet SHA-256 pinned in generated manifest")
    else:
        check("Eww shared stylesheet fallback",
              not (output / "widgets" / "eww-style.scss").exists(),
              "no theme-local style is declared; common Eww appearance remains active")
    check("Eww close contract", "close" in contract["behavior"].lower(), "current source/runtime retained")
    close_source = EWW_CLOSE_SOURCE.read_text(encoding="utf-8") if EWW_CLOSE_SOURCE.is_file() else ""
    close_style = EWW_CLOSE_STYLE.read_text(encoding="utf-8") if EWW_CLOSE_STYLE.is_file() else ""
    check("Eww visible × close action", ':text "×"' in close_source and "flow.py close ${window}" in close_source, "current Eww close button remains wired to the shared close flow")
    check("Eww close button styling", "button.close" in close_style, "current Eww close affordance keeps a dedicated style")

    applications = output / "applications"
    expected = [applications / "kitty" / f"cinnamon-current-{slug}.conf", applications / "konsole" / f"CinnamonCurrent{slug.title().replace('-', '')}.colorscheme", applications / "ptyxis" / f"cinnamon-current-{slug}.palette", applications / "gtk" / "application.css", applications / "sourceview" / f"cinnamon-current-{slug}.xml", applications / "kde" / f"CinnamonCurrent{slug.title().replace('-', '')}.colors", applications / "nano" / "colors.rc", applications / "tilda" / "colors.ini", applications / "cava" / "colors.conf", applications / "gtile" / "stylesheet.css", applications / "fastfetch" / "identity.json"]
    for path in expected:
        check(f"application asset {path.relative_to(applications)}", path.is_file(), str(path))
    check("Fastfetch long local-IP guard", load_json(applications / "fastfetch" / "identity.json")["long"]["local_ip_format"] == "86.75-309", "required user format retained")
    gtk_ok, gtk_detail = gtk_parse(applications / "gtk" / "application.css")
    check("GTK application CSS parse", gtk_ok, gtk_detail)

    return {"profile": slug, "name": profile["name"], "ok": all(item["ok"] for item in checks), "checks": checks}


def verify(spec: dict[str, Any]) -> int:
    eww = load_json(EWW_PALETTES)
    reports = [verify_profile(slug, profile, eww) for slug, profile in spec["profiles"].items()]
    report = {
        "collection": spec["collection"],
        "status": "pass" if all(item["ok"] for item in reports) else "fail",
        "reports": reports,
        "boundaries": [
            "This is static build validation, not live selection evidence.",
            "No profile has been switched, visually inspected, or reboot/login verified by this script.",
            "Custom cursor selection must be checked after any future live activation.",
            "Ptyxis and Tilda need normal closure before their saved preferences can be changed.",
        ],
    }
    write_json(VERIFICATION / "static-report.json", report)
    for item in reports:
        passed = sum(1 for check in item["checks"] if check["ok"])
        print(f"{item['profile']}: {'PASS' if item['ok'] else 'FAIL'} ({passed}/{len(item['checks'])})")
    print(f"report: {VERIFICATION / 'static-report.json'}")
    return 0 if report["status"] == "pass" else 1


def verify_isolated_entry(slug: str, profile: dict[str, Any], path: Path) -> dict[str, Any]:
    contract = isolated_contract(profile)
    required = set(contract["required_checks"])
    entry: dict[str, Any] = {
        "profile": slug,
        "name": profile["name"],
        "path": str(path),
        "contract": contract["kind"],
        "required_checks": contract["required_checks"],
    }
    if not path.is_file():
        entry.update(ok=False, reason="no isolated run recorded")
        return entry
    raw = load_json(path)
    actual = set(raw.get("checks", []))
    entry.update(
        status=raw.get("status"),
        checks=raw.get("checks", []),
        run=raw.get("run"),
        missing=sorted(required - actual),
        error=raw.get("error"),
    )
    valid = raw.get("status") == "passed" and raw.get("profile") == slug and required.issubset(actual)
    fingerprint = contract.get("source_fingerprint")
    if fingerprint:
        current = fingerprint_sources(
            Path(fingerprint["base"]),
            fingerprint["paths"],
            fingerprint["exclude_python_cache"],
        )
        accepted = raw.get(fingerprint["receipt_field"])
        expected = fingerprint["sha256"]
        matches = current == expected and accepted == expected
        entry["source_fingerprint"] = {
            "expected_sha256": expected,
            "current_sha256": current,
            "receipt_field": fingerprint["receipt_field"],
            "receipt_sha256": accepted,
            "matches": matches,
            "exclusions": (
                ["directories named __pycache__", "files ending .pyc", "files ending .pyo"]
                if fingerprint["exclude_python_cache"] else []
            ),
        }
        valid = valid and matches
    if profile.get("workspace_geometry"):
        workspace_path = VERIFICATION / "workspace-latest" / f"{slug}.json"
        gate_spec = importlib.util.spec_from_file_location("current_workspace_gate", ROOT / "workspace_gate.py")
        gate = importlib.util.module_from_spec(gate_spec)
        assert gate_spec.loader is not None
        gate_spec.loader.exec_module(gate)
        workspace_raw = load_json(workspace_path) if workspace_path.is_file() else None
        entry["workspace"] = gate.verify_workspace_receipt(slug, profile, workspace_raw, ROOT)
        entry["workspace"]["path"] = str(workspace_path)
        valid = valid and entry["workspace"]["ok"]
    layout = rail_layout(profile)
    if layout is not None:
        entry["panel"] = verify_isolated_panel(slug, layout, path.with_name(f"{slug}.panel.json"))
        valid = valid and entry["panel"]["ok"]
    entry["ok"] = valid
    return entry


def verify_isolated(spec: dict[str, Any], selected: str | None = None) -> int:
    reports: list[dict[str, Any]] = []
    if selected is not None and selected not in spec["profiles"]:
        raise KeyError(f"Unknown profile: {selected}")
    profiles = spec["profiles"].items() if selected is None else [(selected, spec["profiles"][selected])]
    for slug, profile in profiles:
        path = ISOLATED_LATEST / f"{slug}.json"
        reports.append(verify_isolated_entry(slug, profile, path))
    report = {
        "collection": spec["collection"],
        "status": "pass" if all(item["ok"] for item in reports) else "fail",
        "reports": reports,
        "scope": "Each profile ran in a disposable Xephyr display with its own HOME, XDG directories, D-Bus session, and Cinnamon process. No live Cinnamon settings were changed.",
        "remaining": ["fresh visual checks in the selected real desktop session", "fresh app-specific checks after a profile selection", "cursor state inspection", "logout/login and reboot verification"],
    }
    write_json(VERIFICATION / "isolated-report.json", report)
    for entry in reports:
        print(f"{entry['profile']}: {'PASS' if entry['ok'] else 'FAIL'}")
    print(f"report: {VERIFICATION / 'isolated-report.json'}")
    return 0 if report["status"] == "pass" else 1


def list_profiles(spec: dict[str, Any]) -> int:
    for slug, profile in spec["profiles"].items():
        print(f"{slug:20} {profile['name']} — {profile['style']['identity']}")
    return 0


def print_plan(spec: dict[str, Any], slug: str) -> int:
    profile = spec["profiles"].get(slug)
    if profile is None:
        raise KeyError(f"Unknown profile: {slug}")
    print(json.dumps(activation_plan(slug, profile), indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify", "verify-isolated", "list", "plan", "link-identical"))
    parser.add_argument("profiles", nargs="*", metavar="profile",
                        help="build: profiles to consider (default all); plan: one slug; verify-isolated: optional slug")
    parser.add_argument("--force", action="store_true", help="build: rebuild even when the recorded key and tree match")
    parser.add_argument("--commit", action="store_true", help="link-identical: create the links (default is a dry run)")
    args = parser.parse_args()
    if args.force and args.command != "build":
        parser.error("--force applies only to build")
    if args.commit and args.command != "link-identical":
        parser.error("--commit applies only to link-identical")
    if args.command in ("verify", "list", "link-identical") and args.profiles:
        parser.error(f"{args.command} takes no profile")
    if args.command in ("plan", "verify-isolated") and len(args.profiles) > 1:
        parser.error(f"{args.command} takes one profile slug")
    spec = load_json(SPEC_PATH)
    if args.command == "build":
        return build(spec, args.profiles, force=args.force)
    if args.command == "verify":
        return verify(spec)
    if args.command == "verify-isolated":
        return verify_isolated(spec, args.profiles[0] if args.profiles else None)
    if args.command == "list":
        return list_profiles(spec)
    if args.command == "plan":
        if not args.profiles:
            parser.error("plan requires a profile slug")
        return print_plan(spec, args.profiles[0])
    if args.command == "link-identical":
        report = link_identical(GENERATED, commit=args.commit)
        print(json.dumps(report, indent=2, sort_keys=True))
        if not args.commit:
            print("dry run: nothing linked; rerun with --commit to create the links")
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
