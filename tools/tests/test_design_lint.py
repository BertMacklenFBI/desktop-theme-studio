"""Tests for tools/design_lint.py using synthetic studios in temp dirs.

Run: /usr/bin/python3 -B -m unittest discover -s tools/tests -v
(from the studio root). No live actions; nothing outside the temp dirs is written.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import design_lint  # noqa: E402

FONTS = {"Ubuntu", "Hack"}

GOOD = {
    "name": "Synthetic Pass",
    "id": "synthetic-pass",
    "status": "draft",
    "palette": {
        "desktop": "#1A1D21",
        "background": "#1F2226",
        "surface": "#2A2E34",
        "elevated": "#343941",
        "foreground": "#ECE7DF",
        "muted": "#A7A299",
        "disabled": "#85837E",
        "accent": "#E0915A",
        "secondary": "#8FA9B3",
        "selection": "#C97A45",
        "selection_foreground": "#1A1D21",
        "focus": "#E0915A",
        "border": "#3F454E",
        "control_border": "#7C8590",
        "success": "#8FB08A",
        "warning": "#D9B25F",
        "error": "#E68888",
        "terminal": "#1A1D21",
        "terminal_foreground": "#ECE7DF",
    },
    "rainbow": ["#F2C39A", "#EBA97A", "#E0915A"],
    "typography": {
        "ui_family": "Ubuntu", "ui_size_pt": 11,
        "title_family": "Ubuntu", "title_size_pt": 11, "title_weight": 600,
        "clock_family": "Ubuntu", "clock_size_pt": 10, "clock_weight": 500,
        "monospace_family": "Hack", "monospace_size_pt": 11,
    },
    "geometry": {"panel_height": 44, "spacing": [4, 8, 12]},
    "preserve": ["cinabon menu name"],
    "terminal_ansi": [
        "#3F454E", "#D47A6B", "#8FB08A", "#D9B25F", "#8FA9B3", "#C9A0B4",
        "#8CBFC0", "#D8D2C8", "#7C8590", "#E39587", "#A6C49F", "#E6C67A",
        "#A8C0C9", "#D9B5C7", "#A3D0D0", "#ECE7DF",
    ],
    "semantic_rules": {"small_accent_text": "accent is fine as text"},
}

TEMPLATE_KEYS = [k for k in GOOD["palette"] if k != "disabled"]


class LintTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.themes = os.path.join(self.root, "themes")
        os.makedirs(self.themes)
        tpl = {"palette": {k: "TODO" for k in TEMPLATE_KEYS}}
        self._put("_template", tpl)

    def tearDown(self):
        self._tmp.cleanup()

    def _put(self, pid, design):
        d = os.path.join(self.root, "presets", pid)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "design.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(design, fh)
        return path

    def _lint(self, design, pid="synthetic", families=FONTS):
        path = self._put(pid, design)
        return design_lint.lint(path, root=self.root, themes_dir=self.themes,
                                families=families)

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = design_lint.main(list(argv) + ["--root", self.root,
                                                      "--themes-dir", self.themes])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def _checks(res, severity):
        return [f["check"] for f in res["findings"] if f["severity"] == severity]

    # ------------------------------------------------------------------
    def test_pass(self):
        res = self._lint(copy.deepcopy(GOOD))
        self.assertEqual(res["errors"], 0, res["findings"])
        self.assertEqual(res["exit"], 0)
        self.assertTrue(all(c["pass"] for c in res["contrast"]))

    def test_todo_failure(self):
        d = copy.deepcopy(GOOD)
        d["direction"] = "TODO write the direction"
        res = self._lint(d)
        self.assertEqual(res["exit"], 1)
        self.assertIn("todo", self._checks(res, "error"))
        paths = [f["path"] for f in res["findings"] if f["check"] == "todo"]
        self.assertIn("$.direction", paths)

    def test_low_contrast_failure(self):
        d = copy.deepcopy(GOOD)
        d["palette"]["muted"] = "#4A4A4A"  # ~1.9:1 on #1F2226
        res = self._lint(d)
        self.assertEqual(res["exit"], 1)
        bad = [c for c in res["contrast"] if c["fg"] == "muted" and not c["pass"]]
        self.assertEqual(len(bad), 3)
        self.assertIn("contrast", self._checks(res, "error"))

    def test_disabled_low_contrast_is_error(self):
        d = copy.deepcopy(GOOD)
        d["palette"]["disabled"] = "#3A3D42"
        res = self._lint(d)
        self.assertEqual(res["exit"], 1)
        self.assertTrue(any(c["fg"] == "disabled" and not c["pass"] for c in res["contrast"]))

    def test_fifteen_ansi_failure(self):
        d = copy.deepcopy(GOOD)
        d["terminal_ansi"] = d["terminal_ansi"][:15]
        res = self._lint(d)
        self.assertEqual(res["exit"], 1)
        self.assertIn("ansi", self._checks(res, "error"))

    def test_non_hex_rainbow_failure(self):
        d = copy.deepcopy(GOOD)
        d["rainbow"][1] = "orange"
        res = self._lint(d)
        self.assertIn("rainbow", self._checks(res, "error"))

    def test_fill_only_accent(self):
        d = copy.deepcopy(GOOD)
        d["palette"]["accent"] = "#6A4A30"  # ~2:1 on background
        strict = self._lint(copy.deepcopy(d), pid="strict")
        self.assertEqual(strict["exit"], 1)
        self.assertTrue(any(f["check"] == "contrast" and f["severity"] == "error"
                            and "accent" in f["message"] for f in strict["findings"]))
        d["semantic_rules"]["small_accent_text"] = "Accent is fill-only; never text"
        res = self._lint(d, pid="fill")
        self.assertTrue(res["accent_fill_only"])
        self.assertEqual(res["exit"], 0, res["findings"])
        row = [c for c in res["contrast"] if c["fg"] == "accent"][0]
        self.assertFalse(row["pass"])
        self.assertEqual(row["severity"], "info")

    def test_missing_core_token_error_other_warning(self):
        d = copy.deepcopy(GOOD)
        del d["palette"]["secondary"]
        res = self._lint(d, pid="warnonly")
        self.assertEqual(res["exit"], 0)
        self.assertIn("palette", self._checks(res, "warning"))
        d2 = copy.deepcopy(GOOD)
        del d2["palette"]["control_border"]
        res2 = self._lint(d2, pid="coreerr")
        self.assertEqual(res2["exit"], 1)
        self.assertTrue(any("control_border" in f["message"] for f in res2["findings"]
                            if f["severity"] == "error"))

    def test_template_read_at_runtime(self):
        tpl = {"palette": {k: "TODO" for k in TEMPLATE_KEYS + ["glow"]}}
        self._put("_template", tpl)
        res = self._lint(copy.deepcopy(GOOD))
        self.assertIn("glow", res["required_tokens"])
        self.assertEqual(res["exit"], 0)
        self.assertTrue(any("glow" in f["message"] for f in res["findings"]
                            if f["severity"] == "warning"))

    def test_ansi_low_contrast_warning_slot0_exempt(self):
        d = copy.deepcopy(GOOD)
        d["terminal_ansi"][0] = "#1B1E22"  # slot 0: exempt
        d["terminal_ansi"][4] = "#2A2E34"  # slot 4: warning
        res = self._lint(d)
        self.assertEqual(res["exit"], 0)
        slots = [a["slot"] for a in res["ansi"] if not a["pass"]]
        self.assertEqual(slots, [4])
        self.assertIn("ansi", self._checks(res, "warning"))

    def test_fonts(self):
        res = self._lint(copy.deepcopy(GOOD), families={"ubuntu"})
        self.assertEqual(res["exit"], 1)
        self.assertIn("fonts", self._checks(res, "error"))
        res2 = self._lint(copy.deepcopy(GOOD), pid="nofc", families=None)
        self.assertEqual(res2["exit"], 0)
        self.assertIn("fonts", self._checks(res2, "warning"))

    def test_hue_census(self):
        other = copy.deepcopy(GOOD)
        other["palette"]["accent"] = "#E8A060"  # hue ~28 vs ~25
        self._put("neighbour", other)
        self._put("red-panda-overtime-review-20990101-0000", other)  # skipped
        os.makedirs(os.path.join(self.themes, "BUNDLE", "config"))
        with open(os.path.join(self.themes, "BUNDLE", "config", "theme.json"), "w") as fh:
            json.dump({"accent": "#1fc9a6"}, fh)
        res = self._lint(copy.deepcopy(GOOD))
        sources = {h["source"] for h in res["hue_census"]}
        self.assertIn("presets/neighbour", sources)
        self.assertIn("themes/BUNDLE/config/theme.json", sources)
        self.assertFalse(any("-review-" in s for s in sources))
        self.assertNotIn("presets/synthetic", sources)
        self.assertIn("hue", self._checks(res, "warning"))
        self.assertEqual(res["exit"], 0)

    def test_hue_distance_wraps(self):
        self.assertAlmostEqual(design_lint.hue_distance(355, 5), 10)
        self.assertAlmostEqual(design_lint.contrast_ratio("#000000", "#FFFFFF"), 21.0)

    # ---- CLI --------------------------------------------------------
    def test_cli_default_writes_nothing_and_write_flag(self):
        self._put("clipass", copy.deepcopy(GOOD))
        ident = os.path.join(self.root, "presets", "clipass", "identity")
        orig = design_lint.installed_families
        design_lint.installed_families = lambda: FONTS
        try:
            code, out, _ = self._main("clipass")
            self.assertEqual(code, 0, out)
            self.assertIn("Result: 0 error(s)", out)
            self.assertFalse(os.path.exists(ident))
            code, out, _ = self._main("clipass", "--json")
            self.assertEqual(json.loads(out)["exit"], 0)
            self.assertFalse(os.path.exists(ident))
            code, out, _ = self._main("clipass", "--write")
            self.assertEqual(code, 0)
            with open(os.path.join(ident, "contrast.json")) as fh:
                saved = json.load(fh)
            self.assertEqual(saved["preset"], "clipass")
        finally:
            design_lint.installed_families = orig

    def test_cli_exit_codes(self):
        code, _, _ = self._main("does-not-exist")
        self.assertEqual(code, 2)
        bad = os.path.join(self.root, "presets", "broken")
        os.makedirs(bad)
        with open(os.path.join(bad, "design.json"), "w") as fh:
            fh.write("{not json")
        code, _, err = self._main("broken")
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)
        code, _, _ = self._main()
        self.assertEqual(code, 2)
        d = copy.deepcopy(GOOD)
        d["status"] = "TODO"
        self._put("clitodo", d)
        code, _, _ = self._main("clitodo")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
