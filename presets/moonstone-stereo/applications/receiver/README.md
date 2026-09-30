# Moonstone Stereo receiver

Complete staged Eww configuration. Main window `clock` is an original 1220x290 walnut/aluminum receiver at bottom-center y-130. The main application stage must run before `refine.py`; restore refinement before base.

- `refine.py plan|apply|check|restore --state <fresh-state> [--commit]` controls only scoped frontend/source changes. Apply/restore preview unless committed.
- `audio_meter.py` reads the existing shared Cava panel output only; it never launches capture. LOW BAND / HIGH BAND are relative spectrum indicators, not L/R or calibrated dB.
- `MOONSTONE_PREVIEW=1` yields explicit zero PREVIEW / IDLE without reading live audio. Freeze both s/vu listeners in fully isolated fixtures.
- Original static needle faces and machined-control/brushed-metal SVGs live in `materials/`; no per-frame asset writes.
- Existing scripts/assets are linked for compatibility; preview controls would call the live handler, so do not click them in isolated rendering.
- Actual Grass/GTK parse and fixture tests are recorded in `validation.json`. No worker GUI launch or live settings change occurred.

Full integration and rollback documentation: `docs/moonstone-stereo-applications.md` at the Studio root.

QA refinement: low-contrast instrumentation is darkened; operational date/elapsed text is 12px. Auxiliary control, dashboard, calendar, notes, timer, audio and dock surfaces use the same shallow aluminum cases and charcoal wells. Notes/focus windows center rather than relying on y800 positioning. The refinement now installs carbon.yuck as well as the other frontend files.
