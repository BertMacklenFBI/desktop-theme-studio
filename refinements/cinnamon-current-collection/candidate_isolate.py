#!/usr/bin/env python3
"""Run one generated Cinnamon preset in a disposable Xephyr session.

The parent process gives the child an isolated HOME, XDG directories, D-Bus
session, and X server. It never changes the user's live Cinnamon settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
CANDIDATES = ROOT / "contributions/all-theme-plan/collection-profile-candidates.json"
if CANDIDATES.is_file():
    SPEC = {**SPEC, "profiles": {**SPEC["profiles"], **json.loads(CANDIDATES.read_text())["profiles"]}}
import types
candidate_layout = types.ModuleType("_candidate_layout")
candidate_layout.__file__ = str(ROOT / "candidate_layout.py")
exec(compile((ROOT / "candidate_layout.py").read_bytes(), candidate_layout.__file__, "exec"), candidate_layout.__dict__)
DEFAULT_RUN = ROOT / "verification" / "isolated"
DEFAULT_SCREEN = "2880x1800"


TITLE = 'Overtime status'
BODY = 'Workspace checks complete — take a short break.'
APP_NAME = 'Desktop Theme Studio RPO acceptance'
CHECK = 'notification banner with representative content'


def _contained(rect, outer, label):
    if not isinstance(rect, dict) or not all(rect.get(key) for key in ('visible', 'mapped')):
        raise RuntimeError(label + ' is not visible and mapped')
    if rect.get('opacity', 0) < 200 or min(rect.get('s', [0, 0])) <= 0:
        raise RuntimeError(label + ' is not fully painted with positive bounds')
    for axis in (0, 1):
        start, size = rect['p'][axis], rect['s'][axis]
        if start < outer['p'][axis] - 1 or start + size > outer['p'][axis] + outer['s'][axis] + 1:
            raise RuntimeError(label + ' is outside its required native bounds')


def _check_banner_state(state, screen_size):
    if not isinstance(state, dict) or not state.get('matching_notification'):
        raise RuntimeError('The fixture notification is not the current native banner')
    if state.get('title_text') != TITLE or state.get('body_text') != BODY:
        raise RuntimeError('Native notification title/body differ from representative fixture text')
    if state.get('notification_state') != 2:  # MessageTray.State.SHOWN
        raise RuntimeError('Native notification animation has not reached SHOWN')
    screen = {'p': [0, 0], 's': list(screen_size)}
    _contained(state, screen, 'Native notification banner')
    _contained(state['title'], state, 'Native notification title')
    _contained(state['body'], state, 'Native notification body')
    return state


def exercise_rpo_notification(evaluate, capture_popup, run, private_bus, *,
                              screen_size, palette, inspect_pixels,
                              timeout=8.0, monotonic=time.monotonic, sleep=time.sleep):
    """Capture one real Notify banner and return evidence only after all checks pass."""
    from gi.repository import Gio, GLib

    screen_size = tuple(screen_size)
    if screen_size != (2880, 1800):
        raise RuntimeError('RPO notification supplement requires actual private 2880x1800 display')
    if not 0 < timeout <= 8:
        raise ValueError('Notification acceptance timeout must be positive and at most eight seconds')
    deadline = monotonic() + timeout
    destination = Path(run) / 'rpo-notification-banner.png'
    notification_id = None
    completed = None
    last_problem = None
    first_visible_state = None

    def remaining():
        left = deadline - monotonic()
        if left <= 0:
            raise RuntimeError('Native representative notification capture exceeded its eight-second deadline: ' + str(last_problem) + '; first fixture state: ' + json.dumps(first_visible_state))
        return left

    try:
        reply = private_bus.call_sync(
            'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
            'org.freedesktop.Notifications', 'Notify',
            GLib.Variant('(susssasa{sv}i)', (APP_NAME, 0, 'dialog-information', TITLE, BODY, [],
                {'transient': GLib.Variant('b', True),
                 'suppress-sound': GLib.Variant('b', True)}, 6000)),
            GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE,
            max(1, int(remaining() * 1000)), None)
        notification_id = reply.unpack()[0]
        if not isinstance(notification_id, int) or notification_id <= 0:
            raise RuntimeError('Private Notify did not return a notification ID')
        # Only inspect the daemon entry for our newly returned ID. If another
        # banner is active, do not inspect its text, source or stored records.
        identity = ('(function(){const m=imports.ui.main,t=m.messageTray,'
                    'entry=m.notificationDaemon._notifications[' + str(notification_id) + '];'
                    'return !!(entry && entry.notification && t._notification===entry.notification);})()')
        base = '({isOpen:' + identity + ',actor:imports.ui.main.messageTray._notificationBin})'
        state_expression = (
            '(function(){const m=imports.ui.main,t=m.messageTray,'
            'entry=m.notificationDaemon._notifications[' + str(notification_id) + '];'
            'const n=entry && entry.notification;'
            'if(!n || t._notification!==n)return {matching_notification:false};'
            'const rect=a=>({p:a.get_transformed_position(),s:a.get_transformed_size(),'
            'visible:a.visible,mapped:a.mapped,opacity:a.get_paint_opacity()});'
            'const title=n._titleLabel,body=n._bodyUrlHighlighter && n._bodyUrlHighlighter.actor;'
            'if(!title || !body)return {matching_notification:false};'
            'return Object.assign(rect(t._notificationBin),{matching_notification:true,'
            'notification_state:t._notificationState,'
            'title_text:title.clutter_text.get_text(),body_text:body.clutter_text.get_text(),'
            'title:rect(title),body:rect(body)});})()')
        previous = None
        last_problem = None
        while True:
            remaining()
            state = evaluate(state_expression)
            if state.get('matching_notification') and first_visible_state is None:
                first_visible_state = state
            try:
                _check_banner_state(state, screen_size)
            except (RuntimeError, KeyError, TypeError) as error:
                previous = None
                last_problem = str(error)
            else:
                # Require two consecutive native allocations, including text
                # rectangles. A merely existing notification object is insufficient.
                geometry = [state['p'], state['s'], state['title']['p'], state['title']['s'],
                            state['body']['p'], state['body']['s']]
                if geometry == previous:
                    break
                previous = geometry
            left = remaining()
            sleep(min(0.1, left))
        capture_state = capture_popup(base, destination, timeout=remaining())
        remaining()
        after = _check_banner_state(evaluate(state_expression), screen_size)
        if state['p'] != after['p'] or state['s'] != after['s']:
            raise RuntimeError('Native notification banner moved during its screenshot')
        if capture_state['p'] != after['p'] or capture_state['s'] != after['s']:
            raise RuntimeError('Captured popup is not the measured native notification banner')
        pixels = {}
        for label, bounds in [('banner', after), ('title', after['title']), ('body', after['body'])]:
            evidence = inspect_pixels(destination, bounds, screen_size, palette)
            if evidence.get('passed') is not True:
                raise RuntimeError('Native notification ' + label + ' has no accepted screenshot ink: ' + json.dumps(evidence))
            pixels[label] = evidence
        remaining()
        completed = {
            'check': CHECK, 'passed': True, 'notification_id': notification_id,
            'title': TITLE, 'body': BODY, 'state': after, 'path': str(destination),
            'actual_screen': list(screen_size), 'expiration_ms': 6000,
            'capture_pixels': pixels,
            'evidence_scope': 'Fresh real private Cinnamon Notify banner, contained native title/body and screenshot ink; backend actions and live notifications untested',
        }
        return completed
    finally:
        if notification_id is not None:
            try:
                private_bus.call_sync(
                    'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                    'org.freedesktop.Notifications', 'CloseNotification',
                    GLib.Variant('(u)', (notification_id,)), GLib.VariantType('()'),
                    Gio.DBusCallFlags.NONE, 1000, None)
            except Exception as error:
                if completed is not None:
                    raise RuntimeError('Private fixture notification cleanup failed') from error
            else:
                if completed is not None:
                    completed['cleanup'] = 'CloseNotification sent only for the created private ID'


def ensure_private_screen(requested, private_display, host_display, *, run=subprocess.run, sleep=time.sleep):
    """Restore Cinnamon's private Xephyr framebuffer to the requested review size."""
    requested = validate_screen(requested)
    if (not private_display or not host_display
            or private_display.split(".")[0] == host_display.split(".")[0]):
        raise RuntimeError("Refusing framebuffer resize without a distinct private display")
    expected = tuple(int(part) for part in requested.split("x"))

    def command(args):
        result = run(args, capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError("Private framebuffer command failed: " + result.stderr[-1000:])
        return result.stdout

    def geometry(query):
        match = re.search(r"current\s+(\d+)\s+x\s+(\d+)", query)
        if not match:
            raise RuntimeError("Private RandR did not report framebuffer geometry")
        return tuple(int(value) for value in match.groups())

    before = command(["xrandr", "--query"])
    result = {"requested": requested, "before": list(geometry(before)),
              "query_before": before.splitlines()[:10], "resized": False}
    if tuple(result["before"]) != expected:
        command(["xrandr", "--output", "default", "--mode", requested])
        result["resized"] = True
        sleep(1)
    after = command(["xrandr", "--query"])
    result.update(after=list(geometry(after)), query_after=after.splitlines()[:10])
    if tuple(result["after"]) != expected:
        raise RuntimeError("Private framebuffer differs from requested review size: " + json.dumps(result))
    return result
def verify_workspace_geometry(measured, contract):
    if contract.get('shape') not in ('circle', 'pill', 'rounded'):
        raise RuntimeError('Unknown workspace geometry shape')
    if not measured.get('buttons'):
        raise RuntimeError('Workspace buttons are absent')
    def finite(values, length):
        return (isinstance(values, (list, tuple)) and len(values) == length
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in values))

    actors = [measured.get('strip'), measured.get('panel')]
    actors += [actor for button in measured['buttons'] for actor in (button.get('bounds'), button.get('label'))]
    if any(not actor or not finite(actor.get('p'), 2) or not finite(actor.get('s'), 2) or min(actor['s']) <= 0 for actor in actors):
        raise RuntimeError('Workspace native bounds are incomplete or non-finite')
    if not finite(measured.get('strip_radii'), 4):
        raise RuntimeError('Workspace backing radius evidence is non-finite')
    for button in measured['buttons']:
        if any(not finite(state.get('radii'), 4) for state in button.get('states', [])):
            raise RuntimeError('Workspace state radius evidence is non-finite')
    strip = measured['strip']
    if len(measured.get('strip_radii', [])) != 4 or min(measured['strip_radii']) < min(strip['s']) / 2 - 1:
        raise RuntimeError('Workspace backing strip is not fully rounded')
    if not strip['visible'] or not strip['mapped'] or min(strip['s']) <= 0:
        raise RuntimeError('Workspace strip is not visible')
    panel = measured.get('panel')
    if not panel:
        raise RuntimeError('Workspace panel bounds are absent')
    if panel:
        for axis in (0, 1):
            if strip['p'][axis] < panel['p'][axis] - 1 or strip['p'][axis] + strip['s'][axis] > panel['p'][axis] + panel['s'][axis] + 1:
                raise RuntimeError('Workspace strip overflows the panel')
    previous_end = None
    for button in measured['buttons']:
        bounds, label = button['bounds'], button['label']
        x, y = bounds['p']
        width, height = bounds['s']
        if not bounds['visible'] or not bounds['mapped'] or min(width, height) <= 0:
            raise RuntimeError('Workspace button is not visible')
        if contract['shape'] == 'circle' and abs(width - height) > 1:
            raise RuntimeError('Workspace circle is not circular: ' + str(bounds))
        if contract['shape'] == 'rounded' and width < height - 1:
            raise RuntimeError('Rounded workspace button is taller than wide: ' + str(bounds))
        if contract['shape'] == 'pill' and width < height + 2:
            raise RuntimeError('Workspace pill is not horizontal: ' + str(bounds))
        expected_states = {'', 'hover', 'outlined', 'outlined hover', 'shaded', 'shaded hover', 'outlined shaded', 'outlined shaded hover'}
        if len(button.get('states', [])) != 8 or {row['state'] for row in button['states']} != expected_states:
            raise RuntimeError('Workspace state geometry evidence is incomplete')
        if any(len(state.get('radii', [])) != 4 or min(state['radii']) < min(width, height) / 2 - 1 for state in button['states']):
            raise RuntimeError('Workspace state is not fully rounded')
        for child in (bounds, label):
            for axis in (0, 1):
                if child['p'][axis] < strip['p'][axis] - 1 or child['p'][axis] + child['s'][axis] > strip['p'][axis] + strip['s'][axis] + 1:
                    raise RuntimeError('Workspace control or label overflows the strip')
        for axis in (0, 1):
            if label['p'][axis] < bounds['p'][axis] - 1 or label['p'][axis] + label['s'][axis] > bounds['p'][axis] + bounds['s'][axis] + 1:
                raise RuntimeError('Workspace number overflows its button')
        if not label['visible'] or not label['mapped'] or min(label['s']) <= 0:
            raise RuntimeError('Workspace number is not visible')
        if previous_end is not None and x - previous_end < contract.get('minimum_gap', 3) - 1:
            raise RuntimeError('Workspace buttons overlap or lack spacing')
        previous_end = x + width


