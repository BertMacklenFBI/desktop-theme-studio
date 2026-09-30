"""Paired receipt recovery using real desktop/system writers under temp roots.

No Cinnamon setting, home-directory asset, privileged command, or root-owned
path is touched. Only GUI refresh, watcher control, and the command boundary
are replaced; receipt parsing and both stages' file/tree restoration are real.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT.parents[1]
TIDAL_CANDIDATE = STUDIO / "refinements" / "tidal-observatory-completion" / "candidate"
# Load the staged published-helper candidate so the integration fixture checks
# the exact worker protocol that the paired shortcut will call.
SUPPORT = ROOT.parents[1] / "repairs" / "nim-nitch" / "transaction" / "candidates" / "nim_transaction_support.py"
SUPPORT_SPEC = importlib.util.spec_from_file_location("nim_transaction_support", SUPPORT)
transaction_support = importlib.util.module_from_spec(SUPPORT_SPEC)
sys.modules["nim_transaction_support"] = transaction_support
assert SUPPORT_SPEC.loader is not None
SUPPORT_SPEC.loader.exec_module(transaction_support)
SHORTCUT_SPEC = importlib.util.spec_from_file_location("paired_test_shortcut", ROOT / "shortcut.py")
shortcut = importlib.util.module_from_spec(SHORTCUT_SPEC)
assert SHORTCUT_SPEC.loader is not None
SHORTCUT_SPEC.loader.exec_module(shortcut)

STAGE_FILE = ROOT / "runtime-quality" / "system-stage" / "system.py"
STAGE_SPEC = importlib.util.spec_from_file_location("paired_test_system_stage", STAGE_FILE)
system_stage = importlib.util.module_from_spec(STAGE_SPEC)
assert STAGE_SPEC.loader is not None
sys.modules[STAGE_SPEC.name] = system_stage
STAGE_SPEC.loader.exec_module(system_stage)


class RealPairedRecoveryTests(unittest.TestCase):
    IDENT = "20260922-121212-a1b2c3"
    CANDIDATES = ROOT / "contributions" / "all-theme-plan" / "collection-profile-candidates.json"
    SYSTEM_PROFILES = ROOT / "contributions" / "all-theme-plan" / "system" / "profiles"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.live = shortcut.Runtime().desktop
        self.shortcut_root_before = shortcut.ROOT
        self.shortcut_profiles_before = shortcut.PROFILES
        self.live_profiles_before = self.live.SPEC
        self.live_root_before = self.live.ROOT
        self.live_studio_before = self.live.STUDIO
        shortcut.PROFILES = dict(self.shortcut_profiles_before)
        self.addCleanup(setattr, shortcut, "ROOT", self.shortcut_root_before)
        self.addCleanup(setattr, shortcut, "PROFILES", self.shortcut_profiles_before)
        self.addCleanup(setattr, self.live, "SPEC", self.live_profiles_before)
        self.addCleanup(setattr, self.live, "ROOT", self.live_root_before)
        self.addCleanup(setattr, self.live, "STUDIO", self.live_studio_before)
        self.original_stage_globals = {
            name: getattr(system_stage, name)
            for name in ("COLLECTION", "PROFILE_DIR", "USER_HOME", "STATE_ROOT", "GRUB_CONFIG", "node", "alternatives", "run", "rebuild")
        }
        self.addCleanup(self.restore_stage_globals)
        self.candidates = json.loads(self.CANDIDATES.read_text(encoding="utf-8"))["profiles"]
        self.assertEqual(set(self.candidates), {
            "dusk-ribbons", "macintosh-soft", "macintosh-soft-evergreen",
            "moonstone-stereo", "ocean-silk", "plum-afterglow", "quiet-sage",
        })
        self.configure_candidate(sorted(self.candidates)[0])

    def configure_candidate(self, slug):
        self.slug = slug
        candidate = self.candidates[slug]
        self.desktop_profile = {
            key: candidate[key]
            for key in ("ansi", "cursor", "icons", "light", "name", "palette", "sources", "style", "theme")
        }
        self.live.SPEC = {**self.live_profiles_before, slug: self.desktop_profile}
        shortcut.PROFILES = {**self.shortcut_profiles_before, slug: self.desktop_profile}
        self.profile = json.loads((self.SYSTEM_PROFILES / f"{slug}.json").read_text(encoding="utf-8"))
        self.candidate_root = self.root / slug
        if self.candidate_root.exists():
            shutil.rmtree(self.candidate_root)
        self.desktop_root = self.candidate_root / "desktop-engine"
        self.desktop_root.mkdir(parents=True)
        self.live.ROOT = self.desktop_root
        self.live.STUDIO = self.candidate_root / "studio-state"
        shortcut.ROOT = self.desktop_root
        (self.desktop_root / "isolate.py").write_bytes((ROOT / "isolate.py").read_bytes())
        (self.desktop_root / "profiles.json").write_text(json.dumps({"profiles": {slug: self.desktop_profile}}), encoding="utf-8")
        self.store = self.candidate_root / "shortcut-store"
        self.app = shortcut.Shortcut(self.store, runtime=shortcut.Runtime())
        self.desktop_file = self.candidate_root / "home" / ".config" / "appearance.ini"
        self.desktop_file.parent.mkdir(parents=True)
        self.desktop_before = b"[Appearance]\ncolor=original\n[Behavior]\npreserve=yes\n"
        self.desktop_after = b"[Appearance]\ncolor=selected\n[Behavior]\npreserve=yes\n"
        self.desktop_file.write_bytes(self.desktop_before)

        generated_source = ROOT / "generated" / slug
        self.generated = self.desktop_root / "generated" / slug
        theme_name = self.desktop_profile["theme"]
        shutil.copytree(generated_source / "desktop" / theme_name, self.generated / "desktop" / theme_name)
        shutil.copytree(
            generated_source / "island" / "nothing-island@desktop-theme-studio",
            self.generated / "island" / "nothing-island@desktop-theme-studio",
        )

        self.system_root = self.candidate_root / "system-image"
        self.stage_paths = system_stage.StagePaths(
            stage_id=self.profile["stage_id"],
            plymouth_descriptor=str(self.system_root / "plymouth" / slug / f"{slug}.plymouth"),
            state=self.system_root / "state" / self.profile["stage_id"],
            greeter=self.system_root / "etc" / "lightdm" / "slick-greeter.conf",
            override=self.system_root / "etc" / "default" / "grub.d" / "appearance.cfg",
            theme=self.system_root / "usr" / "share" / "themes" / self.profile["theme"],
            icons=self.system_root / "usr" / "share" / "icons" / self.profile["icons"],
            cursors=self.system_root / "usr" / "share" / "icons" / self.profile["cursors"],
            backgrounds=self.system_root / "usr" / "share" / "backgrounds" / slug,
            grub=self.system_root / "usr" / "share" / "grub" / "themes" / slug,
            plymouth=self.system_root / "usr" / "share" / "plymouth" / "themes" / slug,
        )
        self.stage_paths.state.mkdir(parents=True)
        self.greeter_before = b"[Greeter]\nbackground=original.png\n"
        self.greeter_after = b"[Greeter]\nbackground=selected.png\n"
        self.stage_paths.greeter.parent.mkdir(parents=True, exist_ok=True)
        self.stage_paths.greeter.write_bytes(self.greeter_before)
        system_stage.STATE_ROOT = self.system_root / "state"
        system_stage.GRUB_CONFIG = self.system_root / "boot" / "grub" / "grub.cfg"
        real_node = self.original_stage_globals["node"]

        def temp_root_node(path):
            result = real_node(path)
            if result["kind"] != "missing":
                result.update(uid=0, gid=0)
            return result

        system_stage.node = temp_root_node
        self.operations = [self.operation(self.stage_paths.greeter, self.greeter_after)]
        self.alternatives = {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}}
        system_stage.alternatives = lambda: copy.deepcopy(self.alternatives)

        def fake_run(*args):
            if args and args[0] == "update-alternatives":
                if args[1] == "--install":
                    self.alternatives["entries"][args[4]] = int(args[5])
                elif args[1] == "--set":
                    self.alternatives.update(status="manual", value=args[3])
                elif args[1] == "--remove":
                    self.alternatives["entries"].pop(args[3], None)
                elif args[1] == "--auto":
                    self.alternatives.update(status="auto", value=max(self.alternatives["entries"], key=self.alternatives["entries"].get))
            return ""

        system_stage.run = fake_run
        system_stage.rebuild = lambda _paths, _expected=None: None

    def restore_stage_globals(self):
        for name, value in self.original_stage_globals.items():
            setattr(system_stage, name, value)

    @staticmethod
    def operation(path, content):
        return {
            "path": str(path),
            "planned_before": system_stage.node(path),
            "after": {
                "kind": "file", "sha256": system_stage.sha_bytes(content),
                "mode": 0o644, "uid": 0, "gid": 0,
            },
            "content": base64.b64encode(content).decode("ascii"),
        }

    def install_desktop_stage(self):
        generated = self.generated
        theme = generated / "desktop" / self.live.SPEC[self.slug]["theme"]
        island_id = self.live.applet_uuid(self.live.SPEC[self.slug])
        island = generated / "island" / island_id
        isolate_spec = importlib.util.spec_from_file_location("paired_test_isolate", ROOT / "isolate.py")
        isolate = importlib.util.module_from_spec(isolate_spec)
        assert isolate_spec.loader is not None
        isolate_spec.loader.exec_module(isolate)
        evidence = self.desktop_root / "verification" / "isolated-latest" / f"{self.slug}.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps({
            "status": "passed",
            "theme_sha256": isolate.sha256_tree(theme),
            "island_sha256": isolate.sha256_tree(island),
        }), encoding="utf-8")

        action = {
            "kind": "file", "path": str(self.desktop_file),
            "before": base64.b64encode(self.desktop_before).decode("ascii"),
            "after": base64.b64encode(self.desktop_after).decode("ascii"),
        }
        actions = [action]
        if self.slug == "tidal-observatory":
            actions.extend(self.island_file_actions)
        data = {
            "profile": self.slug, "name": self.live.SPEC[self.slug]["name"],
            "actions": actions, "trees": self.desktop_tree_targets if self.slug == "tidal-observatory" else [], "protected_hashes": {},
            "required_assets": {}, "panel_before": {}, "skipped": [],
        }
        with (
            patch.object(self.live, "plan", return_value=data),
            patch.object(self.live.guard, "assert_allowed"),
            patch.object(self.live, "validate"),
            patch.object(self.live, "refresh"),
            patch.object(self.live, "journal", side_effect=lambda path, state: self.live.E.journal(path, state)),
            patch.object(self.live.base, "run", return_value=""),
            patch.object(self.live.watcher, "pause"),
            patch.object(self.live.watcher, "resume"),
        ):
            receipt_path, receipt = self.live.apply(self.slug, transaction_id=self.IDENT)
        return receipt_path, receipt

    def transaction_runtime(self):
        runtime = shortcut.Runtime()
        runtime._desktop = self.live
        runtime.system_receipt = lambda slug, ident: system_stage.receipt_status(
            self.profile, ident, self.stage_paths
        )
        runtime.system_restore = lambda slug, ident, receipt=None, save=None: system_stage.restore(
            self.profile, self.stage_paths.state / ident, self.stage_paths
        )
        return runtime

    def create_master_receipt(self, runtime):
        data = {
            "schema": 1, "id": self.IDENT, "profile": self.slug,
            "name": self.live.SPEC[self.slug]["name"], "status": "recovery-required",
            "created_at": "2026-09-22T12:12:12+00:00",
            "desktop_started": True, "system_started": True,
            "desktop_receipt": str(self.desktop_root / "state" / self.IDENT / "receipt.json"),
            "system_transaction": self.IDENT,
        }
        self.app = shortcut.Shortcut(self.store, runtime=runtime)
        self.app.save(data)
        return data

    def apply_both_real_stages(self):
        runtime = self.transaction_runtime()
        desktop_receipt_path, _desktop_receipt = self.install_desktop_stage()
        system_stage.apply(
            self.profile, self.operations, self.stage_paths,
            transaction=self.IDENT, source_report={"candidate_fixture": True},
        )
        self.assertEqual(self.desktop_file.read_bytes(), self.desktop_after)
        self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_after)
        self.create_master_receipt(runtime)
        return desktop_receipt_path

    def restore_pair(self):
        with (
            patch.object(self.live.guard, "assert_allowed"),
            patch.object(self.live, "validate"),
            patch.object(self.live.base, "layout", return_value={}),
            patch.object(self.live, "refresh"),
            patch.object(self.live, "journal", side_effect=lambda path, state: self.live.E.journal(path, state)),
            patch.object(self.live.base, "run", return_value=""),
            patch.object(self.live.watcher, "pause"),
            patch.object(self.live.watcher, "resume"),
        ):
            return self.app.recover(self.app.records()[0])

    def test_all_seven_candidate_desktop_and_system_receipts_restore(self):
        for slug in sorted(self.candidates):
            with self.subTest(theme=slug):
                self.configure_candidate(slug)
                desktop_receipt_path = self.apply_both_real_stages()
                result = self.restore_pair()
                self.assertEqual(result["status"], "restored")
                self.assertEqual(self.app.records()[0]["status"], "restored")
                self.assertEqual(json.loads(desktop_receipt_path.read_text())["status"], "restored")
                self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "restored")
                self.assertEqual(self.desktop_file.read_bytes(), self.desktop_before)
                self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_before)
                self.assertEqual(self.alternatives, {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}})

    def test_real_system_conflict_keeps_master_retryable_after_desktop_restore(self):
        self.configure_candidate("quiet-sage")
        desktop_receipt_path = self.apply_both_real_stages()
        self.stage_paths.greeter.write_bytes(b"[Greeter]\nbackground=external.png\n")

        with (
            patch.object(self.live.guard, "assert_allowed"),
            patch.object(self.live, "validate"),
            patch.object(self.live.base, "layout", return_value={}),
            patch.object(self.live, "refresh"),
            patch.object(self.live, "journal", side_effect=lambda path, state: self.live.E.journal(path, state)),
            patch.object(self.live.base, "run", return_value=""),
            patch.object(self.live.watcher, "pause"),
            patch.object(self.live.watcher, "resume"),
            self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"),
        ):
            self.app.recover(self.app.records()[0])

        self.assertEqual(self.app.records()[0]["status"], "recovery-required")
        self.assertTrue(any("Boot/login:" in error for error in self.app.records()[0]["recovery_errors"]))
        self.assertEqual(self.desktop_file.read_bytes(), self.desktop_before)
        self.assertEqual(self.stage_paths.greeter.read_bytes(), b"[Greeter]\nbackground=external.png\n")
        self.assertEqual(json.loads(desktop_receipt_path.read_text())["status"], "restored")
        self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "applied")

    def test_tidal_candidate_assets_restore_through_the_paired_receipt(self):
        catalog_path = STUDIO / "repairs/nim-nitch/catalog/generated/themes.json"
        shortcut_path = STUDIO / "refinements/cinnamon-current-collection/profiles.json"
        catalog_before = catalog_path.read_bytes()
        shortcut_before = shortcut_path.read_bytes()
        self.configure_tidal_candidate()
        self.assertEqual(self.live.applet_uuid(self.desktop_profile), "tidal-island@tidal-observatory")
        desktop_receipt_path = self.apply_both_real_stages()

        for tree in self.desktop_tree_targets:
            target = Path(tree["target"])
            self.assertEqual(self.live.base.tree(target), tree["sha"], str(target))
        for action in self.island_file_actions:
            self.assertEqual(Path(action["path"]).read_bytes(), base64.b64decode(action["after"]))
        grub_theme = self.stage_paths.grub / "theme.txt"
        plymouth_script = self.stage_paths.plymouth / f"{self.profile['stage_id']}.script"
        self.assertEqual(grub_theme.read_bytes(), (TIDAL_CANDIDATE / "system/rendered/grub/theme.txt").read_bytes())
        self.assertEqual(plymouth_script.read_bytes(), (TIDAL_CANDIDATE / "system/rendered/plymouth/cinnamon-current-tidal-observatory.script").read_bytes())
        self.assertIn(b"autologin-user=fixture", self.stage_paths.greeter.read_bytes())
        self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_after)

        result = self.restore_pair()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.app.records()[0]["status"], "restored")
        self.assertEqual(json.loads(desktop_receipt_path.read_text())["status"], "restored")
        self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "restored")
        self.assertEqual(self.desktop_file.read_bytes(), self.desktop_before)
        for action in self.island_file_actions:
            self.assertEqual(Path(action["path"]).read_bytes(), base64.b64decode(action["before"]))
        self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_before)
        self.assertEqual(self.alternatives, {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}})
        for tree in self.desktop_tree_targets:
            self.assertFalse(Path(tree["target"]).exists())
        self.assertFalse(grub_theme.exists())
        self.assertFalse(plymouth_script.exists())

        # This private recovery rehearsal must preserve the actual registry,
        # whether Tidal is already published or remains a staged candidate.
        self.assertEqual(catalog_path.read_bytes(), catalog_before)
        self.assertEqual(shortcut_path.read_bytes(), shortcut_before)

    def test_tidal_candidate_partial_system_write_rolls_back_then_restores_desktop(self):
        self.configure_tidal_candidate()
        desktop_receipt_path, _ = self.install_desktop_stage()
        runtime = self.transaction_runtime()
        self.create_master_receipt(runtime)
        real_write = system_stage.write_operation
        written = 0
        failed = False

        def fail_after_partial_write(paths, operation, folder=None, before=False):
            nonlocal written, failed
            if not before:
                written += 1
                if written == 5 and not failed:
                    failed = True
                    raise OSError("injected Tidal system write failure")
            return real_write(paths, operation, folder, before=before)

        with patch.object(system_stage, "write_operation", side_effect=fail_after_partial_write):
            with self.assertRaisesRegex(RuntimeError, "Apply failed and configuration restored"):
                system_stage.apply(
                    self.profile, self.operations, self.stage_paths,
                    transaction=self.IDENT, source_report={"candidate_fixture": True},
                )

        self.assertTrue(failed)
        self.assertEqual(written, 5)
        self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "rolled-back")
        self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_before)
        self.assertEqual(self.alternatives, {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}})
        self.assertTrue(all(not path.exists() for path in self.stage_paths.trees))
        self.assertTrue(any(Path(tree["target"]).exists() for tree in self.desktop_tree_targets))

        result = self.restore_pair()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.app.records()[0]["status"], "restored")
        self.assertEqual(json.loads(desktop_receipt_path.read_text())["status"], "restored")
        self.assertEqual(self.desktop_file.read_bytes(), self.desktop_before)
        for action in self.island_file_actions:
            self.assertEqual(Path(action["path"]).read_bytes(), base64.b64decode(action["before"]))
        for tree in self.desktop_tree_targets:
            self.assertFalse(Path(tree["target"]).exists())

    def test_tidal_external_greeter_conflict_is_preserved_and_retryable(self):
        self.configure_tidal_candidate()
        desktop_receipt_path = self.apply_both_real_stages()
        external = b"[Greeter]\nbackground=operator-edit.png\nautologin-user=fixture\n"
        self.stage_paths.greeter.write_bytes(external)

        with (
            patch.object(self.live.guard, "assert_allowed"),
            patch.object(self.live, "validate"),
            patch.object(self.live.base, "layout", return_value={}),
            patch.object(self.live, "refresh"),
            patch.object(self.live, "journal", side_effect=lambda path, state: self.live.E.journal(path, state)),
            patch.object(self.live.base, "run", return_value=""),
            patch.object(self.live.watcher, "pause"),
            patch.object(self.live.watcher, "resume"),
            self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"),
        ):
            self.app.recover(self.app.records()[0])

        self.assertEqual(self.app.records()[0]["status"], "recovery-required")
        self.assertEqual(self.stage_paths.greeter.read_bytes(), external)
        self.assertEqual(self.desktop_file.read_bytes(), self.desktop_before)
        self.assertEqual(json.loads(desktop_receipt_path.read_text())["status"], "restored")
        self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "applied")

        self.stage_paths.greeter.write_bytes(self.greeter_after)
        result = self.restore_pair()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.app.records()[0]["status"], "restored")
        self.assertEqual(self.stage_paths.greeter.read_bytes(), self.greeter_before)
        self.assertEqual(system_stage.receipt_status(self.profile, self.IDENT, self.stage_paths)["status"], "restored")
        self.assertEqual(self.alternatives, {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}})

    def configure_tidal_candidate(self):
        """Materialize the real Tidal trees and boot payload below disposable roots."""
        design = json.loads((TIDAL_CANDIDATE / "design.json").read_text(encoding="utf-8"))
        identity = json.loads((TIDAL_CANDIDATE / "identity" / "manifest.json").read_text(encoding="utf-8"))
        system_manifest = json.loads((TIDAL_CANDIDATE / "system" / "install-manifest.json").read_text(encoding="utf-8"))
        self.slug = "tidal-observatory"
        self.candidate_root = self.root / self.slug
        if self.candidate_root.exists():
            shutil.rmtree(self.candidate_root)
        self.desktop_root = self.candidate_root / "desktop-engine"
        self.desktop_root.mkdir(parents=True)
        self.live.ROOT = self.desktop_root
        self.live.STUDIO = self.candidate_root / "studio-state"
        shortcut.ROOT = self.desktop_root
        (self.desktop_root / "isolate.py").write_bytes((ROOT / "isolate.py").read_bytes())

        canonical = {
            "desktop": design["palette"]["desktop"], "background": design["palette"]["background"],
            "foreground": design["palette"]["foreground"], "surface": design["palette"]["surface"],
            "border": design["palette"]["border"], "control_border": design["palette"]["control_border"],
            "selection": design["palette"]["selection"], "selection_foreground": design["palette"]["selection_foreground"],
            "accent": design["palette"]["accent"], "elevated": design["palette"]["elevated"],
            "muted": design["palette"]["muted"], "error": design["palette"]["error"],
        }
        self.desktop_profile = {
            "ansi": design["terminal_ansi"], "cursor": identity["cursor_theme"],
            "icons": identity["theme"], "light": False, "name": design["name"],
            "palette": design["palette"], "sources": {"theme": str(TIDAL_CANDIDATE / "desktop/theme/Tidal Observatory"),
                "icons": str(TIDAL_CANDIDATE / "identity/icons/Tidal-Observatory"),
                "cursor": str(TIDAL_CANDIDATE / "identity/cursors/Tidal-Observatory-Cursors")},
            "style": "dark", "theme": design["name"], "island_uuid": "tidal-island@tidal-observatory",
        }
        self.live.SPEC = {**self.live_profiles_before, self.slug: self.desktop_profile}
        shortcut.PROFILES = {**self.shortcut_profiles_before, self.slug: self.desktop_profile}
        (self.desktop_root / "profiles.json").write_text(json.dumps({"profiles": {self.slug: self.desktop_profile}}), encoding="utf-8")
        self.store = self.candidate_root / "shortcut-store"
        self.app = shortcut.Shortcut(self.store, runtime=shortcut.Runtime())
        self.desktop_file = self.candidate_root / "home" / ".config" / "appearance.ini"
        self.desktop_file.parent.mkdir(parents=True)
        self.desktop_before = b"[Appearance]\ncolor=original\n[Behavior]\npreserve=yes\n"
        self.desktop_after = b"[Appearance]\ncolor=tidal-observatory\n[Behavior]\npreserve=yes\n"
        self.desktop_file.write_bytes(self.desktop_before)

        self.generated = self.desktop_root / "generated" / self.slug
        desktop_sources = {
            "theme": (TIDAL_CANDIDATE / "desktop/theme/Tidal Observatory", self.generated / "desktop/Tidal Observatory"),
            "island": (TIDAL_CANDIDATE / "desktop/composition/applets/tidal-island@tidal-observatory", self.generated / "island/tidal-island@tidal-observatory"),
            "icons": (TIDAL_CANDIDATE / "identity/icons/Tidal-Observatory", self.generated / "icons/Tidal-Observatory"),
            "cursors": (TIDAL_CANDIDATE / "identity/cursors/Tidal-Observatory-Cursors", self.generated / "cursors/Tidal-Observatory-Cursors"),
        }
        for source, destination in desktop_sources.values():
            shutil.copytree(source, destination, symlinks=True)

        source_profile = TIDAL_CANDIDATE / "system"
        engine_collection = self.candidate_root / "system-engine"
        generated = engine_collection / "generated" / self.slug
        roots = {
            "theme": generated / "desktop" / "Tidal Observatory",
            "icons": generated / "icons" / "Tidal-Observatory",
            "cursors": generated / "cursors" / "Tidal-Observatory-Cursors",
            "wallpaper": generated / "artwork" / "wallpaper.png",
            "logo": generated / "artwork" / "logo.png",
        }
        for key, source in (("theme", TIDAL_CANDIDATE / "desktop/theme/Tidal Observatory"),
                            ("icons", TIDAL_CANDIDATE / "identity/icons/Tidal-Observatory"),
                            ("cursors", TIDAL_CANDIDATE / "identity/cursors/Tidal-Observatory-Cursors")):
            shutil.copytree(source, roots[key], symlinks=True)
        roots["wallpaper"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TIDAL_CANDIDATE / "desktop/assets/wallpaper-supplied-2880x1800.png", roots["wallpaper"])
        shutil.copy2(source_profile / "assets/plymouth/aperture.png", roots["logo"])

        stage_id = "cinnamon-current-tidal-observatory"
        payload_root = generated / "system-payload"
        payload_root.mkdir(parents=True)
        prefix_map = {
            f"/usr/share/grub/themes/{stage_id}/": "grub/",
            f"/usr/share/plymouth/themes/{stage_id}/": "plymouth/",
        }
        payload_hashes = {}
        for entry in system_manifest["files"]:
            for destination_prefix, payload_prefix in prefix_map.items():
                if entry["destination"].startswith(destination_prefix):
                    source = TIDAL_CANDIDATE / entry["source"]
                    payload = source.read_bytes()
                    self.assertEqual(system_stage.sha_bytes(payload), entry["sha256"], entry["source"])
                    relative = payload_prefix + Path(entry["destination"]).name
                    output = payload_root / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(payload)
                    payload_hashes[relative] = system_stage.sha_bytes(payload)
        self.assertIn("grub/theme.txt", payload_hashes)
        self.assertIn(f"plymouth/{stage_id}.script", payload_hashes)
        self.assertIn(f"plymouth/{stage_id}.plymouth", payload_hashes)
        payload_manifest = {"schema": 1, "stage_id": stage_id, "files": payload_hashes}
        manifest_path = payload_root / "manifest.json"
        manifest_path.write_text(json.dumps(payload_manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        expected = {
            "theme_tree_sha256": system_stage.source_tree_hash(roots["theme"])[0],
            "icon_tree_sha256": system_stage.source_tree_hash(roots["icons"])[0],
            "cursor_tree_sha256": system_stage.source_tree_hash(roots["cursors"], system_stage.derived_cursor_aliases(roots["cursors"]))[0],
            "wallpaper_sha256": system_stage.sha_bytes(roots["wallpaper"].read_bytes()),
            "logo_sha256": system_stage.sha_bytes(roots["logo"].read_bytes()),
        }
        self.profile = {
            "schema": 1, "slug": self.slug, "stage_id": stage_id, "display_name": design["name"],
            "theme": "Tidal Observatory", "icons": "Tidal-Observatory", "cursors": "Tidal-Observatory-Cursors",
            "font_name": "Ubuntu 11", "palette": canonical,
            "sources": {key: str(value) for key, value in roots.items()}, "expected": expected,
            "system_payload": {"manifest": str(manifest_path), "manifest_sha256": system_stage.sha_bytes(manifest_path.read_bytes())},
        }
        stage_profiles = engine_collection / "runtime-quality/system-stage/profiles"
        stage_profiles.mkdir(parents=True)
        (stage_profiles / f"{self.slug}.json").write_text(json.dumps(self.profile, indent=2) + "\n", encoding="utf-8")
        system_stage.COLLECTION = engine_collection
        system_stage.PROFILE_DIR = stage_profiles
        system_stage.USER_HOME = self.candidate_root / "home"

        self.system_root = self.candidate_root / "system-image"
        self.stage_paths = system_stage.StagePaths(
            stage_id=stage_id, plymouth_descriptor=str(self.system_root / "usr/share/plymouth/themes" / stage_id / f"{stage_id}.plymouth"),
            state=self.system_root / "state" / stage_id,
            greeter=self.system_root / "etc/lightdm/slick-greeter.conf",
            override=self.system_root / "etc/default/grub.d/appearance.cfg",
            theme=self.system_root / "usr/share/themes/Tidal Observatory",
            icons=self.system_root / "usr/share/icons/Tidal-Observatory",
            cursors=self.system_root / "usr/share/icons/Tidal-Observatory-Cursors",
            backgrounds=self.system_root / "usr/share/backgrounds" / stage_id,
            grub=self.system_root / "usr/share/grub/themes" / stage_id,
            plymouth=self.system_root / "usr/share/plymouth/themes" / stage_id,
        )
        self.stage_paths.state.mkdir(parents=True)
        # Model the standard parent directories as pre-existing host state;
        # the engine may create only its declared theme trees and files.
        for path in (
            self.stage_paths.greeter.parent, self.stage_paths.override.parent,
            self.stage_paths.theme.parent, self.stage_paths.icons.parent,
            self.stage_paths.cursors.parent, self.stage_paths.backgrounds.parent,
            self.stage_paths.grub.parent, self.stage_paths.plymouth.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.greeter_before = b"[Greeter]\nkeyboard=us\nautologin-user=fixture\n[Seat:*]\nautologin-user-timeout=0\n"
        self.stage_paths.greeter.parent.mkdir(parents=True, exist_ok=True)
        self.stage_paths.greeter.write_bytes(self.greeter_before)
        system_stage.STATE_ROOT = self.system_root / "state"
        system_stage.GRUB_CONFIG = self.system_root / "boot/grub/grub.cfg"
        real_node = self.original_stage_globals["node"]

        def temp_root_node(path):
            result = real_node(path)
            if result["kind"] != "missing":
                result.update(uid=0, gid=0)
            return result

        system_stage.node = temp_root_node
        self.alternatives = {"status": "auto", "value": "/baseline.plymouth", "entries": {"/baseline.plymouth": 100}}
        system_stage.alternatives = lambda: copy.deepcopy(self.alternatives)

        def fake_run(*args):
            if args and args[0] == "update-alternatives":
                if args[1] == "--install":
                    self.alternatives["entries"][args[4]] = int(args[5])
                elif args[1] == "--set":
                    self.alternatives.update(status="manual", value=args[3])
                elif args[1] == "--remove":
                    self.alternatives["entries"].pop(args[3], None)
                elif args[1] == "--auto":
                    self.alternatives.update(status="auto", value=max(self.alternatives["entries"], key=self.alternatives["entries"].get))
            return ""

        system_stage.run = fake_run
        system_stage.rebuild = lambda _paths, _expected=None: None
        source_report = system_stage.verify_sources(self.profile)
        self.operations, _missing, build_report = system_stage.build(self.profile, self.stage_paths)
        self.assertEqual(build_report, source_report)
        self.greeter_after = next(
            base64.b64decode(operation["content"]) for operation in self.operations
            if operation["path"] == str(self.stage_paths.greeter)
        )
        self.assertIn(b"autologin-user=fixture", self.greeter_after)
        self.assertIn(b"theme-name=Tidal Observatory", self.greeter_after)
        self.desktop_tree_targets = []
        self.island_file_actions = []
        self.island_target = self.candidate_root / "home/.local/share/cinnamon/applets/tidal-island@tidal-observatory"
        self.island_target.mkdir(parents=True)
        island_source = self.generated / "island/tidal-island@tidal-observatory"
        for filename in ("applet.js", "stylesheet.css"):
            target_file = self.island_target / filename
            before = ("// pre-existing " + filename + "\n").encode()
            after = (island_source / filename).read_bytes()
            target_file.write_bytes(before)
            self.island_file_actions.append({
                "kind": "file", "path": str(target_file),
                "before": base64.b64encode(before).decode("ascii"),
                "after": base64.b64encode(after).decode("ascii"),
            })
        for key, name, target in (
            ("theme", "Tidal Observatory", self.candidate_root / "home/.themes/Tidal Observatory"),
            ("icons", "Tidal-Observatory", self.candidate_root / "home/.icons/Tidal-Observatory"),
            ("cursors", "Tidal-Observatory-Cursors", self.candidate_root / "home/.icons/Tidal-Observatory-Cursors"),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            source = desktop_sources[key][1]
            tree = {"source": str(source), "target": str(target), "sha": self.live.base.tree(source), "before_sha": self.live.base.tree_state(target)}
            self.live.base.prepare_tree_record(tree)
            self.desktop_tree_targets.append(tree)


if __name__ == "__main__":
    unittest.main()
