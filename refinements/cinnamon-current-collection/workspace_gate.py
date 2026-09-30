"""Current-source acceptance gate for opted-in native workspace geometry.

This module only reads receipts and staged collection files. It never starts a
GUI, modifies a theme, or writes settings. The coordinator owns its call site.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import types


NATIVE_CHECKS = (
    "Native rounded workspace allocation, label and strip containment",
    "All native numbered workspace controls clicked and original workspace restored",
)
POPUP_CHECKS = (
    "menu@cinnamon.org opens and closes",
    "notifications@cinnamon.org opens and closes",
    "sound@cinnamon.org opens and closes",
    "Island actual × close",
    "Island actual Escape",
    "Island outside click",
    "Island ReloadXlet lifecycle",
    "Cinnamon theme reload",
)
EVIDENCE_SCOPE = (
    "fresh workspace geometry, clicks, popups; broader prior acceptance "
    "retained only for unchanged surfaces"
)


def _component(value, field):
    if (not isinstance(value, str) or not value or value in (".", "..")
            or Path(value).name != value or "/" in value or "\\" in value):
        raise ValueError("Invalid workspace source component: " + field)
    return value


def _file_hash(path):
    if not path.is_file():
        raise ValueError("Required workspace source file is absent: " + path.name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(isolate, path):
    if not path.is_dir() or not any(item.is_file() for item in path.rglob("*")):
        raise ValueError("Required workspace source tree is absent or empty: " + path.name)
    return isolate.sha256_tree(path)


def verify_workspace_receipt(slug, profile, raw, collection_root):
    """Return a fail-closed review of a native workspace receipt and its sources.

    The nine bindings use the exact fingerprint conventions of isolate.py.
    Profiles without a workspace_geometry contract retain their existing gate.
    """
    result = {"ok": False, "reason": "", "bindings": {}, "checks": {},
              "evidence_scope": EVIDENCE_SCOPE}
    try:
        if not isinstance(profile, dict):
            raise ValueError("Workspace profile metadata is not an object")
        contract = profile.get("workspace_geometry")
        if contract is None:
            result.update(ok=True, reason="Profile does not opt into workspace geometry",
                          evidence_scope="Workspace gate does not apply to this profile.")
            return result
        if not isinstance(contract, dict):
            raise ValueError("Workspace geometry contract is not an object")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
            raise ValueError("Workspace profile identity is invalid")
        if not isinstance(raw, dict):
            raise ValueError("Workspace receipt metadata is not an object")
        # Receipts are JSON data. Reject NaN/infinity in any evidence field,
        # including fields not passed through the allocated-bounds validator.
        json.dumps(raw, allow_nan=False)
        if (raw.get("status") != "passed" or raw.get("profile") != slug
                or not isinstance(profile.get("name"), str)
                or raw.get("name") != profile["name"]):
            raise ValueError("Workspace receipt status or profile identity differs")

        root = Path(collection_root).resolve()
        harness_name = profile.get('private_harness', 'isolate.py')
        if harness_name not in ('isolate.py', 'candidate_isolate.py'):
            raise ValueError('Unreviewed workspace private harness')
        harness = root / harness_name
        harness_bytes = harness.read_bytes()
        # Compile the exact current bytes without import-loader pycache writes.
        # Its __name__ is not __main__, and GUI imports/launches are in functions
        # that this gate never calls.
        isolate = types.ModuleType("_workspace_gate_current_isolate")
        isolate.__file__ = str(harness)
        exec(compile(harness_bytes, str(harness), "exec"), isolate.__dict__)
        canonical_profiles = isolate.SPEC.get("profiles", {})
        if canonical_profiles.get(slug) != profile:
            raise ValueError("Workspace profile is absent or differs from the current canonical profile")

        recorded_checks = raw.get("checks")
        if (not isinstance(recorded_checks, list)
                or any(not isinstance(check, str) for check in recorded_checks)):
            raise ValueError("Workspace native check evidence is incomplete")
        required = (*NATIVE_CHECKS, *POPUP_CHECKS)
        missing = sorted(set(required) - set(recorded_checks))
        result["checks"] = {"required": list(required), "missing": missing}
        if missing:
            raise ValueError("Missing native workspace checks: " + ", ".join(missing))

        expected_screen = isolate.DEFAULT_SCREEN
        if raw.get("preview_screen") != expected_screen:
            raise ValueError("Workspace preview size differs from the current acceptance framebuffer")
        width, height = (int(part) for part in isolate.validate_screen(expected_screen).split("x"))
        actual_screen = raw.get("actual_screen")
        if (not isinstance(actual_screen, dict)
                or type(actual_screen.get("width")) is not int
                or type(actual_screen.get("height")) is not int
                or actual_screen["width"] != width or actual_screen["height"] != height):
            raise ValueError("Workspace actual framebuffer differs from its preview size")
        result["checks"]["framebuffer"] = expected_screen

        measured = raw.get("workspace_geometry")
        if not isinstance(measured, dict):
            raise ValueError("Native workspace geometry evidence is absent")
        isolate.verify_workspace_geometry(measured, contract)
        clicks = raw.get("workspace_clicks")
        expected_clicks = list(range(1, len(measured["buttons"]) + 1))
        if (not isinstance(clicks, list) or not clicks
                or any(type(value) is not int for value in clicks)
                or clicks != expected_clicks):
            raise ValueError("Native workspace clicks are absent or incomplete")
        result["checks"].update(geometry=True, clicks=clicks)

        generated = root / "generated" / slug
        theme = _component(profile.get("theme"), "theme")
        icons = _component(profile.get("icons"), "icons")
        cursor = _component(profile.get("cursor"), "cursor")
        island = _component(profile.get("island_uuid", "nothing-island@desktop-theme-studio"), "island_uuid")
        expected_bindings = {
            "harness_sha256": hashlib.sha256(harness_bytes).hexdigest(),
            "workspace_runtime_sha256": _tree_hash(isolate, root / "runtime" / "workspace-switcher-rounded"),
            "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
            "theme_sha256": _tree_hash(isolate, generated / "desktop" / theme),
            "island_sha256": _tree_hash(isolate, generated / "island" / island),
            "icon_sha256": _tree_hash(isolate, generated / "icons" / icons),
            "cursor_sha256": _tree_hash(isolate, generated / "cursors" / cursor),
            "wallpaper_sha256": _file_hash(generated / "artwork" / "wallpaper.png"),
            "menu_logo_sha256": _file_hash(generated / "artwork" / "menu-logo.png"),
        }
        if harness_name == 'candidate_isolate.py':
            expected_bindings['candidate_layout_sha256'] = _file_hash(root / 'candidate_layout.py')
            expected_bindings['candidate_receiver_sha256'] = _file_hash(root / 'candidate_receiver.py')
        recorded_bindings = raw.get("fixture_fingerprints")
        if not isinstance(recorded_bindings, dict):
            raise ValueError("Workspace source fingerprints are absent")
        failures = []
        for key, expected in expected_bindings.items():
            accepted = recorded_bindings.get(key)
            matches = isinstance(accepted, str) and bool(re.fullmatch(r"[0-9a-f]{64}", accepted)) and accepted == expected
            result["bindings"][key] = {"current_sha256": expected, "receipt_sha256": accepted, "matches": matches}
            if not matches:
                failures.append(key)
        if failures:
            raise ValueError("Stale or incomplete workspace source bindings: " + ", ".join(failures))
        result.update(ok=True, reason="Current native workspace geometry, clicks, popups, framebuffer and all nine source bindings accepted")
    except Exception as error:
        result.update(ok=False, reason=str(error) or type(error).__name__)
    return result