POPUP_APPLETS = (
    "menu@cinnamon.org", "notifications@cinnamon.org", "sound@cinnamon.org",
    "nothing-island@desktop-theme-studio",
)


def popup_capture_pixels(path, state, screen_size, palette):
    """Reject blank or stale captures inside the measured native popup bounds."""
    from PIL import Image, ImageStat
    with Image.open(path) as shot:
        if shot.size != tuple(screen_size):
            return {"passed": False, "reason": "Screenshot dimensions differ from the actual display",
                    "capture_size": list(shot.size), "actual_size": list(screen_size)}
        x, y = state["p"]
        width, height = state["s"]
        box = (max(0, math.ceil(x + 2)), max(0, math.ceil(y + 2)),
               min(shot.width, math.floor(x + width - 2)),
               min(shot.height, math.floor(y + height - 2)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return {"passed": False, "reason": "Popup crop has no area", "crop": list(box)}
        crop = shot.convert("RGB").crop(box)
    histogram = crop.getcolors(crop.width * crop.height)
    area = crop.width * crop.height
    colors = [tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
              for key, value in palette.items()
              if key in ("background", "surface", "raised", "foreground", "muted", "selection")]
    ink = {tuple(int(palette[key][i:i + 2], 16) for i in (1, 3, 5))
           for key in ("foreground", "muted") if key in palette}
    matched = sum(count for count, rgb in histogram
                  if any(max(abs(a - b) for a, b in zip(rgb, color)) <= 20
                         for color in colors)) / area
    ink_fraction = sum(count for count, rgb in histogram
                       if any(max(abs(a - b) for a, b in zip(rgb, color)) <= 20
                              for color in ink)) / area
    deviation = max(ImageStat.Stat(crop).stddev)
    brightness = max(max(rgb) for count, rgb in histogram)
    return {"passed": brightness > 8 and deviation > 0.5 and matched >= 0.2 and ink_fraction >= 0.001,
            "capture_size": list(screen_size), "crop": list(box),
            "maximum_brightness": brightness, "maximum_stddev": deviation,
            "palette_fraction": matched, "ink_fraction": ink_fraction,
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def capture_painted_popup(capture, read_state, inspect_pixels, *, timeout=6,
                          monotonic=time.monotonic, sleep=time.sleep):
    """Wait for actual painted pixels while checking the popup stays mapped."""
    deadline = monotonic() + timeout
    attempts = []
    while True:
        before = read_state()
        capture()
        after = read_state()
        pixels = inspect_pixels(before)
        attempts.append({"before": before, "after": after, "pixels": pixels})
        if before["p"] == after["p"] and before["s"] == after["s"] and pixels.get("passed") is True:
            return {"state": after, "attempts": attempts}
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError("Popup did not produce a stable painted screenshot: " + json.dumps(attempts[-1]))
        sleep(min(0.25, remaining))


def wait_for_popup_applets(evaluate, shell, *, applets=POPUP_APPLETS, timeout=45, monotonic=time.monotonic,
                          sleep=time.sleep):
    """Wait for constructed private applets, then let visibility probes judge them."""
    code = "(function(){return " + json.dumps(applets) + ".map(function(uuid){"
    code += "const definitions=imports.ui.appletManager.filterDefinitionsByUUID(uuid);"
    code += "const applet=definitions.length ? definitions[0].applet : null;"
    code += "const menu=applet && applet.menu;return {uuid:uuid,ready:!!(applet && "
    code += "applet.actor && menu && menu.actor && typeof menu.open==='function' && "
    code += "typeof menu.close==='function')};});})()"
    started = monotonic()
    last = None
    while True:
        if shell.poll() is not None:
            raise RuntimeError("Isolated Cinnamon exited while loading popup applets")
        try:
            last = evaluate(code)
            if (isinstance(last, list) and len(last) == len(applets)
                    and {row.get("uuid") for row in last if isinstance(row, dict)} == set(applets)
                    and all(isinstance(row, dict) and row.get("ready") is True for row in last)):
                return {"elapsed_seconds": round(monotonic() - started, 3), "applets": last}
        except RuntimeError as error:
            # Cinnamon can export its D-Bus peer before Eval or appletManager
            # is initialized. Retain the exception for a bounded timeout.
            last = {"evaluation_error": str(error)}
        remaining = timeout - (monotonic() - started)
        if remaining <= 0:
            raise RuntimeError("Private popup applets did not become ready: " + json.dumps(last))
        sleep(min(0.5, remaining))


def decode_evaluation(success: bool, raw: str, code: str):
    """Keep native exceptions and nested JSON from the private Eval peer."""
    value = raw
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            break
    if not success or not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(code + ": " + str(value))
    value = value.get("value")
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


def validate_screen(value: str) -> str:
    match = re.fullmatch(r"([0-9]{3,5})x([0-9]{3,5})", value)
    if not match or not 640 <= int(match.group(1)) <= 16384 or not 480 <= int(match.group(2)) <= 16384:
        raise argparse.ArgumentTypeError("screen must be WIDTHxHEIGHT within 640x480..16384x16384")
    return value


def profile(slug: str) -> dict:
    candidate_file = os.environ.get("CURRENT_COLLECTION_PROFILE_CANDIDATES")
    spec = json.loads(Path(candidate_file).read_text(encoding="utf-8")) if candidate_file else SPEC
    value = spec["profiles"].get(slug)
    if value is None:
        raise KeyError(f"Unknown profile: {slug}")
    return value


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(item.read_bytes())
    return digest.hexdigest()


def existing_spice_setting(key: str) -> dict:
    directory = Path.home() / ".config" / "cinnamon" / "spices"
    candidates = sorted(directory.rglob("*.json"))
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and key in value:
            return value
    raise RuntimeError(f"No current Cinnamon spice setting exposes {key!r}")


def copytree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))


def cleanup_fixture(run: Path) -> None:
    """Remove only this runner's disposable HOME/XDG copy trees."""
    for name in ("home", "config", "data", "cache", "state"):
        path = run / name
        if path.exists():
            shutil.rmtree(path)


def reap_private_children(environment):
    """Reap only this disposable HOME/display's remaining owned children."""
    import signal
    home, display = environment['HOME'], environment['DISPLAY']
    if display == environment.get('CURRENT_COLLECTION_HOST_DISPLAY') or home == str(Path.home()):
        raise RuntimeError('Refusing cleanup outside the private HOME/display')
    def owned():
        result = {}
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                if entry.stat().st_uid != os.getuid():continue
                values = dict(row.split(b'=',1) for row in (entry/'environ').read_bytes().split(b'\0') if b'=' in row)
                if values.get(b'HOME') != home.encode() or values.get(b'DISPLAY') != display.encode():continue
                fields = (entry/'stat').read_text().rsplit(')',1)[1].split()
                result[int(entry.name)] = fields[19]
            except (OSError,ValueError):continue
        return result
    initial = owned()
    for pid, birth in initial.items():
        if owned().get(pid) == birth:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
    deadline = time.monotonic()+3
    while owned() and time.monotonic()<deadline:time.sleep(.1)
    for pid, birth in owned().items():
        if initial.get(pid)==birth:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
    return {'scope':'Exact disposable HOME, private display, user UID and PID birth identity',
            'owned_pids':sorted(initial),'remaining_owned_pids':sorted(owned())}


def cleanup_private_server(server, read_fd, environment, run):
    try:
        private_display = environment.get('DISPLAY')
        if private_display and private_display != environment.get('CURRENT_COLLECTION_HOST_DISPLAY'):
            cleanup = reap_private_children(environment)
            (run / 'private-process-cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
    finally:
        try:
            os.close(read_fd)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def parent(slug: str) -> int:
    item = profile(slug)
    generated_root = Path(os.environ.get("CURRENT_COLLECTION_GENERATED_ROOT", ROOT / "generated"))
    verify_root = Path(os.environ.get("CURRENT_COLLECTION_VERIFY_ROOT", DEFAULT_RUN))
    generated = generated_root / slug
    source_theme = generated / "desktop" / item["theme"]
    island_uuid = item.get("island_uuid", "nothing-island@desktop-theme-studio")
    source_island = generated / "island" / island_uuid
    wallpaper = generated / "artwork" / "wallpaper.png"
    menu_logo = generated / "artwork" / "menu-logo.png"
    source_icons = generated / "icons" / item["icons"]
    source_cursors = generated / "cursors" / item["cursor"]
    source_workspace = ROOT / "runtime" / "workspace-switcher-rounded"
    if not all(path.exists() for path in (source_theme, source_island, source_icons, source_cursors, wallpaper, menu_logo)):
        raise RuntimeError(f"Build {slug} before its isolated test")
    fixture_fingerprints = {
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidate_layout_sha256": hashlib.sha256((ROOT / "candidate_layout.py").read_bytes()).hexdigest(),
        "candidate_receiver_sha256": hashlib.sha256((ROOT / "candidate_receiver.py").read_bytes()).hexdigest(),
        "workspace_runtime_sha256": sha256_tree(source_workspace),
        "profile_sha256": hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "theme_sha256": sha256_tree(source_theme),
        "island_sha256": sha256_tree(source_island),
        "icon_sha256": sha256_tree(source_icons),
        "cursor_sha256": sha256_tree(source_cursors),
        "wallpaper_sha256": hashlib.sha256(wallpaper.read_bytes()).hexdigest(),
        "menu_logo_sha256": hashlib.sha256(menu_logo.read_bytes()).hexdigest(),
    }

    if item.get('authored_eww_bundle'):
        fixture_fingerprints['authored_receiver_tree_sha256'] = sha256_tree(generated / 'widgets/authored')

    run_parent = verify_root / slug
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = run_parent / stamp
    duplicate = 1
    while run.exists():
        run = run_parent / f"{stamp}-{duplicate:02d}"
        duplicate += 1
    run.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["CURRENT_COLLECTION_RUN"] = str(run)
    env["CURRENT_COLLECTION_PROFILE"] = slug
    env["CURRENT_COLLECTION_GENERATED_ROOT"] = str(generated_root)
    env["CURRENT_COLLECTION_VERIFY_ROOT"] = str(verify_root)
    env["CURRENT_COLLECTION_FIXTURE_FINGERPRINTS"] = json.dumps(fixture_fingerprints, sort_keys=True)
    for name, part in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        path = run / part
        path.mkdir(mode=0o700)
        env[name] = str(path)
    # Native grouped-window-list reads this HOME path directly even when
    # XDG_CONFIG_HOME is separate. Populate the disposable empty namespace.
    (run / 'home/.config/autostart').mkdir(parents=True,exist_ok=True)
    (run / 'config/autostart').mkdir(parents=True,exist_ok=True)
    runtime = tempfile.TemporaryDirectory(prefix="cinnamon-current-preview-")
    env["XDG_RUNTIME_DIR"] = runtime.name
    try:
        copytree(source_workspace, run / "data" / "cinnamon" / "applets" / "workspace-switcher@cinnamon.org")
        copytree(source_theme, run / "data" / "themes" / item["theme"])
        copytree(source_island, run / "data" / "cinnamon" / "applets" / island_uuid)
        # Preview the exact packaged trees consumed by the candidate system
        # profile, rather than reaching back to mutable upstream source dirs.
        copytree(source_icons, run / "data" / "icons" / item["icons"])
        copytree(source_cursors, run / "data" / "icons" / item["cursor"])
        font_source = source_island / "fonts"
        if font_source.is_dir():
            copytree(font_source, run / "data" / "fonts" / "nothing-island")
            subprocess.run(["fc-cache", "-f", str(run / "data" / "fonts")], env=env, capture_output=True, check=True, timeout=30)

        menu = existing_spice_setting("menu-icon")
        menu["menu-icon"]["value"] = str(menu_logo)
        menu_target = run / "config" / "cinnamon" / "spices" / "menu@cinnamon.org" / "901.json"
        menu_target.parent.mkdir(parents=True, exist_ok=True)
        menu_target.write_text(json.dumps(menu, indent=2) + "\n", encoding="utf-8")
        workspace = existing_spice_setting("display-type")
        workspace["display-type"]["value"] = "buttons"
        workspace_target = run / "config" / "cinnamon" / "spices" / "workspace-switcher@cinnamon.org" / "902.json"
        workspace_target.parent.mkdir(parents=True, exist_ok=True)
        workspace_target.write_text(json.dumps(workspace, indent=2) + "\n", encoding="utf-8")
        gtk = run / "config" / "gtk-3.0" / "settings.ini"
        gtk.parent.mkdir(parents=True, exist_ok=True)
        ui_font = item.get('typography', {}).get('interface', 'Ubuntu 11')
        gtk.write_text("[Settings]\n" + f"gtk-theme-name={item['theme']}\n" + f"gtk-icon-theme-name={item['icons']}\n" + f"gtk-cursor-theme-name={item['cursor']}\n" + f"gtk-font-name={ui_font}\n", encoding="utf-8")

        env.update({
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "LP_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "CLUTTER_DEFAULT_FPS": "15",
            "GSETTINGS_BACKEND": "dconf",
            "XDG_CURRENT_DESKTOP": "X-Cinnamon",
            "XDG_SESSION_TYPE": "x11",
            "CURRENT_COLLECTION_HOST_DISPLAY": os.environ.get("DISPLAY", ""),
            "CURRENT_COLLECTION_HOST_BUS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        })
        # Both the private server and Cinnamon use software rendering. Avoid
        # loading the host NVIDIA EGL vendor in disposable Xephyr previews.
        mesa_vendor = Path("/usr/share/glvnd/egl_vendor.d/50_mesa.json")
        if mesa_vendor.is_file():
            env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(mesa_vendor)
            env["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
        for key in ("SESSION_MANAGER", "GTK_THEME", "DCONF_PROFILE", "CINNAMON_VERSION"):
            env.pop(key, None)
        read_fd, write_fd = os.pipe()
        with (run / "xephyr.log").open("w", encoding="utf-8") as log:
            screen = os.environ.get("CURRENT_COLLECTION_PREVIEW_SCREEN", DEFAULT_SCREEN)
            server = subprocess.Popen(["Xephyr", "-displayfd", str(write_fd), "-screen", screen, "-nolisten", "tcp", "-noreset", "-no-host-grab", "-title", f"{item['name']} — isolated preview"], env=env, pass_fds=(write_fd,), stdout=log, stderr=log)
            os.close(write_fd)
            try:
                if not select.select([read_fd], [], [], 15)[0]:
                    raise RuntimeError("Private display startup timed out")
                number = os.read(read_fd, 32).decode("utf-8").strip()
                if not number.isdigit():
                    raise RuntimeError("Private display startup failed")
                env["DISPLAY"] = ":" + number
                completed = subprocess.run(["dbus-run-session", "--", sys.executable, __file__, "worker"], env=env, timeout=240)
                cleanup_fixture(run)
                return completed.returncode
            finally:
                cleanup_private_server(server, read_fd, env, run)
    except Exception as error:
        # Preserve environment/setup failures as explicit evidence instead of
        # leaving a timestamped run directory that looks like it disappeared.
        failed = {
            "profile": slug,
            "name": item["name"],
            "status": "blocked",
            "reason": str(error),
            "run": str(run),
            "generated_root": str(generated_root),
            "candidate_file": os.environ.get("CURRENT_COLLECTION_PROFILE_CANDIDATES"),
            "preview_screen": os.environ.get("CURRENT_COLLECTION_PREVIEW_SCREEN", DEFAULT_SCREEN),
            "fixture_fingerprints": fixture_fingerprints,
            "fixture_cleanup": "Private HOME/XDG copy trees removed; report and Xephyr log retained.",
            "live_settings_modified": False,
        }
        cleanup_fixture(run)
        (run / "report.json").write_text(json.dumps(failed, indent=2) + "\n", encoding="utf-8")
        latest = verify_root.parent / "isolated-latest" / f"{slug}.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps(failed, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failed), flush=True)
        return 2
    finally:
        runtime.cleanup()


def worker() -> int:
    from gi.repository import Gio, GLib

    run = Path(os.environ["CURRENT_COLLECTION_RUN"])
    slug = os.environ["CURRENT_COLLECTION_PROFILE"]
    item = profile(slug)
    assert os.environ["DISPLAY"] != os.environ["CURRENT_COLLECTION_HOST_DISPLAY"]
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] != os.environ["CURRENT_COLLECTION_HOST_BUS"]
    source = run / "data" / "themes" / item["theme"]
    island_uuid = item.get("island_uuid", "nothing-island@desktop-theme-studio")
    island_source = run / "data" / "cinnamon" / "applets" / island_uuid
    generated_root = Path(os.environ.get("CURRENT_COLLECTION_GENERATED_ROOT", ROOT / "generated"))
    wallpaper = generated_root / slug / "artwork" / "wallpaper.png"

    def setting(schema: str, key: str, value: object) -> None:
        current = Gio.Settings.new(schema).get_value(key)
        Gio.Settings.new(schema).set_value(key, GLib.Variant(current.get_type_string(), value))
        Gio.Settings.sync()

    def bus(method: str, *args: str) -> str:
        result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.Cinnamon", "--object-path", "/org/Cinnamon", "--method", method, *args], capture_output=True, text=True, timeout=15)
        result.check_returncode()
        return result.stdout.strip()

    def evaluate(code: str):
        # Catch the initial expression once: retrying a failed popup open can
        # hide its exception because isOpen may already have been set.
        script = "(function(){try{return JSON.stringify({ok:true,value:eval(" + json.dumps(code) + ")});}"
        script += "catch(error){return JSON.stringify({ok:false,error:String(error),stack:error.stack});}})()"
        result = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            "org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "Eval",
            GLib.Variant("(s)", (script,)), GLib.VariantType("(bs)"),
            Gio.DBusCallFlags.NONE, 15000, None).unpack()
        return decode_evaluation(result[0], result[1], code)

    panel = candidate_layout.settings(item)
    for key, value in panel.items():
        setting("org.cinnamon", key, value)
    for schema in ("org.cinnamon.desktop.interface", "org.gnome.desktop.interface"):
        setting(schema, "gtk-theme", item["theme"])
        setting(schema, "icon-theme", item["icons"])
        setting(schema, "font-name", item.get('typography', {}).get('interface', 'Ubuntu 11'))
    setting("org.cinnamon.desktop.interface", "cursor-theme", item["cursor"])
    setting("org.cinnamon.theme", "name", item["theme"])
    setting("org.cinnamon.desktop.wm.preferences", "theme", item["theme"])
    setting("org.cinnamon.desktop.background", "picture-uri", wallpaper.as_uri())
    setting("org.gnome.desktop.background", "picture-uri", wallpaper.as_uri())

    log = (run / "cinnamon.log").open("w", encoding="utf-8")
    report: dict[str, object] = {"profile": slug, "name": item["name"], "status": "running", "run": str(run), "preview_screen": os.environ.get("CURRENT_COLLECTION_PREVIEW_SCREEN", DEFAULT_SCREEN), "fixture_fingerprints": json.loads(os.environ.get("CURRENT_COLLECTION_FIXTURE_FINGERPRINTS", "{}")), "theme_sha256": sha256_tree(source), "icon_sha256": sha256_tree(run / "data" / "icons" / item["icons"]), "cursor_sha256": sha256_tree(run / "data" / "icons" / item["cursor"]), "island_sha256": sha256_tree(island_source), "wallpaper_sha256": hashlib.sha256(wallpaper.read_bytes()).hexdigest(), "menu_logo_sha256": hashlib.sha256((generated_root / slug / "artwork" / "menu-logo.png").read_bytes()).hexdigest(), "checks": []}
    processes: list[subprocess.Popen] = []
    try:
        shell = subprocess.Popen(["cinnamon"], stdout=log, stderr=log)
        processes.append(shell)
        processes.append(subprocess.Popen(["/usr/libexec/csd-background", "--exit-time=100"], stdout=log, stderr=log))
        deadline = time.monotonic() + 45
        while True:
            try:
                bus("org.freedesktop.DBus.Peer.Ping")
                break
            except Exception:
                if shell.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Isolated Cinnamon did not start")
                time.sleep(1)
        # The D-Bus peer precedes applet construction on slower starts. Wait
        # for each actual popup object instead of assuming an elapsed delay.
        report["applet_readiness"] = wait_for_popup_applets(evaluate, shell, applets=(*POPUP_APPLETS[:3], island_uuid))
        report["private_framebuffer"] = ensure_private_screen(
            report["preview_screen"], os.environ["DISPLAY"], os.environ["CURRENT_COLLECTION_HOST_DISPLAY"])
        subprocess.run(["feh", "--no-fehbg", "--bg-fill", str(wallpaper)], check=True, capture_output=True, timeout=8)
        from Xlib import X, XK, display
        from Xlib.ext import xtest

        private_x = display.Display()
        screen = private_x.screen()
        report["actual_screen"] = {"width": screen.width_in_pixels, "height": screen.height_in_pixels}
        if [screen.width_in_pixels, screen.height_in_pixels] != report["private_framebuffer"]["after"]:
            raise RuntimeError("Private Xlib screen differs from verified RandR framebuffer")

        report['candidate_layout'] = evaluate("({screen:[global.screen_width,global.screen_height],panels:imports.ui.main.panelManager.panels.filter(p=>p).map(p=>({id:p.panelId,height:p.actor.get_height(),mapped:p.actor.mapped,position:p.actor.get_transformed_position(),size:p.actor.get_transformed_size()}))})")
        candidate_layout.assert_native(report['candidate_layout'], item)
        report['checks'].append('Native declared candidate panel composition')
        workspace = 'imports.ui.appletManager.filterDefinitionsByUUID("workspace-switcher@cinnamon.org")[0].applet'
        deadline = time.monotonic() + 6
        while not evaluate(workspace + '.buttons && ' + workspace + '.buttons.length>0'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Private workspace buttons did not become ready')
            time.sleep(0.25)
        def workspace_allocation():
            return evaluate("(function(){const a=" + workspace + ";return {panelHeight:a._panelHeight,"
                "queued:!!a.createButtonsQueued,buttons:a.buttons.map(b=>({height:b.actor.height,"
                "minimumSet:b.actor.min_height_set,preferred:b.actor.get_preferred_height(-1),"
                "size:b.actor.get_transformed_size(),mapped:b.actor.mapped})),strip:a.actor.get_transformed_size()};})()")
        report['workspace_initialization'] = {'scope':'Cold launch followed by standard theme reload; normal theme-switch acceptance',
            'before':workspace_allocation()}
        bus('org.Cinnamon.ReloadTheme')
        deadline = time.monotonic() + 8
        previous_allocation = None
        stable = 0
        while True:
            measured_allocation = workspace_allocation()
            sizes = [row['size'] for row in measured_allocation['buttons']]
            settled = (not measured_allocation['queued'] and bool(sizes)
                and all(row['mapped'] and abs(row['size'][1] - measured_allocation['panelHeight']) <= 1
                        for row in measured_allocation['buttons']))
            stable = stable + 1 if settled and measured_allocation == previous_allocation else 0
            if stable >= 2:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Native workspace allocation did not settle after standard theme reload: ' + json.dumps(measured_allocation))
            previous_allocation = measured_allocation
            time.sleep(0.25)
        report['workspace_initialization']['after'] = measured_allocation
        report['checks'].append('Native workspace initialization after standard theme reload')
        report['workspace_geometry'] = evaluate("(function(){const a=" + workspace + ";"
            "const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
            "const bounds=b=>({p:b.get_transformed_position(),s:b.get_transformed_size(),"
            "visible:b.visible,mapped:b.mapped});"
            "return {panelHeight:a._panelHeight,panel:bounds(a.panel.actor),strip:bounds(a.actor),strip_radii:[0,1,2,3].map(c=>a.actor.get_theme_node().get_border_radius(c)),buttons:a.buttons.map(b=>{"
            "const actor=b.actor,old=actor.get_style_pseudo_class();"
            "try{return {bounds:bounds(actor),label:bounds(actor.get_children()[0]),"
            "states:['','hover','outlined','outlined hover','shaded','shaded hover','outlined shaded','outlined shaded hover'].map(state=>{"
            "actor.set_style_pseudo_class(state);const n=actor.get_theme_node();"
            "return {state:state,radii:[0,1,2,3].map(c=>n.get_border_radius(c)),"
            "ink:rgba(n.get_foreground_color()),fill:rgba(n.get_background_color())};})};}"
            "finally{actor.set_style_pseudo_class(old);}})};})()")
        if item.get('workspace_geometry'):
            verify_workspace_geometry(report['workspace_geometry'], item['workspace_geometry'])
            report['checks'].append('Native rounded workspace allocation, label and strip containment')
            original_workspace = evaluate('global.workspace_manager.get_active_workspace_index()')
            visits = []
            input_trace = []
            evaluate("(function(){const a=" + workspace + ";global.__dtsCandidateInput=[];a.__dtsInputSignals=a.buttons.map((b,i)=>b.actor.connect('button-release-event',(_a,e)=>{global.__dtsCandidateInput.push({index:i,button:e.get_button(),coords:e.get_coords(),time:e.get_time()});return false;}));return true;})()")
            try:
                for index, button in enumerate(report['workspace_geometry']['buttons']):
                    bounds = button['bounds']
                    target_x = int(bounds['p'][0] + bounds['s'][0] / 2)
                    target_y = int(bounds['p'][1] + bounds['s'][1] / 2)
                    # Throttle before input. Queue motion/press/release together
                    # on the owned private X connection, without a host grab or
                    # a long warp-to-click interval vulnerable to pointer drift.
                    time.sleep(0.25)
                    xtest.fake_input(private_x, X.MotionNotify, x=target_x, y=target_y)
                    xtest.fake_input(private_x, X.ButtonPress, 1)
                    xtest.fake_input(private_x, X.ButtonRelease, 1)
                    private_x.sync()
                    time.sleep(0.05)
                    pointer = private_x.screen().root.query_pointer()
                    input_trace.append({'index':index,'intended_x':target_x,'intended_y':target_y,
                                        'x':pointer.root_x,'y':pointer.root_y})
                    until = time.monotonic() + 8
                    while True:
                        active = evaluate('global.workspace_manager.get_active_workspace_index()')
                        events = evaluate('global.__dtsCandidateInput')
                        received = any(event['index'] == index and event['button'] == 1
                            and abs(event['coords'][0] - target_x) <= 1
                            and abs(event['coords'][1] - target_y) <= 1 for event in events)
                        if active == index and received:
                            break
                        if time.monotonic() > until:
                            raise RuntimeError('Native workspace click lacks matching release/selection: ' + str(index + 1))
                        time.sleep(0.1)
                    visits.append(index + 1)
            finally:
                report['workspace_input_trace'] = {'pointer_targets':input_trace,'visited':visits,
                    'events':evaluate("(function(){const a=" + workspace + ";a.buttons.forEach((b,i)=>b.actor.disconnect(a.__dtsInputSignals[i]));return global.__dtsCandidateInput;})()")}
                evaluate('global.workspace_manager.get_workspace_by_index(' + str(original_workspace) + ').activate(global.get_current_time()); true')
            report['workspace_clicks'] = visits
            report['checks'].append('All native numbered workspace controls clicked and original workspace restored')

        if item.get("legacy_completion"):
            report["panel_paints"] = evaluate("(function(){const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
                "return imports.ui.main.panelManager.panels.filter(p=>p).map(p=>({"
                "classes:p.actor.get_style_class_name(),root:rgba(p.actor.get_theme_node().get_background_color()),"
                "zones:[p._leftBox,p._centerBox,p._rightBox].map(a=>({name:a.name,fill:rgba(a.get_theme_node().get_background_color())}))}));})()")
            workspace = 'imports.ui.appletManager.filterDefinitionsByUUID("workspace-switcher@cinnamon.org")[0].applet'
            deadline = time.monotonic() + 6
            while not evaluate(workspace + '.buttons && ' + workspace + '.buttons.length>0'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Private workspace buttons did not become ready')
                time.sleep(0.25)
            workspace_paints = evaluate("(function(){const b=" + workspace + ".buttons[0].actor;"
                "const old=b.get_style_pseudo_class();const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
                "try{return ['', 'hover', 'outlined', 'outlined hover'].map(state=>{b.set_style_pseudo_class(state);"
                "const n=b.get_theme_node();return {state:state,ink:rgba(n.get_foreground_color()),fill:rgba(n.get_background_color())};});}"
                "finally{b.set_style_pseudo_class(old);}})()")
            report['workspace_paints'] = workspace_paints
            for row in workspace_paints:
                for field, role in (('ink','selection_foreground' if 'outlined' in row['state'] else 'foreground'),
                         ('fill','selection' if 'outlined' in row['state'] else 'hover' if row['state']=='hover' else 'surface')):
                    expected = [int(item['palette'][role][i:i+2],16) for i in (1,3,5)] + [255]
                    if row[field] != expected:
                        raise RuntimeError('Native workspace paint differs from '+role+': '+str(row))

        states = ['', 'hover', 'outlined', 'outlined hover', 'shaded', 'shaded hover', 'outlined shaded', 'outlined shaded hover']
        report['candidate_workspace_paints'] = evaluate("(function(){const b=" + workspace + ".buttons[0];const a=b.actor;const l=a.get_child();const old=a.get_style_pseudo_class();const rgba=c=>[c.red,c.green,c.blue,c.alpha];try{return " + json.dumps(states) + ".map(state=>{a.set_style_pseudo_class(state);const n=a.get_theme_node();return {state:state,ink:rgba(n.get_foreground_color()),label:l?rgba(l.get_theme_node().get_foreground_color()):null,fill:rgba(n.get_background_color())};});}finally{a.set_style_pseudo_class(old);}})()")
        colors = item['candidate_state_colors']
        for row in report['candidate_workspace_paints']:
            prefix = 'active' if 'outlined' in row['state'] else 'hover' if 'hover' in row['state'] else 'normal'
            for key in ('ink', 'fill'):
                value = colors[prefix + '_' + key]
                expected = [int(value[index:index+2], 16) for index in (1,3,5)] + [255]
                if row[key] != expected or (key == 'ink' and row['label'] is not None and row['label'] != expected):
                    raise RuntimeError('Candidate native workspace paint differs: ' + str(row))
        report['checks'].append('Eight native workspace state ink and fill pairs')

        def popup_visibility(base: str, timeout=6):
            deadline = time.monotonic() + timeout
            while True:
                visible = evaluate("JSON.stringify({open:" + base + ".isOpen,visible:" + base + ".actor.visible,mapped:" + base + ".actor.mapped,opacity:" + base + ".actor.opacity,p:" + base + ".actor.get_transformed_position(),s:" + base + ".actor.get_transformed_size()})")
                if (all(visible[key] for key in ("open", "visible", "mapped")) and visible["opacity"] >= 200
                        and min(visible["s"]) > 0 and min(visible["p"]) >= 0
                        and visible["p"][0] + visible["s"][0] <= screen.width_in_pixels + 1
                        and visible["p"][1] + visible["s"][1] <= screen.height_in_pixels + 1):
                    return visible
                if time.monotonic() >= deadline:
                    raise RuntimeError("Popup did not become visible within the actual private display: " + json.dumps(visible))
                time.sleep(0.1)

        report["popup_visibility"] = {}
        report["popup_capture_contract_version"] = 1
        report["popup_capture_evidence"] = {}

        def capture_popup(base, destination, timeout=6):
            evidence = capture_painted_popup(
                lambda: subprocess.run(["scrot", "--overwrite", str(destination)], check=True, timeout=15),
                lambda: popup_visibility(base, timeout=timeout),
                lambda state: popup_capture_pixels(destination, state,
                    (screen.width_in_pixels, screen.height_in_pixels), item["palette"]),
                timeout=timeout,
            )
            report["popup_capture_evidence"][destination.name] = evidence
            return evidence["state"]

        for name in ("menu@cinnamon.org", "notifications@cinnamon.org", "sound@cinnamon.org"):
            base = "imports.ui.appletManager.filterDefinitionsByUUID(" + json.dumps(name) + ")[0].applet.menu"
            evaluate(base + ".open(true); true")
            report["popup_visibility"][name] = popup_visibility(base)
            if name == "menu@cinnamon.org":
                if item.get('legacy_completion'):
                    menu_applet = 'imports.ui.appletManager.filterDefinitionsByUUID("menu@cinnamon.org")[0].applet'
                    search = evaluate("(function(){const a=" + menu_applet + ".searchEntry;const old=a.get_style_pseudo_class();"
                        "const rgba=c=>[c.red,c.green,c.blue,c.alpha];try{return ['', 'hover', 'focus'].map(state=>{"
                        "a.set_style_pseudo_class(state);const n=a.get_theme_node();return {state:state,ink:rgba(n.get_foreground_color()),fill:rgba(n.get_background_color())};});}finally{a.set_style_pseudo_class(old);}})()")
                    report['menu_search_paints'] = search
                    for row in search:
                        for field, role in (('ink','foreground'),('fill','background')):
                            expected=[int(item['palette'][role][i:i+2],16) for i in (1,3,5)]+[255]
                            if row[field] != expected:
                                raise RuntimeError('Native search paint differs from '+role+': '+str(row))
                report["popup_visibility"][name] = capture_popup(base, run / "menu.png")
                if item.get("legacy_completion"):
                    menu_applet = 'imports.ui.appletManager.filterDefinitionsByUUID("menu@cinnamon.org")[0].applet'
                    username = evaluate("(function(){const n=" + menu_applet + ".userIcon._label.get_theme_node();"
                                        "const c=n.get_foreground_color();return [c.red,c.green,c.blue,c.alpha];})()")
                    report["menu_username_ink"] = username
                    expected = [int(item["palette"]["foreground"][i:i + 2], 16) for i in (1, 3, 5)] + [255]
                    if username != expected:
                        raise RuntimeError("Native sidebar username ink differs from foreground: " + str(username))
                    system = evaluate("(function(){const a=" + menu_applet + ";const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
                        "return a.systemBox.get_children().map(b=>{const old=b.get_style_pseudo_class();"
                        "try{return ['', 'hover'].map(state=>{b.set_style_pseudo_class(state);const n=b.get_theme_node();"
                        "return {classes:b.get_style_class_name(),state:state,fill:rgba(n.get_background_color()),"
                        "icon:rgba(b._delegate.icon.get_theme_node().get_foreground_color())};});}finally{b.set_style_pseudo_class(old);}});})()")
                    report['menu_system_paints'] = system
                    for button in system:
                        for row in button:
                            for field, role in (('icon','foreground'),('fill','hover' if row['state']=='hover' else 'raised')):
                                expected=[int(item['palette'][role][i:i+2],16) for i in (1,3,5)]+[255]
                                if row[field]!=expected:
                                    raise RuntimeError('Native menu system paint differs from '+role+': '+str(row))
                if item.get("legacy_completion") and item["theme"] == "Eucalyptus-Glass-Panel":
                    evaluate(menu_applet + "._setCategoriesButtonActive(false); true")
                    grey = evaluate("(function(){const b=" + menu_applet + "._categoryButtons[0];"
                                    "const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
                                    "return JSON.stringify({class:b.actor.get_style_class_name(),"
                                    "label:rgba(b.label.get_theme_node().get_foreground_color()),"
                                    "icon:rgba(b.icon.get_theme_node().get_foreground_color()),"
                                    "fill:rgba(b.actor.get_theme_node().get_background_color()),"
                                    "icon_opacity:b.icon.opacity});})()")
                    report["menu_greyed_paints"] = grey
                    expected = [int(item["palette"]["muted"][i:i + 2], 16) for i in (1, 3, 5)] + [255]
                    if grey["label"] != expected or grey["icon"] != expected or grey["icon_opacity"] != 255:
                        raise RuntimeError("Native greyed menu ink differs from muted palette: " + str(grey))
                    evaluate(menu_applet + ".menu.actor.queue_redraw(); true")
                    time.sleep(0.5)
                    subprocess.run(["scrot", "--overwrite", str(run / "menu-greyed.png")], check=True, timeout=15)
                    evaluate(menu_applet + "._setCategoriesButtonActive(true); true")
            evaluate(base + ".close(); true")
            report["checks"].append(name + " opens and closes")

        def ieval(code: str):
            return evaluate(code)

        def click(x: float, y: float) -> None:
            xtest.fake_input(private_x, X.MotionNotify, x=int(x), y=int(y))
            private_x.sync()
            time.sleep(0.1)
            xtest.fake_input(private_x, X.ButtonPress, 1)
            private_x.sync()
            time.sleep(0.1)
            xtest.fake_input(private_x, X.ButtonRelease, 1)
            private_x.sync()
            time.sleep(0.4)

        island = "imports.ui.appletManager.filterDefinitionsByUUID(" + json.dumps(island_uuid) + ")[0].applet"
        ieval(island + ".menu.open(true); true")
        visible = popup_visibility(island + ".menu")
        report["island_visibility"] = visible
        if (not all(visible[key] for key in ("open", "visible", "mapped")) or visible["opacity"] < 200
                or min(visible["s"]) <= 0 or min(visible["p"]) < 0
                or visible["p"][0] + visible["s"][0] > screen.width_in_pixels + 1
                or visible["p"][1] + visible["s"][1] > screen.height_in_pixels + 1):
            raise RuntimeError("Island popup is hidden, transparent, zero-sized or outside the actual private display")
        if item.get("legacy_completion"):
            # Read the same native theme-node properties used by Slider's
            # Cairo renderer; source-order simulations alone miss shell wins.
            paints = ieval("(function(){const a=" + island + ";"
                           "const n=a._seek.actor.get_theme_node();const rgba=c=>[c.red,c.green,c.blue,c.alpha];"
                           "return JSON.stringify({classes:a._seek.actor.get_style_class_name(),"
                           "active:rgba(n.get_color('-slider-active-background-color')),"
                           "inactive:rgba(n.get_color('-slider-background-color')),"
                           "handle:rgba(n.get_foreground_color()),"
                           "clock:rgba(a._bigClock.get_theme_node().get_foreground_color()),"
                           "metric:rgba(a._cpu.get_theme_node().get_foreground_color())});})()")
            report["island_paints"] = paints
            for paint, role in (("active", "focus"), ("inactive", "background"),
                                ("handle", "focus"), ("clock", "foreground"),
                                ("metric", "foreground")):
                expected = [int(item["palette"][role][i:i + 2], 16) for i in (1, 3, 5)] + [255]
                if paints[paint] != expected:
                    raise RuntimeError("Native Island " + paint + " paint differs from " + role + ": " + str(paints[paint]))
        report['candidate_compact_paints'] = ieval("(function(){const a=" + island + ";const rgba=c=>[c.red,c.green,c.blue,c.alpha];return {fill:rgba(a._compact.get_theme_node().get_background_color()),clock:rgba(a._compactClock.get_theme_node().get_foreground_color()),weather:rgba(a._compactWeather.get_theme_node().get_foreground_color()),status:rgba(a._compactStatus.get_theme_node().get_foreground_color())};})()")
        for key, value in report['candidate_compact_paints'].items():
            color = item['candidate_state_colors']['compact_fill' if key == 'fill' else 'compact_ink']
            if value != [int(color[index:index+2],16) for index in (1,3,5)] + [255]:
                raise RuntimeError('Native compact Island paint differs: ' + key + ' ' + str(value))
        report['checks'].append('Native compact Island inline fill and all three label inks')
        report["island_visibility"] = capture_popup(island + ".menu", run / "island.png")
        geometry = ieval("JSON.stringify({p:" + island + "._closeButton.get_transformed_position(),s:" + island + "._closeButton.get_transformed_size()})")
        click(geometry["p"][0] + geometry["s"][0] / 2, geometry["p"][1] + geometry["s"][1] / 2)
        if ieval(island + ".menu.isOpen"):
            raise RuntimeError("Island × did not close the menu")
        report["checks"].append("Island actual × close")
        ieval(island + ".menu.open(true); " + island + "._closeButton.grab_key_focus(); true")
        time.sleep(0.3)
        escape = private_x.keysym_to_keycode(XK.string_to_keysym("Escape"))
        xtest.fake_input(private_x, X.KeyPress, escape)
        private_x.sync()
        time.sleep(0.1)
        xtest.fake_input(private_x, X.KeyRelease, escape)
        private_x.sync()
        time.sleep(0.4)
        if ieval(island + ".menu.isOpen"):
            raise RuntimeError("Island Escape did not close the menu")
        report["checks"].append("Island actual Escape")
        ieval(island + ".menu.open(true); true")
        time.sleep(0.3)
        click(40, 800)
        if ieval(island + ".menu.isOpen"):
            raise RuntimeError("Island outside click did not close the menu")
        report["checks"].append("Island outside click")
        bus("org.Cinnamon.ReloadXlet", island_uuid, "APPLET")
        time.sleep(1.5)
        ieval(island + ".menu.open(); true")
        ieval(island + ".menu.close(); true")
        report["checks"].append("Island ReloadXlet lifecycle")
        bus("org.Cinnamon.ReloadTheme")
        time.sleep(0.8)
        subprocess.run(["scrot", "--overwrite", str(run / "desktop.png")], check=True, timeout=15)
        report["checks"].append("Cinnamon theme reload")
        if slug == 'red-panda-overtime':
            try:
                report['notification_banner'] = exercise_rpo_notification(
                    evaluate, capture_popup, run, Gio.bus_get_sync(Gio.BusType.SESSION, None),
                    screen_size=(screen.width_in_pixels, screen.height_in_pixels),
                    palette=item['palette'], inspect_pixels=popup_capture_pixels)
                report['checks'].append(report['notification_banner']['check'])
            except Exception as banner_error:
                # This independent historical gap does not turn successful
                # workspace acceptance into notification acceptance.
                report['notification_banner'] = {'passed':False,'error':str(banner_error),
                    'scope':'Separate broader RPO banner acceptance; workspace and popup checks above remain recorded'}
        if item.get('authored_eww_bundle'):
            receiver = types.ModuleType('_candidate_receiver')
            receiver.__file__ = str(ROOT / 'candidate_receiver.py')
            exec(compile((ROOT / 'candidate_receiver.py').read_bytes(), receiver.__file__, 'exec'), receiver.__dict__)
            report['authored_receiver'] = receiver.exercise(item, generated_root / slug, run, private_x,
                screen_size=(screen.width_in_pixels, screen.height_in_pixels))
            report['checks'].append('Authored receiver native windows, painted captures and launcher open-close')
        if shell.poll() is not None:
            raise RuntimeError("Isolated Cinnamon exited")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log.close()
        (run / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        latest_root = Path(os.environ.get("CURRENT_COLLECTION_VERIFY_ROOT", DEFAULT_RUN)).parent / "isolated-latest"
        latest = latest_root / f"{slug}.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "worker":
        return worker()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="generated profile slug")
    parser.add_argument("--candidate-file", help="unregistered profile JSON with a top-level profiles object")
    parser.add_argument("--generated-root", type=Path, help="generated tree root for candidate preview")
    parser.add_argument("--output-root", type=Path, help="isolated receipt directory")
    parser.add_argument("--screen", type=validate_screen, default=DEFAULT_SCREEN, help="private Xephyr size (default: 2880x1800)")
    args = parser.parse_args()
    if args.candidate_file:
        os.environ["CURRENT_COLLECTION_PROFILE_CANDIDATES"] = str(Path(args.candidate_file).resolve())
    if args.generated_root:
        os.environ["CURRENT_COLLECTION_GENERATED_ROOT"] = str(args.generated_root.resolve())
    if args.output_root:
        os.environ["CURRENT_COLLECTION_VERIFY_ROOT"] = str(args.output_root.resolve())
    os.environ["CURRENT_COLLECTION_PREVIEW_SCREEN"] = args.screen
    return parent(args.profile)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
