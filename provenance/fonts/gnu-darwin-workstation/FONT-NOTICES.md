# Staged GRUB font notices

The three PFF2 font payloads reproduce byte-for-byte from the installed Liberation Sans and Liberation Mono source fonts supplied by `fonts-liberation` version `1:2.1.5-3`. Conversion uses `grub-mkfont` with the recorded size, range, family names and bold flag. `grub-font-provenance.json` contains the input/output SHA256 records and commands.

The font license is SIL Open Font License 1.1. The full installed package copyright and license notice is retained verbatim in `Liberation-fonts-copyright`. The derivative family names are the preset name and its Mono variant. These notices are staged for coordinator packaging; source fonts and preset font payloads were unchanged.
