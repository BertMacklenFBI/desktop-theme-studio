"""Pure tests for receipt comparisons of Eww's rewritten JSON state file.

Fixtures are in memory. The tests do not import the desktop controller, access
home preferences, open applications, or call a live transaction.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("collection_eww_state_test", ROOT / "eww_state.py")
eww_state = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(eww_state)

STATE_PATH = Path("/fixture/eww/state/theme.json")
MISSING = {"__macintosh_soft_missing__": True}


def encoded(raw):
    return base64.b64encode(raw if isinstance(raw, bytes) else raw.encode("utf-8")).decode("ascii")


def state(value, **options):
    return encoded(json.dumps(value, **options))


class EwwStateTests(unittest.TestCase):
    def setUp(self):
        self.old = {"cinnamon_theme": "Lavender-Glass", "palette": "Lavender-Glass", "selection_text": "#303449", "base": "#303449"}
        self.new = {"cinnamon_theme": "pastel leather", "palette": "pastel leather", "selection_text": "#f2e6dc", "base": "#f2e6dc"}
        self.before = encoded(json.dumps(self.old, separators=(",", ":")) + "\n")
        self.after = encoded(json.dumps(self.new, indent=2) + "\n")
        self.action = {"kind": "file", "path": str(STATE_PATH), "before": self.before, "after": self.after, "attempted": True, "applied": True}

    def normalise(self, actual, action=None):
        return eww_state.normalise(self.action if action is None else action, actual, STATE_PATH)

    def test_kept_receipt_accepts_watcher_key_order_and_whitespace_only(self):
        receipt = {"status": "kept", "actions": [self.action]}
        untouched = copy.deepcopy(receipt)
        rewritten = state(self.new, sort_keys=True, indent=4)
        self.assertNotEqual(rewritten, self.after)
        actual = self.normalise(rewritten)
        self.assertEqual(actual, receipt["actions"][0]["after"])
        self.assertEqual(receipt, untouched, "Compatibility must not rewrite an existing kept receipt")

    def test_restore_preflight_accepts_reformat_and_writes_original_before_bytes(self):
        rewritten_after = state(self.new, sort_keys=True)
        observed = self.normalise(rewritten_after)
        self.assertIn(observed, (self.before, self.after), "The existing restore conflict check should accept only the recorded states")
        writer = Mock()
        if observed == self.after:
            writer(self.action, self.action["before"])
        writer.assert_called_once_with(self.action, self.before)
        self.assertEqual(base64.b64decode(writer.call_args.args[1]), json.dumps(self.old, separators=(",", ":")).encode() + b"\n")
        self.assertEqual(self.action["before"], self.before)

    def test_already_restored_reformat_matches_before_and_needs_no_second_write(self):
        rewritten_before = state(self.old, sort_keys=True, indent=3)
        observed = self.normalise(rewritten_before)
        self.assertEqual(observed, self.before)
        self.assertNotEqual(observed, self.after)
        writer = Mock()
        if observed == self.after:
            writer(self.action, self.action["before"])
        writer.assert_not_called()

    def test_semantically_identical_candidates_prefer_exact_recorded_after(self):
        original = {"a": 1, "b": {"z": "é", "y": [2, 3]}}
        before = state(original, separators=(",", ":"), ensure_ascii=False)
        after = encoded(json.dumps(original, indent=4, sort_keys=True) + "\n")
        action = {**self.action, "before": before, "after": after}
        actual = state({"b": {"y": [2, 3], "z": "é"}, "a": 1}, ensure_ascii=False)
        self.assertNotEqual(before, after)
        self.assertNotEqual(actual, after)
        self.assertEqual(self.normalise(actual, action), after)

    def test_other_action_paths_and_kinds_keep_raw_mismatches(self):
        actual = state(self.new, sort_keys=True)
        actions = (
            {**self.action, "path": "/fixture/other/state/theme.json"},
            {**self.action, "path": "/fixture/eww/state/./theme.json"},
            {**self.action, "path": "/fixture/eww/state/theme.json.bak"},
            {**self.action, "kind": "text"},
            {**self.action, "kind": "json"},
            {"kind": "file", "before": self.before, "after": self.after},
        )
        for action in actions:
            with self.subTest(action=action):
                self.assertEqual(self.normalise(actual, action), actual)
                self.assertNotEqual(actual, action["after"])

    def test_extra_missing_or_changed_content_remains_a_restore_conflict(self):
        changed = (
            {**self.new, "external": "new key"},
            {key: value for key, value in self.new.items() if key != "base"},
            {**self.new, "base": "#000000"},
            {**self.new, "palette": "another theme"},
            {**self.new, "base": None},
        )
        for value in changed:
            with self.subTest(value=value):
                actual = state(value, sort_keys=True)
                observed = self.normalise(actual)
                self.assertEqual(observed, actual)
                self.assertNotIn(observed, (self.before, self.after))

    def test_nested_type_and_sequence_changes_are_not_formatting(self):
        expected = {"options": {"enabled": True, "bands": ["sage", "rose"]}, "count": 1}
        action = {**self.action, "after": state(expected), "before": MISSING}
        changed = (
            {"options": {"enabled": 1, "bands": ["sage", "rose"]}, "count": 1},
            {"options": {"enabled": True, "bands": ["sage", "rose"]}, "count": True},
            {"options": {"enabled": True, "bands": ["rose", "sage"]}, "count": 1},
            {"options": {"enabled": True, "bands": ["sage", "rose", "sky"]}, "count": 1},
            {"options": {"enabled": True, "bands": {"0": "sage", "1": "rose"}}, "count": 1},
        )
        for value in changed:
            with self.subTest(value=value):
                actual = state(value, indent=2)
                self.assertEqual(self.normalise(actual, action), actual)
                self.assertNotEqual(actual, action["after"])

    def test_invalid_encodings_duplicate_keys_and_nonobjects_are_not_normalised(self):
        invalid = (
            "%%%not-base64%%%",
            self.after + "!",
            encoded(b"\xff\xfe"),
            encoded('{"palette":'),
            encoded('{"palette":"pastel leather","palette":"pastel leather"}'),
            encoded('{"outer":{"a":1,"a":1}}'),
            encoded('[{"palette":"pastel leather"}]'),
            encoded('"pastel leather"'),
            encoded("null"),
            encoded("true"),
            encoded("42"),
        )
        for actual in invalid:
            with self.subTest(actual=actual):
                self.assertEqual(self.normalise(actual), actual)
                self.assertNotIn(actual, (self.before, self.after))

    def test_duplicate_keys_cannot_match_last_wins_candidate_values(self):
        action = {**self.action, "after": state({"outer": {"a": 2}}), "before": MISSING}
        actual = encoded('{"outer":{"a":1,"a":2}}')
        self.assertEqual(self.normalise(actual, action), actual)
        self.assertNotEqual(actual, action["after"])
        malformed_candidate = {**action, "after": actual}
        reformatted_valid = state({"outer": {"a": 2}}, indent=2)
        self.assertEqual(self.normalise(reformatted_valid, malformed_candidate), reformatted_valid)

    def test_invalid_candidate_does_not_prevent_matching_valid_before(self):
        action = {**self.action, "after": "%%%not-base64%%%"}
        actual = state(self.old, sort_keys=True, indent=4)
        self.assertEqual(self.normalise(actual, action), self.before)

    def test_nonfinite_and_overflowed_json_numbers_remain_mismatches(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            with self.subTest(literal=literal):
                # Different original encodings prevent raw equality from
                # masking an erroneous non-finite canonical comparison.
                actual = encoded('{ "nested": { "value": ' + literal + ' } }')
                candidate = encoded('{"nested":{"value":' + literal + '}}')
                action = {**self.action, "before": MISSING, "after": candidate}
                self.assertNotEqual(actual, candidate)
                self.assertEqual(self.normalise(actual, action), actual)
                self.assertNotEqual(self.normalise(actual, action), candidate)

    def test_missing_sentinels_are_preserved_and_do_not_become_json_objects(self):
        for actual in (MISSING, None, {"other_missing": True}):
            with self.subTest(actual=actual):
                self.assertIs(self.normalise(actual), actual)
        action = {**self.action, "before": MISSING}
        actual = state(self.new, sort_keys=True)
        self.assertEqual(self.normalise(actual, action), self.after)
        json_sentinel = state(MISSING)
        self.assertEqual(self.normalise(json_sentinel, action), json_sentinel)
        self.assertNotEqual(json_sentinel, MISSING)


if __name__ == "__main__":
    unittest.main()
