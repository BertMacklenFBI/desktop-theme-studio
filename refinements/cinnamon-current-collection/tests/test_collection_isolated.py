"""Isolated-evidence contract tests; all fixtures live in temporary directories."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("current_collection_builder", ROOT / "collection.py")
collection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collection)
ISOLATE_SPEC = importlib.util.spec_from_file_location("current_collection_isolate", ROOT / "isolate.py")
isolate = importlib.util.module_from_spec(ISOLATE_SPEC)
assert ISOLATE_SPEC.loader is not None
ISOLATE_SPEC.loader.exec_module(isolate)


class PrivateFramebufferTests(unittest.TestCase):
    def run_fixture(self, outputs, private=":8", host=":0"):
        self.commands = []
        rows = iter(outputs)
        def run(args, **kwargs):
            self.commands.append(args)
            code, output = next(rows)
            return type("Result", (), {"returncode": code, "stdout": output, "stderr": output})()
        return isolate.ensure_private_screen("2880x1800", private, host, run=run, sleep=lambda seconds: None)

    def test_target_size_needs_no_mutation(self):
        query = "Screen 0: current 2880 x 1800, maximum 2880 x 1800"
        result = self.run_fixture([(0, query), (0, query)])
        self.assertFalse(result["resized"])
        self.assertEqual(self.commands, [["xrandr", "--query"], ["xrandr", "--query"]])

    def test_cinnamon_reduced_framebuffer_is_restored(self):
        result = self.run_fixture([(0, "current 1600 x 1200"), (0, ""), (0, "current 2880 x 1800")])
        self.assertEqual(result["before"], [1600, 1200])
        self.assertEqual(result["after"], [2880, 1800])
        self.assertEqual(self.commands[1], ["xrandr", "--output", "default", "--mode", "2880x1800"])

    def test_host_display_is_rejected_before_any_command(self):
        for private, host in [(":0", ":0"), (":0.1", ":0.0"), ("", ":0"), (":8", "")]:
            with self.subTest(private=private, host=host), self.assertRaisesRegex(RuntimeError, "distinct private display"):
                self.run_fixture([], private, host)
            self.assertEqual(self.commands, [])

    def test_failed_query_cannot_become_acceptance(self):
        with self.assertRaisesRegex(RuntimeError, "command failed"):
            self.run_fixture([(1, "display unavailable")])

    def test_failed_resize_cannot_become_acceptance(self):
        with self.assertRaisesRegex(RuntimeError, "command failed"):
            self.run_fixture([(0, "current 1600 x 1200"), (1, "unsupported mode")])

    def test_successful_command_with_wrong_framebuffer_fails(self):
        with self.assertRaisesRegex(RuntimeError, "differs from requested review size"):
            self.run_fixture([(0, "current 1600 x 1200"), (0, ""), (0, "current 1600 x 1200")])

    def test_unparseable_query_fails_without_resize(self):
        with self.assertRaisesRegex(RuntimeError, "did not report framebuffer"):
            self.run_fixture([(0, "Screen 0: unavailable")])


class PopupStartupTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.shell = type("PrivateShell", (), {"poll": lambda self: None})()

    def sleep(self, seconds):
        self.now += seconds

    def statuses(self, ready=True):
        return [{"uuid": uuid, "ready": ready} for uuid in isolate.POPUP_APPLETS]

    def wait(self, evaluate, timeout=2):
        return isolate.wait_for_popup_applets(evaluate, self.shell, timeout=timeout,
                                             monotonic=lambda: self.now, sleep=self.sleep)

    def test_waits_for_late_menu_after_dbus_is_ready(self):
        def evaluate(code):
            rows = self.statuses()
            rows[0]["ready"] = self.now >= 1
            return rows
        result = self.wait(evaluate)
        self.assertEqual(result["elapsed_seconds"], 1)
        self.assertTrue(all(row["ready"] for row in result["applets"]))

    def test_unavailable_eval_can_initialize_later(self):
        def evaluate(code):
            if self.now == 0:
                raise RuntimeError("appletManager unavailable")
            return self.statuses()
        self.assertEqual(self.wait(evaluate)["elapsed_seconds"], 0.5)

    def test_missing_applet_times_out_with_diagnostic(self):
        with self.assertRaisesRegex(RuntimeError, "menu@cinnamon.org"):
            self.wait(lambda code: self.statuses(False))
        self.assertEqual(self.now, 2)

    def test_incomplete_or_duplicate_applet_list_is_not_ready(self):
        for rows in [self.statuses()[:-1], [self.statuses()[0]] * 4]:
            self.now = 0
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, "did not become ready"):
                self.wait(lambda code: rows)

    def test_shell_exit_fails_without_waiting(self):
        self.shell = type("ExitedShell", (), {"poll": lambda self: 1})()
        with self.assertRaisesRegex(RuntimeError, "exited while loading"):
            self.wait(lambda code: self.statuses())
        self.assertEqual(self.now, 0)


class PopupCaptureTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.state = {"p": [0, 0], "s": [80, 80]}

    def sleep(self, seconds):
        self.now += seconds

    def capture(self, pixels, read_state=None):
        return isolate.capture_painted_popup(lambda: None, read_state or (lambda: self.state), pixels,
            timeout=1, monotonic=lambda: self.now, sleep=self.sleep)

    def test_blank_initial_frame_is_retried_before_acceptance(self):
        result = self.capture(lambda state: {"passed": self.now >= 0.5})
        self.assertEqual(self.now, 0.5)
        self.assertEqual(len(result["attempts"]), 3)

    def test_permanently_blank_frame_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "stable painted screenshot"):
            self.capture(lambda state: {"passed": False})
        self.assertEqual(self.now, 1)

    def test_popup_moving_during_capture_cannot_pass(self):
        count = [0]
        def state():
            count[0] += 1
            return {"p": [count[0], 0], "s": [80, 80]}
        with self.assertRaisesRegex(RuntimeError, "stable painted screenshot"):
            self.capture(lambda state: {"passed": True}, state)

    def test_black_and_uniform_crops_fail_but_painted_palette_passes(self):
        from PIL import Image
        palette = {"background": "#202020", "foreground": "#eeeeee"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.png"
            for color in ["black", "#202020"]:
                Image.new("RGB", (80, 80), color).save(path)
                self.assertFalse(isolate.popup_capture_pixels(path, self.state, (80, 80), palette)["passed"])
            shot = Image.new("RGB", (80, 80), "#202020")
            shot.paste("#eeeeee", (10, 10, 40, 30))
            shot.save(path)
            self.assertTrue(isolate.popup_capture_pixels(path, self.state, (80, 80), palette)["passed"])
            self.assertFalse(isolate.popup_capture_pixels(path, self.state, (160, 120), palette)["passed"])

    def test_startup_fade_with_matching_background_cannot_pass_without_text_ink(self):
        from PIL import Image
        palette = {"background": "#202020", "foreground": "#eeeeee", "muted": "#bbbbbb"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.png"
            shot = Image.new("RGB", (80, 80), "#202020")
            shot.paste("#6a6a6a", (10, 10, 40, 30))
            shot.save(path)
            faded = isolate.popup_capture_pixels(path, self.state, (80, 80), palette)
            self.assertGreater(faded["palette_fraction"], 0.2)
            self.assertGreater(faded["maximum_stddev"], 0.5)
            self.assertEqual(faded["ink_fraction"], 0)
            self.assertFalse(faded["passed"])
            shot.paste("#eeeeee", (10, 10, 40, 30))
            shot.save(path)
            self.assertTrue(isolate.popup_capture_pixels(path, self.state, (80, 80), palette)["passed"])


class IsolatedContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name) / "preset"
        (self.base / "theme").mkdir(parents=True)
        (self.base / "theme" / "style.css").write_text("original\n")
        (self.base / "art.png").write_bytes(b"art")
        self.required = ["shell composition", "all widgets rendered"]
        self.fingerprint = collection.fingerprint_sources(self.base, ["theme", "art.png"])
        self.profile = {
            "name": "Fixture",
            "isolated_verification": {
                "required_checks": self.required,
                "source_fingerprint": {
                    "base": str(self.base),
                    "paths": ["theme", "art.png"],
                    "exclude_python_cache": True,
                    "receipt_field": "accepted_source_sha256",
                    "sha256": self.fingerprint,
                },
            },
        }
        self.receipt = Path(self.temporary.name) / "receipt.json"

    def write_receipt(self, **changes):
        value = {
            "status": "passed",
            "profile": "fixture",
            "checks": list(self.required),
            "accepted_source_sha256": self.fingerprint,
            "run": "temporary-private-display",
        }
        value.update(changes)
        self.receipt.write_text(json.dumps(value))

    def test_legacy_profiles_keep_every_existing_interaction_requirement(self):
        contract = collection.isolated_contract({"name": "Legacy"})
        self.assertEqual(contract["kind"], "legacy-default")
        self.assertEqual(tuple(contract["required_checks"]), collection.LEGACY_ISOLATED_CHECKS)
        self.assertIn("Island ReloadXlet lifecycle", contract["required_checks"])
        self.assertIn("Island actual Escape", contract["required_checks"])

    def test_profile_contract_accepts_its_checks_and_matching_current_source(self):
        self.write_receipt()
        report = collection.verify_isolated_entry("fixture", self.profile, self.receipt)
        self.assertTrue(report["ok"])
        self.assertEqual(report["contract"], "profile")
        self.assertTrue(report["source_fingerprint"]["matches"])
        self.assertEqual(report["missing"], [])

    def test_profile_contract_rejects_missing_declared_visual_check(self):
        self.write_receipt(checks=[self.required[0]])
        report = collection.verify_isolated_entry("fixture", self.profile, self.receipt)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing"], [self.required[1]])

    def test_profile_contract_rejects_source_drift_after_acceptance(self):
        self.write_receipt()
        (self.base / "theme" / "style.css").write_text("changed\n")
        report = collection.verify_isolated_entry("fixture", self.profile, self.receipt)
        self.assertFalse(report["ok"])
        self.assertFalse(report["source_fingerprint"]["matches"])
        self.assertNotEqual(
            report["source_fingerprint"]["current_sha256"],
            report["source_fingerprint"]["expected_sha256"],
        )

    def test_profile_contract_rejects_receipt_for_a_different_source(self):
        self.write_receipt(accepted_source_sha256="0" * 64)
        report = collection.verify_isolated_entry("fixture", self.profile, self.receipt)
        self.assertFalse(report["ok"])
        self.assertEqual(report["source_fingerprint"]["current_sha256"], self.fingerprint)
        self.assertFalse(report["source_fingerprint"]["matches"])

    def test_declared_python_cache_exclusion_ignores_only_cache_artifacts(self):
        before = collection.fingerprint_sources(
            self.base, ["theme", "art.png"], exclude_python_cache=True
        )
        cache = self.base / "theme" / "__pycache__"
        cache.mkdir()
        (cache / "helper.cpython-312.pyc").write_bytes(b"transient")
        (self.base / "theme" / "orphan.pyo").write_bytes(b"transient")
        self.assertEqual(
            collection.fingerprint_sources(
                self.base, ["theme", "art.png"], exclude_python_cache=True
            ),
            before,
        )
        self.assertNotEqual(
            collection.fingerprint_sources(
                self.base, ["theme", "art.png"], exclude_python_cache=False
            ),
            before,
        )
        (self.base / "theme" / "authored.py").write_text("changed source\n")
        self.assertNotEqual(
            collection.fingerprint_sources(
                self.base, ["theme", "art.png"], exclude_python_cache=True
            ),
            before,
        )


if __name__ == "__main__":
    unittest.main()
