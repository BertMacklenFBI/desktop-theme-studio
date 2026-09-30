# Desktop coverage audit

Generated: 2026-09-20T04:33:59.982713+00:00

Configuration observations are not visual or functional verification. See the three specialist reports for evidence and remaining checks.

| Area | Surface | Status | Remaining verification / gaps |
|---|---|---|---|
| desktop-artwork | Cinnamon shell | observed-config | No current-session screenshot inspected. |
| desktop-artwork | Top panel and bottom dock | observed-config | Allocation height may differ from visibly painted height. |
| desktop-artwork | Panel applets | observed-config | Third-party applets can use their own CSS and cannot be assumed covered by shell palette. |
| desktop-artwork | Application launcher and menu logo | observed-config | Exact visible label must be preserved unless design explicitly changes it. |
| desktop-artwork | Centered clock and calendar | observed-config | Calendar popup and clock alignment not visually checked. |
| desktop-artwork | CPU/RAM/network graphs | observed-config | Refresh/legibility not exercised. |
| desktop-artwork | System tray and status icons | observed-config | Application tray icons may be embedded or inherited. |
| desktop-artwork | Notification banners and history | observed-config | No test notification sent. |
| desktop-artwork | Volume/brightness/snap on-screen displays | observed-config | Extension stylesheet may override shell. |
| desktop-artwork | Tooltips and popup menus | observed-config | CSS presence is not visual verification. |
| desktop-artwork | Alt-Tab switcher | observed-config | Switcher previews not inspected. |
| desktop-artwork | Workspaces and overview | observed-config | No workspace or overview interaction performed. |
| desktop-artwork | Nemo desktop icons | observed-config | Disabled surfaces should be designed but not enabled just for theme coverage. |
| desktop-artwork | Window borders and titlebar controls | observed-config | Client-drawn decorations can bypass Metacity. |
| desktop-artwork | GTK2/3/4 controls and file dialogs | observed-config | Application agent owns overrides and app-specific exceptions. |
| desktop-artwork | Applications, folders, devices and MIME icons | observed-config | Inherited icons mean not every icon is individually recolored. |
| desktop-artwork | Pointer cursor family | observed-config | Fallback shapes and applications retaining old cursor need checks. |
| desktop-artwork | Interface, titlebar, document and monospace fonts | observed-config | Font availability/fallback and per-application fonts not rendered. |
| desktop-artwork | Wallpaper artwork and scaling | observed-config | Actual wallpaper image not inspected in this audit; original artwork preserved. |
| desktop-artwork | Desktop desklets | observed-config | Coordinates may be off-screen depending on display layout; no visibility claim.; Desklet CSS and inline styles require separate coverage. |
| desktop-artwork | Shell extensions | observed-config | Installed but disabled visual extensions are not active styling. |
| desktop-artwork | Custom music dashboard | observed-config | Projects/mint-dashboard is a separate older source, not the autostart target.; Running visibility/function not checked. |
| desktop-artwork | Eww theme widgets | observed-config | Autostart enabled does not prove widgets currently running/visible. |
| desktop-artwork | Translucency and compositor | observed-config | Old theme-named helper may apply opacity across newer presets.; No compositor restart or opacity change performed. |
| desktop-artwork | Desktop animation behavior | observed-config | Performance and timing untested. |
| desktop-artwork | Run, authentication and session dialogs | observed-config | Polkit/lock/login use additional theme boundaries; assigned to boot/login agent. |
| applications-terminal | GTK 2 applications | observed-config | Check older widgets, menus, tooltips, disabled states and file dialogs. |
| applications-terminal | GTK 3 applications | observed-config | Verify Nemo, Xed, Xreader, Mint settings, notifications belonging to applications, and authentication dialogs. |
| applications-terminal | GTK 4 applications | observed-config | Verify selector compatibility and widget states separately from GTK3; preserve symlink target in backup. |
| applications-terminal | Libadwaita applications | observed-config | Per-app check required; installed library plus GTK_THEME does not prove matching accents, dialogs or widget rendering. |
| applications-terminal | Qt 5 and KDE applications | observed-config | No explicit unified Qt palette/selection verified; inspect KDE application style, icons, dialogs and syntax themes. |
| applications-terminal | Qt 6 applications | observed-config | Toolkit presence does not establish effective theme in native or sandboxed Qt6 applications. |
| applications-terminal | Flatpak theme access and overrides | observed-config | Inspect app-specific overrides and sandbox visibility before changing grants.; Test GTK, libadwaita, Qt and Electron applications individually; access grants alone do not establish matching appearance. |
| applications-terminal | GNOME Terminal | observed-config | Decide whether to preserve historical named profiles instead of recoloring every profile; exact visible names must be intentional.; Verify bold, selection, cursor, tabs and transparency. |
| applications-terminal | Kitty | observed-config | Current Kitty palette differs from Dusk-Ribbons desktop. |
| applications-terminal | Konsole | observed-config | Current selected terminal palette differs from Dusk-Ribbons desktop. |
| applications-terminal | Ptyxis Flatpak | observed-config | Verify palette visibility in Flatpak; selected palette differs from Dusk-Ribbons. |
| applications-terminal | Tilda drop-down terminal | observed-config | Need dedicated palette adapter and visual check; config contains behavior settings so avoid whole-file replacement. |
| applications-terminal | Shell prompt | observed-config | Do not replace shell startup files. A future prompt should be a separate sourced appearance fragment with a minimal reviewed insertion. |
| applications-terminal | Fastfetch and supplied logo | observed-config | Verify actual logo framing, transparency, color rendering and shell wrapper behavior; do not invoke the mutating wrapper during audit. |
| applications-terminal | Nano | observed-config | Current palette differs from Dusk-Ribbons; preserve editing options and syntax includes. |
| applications-terminal | Xed | observed-config | Need syntax palette plus GTK visual check. |
| applications-terminal | GNOME Builder | observed-config | Need syntax palette and application chrome check. |
| applications-terminal | Kate and KWrite | observed-config | Do not collapse section-specific settings; syntax and application themes require separate adapters. |
| applications-terminal | Cursor editor | unverified | Determine active configuration location through application settings; no profile or workspace data scanned. |
| applications-terminal | Firefox browser chrome | unverified | Browser chrome, tab bar, new tab background, extension icons and website appearance not verified.; Use in-app appearance settings; do not copy browser profiles or modify site content globally by default. |
| applications-terminal | Thunderbird mail chrome | unverified | In-app theme inspection required without reading messages/accounts/profile secrets. |
| applications-terminal | Electron and custom-rendered applications | unverified | Each application needs its own supported theme settings and visual verification.; Obsidian vault theme settings not inspected; no vault search or private content access performed. |
| applications-terminal | Steam, games and launchers | unverified | Steam chrome and game content have independent appearance; inventory launcher icons and supported appearance settings separately. |
| applications-terminal | LibreOffice | unverified | Application UI, icons and document canvas require separate checks; document content colors should remain untouched. |
| applications-terminal | GIMP | observed-config | GIMP uses its own Dark theme; themerc is generated and explicitly says to edit personal gtkrc instead. |
| applications-terminal | Krita and Blender | unverified | Application palettes and canvas/background settings not established; use dedicated app theme assets without changing artwork color management. |
| applications-terminal | Media application chrome | unverified | Verify chrome, controls, playlists and OSD independently from media content. |
| applications-terminal | MPV visualizer and OSD | observed-config | Preserve IPC, MPRIS, profiles and output mode; appearance changes must be limited to declared color fields.; Playback and command functionality were not exercised. |
| applications-terminal | Cava and panel visualizer | observed-config | Panel colors belong to consumer rendering; do not overwrite signal/format settings to theme bars. |
| applications-terminal | Mint Dashboard overlay | observed-config | Dashboard theme differs from Dusk-Ribbons; patch only theme fields and scoped UI styles.; Layout, weather, links and music settings must survive; do not copy whole mixed config as an appearance preset. |
| applications-terminal | Additional desktop application coverage | unverified | Verify representative toolkit surfaces plus per-app exceptions; add every new installed app to inventory. |
| applications-terminal | Universal pixel-identical application theming | unsupported | A universal force-theme adapter is intentionally unsupported; use supported per-app settings and record exceptions. |
| boot-login-completeness | LightDM / Slick Greeter | observed-config | Desktop is configured for Dusk-Ribbons while greeter remains Nocturne-Studio.; Automatic login may skip greeter display; reboot alone is insufficient to verify greeter.; No explicit greeter font-name; actual selected font not observed. |
| boot-login-completeness | System theme, icons, cursor, wallpaper and accessibility assets | observed-config | System assets need separate installation for each new preset.; Some theme/background files and directories are group-writable; ownership mapping appears as nobody/nogroup in sandbox and needs host-level validation before installation.; Readability is filesystem evidence, not a greeter-user runtime test. |
| boot-login-completeness | GRUB graphical menu | observed-config | Generated GRUB selection and timeout cannot be verified with current read permissions.; No reboot visual evidence.; Theme differs from current Dusk-Ribbons desktop. |
| boot-login-completeness | Plymouth startup splash | observed-config | Selected theme is text-based; no custom graphical splash is selected.; Initramfs timestamp is newer than theme descriptor but does not prove bundled theme content.; No startup screenshot or reboot verification. |
| boot-login-completeness | Shutdown and reboot transition splash | unverified | Shutdown/reboot display not observed; startup evidence would not establish shutdown behavior. |
| boot-login-completeness | Cinnamon lock screen and unlock controls | observed-config | No lock/unlock visual or functional verification.; Theme-specific screensaver selector coverage must be checked before declaring a complete preset.; Read-only dconf warning occurred; queried persisted values returned, no write attempted. |
| boot-login-completeness | Polkit authentication dialogs | unverified | Actual running authentication dialog appearance not observed.; Appearance adapters must preserve authentication behavior and password visibility/contrast. |
| boot-login-completeness | Linux virtual console / recovery appearance | observed-config | Console font/colors are separate from GUI terminal themes.; Recovery and emergency consoles have not been rendered or customized. |
| boot-login-completeness | Early disk unlock prompt | unverified | No claim can be made about an encrypted-root or external-device unlock surface solely from crypttab absence.; If applicable, test prompt legibility and input through supported Plymouth customization only. |
| boot-login-completeness | Mac firmware boot picker and pre-Linux logo | unsupported | Desktop Theme Studio does not provide a supported firmware appearance adapter. |
