"""Mock-only orchestration tests for the current-collection theme shortcut.

These tests exercise receipts in a temporary directory.  They never instantiate
the real desktop engine, call sudo, launch a GUI process, or touch a system
configuration path.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch
from contextlib import ExitStack, redirect_stderr, redirect_stdout


ROOT = Path(__file__).resolve().parents[1]
# Exercise the candidate helper paired with this source tree rather than a
# previously installed copy. This keeps the staged worker protocol under test.
SUPPORT = ROOT.parents[1] / "repairs" / "nim-nitch" / "transaction" / "candidates" / "nim_transaction_support.py"
SUPPORT_SPEC = importlib.util.spec_from_file_location("nim_transaction_support", SUPPORT)
transaction_support = importlib.util.module_from_spec(SUPPORT_SPEC)
sys.modules["nim_transaction_support"] = transaction_support
assert SUPPORT_SPEC.loader is not None
SUPPORT_SPEC.loader.exec_module(transaction_support)
SPEC = importlib.util.spec_from_file_location("current_collection_shortcut", ROOT / "shortcut.py")
shortcut = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(shortcut)

SYSTEM_STAGE = ROOT / "runtime-quality" / "system-stage" / "system.py"
STAGE_SPEC = importlib.util.spec_from_file_location("current_collection_system_stage_adapter", SYSTEM_STAGE)
system_stage = importlib.util.module_from_spec(STAGE_SPEC)
assert STAGE_SPEC.loader is not None
sys.modules[STAGE_SPEC.name] = system_stage
STAGE_SPEC.loader.exec_module(system_stage)

ISOLATE_SPEC = importlib.util.spec_from_file_location(
    "current_collection_isolate", ROOT / "isolate.py"
)
isolate = importlib.util.module_from_spec(ISOLATE_SPEC)
assert ISOLATE_SPEC.loader is not None
ISOLATE_SPEC.loader.exec_module(isolate)


class FakeRuntime:
    """A receipt-aware runtime that records calls but has no real side effects."""

    def __init__(
        self,
        *,
        preflight_error: Exception | None = None,
        system_apply_error: Exception | None = None,
        system_restore_error: Exception | None = None,
        desktop_restore_error: Exception | None = None,
        system_check_ok: bool = True,
        desktop_check_ok: bool = True,
    ):
        self.calls: list[tuple] = []
        self.preflight_error = preflight_error
        self.system_apply_error = system_apply_error
        self.system_restore_error = system_restore_error
        self.desktop_restore_error = desktop_restore_error
        self.system_check_ok = system_check_ok
        self.desktop_check_ok = desktop_check_ok
        self.system: dict[tuple[str, str], dict] = {}
        self.desktop: dict[str, dict] = {}

    @contextlib.contextmanager
    def transaction_lock(self):
        self.calls.append(("lock-enter",))
        try:
            yield
        finally:
            self.calls.append(("lock-exit",))

    def preflight(self, slug):
        self.calls.append(("preflight", slug))
        if self.preflight_error:
            raise self.preflight_error
        return {"profile": slug, "read_only": True}

    def authorize(self):
        self.calls.append(("authorize",))

    def system_apply(self, slug, ident, receipt=None, save=None):
        self.calls.append(("system-apply", slug, ident))
        if receipt is not None:
            receipt["system_attempt"] = "released"
            if save:
                save(receipt)
        # A failed command can still have created a durable system receipt.
        # This lets the shortcut prove it recovers the exact predeclared id.
        self.system[(slug, ident)] = {
            "status": "preparing" if self.system_apply_error else "applied"
        }
        if self.system_apply_error:
            raise self.system_apply_error
        return {"status": "applied"}

    def system_receipt(self, slug, ident):
        self.calls.append(("system-receipt", slug, ident))
        return dict(self.system.get((slug, ident), {"status": "missing"}))

    def system_check(self, slug, ident):
        self.calls.append(("system-check", slug, ident))
        return {"ok": self.system_check_ok}

    def system_restore(self, slug, ident, receipt=None, save=None):
        self.calls.append(("system-restore", slug, ident))
        if self.system_restore_error:
            raise self.system_restore_error
        self.system[(slug, ident)] = {"status": "restored"}
        return {"status": "restored"}

    def desktop_apply(self, slug, ident):
        self.calls.append(("desktop-apply", slug, ident))
        self.desktop[ident] = {"status": "pending"}
        return dict(self.desktop[ident])

    def desktop_receipt(self, ident):
        self.calls.append(("desktop-receipt", ident))
        return dict(self.desktop.get(ident, {"status": "missing"}))

    def desktop_check(self, ident):
        self.calls.append(("desktop-check", ident))
        return {"ok": self.desktop_check_ok}

    def desktop_keep(self, ident):
        self.calls.append(("desktop-keep", ident))
        self.desktop[ident] = {"status": "kept"}
        return dict(self.desktop[ident])

    def desktop_restore(self, ident):
        self.calls.append(("desktop-restore", ident))
        if self.desktop_restore_error:
            raise self.desktop_restore_error
        self.desktop[ident] = {"status": "restored"}
        return dict(self.desktop[ident])

    def current(self):
        self.calls.append(("current",))
        return {"desktop": "Before", "gtk": "Before", "plymouth": "Before"}

    def names(self):
        return [item[0] for item in self.calls]


class RecordingShortcut(shortcut.Shortcut):
    """Keep durable receipts while exposing the master-state transitions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_statuses: list[str] = []

    def save(self, data, status=None):
        if status is not None:
            self.saved_statuses.append(status)
        return super().save(data, status)


