"""Compare Eww's generated state without treating JSON formatting as drift.

Receipts retain their exact original bytes. Only the one watcher-owned state
file may match an entire before/after JSON object despite whitespace/key order.
"""
import base64
import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _canonical(encoded):
    if not isinstance(encoded, str):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        if not isinstance(value, dict):
            return None
        # Serializing instead of Python equality also distinguishes True from
        # 1 at any depth, and never drops fields or changes array order.
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


def normalise(action, actual, state_path):
    """Return recorded bytes only when the complete state object matches."""
    if action.get("kind") != "file" or action.get("path") != str(state_path):
        return actual
    canonical = _canonical(actual)
    if canonical is None:
        return actual
    for key in ("after", "before"):
        candidate = action.get(key)
        if _canonical(candidate) == canonical:
            return candidate
    return actual
