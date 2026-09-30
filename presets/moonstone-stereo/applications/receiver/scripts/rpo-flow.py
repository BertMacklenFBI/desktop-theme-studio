#!/usr/bin/env python3
"""Open/close Red Panda Overtime Eww tools with scoped Escape dismissal.

This helper owns no media or system actions. Those continue to use the existing
Eww backend and scripts/ui commands referenced by the Yuck file.
"""
from __future__ import annotations

import argparse
import re
import select
import subprocess
import sys
import time
from pathlib import Path

from Xlib import X, XK, display, error
from Xlib.protocol import event

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
EWW = ROOT / "bin/eww"
ISLAND = "rpo-island"
TOOLS = {"rpo-karaoke", "rpo-timecard", "rpo-receipt", "rpo-meters", "rpo-switchboard", "rpo-index"}
NAMES = TOOLS | {ISLAND}


def eww(*args):
    return subprocess.check_output([str(EWW), "-c", str(CONFIG), "--no-daemonize", *args], text=True, stderr=subprocess.PIPE, timeout=12)


def active():
    try:
        return {instance.strip(): definition.strip() for line in eww("active-windows").splitlines() if ":" in line for instance, definition in [line.split(":", 1)]}
    except subprocess.CalledProcessError:
        return {}


def tool_xids(name):
    output = subprocess.check_output(["xwininfo", "-root", "-tree"], text=True, timeout=8)
    pattern = r'^\s*(0x[0-9a-f]+)\s+"Eww - ' + re.escape(name) + r'"'
    return [int(match.group(1), 16) for line in output.splitlines() if (match := re.match(pattern, line))]


def property_value(connection, window_id, name):
    try:
        prop = connection.create_resource_object("window", window_id).get_full_property(connection.intern_atom(name), X.AnyPropertyType)
        return int(prop.value[0]) if prop is not None and len(prop.value) else 0
    except error.XError:
        return 0


def focus(connection, window_id):
    if not window_id:
        return
    message = event.ClientMessage(window=window_id, client_type=connection.intern_atom("_NET_ACTIVE_WINDOW"), data=(32, [2, X.CurrentTime, 0, 0, 0]))
    connection.screen().root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    connection.flush()


def close_window(name, connection=None):
    if name not in NAMES:
        raise RuntimeError("Unknown Red Panda Overtime window")
    own_connection = connection is None
    connection = connection or display.Display()
    ids = tool_xids(name)
    origin = property_value(connection, ids[0], "_RPO_RETURN_WINDOW") if ids else 0
    rows = active()
    for instance, definition in rows.items():
        if definition == name or instance == name:
            eww("close", instance)
    if origin:
        focus(connection, origin)
    if own_connection:
        connection.close()


def is_escape(event_type, detail, escape_code):
    return event_type == X.KeyPress and detail == escape_code


def watch(window_id, name, origin=0, display_factory=display.Display, close_func=close_window):
    connection = display_factory()
    window = connection.create_resource_object("window", window_id)
    escape_code = connection.keysym_to_keycode(XK.string_to_keysym("Escape"))
    dismissed = False
    try:
        window.change_attributes(event_mask=X.StructureNotifyMask)
        for mask in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            window.grab_key(escape_code, mask, False, X.GrabModeAsync, X.GrabModeAsync)
        connection.sync()
        while True:
            if connection.pending_events():
                incoming = connection.next_event()
                if incoming.type in (X.DestroyNotify, X.UnmapNotify):
                    break
                if is_escape(incoming.type, incoming.detail, escape_code):
                    close_func(name, connection)
                    dismissed = True
                    break
            else:
                select.select([connection.fileno()], [], [], 2)
                if window.get_attributes().map_state != X.IsViewable:
                    break
    except error.XError:
        pass
    finally:
        connection.close()
    return dismissed


def close_other_tools(except_name=None):
    rows = active()
    for instance, definition in rows.items():
        if definition in TOOLS and definition != except_name:
            eww("close", instance)


def open_window(name):
    if name not in NAMES:
        raise RuntimeError("Unknown Red Panda Overtime window")
    connection = display.Display()
    try:
        origin = property_value(connection, connection.screen().root.id, "_NET_ACTIVE_WINDOW")
        if name in TOOLS:
            close_other_tools(name)
        if name not in active().values():
            eww("open", name)
        deadline = time.monotonic() + 4
        ids = []
        while time.monotonic() < deadline:
            ids = tool_xids(name)
            if ids:
                break
            time.sleep(.1)
        if not ids:
            raise RuntimeError("Eww window opened without a discoverable X11 window")
        atom = connection.intern_atom("_RPO_RETURN_WINDOW")
        for window_id in ids:
            window = connection.create_resource_object("window", window_id)
            window.change_property(atom, connection.intern_atom("WINDOW"), 32, [origin])
            subprocess.Popen([sys.executable, str(Path(__file__)), "watch", str(window_id), name, str(origin)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        connection.sync()
        subprocess.run(["wmctrl", "-ia", hex(ids[0])], check=False, timeout=3)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("open", "toggle", "close", "watch"))
    parser.add_argument("name")
    parser.add_argument("extra", nargs="*")
    args = parser.parse_args()
    if args.action == "watch":
        if len(args.extra) != 2 or args.extra[0] not in NAMES:
            raise RuntimeError("watch requires WINDOW_ID NAME ORIGIN")
        return 0 if watch(int(args.name), args.extra[0], int(args.extra[1])) else 0
    if args.name not in NAMES:
        raise RuntimeError("Unknown Red Panda Overtime window")
    if args.action == "close":
        close_window(args.name)
    elif args.action == "toggle" and args.name in active().values():
        close_window(args.name)
    else:
        open_window(args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
