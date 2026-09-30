# Shortcut test plan

`shortcut.py` is an orchestrator over the existing desktop transaction and the
root-owned GRUB, Plymouth, and Slick Greeter transaction. These tests use a
temporary shortcut-receipt store and a fake runtime only. They do not import
the live desktop engine, invoke `sudo`, run system commands, launch a GUI, or
write outside the test temporary directory.

The test suite verifies the following contract.

- `theme list` reports all six canonical profile ids. Name resolution accepts
  canonical ids and normalized display names, including `Graphite & Brass` and
  `Graphite and Brass`, while rejecting unknown or ambiguous input.
- A runtime preview calls only the desktop plan and the system preview. It
  creates no master receipt, asks for no authentication, and may report an
  open application writer without treating it as a switch blocker.
- A switch calls desktop/app-writer preflight before authentication and before
  every privileged boot/login operation. On success it orders system apply and
  check before desktop apply, check, and keep, with durable master states
  `system-applying`, `desktop-applying`, `committing`, and `active`.
- If system apply or desktop validation fails, recovery inspects the exact
  predeclared id, restores the desktop before the boot/login stage when both
  exist, and records a durable `rolled-back` master receipt only after both
  cleanup operations succeed.
- A normal undo restores the exact kept desktop receipt before its matching
  system receipt. An unfinished master receipt blocks a new switch before
  preflight or authentication. Multiple retained changes are undone in actual
  `created_at` order, newest first, even when their directory ids sort the
  other way.
- `status` is observational: it does not authenticate, lock a transaction, or
  rewrite a receipt.

- The runtime adapter must serialize the actual system-stage command contract:
  `PROFILE ACTION --transaction ID`, with `--commit` only for committed apply
  and restore, and `sudo -n --` only for the system operations that need it.
  A fixture-driven invocation of the real `system.py` parser verifies that
  `receipt-status`, targeted `check`, committed `apply`, and committed
  `restore` accept and preserve that exact transaction id without accessing a
  root path or writer.
- Recovery treats a missing receipt as an error when the durable master says
  that stage started. A missing known desktop receipt keeps the master in
  `recovery-required` and prevents a system unwind; a missing known system
  receipt also remains retryable rather than being incorrectly marked rolled
  back. A successful system-apply response records this obligation before
  looking up the receipt, so a missing first lookup cannot claim a rollback.

Run from the current-collection directory:

```sh
python3 -B -m unittest -v tests/test_shortcut.py
```

The plan intentionally excludes boot, Plymouth, GRUB, Slick Greeter, desktop,
and GUI integration tests. Those belong to the coordinator's serial live and
reboot/login verification flows.
