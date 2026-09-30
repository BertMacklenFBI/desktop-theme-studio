# Desktop Theme Studio

Use a coordinator and three domain specialists when implementing a whole theme: desktop/artwork, applications/terminal, and boot/login/completeness. The user also authorizes additional reviewers and nested delegation for concrete independent tasks. Respect the runtime concurrency limit, assign separate file ownership, and reuse available workers. An independent design/usability reviewer should inspect the combined result before activation when the scope benefits from it.

## Ownership
- Coordinator: `studio.py`, wrappers, `tests/`, `state/`, combined reports, manifests, shared design specification, integration and all live application.
- Desktop/artwork: `audits/desktop.json`, `docs/desktop-audit.md`, and explicitly assigned theme source directories.
- Applications/terminal: `audits/applications.json`, `docs/applications-audit.md`, and explicitly assigned app source directories.
- Boot/login: `audits/system.json`, `docs/system-audit.md`, and explicitly assigned staged system assets.
- Workers never change live settings, install into the home/system directories, restart processes, reboot, or edit another worker's files. Report handoffs with files, evidence, limitations and remaining work.

## Design and completion
Read `docs/design-spec.md` first. Every theme uses a single named palette and typography specification. Preserve exact names and supplied artwork. Do not replace or regenerate artwork without an explicit design request.

Inventory installed surfaces before declaring scope complete. Update coverage evidence separately for built assets, selected configuration, visual inspection, functionality and reboot/login verification. Do not treat an old screenshot or saved installer as current verification. Mark unavailable surfaces with a reason.

Protect shell/music commands and hash them before and after work. Appearance configuration edits must not overwrite unrelated app preferences. Use staged files with separate ownership and fresh backups. Only the coordinator applies live changes, one stage at a time. Do not run `cinnamon --replace`, broad process kills, or parallel GUI tests. Preserve existing disabled-installer markers and guarded rollout requirements.

The initial framework deliberately supports regular appearance files and GSettings only. `studio.py apply` previews unless `--commit` is supplied. Shell/panel and root-owned boot/login stages remain subject to the documented guarded/manual workflows; never claim the generic transaction runner protects against a desktop/session crash. Never silently override a restore conflict.