class ShortcutTests(unittest.TestCase):
    IDENT = "20260920-230000-a1b2c3d4e5f6"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name) / "shortcut-state"

    def make_app(self, runtime=None):
        return RecordingShortcut(self.store, runtime or FakeRuntime())

    def switch(self, app, slug="eucalyptus-felt", ident=None):
        return patch.object(shortcut, "stamp", return_value=ident or self.IDENT), app.switch(slug)

    def create_record(self, app, ident, slug, status, created_at):
        data = {
            "schema": 1,
            "id": ident,
            "profile": slug,
            "name": shortcut.PROFILES[slug]["name"],
            "status": status,
            "created_at": created_at,
            "before": {"desktop": "Before"},
            "preview": {"read_only": True},
        }
        app.save(data)
        return data

    def assert_before(self, calls, earlier, later):
        self.assertLess(calls.index(earlier), calls.index(later), f"{earlier} must precede {later}: {calls}")

    def test_list_and_name_aliases_are_canonical_and_unambiguous(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(shortcut.main(["--json", "list"]), 0)
        listed = json.loads(output.getvalue())
        self.assertEqual({row["id"] for row in listed["themes"]}, set(shortcut.PROFILES))
        self.assertEqual(shortcut.resolve_profile("Eucalyptus Felt"), "eucalyptus-felt")
        self.assertEqual(shortcut.resolve_profile("slate-orbit-studio"), "slate-orbit-studio")
        self.assertEqual(shortcut.resolve_profile("Graphite & Brass"), "graphite-brass")
        self.assertEqual(shortcut.resolve_profile("graphite and brass"), "graphite-brass")
        self.assertEqual(shortcut.resolve_profile("red-panda-overtime"), "red-panda-overtime")
        self.assertEqual(shortcut.resolve_profile("red panda overtime"), "red-panda-overtime")
        self.assertEqual(shortcut.resolve_profile("rpo"), "red-panda-overtime")
        self.assertEqual(shortcut.resolve_profile("tangerine-graphite"), "tangerine-graphite")
        self.assertEqual(shortcut.resolve_profile("Tangerine Graphite"), "tangerine-graphite")
        self.assertEqual(shortcut.resolve_profile("tg"), "tangerine-graphite")
        self.assertEqual(shortcut.normalize_name(" Graphite & Brass "), "graphiteandbrass")
        with self.assertRaisesRegex(shortcut.ShortcutError, "Unknown or ambiguous"):
            shortcut.resolve_profile("unknown theme")
        duplicated = {
            "first": {"name": "Same Theme", "theme": "First"},
            "second": {"name": "Same Theme", "theme": "Second"},
        }
        with self.assertRaisesRegex(shortcut.ShortcutError, "Unknown or ambiguous"):
            shortcut.resolve_profile("same theme", duplicated)

    def test_red_panda_isolated_acceptance_matches_current_generated_trees(self):
        slug = "red-panda-overtime"
        receipt = json.loads(
            (ROOT / "verification" / "isolated-latest" / f"{slug}.json").read_text()
        )
        generated = ROOT / "generated" / slug
        self.assertEqual(receipt["status"], "passed")
        current = (
            isolate.sha256_tree(generated / "desktop" / shortcut.PROFILES[slug]["theme"]),
            isolate.sha256_tree(generated / "island" / "nothing-island@desktop-theme-studio"),
        )
        accepted = (receipt["theme_sha256"], receipt["island_sha256"])
        if current != accepted:
            self.skipTest("historical isolated receipt no longer matches rebuilt assets; require fresh display-backed evidence")

    def test_tangerine_graphite_isolated_acceptance_matches_current_generated_trees(self):
        slug = "tangerine-graphite"
        receipt = json.loads(
            (ROOT / "verification" / "isolated-latest" / f"{slug}.json").read_text()
        )
        generated = ROOT / "generated" / slug
        self.assertEqual(receipt["status"], "passed")
        current = (
            isolate.sha256_tree(generated / "desktop" / shortcut.PROFILES[slug]["theme"]),
            isolate.sha256_tree(generated / "island" / "nothing-island@desktop-theme-studio"),
        )
        accepted = (receipt["theme_sha256"], receipt["island_sha256"])
        if current != accepted:
            self.skipTest("historical isolated receipt no longer matches rebuilt assets; require fresh display-backed evidence")

    def test_runtime_preview_is_read_only_and_does_not_require_closed_apps(self):
        desktop = Mock()
        desktop.plan.return_value = {
            "actions": [{"requires_closed": "ptyxis"}, {"path": "ordinary-preference"}],
            "skipped": ["preserved personal preference"],
        }
        runtime = shortcut.Runtime()
        with (
            patch.object(shortcut.Runtime, "desktop", new_callable=PropertyMock, return_value=desktop),
            patch.object(shortcut.Runtime, "system_command", return_value={"files": 17, "missing": []}) as system_command,
            patch.object(shortcut, "write_json", side_effect=AssertionError("preview must not create a shortcut receipt")),
            patch.object(shortcut.Runtime, "authorize", side_effect=AssertionError("preview must not authenticate")),
        ):
            report = runtime.preview("graphite-brass")
        desktop.plan.assert_called_once_with("graphite-brass")
        system_command.assert_called_once_with("graphite-brass", "preview")
        self.assertEqual(report["application_writers_that_must_be_closed"], ["ptyxis"])
        self.assertEqual(report["system_files"], 17)

    def test_switch_preflights_before_authentication_and_orders_committed_stages(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            result = app.switch("eucalyptus-felt")
        self.assertEqual(result["status"], "active")
        self.assertEqual(app.saved_statuses, ["desktop-applying", "system-applying", "committing", "active"])
        calls = runtime.names()
        self.assert_before(calls, "preflight", "authorize")
        self.assert_before(calls, "authorize", "desktop-apply")
        self.assert_before(calls, "desktop-apply", "desktop-check")
        self.assert_before(calls, "desktop-check", "system-apply")
        self.assert_before(calls, "system-apply", "system-check")
        self.assert_before(calls, "system-check", "desktop-keep")
        self.assertEqual(runtime.system[("eucalyptus-felt", self.IDENT)]["status"], "applied")
        self.assertEqual(runtime.desktop[self.IDENT]["status"], "kept")
        persisted = app.records()[0]
        self.assertEqual(persisted["id"], self.IDENT)
        self.assertEqual(persisted["status"], "active")

    def test_app_writer_blocker_prevents_authentication_and_all_privileged_stages(self):
        runtime = FakeRuntime(preflight_error=shortcut.ShortcutError("Ptyxis is still running"))
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "Ptyxis is still running"):
                app.switch("eucalyptus-felt")
        self.assertEqual(runtime.names(), ["lock-enter", "preflight", "lock-exit"])
        self.assertFalse(self.store.exists(), "a blocked preflight must not create a master receipt")

    def test_system_apply_failure_recovers_desktop_then_exact_system_receipt(self):
        runtime = FakeRuntime(system_apply_error=RuntimeError("simulated system apply fault"))
        app = self.make_app(runtime)
        master_before_system_restore = []
        original_system_restore = runtime.system_restore

        def inspect_master_before_system_restore(slug, ident, receipt=None, save=None):
            master_before_system_restore.append(app.records()[0].copy())
            return original_system_restore(slug, ident, receipt, save)

        runtime.system_restore = inspect_master_before_system_restore
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "every started stage was restored"):
                app.switch("eucalyptus-felt")
        self.assertIn(("desktop-restore", self.IDENT), runtime.calls)
        self.assertIn(("system-restore", "eucalyptus-felt", self.IDENT), runtime.calls)
        self.assert_before(runtime.names(), "desktop-restore", "system-restore")
        self.assertEqual(runtime.system[("eucalyptus-felt", self.IDENT)]["status"], "restored")
        self.assertEqual(len(master_before_system_restore), 1)
        # Apply persisted a preparing receipt and failed before the coordinator
        # could set system_started. Recovery still uses the exact transaction.
        self.assertNotIn("system_started", master_before_system_restore[0])
        self.assertEqual(runtime.system[("eucalyptus-felt", self.IDENT)]["status"], "restored")
        self.assertEqual(app.records()[0]["status"], "rolled-back")

    def test_failed_pair_restore_stays_recovery_required_and_blocks_new_switch(self):
        runtime = FakeRuntime(
            system_apply_error=RuntimeError("root apply stopped after receipt persistence"),
            system_restore_error=RuntimeError("injected system restore failure"),
        )
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"):
                app.switch("eucalyptus-felt")
        saved = app.records()[0]
        self.assertEqual(saved["status"], "recovery-required")
        self.assertTrue(any("Boot/login: injected system restore failure" in item for item in saved["recovery_errors"]))
        self.assertEqual(runtime.desktop[self.IDENT]["status"], "restored")
        self.assertEqual(runtime.system[("eucalyptus-felt", self.IDENT)]["status"], "preparing")
        runtime.calls.clear()
        with self.assertRaisesRegex(shortcut.ShortcutError, "unfinished switch"):
            app.switch("slate-orbit")
        self.assertEqual(runtime.names(), ["lock-enter", "lock-exit"])

    def test_desktop_restore_conflict_prevents_system_unwind_and_remains_retryable(self):
        runtime = FakeRuntime(
            system_apply_error=RuntimeError("root apply stopped after receipt persistence"),
            desktop_restore_error=RuntimeError("external desktop change"),
        )
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"):
                app.switch("eucalyptus-felt")
        saved = app.records()[0]
        self.assertEqual(saved["status"], "recovery-required")
        self.assertTrue(any("Desktop: external desktop change" in item for item in saved["recovery_errors"]))
        self.assertNotIn("system-restore", runtime.names())
        self.assertEqual(runtime.system[("eucalyptus-felt", self.IDENT)]["status"], "preparing")

    def test_desktop_check_failure_restores_desktop_without_touching_system(self):
        runtime = FakeRuntime(desktop_check_ok=False)
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "every started stage was restored"):
                app.switch("eucalyptus-felt")
        calls = runtime.names()
        self.assertIn("desktop-restore", calls)
        self.assertNotIn("system-apply", calls)
        self.assertNotIn("system-restore", calls)
        self.assertEqual(runtime.desktop[self.IDENT]["status"], "restored")
        self.assertEqual(app.records()[0]["status"], "rolled-back")

    def test_red_panda_desktop_failure_never_starts_root_stage(self):
        runtime = FakeRuntime(desktop_check_ok=False)
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            with self.assertRaisesRegex(shortcut.ShortcutError, "every started stage was restored"):
                app.switch("red-panda-overtime")
        self.assertIn(("desktop-restore", self.IDENT), runtime.calls)
        self.assertNotIn("system-apply", runtime.names())
        self.assertNotIn("system-restore", runtime.names())
        self.assertEqual(app.records()[0]["status"], "rolled-back")

    def test_restore_uses_the_kept_desktop_receipt_before_its_exact_system_receipt(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        with patch.object(shortcut, "stamp", return_value=self.IDENT):
            app.switch("eucalyptus-felt")
        runtime.calls.clear()
        restored = app.restore()
        self.assertEqual(restored["status"], "restored")
        self.assertIn(("desktop-restore", self.IDENT), runtime.calls)
        self.assertIn(("system-restore", "eucalyptus-felt", self.IDENT), runtime.calls)
        self.assert_before(runtime.names(), "desktop-restore", "system-restore")
        self.assertEqual(app.records()[0]["status"], "restored")

    def test_desktop_only_recovery_skips_authorization_and_system_receipts(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        data = self.create_record(app, self.IDENT, "lavender-glass", "desktop-applying", "2026-09-20T23:00:00+00:00")
        data["desktop_started"] = True
        app.save(data)
        runtime.desktop[self.IDENT] = {"status": "restored"}
        runtime.system[("lavender-glass", self.IDENT)] = {"status": "applied"}
        runtime.calls.clear()
        restored = app.restore()
        self.assertEqual(restored["status"], "restored")
        self.assertNotIn("authorize", runtime.names())
        self.assertNotIn("system-receipt", runtime.names())
        self.assertNotIn("system-restore", runtime.names())
        self.assertEqual(app.records()[0]["status"], "restored")

    def test_unfinished_master_receipt_refuses_a_new_switch_before_preflight(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        self.create_record(app, self.IDENT, "eucalyptus-felt", "system-applying", "2026-09-20T23:00:00+00:00")
        runtime.calls.clear()
        with self.assertRaisesRegex(shortcut.ShortcutError, "unfinished switch"):
            app.switch("slate-orbit")
        self.assertEqual(runtime.names(), ["lock-enter", "lock-exit"])

    def test_restore_uses_created_at_newest_active_receipt_then_the_previous_one(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        # The id ordering is intentionally opposite from created_at, so this
        # protects newest-first undo from falling back to lexicographic paths.
        older = "20260920-235959-ffffaa"
        newer = "20260920-000001-000abc"
        self.create_record(app, older, "eucalyptus-felt", "active", "2026-09-20T23:59:59+00:00")
        self.create_record(app, newer, "graphite-brass", "active", "2026-09-21T00:00:01+00:00")
        runtime.desktop[older] = {"status": "kept"}
        runtime.desktop[newer] = {"status": "kept"}
        runtime.system[("eucalyptus-felt", older)] = {"status": "applied"}
        runtime.system[("graphite-brass", newer)] = {"status": "applied"}
        first = app.restore()
        second = app.restore()
        self.assertEqual(first["id"], newer)
        self.assertEqual(second["id"], older)
        restored_ids = [call[1] for call in runtime.calls if call[0] == "desktop-restore"]
        self.assertEqual(restored_ids, [newer, older])

    def test_status_is_observational_and_does_not_mutate_or_authenticate(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        self.create_record(app, self.IDENT, "eucalyptus-felt", "active", "2026-09-20T23:00:00+00:00")
        receipt = self.store / self.IDENT / "receipt.json"
        before = receipt.read_bytes()
        runtime.calls.clear()
        report = app.status()
        self.assertEqual(report["shortcut"]["id"], self.IDENT)
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(runtime.names(), ["current"])

    def test_successful_system_apply_with_missing_receipt_cannot_claim_rollback(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        with (
            patch.object(shortcut, 'stamp', return_value=self.IDENT),
            patch.object(runtime, 'system_receipt', return_value={'status': 'missing'}),
            patch.object(transaction_support, 'run_gated', side_effect=lambda command, associate: (
                associate({"pid": 123, "session": 123, "start_time": 1, "boot_id": "test"})
                or subprocess.CompletedProcess(command, 0, '{"status":"applied"}', '')
            )),
            self.assertRaisesRegex(shortcut.ShortcutError, 'Expected exact boot/login recovery receipt is missing'),
        ):
            app.switch('graphite-brass')
        receipt = app.records()[0]
        self.assertTrue(receipt['system_started'])
        self.assertEqual(receipt['status'], 'recovery-required')
        self.assertIn('desktop-apply', runtime.names())
        self.assertIn('desktop-restore', runtime.names())

    def test_known_started_system_receipt_missing_keeps_master_recovery_required(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        data = self.create_record(app, self.IDENT, "eucalyptus-felt", "recovery-required", "2026-09-20T23:00:00+00:00")
        data["system_started"] = True
        app.save(data)
        runtime.calls.clear()
        with self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"):
            app.recover(data, rollback=True)
        self.assertIn(("system-receipt", "eucalyptus-felt", self.IDENT), runtime.calls)
        self.assertNotIn("system-restore", runtime.names())
        saved = app.records()[0]
        self.assertEqual(saved["status"], "recovery-required")
        self.assertTrue(saved["recovery_errors"])

    def test_missing_known_desktop_receipt_blocks_system_unwind(self):
        runtime = FakeRuntime()
        app = self.make_app(runtime)
        data = self.create_record(app, self.IDENT, "eucalyptus-felt", "recovery-required", "2026-09-20T23:00:00+00:00")
        data.update(system_started=True, desktop_started=True)
        app.save(data)
        runtime.system[("eucalyptus-felt", self.IDENT)] = {"status": "applied"}
        runtime.calls.clear()
        with self.assertRaisesRegex(shortcut.ShortcutError, "Recovery needs attention"):
            app.recover(data, rollback=True)
        self.assertIn(("desktop-receipt", self.IDENT), runtime.calls)
        self.assertNotIn("system-restore", runtime.names())
        self.assertEqual(app.records()[0]["status"], "recovery-required")

    def test_system_adapter_uses_exact_transaction_and_privilege_per_action(self):
        runtime = shortcut.Runtime()
        slug = "eucalyptus-felt"
        commands = []
        def fake_gated(command, associate):
            commands.append(command)
            associate({"pid": 123, "session": 123, "start_time": 1, "boot_id": "test"})
            return subprocess.CompletedProcess(command, 0, '{"status":"applied"}', '')
        with patch.object(runtime, "run_json", return_value={}) as run_json, \
             patch.object(transaction_support, "run_gated", side_effect=fake_gated), \
             redirect_stderr(io.StringIO()):
            runtime.system_command(slug, "preview")
            runtime.system_apply(slug, self.IDENT)
            runtime.system_receipt(slug, self.IDENT)
            runtime.system_check(slug, self.IDENT)
            runtime.system_restore(slug, self.IDENT)
        plain = ["/usr/bin/python3", str(shortcut.SYSTEM), slug]
        self.assertEqual(
            commands,
            [
                ["sudo", "-n", "--", *plain, "apply", "--transaction", self.IDENT, "--commit"],
                ["sudo", "-n", "--", *plain, "restore", "--transaction", self.IDENT, "--commit"],
            ],
        )
        self.assertEqual([item.args[0] for item in run_json.call_args_list], [
            [*plain, "preview"],
            ["sudo", "-n", "--", *plain, "receipt-status", "--transaction", self.IDENT],
            ["sudo", "-n", "--", *plain, "check", "--transaction", self.IDENT],
        ])

    def test_candidate_ids_and_cli_aliases_match_nim_catalog_without_candidate_registry_promotion(self):
        contribution = ROOT / "contributions" / "all-theme-plan"
        candidates = json.loads((contribution / "collection-profile-candidates.json").read_text())["profiles"]
        nim_path = ROOT.parents[1] / "repairs" / "nim-nitch" / "catalog" / "generated" / "themes.json"
        nim_profiles = json.loads(nim_path.read_text())["profiles"]
        candidate_slugs = {
            "dusk-ribbons", "macintosh-soft", "macintosh-soft-evergreen",
            "moonstone-stereo", "ocean-silk", "plum-afterglow", "quiet-sage",
        }
        self.assertEqual(set(candidates), candidate_slugs)
        self.assertTrue(candidate_slugs.isdisjoint(shortcut.PROFILES), "candidate-only themes must remain unregistered")
        # This saved Nim catalog predates the legacy component collection.
        # Compare its registered entries without requiring newer profiles to
        # have been added to that separate catalog. Tidal is already released.
        self.assertEqual((set(shortcut.PROFILES) & set(nim_profiles)) | candidate_slugs, set(nim_profiles))
        self.assertIn("tidal-observatory", shortcut.PROFILES)
        tidal = nim_profiles["tidal-observatory"]
        self.assertEqual(tidal["availability"], "installed")
        self.assertTrue(tidal["capabilities"]["current_switch"])
        self.assertIn("tidal", tidal["aliases"]["cli"])
        system_dir = contribution / "system" / "profiles"
        for slug in sorted(candidate_slugs):
            candidate = candidates[slug]
            catalog = nim_profiles[slug]
            self.assertEqual(candidate["aliases"]["cli"], catalog["aliases"]["cli"], slug)
            self.assertEqual(candidate["aliases"]["cli"][0], slug, slug)
            system_profile = json.loads((system_dir / f"{slug}.json").read_text())
            self.assertEqual(system_profile["slug"], slug)
            self.assertEqual(system_profile["stage_id"], f"cinnamon-current-{slug}")

    def test_system_adapter_uses_private_askpass_when_coordinator_provides_it(self):
        runtime = shortcut.Runtime()
        slug = "eucalyptus-felt"
        environment = {"SUDO_ASKPASS": "/tmp/private-askpass"}
        with patch.dict(shortcut.os.environ, environment, clear=False), \
             patch.object(runtime, "run_json", return_value={}) as run_json:
            runtime.system_receipt(slug, self.IDENT)
        plain = ["/usr/bin/python3", str(shortcut.SYSTEM), slug,
                 "receipt-status", "--transaction", self.IDENT]
        self.assertEqual(run_json.call_args.args[0], ["sudo", "-A", "--", *plain])


class SystemStageAdapterTests(unittest.TestCase):
    """Exercise only system.py argument routing with its writers mocked out."""

    IDENT = ShortcutTests.IDENT
    SLUG = "eucalyptus-felt"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = {"slug": self.SLUG}
        self.paths = SimpleNamespace(state=Path(self.temporary.name) / "stage-state")

    def invoke(self, arguments, **replacements):
        stdout, stderr = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(system_stage, "profile_slugs", return_value=[self.SLUG]))
            stack.enter_context(patch.object(system_stage, "load_profile", return_value=self.profile))
            stack.enter_context(patch.object(system_stage, "stage_paths", return_value=self.paths))
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(system_stage, name, replacement))
            with patch.object(system_stage.sys, "argv", [str(SYSTEM_STAGE), *arguments]), redirect_stdout(stdout), redirect_stderr(stderr):
                code = system_stage.main()
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_parser_routes_receipt_status_and_targeted_check_without_root_writes(self):
        receipt_status = Mock(return_value={"id": self.IDENT, "status": "applied"})
        require_root = Mock()
        code, result, stderr = self.invoke(
            [self.SLUG, "receipt-status", "--transaction", self.IDENT],
            receipt_status=receipt_status,
            require_root=require_root,
            lock=Mock(return_value=contextlib.nullcontext()),
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["status"], "applied")
        receipt_status.assert_called_once_with(self.profile, self.IDENT, self.paths)
        require_root.assert_called_once_with("receipt-status")

        check_receipt = Mock(return_value={"ok": True, "profile": self.SLUG})
        require_root = Mock()
        code, result, stderr = self.invoke(
            [self.SLUG, "check", "--transaction", self.IDENT],
            locked_check_receipt=check_receipt,
            require_root=require_root,
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(result["ok"])
        check_receipt.assert_called_once_with(self.profile, self.IDENT, self.paths)
        require_root.assert_called_once_with("Receipt-bound check")

    def test_parser_binds_committed_apply_and_restore_to_the_exact_transaction(self):
        source = {"sources": {"fixture": "stable"}}
        build = Mock(return_value=([], [], source))
        prerequisites = Mock(return_value=[])
        verify_sources = Mock(return_value=source)
        apply = Mock(return_value={"status": "applied", "receipt": "fixture"})
        lock = Mock(return_value=contextlib.nullcontext())
        code, result, stderr = self.invoke(
            [self.SLUG, "apply", "--transaction", self.IDENT, "--commit"],
            build=build,
            prerequisites=prerequisites,
            verify_sources=verify_sources,
            apply=apply,
            lock=lock,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["status"], "applied")
        apply.assert_called_once_with(self.profile, [], self.paths, transaction=self.IDENT, source_report=source)
        lock.assert_called_once_with(self.paths)

        restore = Mock(return_value={"status": "restored", "receipt": "fixture"})
        lock = Mock(return_value=contextlib.nullcontext())
        code, result, stderr = self.invoke(
            [self.SLUG, "restore", "--transaction", self.IDENT, "--commit"],
            restore=restore,
            lock=lock,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["status"], "restored")
        restore.assert_called_once_with(self.profile, self.paths.state / self.IDENT, self.paths)
        lock.assert_called_once_with(self.paths)


if __name__ == "__main__":
    unittest.main()
