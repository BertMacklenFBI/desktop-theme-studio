# GNU-Darwin Workstation staged system appearance

Black workspace, square beveled gray surfaces, slate selections and text-only identity. No invented official logo or copied screenshot artwork. GRUB fonts are generated from installed Liberation Sans/Mono. Greeter selects Liberation Sans 10 and cursor24. Plymouth retains password/question entry, caps-lock indicator, message, boot-progress and shutdown callbacks. Mockups are simulated image compositions, never actual boot or prompt captures.

Run each generate.py, then system/render.py, system/test_system.py, system/plymouth/check.py and system/system.py preview using /usr/bin/python3 -B. All output stays staged. Receipts for any future authorized installation belong to /var/lib/desktop-theme-studio/gnu-darwin-workstation. Timeout remains in 98_mintsysadm.cfg; no EFI, PAM, autologin or encryption edits.

The current /etc/plymouth/plymouthd.conf is absent; backup files are not active overrides. Future authorized install would create Theme=gnu-darwin-workstation under transaction protection. No installation, real boot, shutdown, login, lock/unlock, authentication or disk-unlock testing occurred. Firmware appearance is unsupported and console/recovery configuration is preserved.
