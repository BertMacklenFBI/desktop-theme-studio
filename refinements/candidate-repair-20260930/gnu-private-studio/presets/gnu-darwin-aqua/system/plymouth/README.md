# GNU-Darwin Aqua staged system appearance

Original Aqua palette assets generated from this preset's design and wallpaper. No installation or live boot/login test performed. GRUB uses Liberation Sans/Mono PFF2 fonts, silver menu, blue selection and an opaque caption strip. Plymouth includes password bullets, question entry, caps-lock state, message and shutdown callbacks; five images in `plymouth/mockups/` are simulated geometry previews, not actual Plymouth captures.

Run generators, `render.py`, `test_system.py`, `plymouth/check.py`, then `system.py preview` with `/usr/bin/python3 -B`. Generated templates are in `rendered/`; slick-greeter.preview.conf selects Liberation Sans 11 and cursor24. Receipts remain namespaced under /var/lib/desktop-theme-studio/gnu-darwin-aqua. GRUB timeout belongs to 98_mintsysadm.cfg and is preserved. EFI, PAM, autologin and encryption configuration are untouched.

The active /etc/plymouth/plymouthd.conf is absent on this host. A Tangerine-named backup is not an active override. Future explicit system installation would create Theme=gnu-darwin-aqua using the receipt-backed installer; no unrelated restore is needed now. All real boot, shutdown, greeter, lock/unlock, authentication and early disk-unlock behavior remains unverified. Firmware appearance is unsupported; Linux virtual console/recovery appearance is preserved rather than customized.
