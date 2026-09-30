"""Headless tests for refreshing only the Konsole tab running the shortcut.

All preferences are temporary fixtures and subprocess execution is mocked.
No test opens a terminal, writes a live preference, or emits escape sequences.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("current_collection_konsole_runtime", ROOT / "konsole_runtime.py")
konsole_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(konsole_runtime)


class TerminalStream(io.StringIO):
    def isatty(self):
        return True


class KonsoleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.konsolerc = self.home / ".config/konsolerc"
        self.konsolerc.parent.mkdir()
        self.konsolerc.write_text("[Desktop Entry]\nDefaultProfile=Nocturne.profile\n[Other]\nKeep=true\n")
        self.profile = self.home / ".local/share/konsole/Nocturne.profile"
        self.profile.parent.mkdir(parents=True)
        self.write_scheme("CinnamonCurrentGraphiteBrass")
        self.environ = {"KONSOLE_VERSION": "230805"}
        self.stream = TerminalStream()

    def write_scheme(self, scheme):
        self.profile.write_text("[Appearance]\nColorScheme=" + scheme + "\nFont=Hack,11\n[General]\nCommand=/bin/bash\n")

    def snapshot(self):
        return {path.relative_to(self.home): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}

    def refresh(self, **kwargs):
        return konsole_runtime.refresh(home=self.home, environ=self.environ, stream=self.stream, **kwargs)

    def test_refresh_targets_current_tab_with_only_selected_color_scheme(self):
        before = self.snapshot()
        with patch.object(konsole_runtime.subprocess, "run") as run:
            result = self.refresh()
        run.assert_called_once_with(
            ["/usr/bin/konsoleprofile", "ColorScheme=CinnamonCurrentGraphiteBrass"],
            stdout=self.stream, stderr=subprocess.PIPE, text=True, timeout=3, check=True,
        )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["scheme"], "CinnamonCurrentGraphiteBrass")
        self.assertEqual(result["scope"], "current-Konsole-tab")
        self.assertEqual(self.snapshot(), before, "Runtime refresh must not rewrite preferences")

    def test_non_konsole_or_redirected_stream_is_a_no_op(self):
        before = self.snapshot()
        for environ, stream in (({}, self.stream), (self.environ, io.StringIO())):
            with self.subTest(environ=environ, terminal=stream.isatty()):
                with patch.object(konsole_runtime.subprocess, "run") as run:
                    result = konsole_runtime.refresh(home=self.home, environ=environ, stream=stream)
                self.assertEqual(result["status"], "not-applicable")
                run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_profile_names_cannot_select_another_path(self):
        for name in ("../Nocturne.profile", "/tmp/Nocturne.profile", "folder/Nocturne.profile", "Nocturne.profile\x1b", ""):
            with self.subTest(name=name):
                self.konsolerc.write_text("[Desktop Entry]\nDefaultProfile=" + name + "\n")
                with patch.object(konsole_runtime.subprocess, "run") as run:
                    result = self.refresh()
                self.assertEqual(result["status"], "deferred")
                self.assertTrue(result.get("reason"))
                run.assert_not_called()

    def test_invalid_scheme_is_deferred_without_terminal_output(self):
        for scheme in ("../foreign", "/tmp/foreign", "bad;Command=/bin/sh", "bad scheme", "bad\x1b]50;", "-option", "x" * 129, ""):
            with self.subTest(scheme=scheme):
                self.write_scheme(scheme)
                with patch.object(konsole_runtime.subprocess, "run") as run:
                    result = self.refresh()
                self.assertEqual(result["status"], "deferred")
                self.assertTrue(result.get("reason"))
                run.assert_not_called()
        self.assertEqual(self.stream.getvalue(), "")

    def test_missing_profile_or_color_scheme_is_deferred(self):
        for absent in ("profile", "scheme", "default"):
            with self.subTest(absent=absent):
                self.konsolerc.write_text("[Desktop Entry]\nDefaultProfile=Nocturne.profile\n")
                self.write_scheme("CinnamonCurrentGraphiteBrass")
                if absent == "profile":
                    self.profile.unlink()
                elif absent == "scheme":
                    self.profile.write_text("[Appearance]\nFont=Hack,11\n")
                else:
                    self.konsolerc.write_text("[Desktop Entry]\nOther=value\n")
                with patch.object(konsole_runtime.subprocess, "run") as run:
                    result = self.refresh()
                self.assertEqual(result["status"], "deferred")
                self.assertTrue(result.get("reason"))
                run.assert_not_called()

    def test_refresh_after_restore_reads_restored_profile_from_disk(self):
        with patch.object(konsole_runtime.subprocess, "run") as run:
            first = self.refresh()
            self.write_scheme("ocean-silk-current")
            before_restore_refresh = self.snapshot()
            restored = self.refresh()
        self.assertEqual(first["scheme"], "CinnamonCurrentGraphiteBrass")
        self.assertEqual(restored["status"], "updated")
        self.assertEqual(restored["scheme"], "ocean-silk-current")
        self.assertEqual(run.call_args_list[-1].args[0], ["/usr/bin/konsoleprofile", "ColorScheme=ocean-silk-current"])
        self.assertEqual(self.snapshot(), before_restore_refresh)

    def test_unavailable_or_failed_runtime_command_is_deferred(self):
        failures = (
            FileNotFoundError("konsoleprofile unavailable"),
            subprocess.TimeoutExpired(["/usr/bin/konsoleprofile"], 3),
            subprocess.CalledProcessError(1, ["/usr/bin/konsoleprofile"], stderr="failed"),
        )
        before = self.snapshot()
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(konsole_runtime.subprocess, "run", side_effect=failure):
                    result = self.refresh()
                self.assertEqual(result["status"], "deferred")
                self.assertTrue(result.get("reason"))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
