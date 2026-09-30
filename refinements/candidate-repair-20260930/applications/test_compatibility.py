#!/usr/bin/python3
"""Explicit old/current appearance layouts; no live commands or writes."""
import importlib.util,re,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('compat',Path(__file__).with_name('appearance_compat.py'));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class Compatibility(unittest.TestCase):
    def test_prompt_two_rgb_fields_and_unknown_layout(self):
        source=c.PROMPT_SKELETON.replace('RGB','38;2;190;176;187',1).replace('RGB','38;2;148;205;190',1)
        after=c.prompt_colors(source,'38;2;80;93;102','38;2;53;98;98')
        self.assertEqual(c.prompt_colors(after,'38;2;80;93;102','38;2;53;98;98'),after)
        self.assertEqual(c.RGB.sub('RGB',source),c.RGB.sub('RGB',after))
        for raw in [source+'alias play=changed\n',source.replace('\\w','\\W'),source.replace('38;2;190','38;2;999')]:
            with self.assertRaises(RuntimeError):c.prompt_colors(raw,'38;2;1;2;3','38;2;4;5;6')
    def test_kitty_preserves_commands_and_other_includes(self):
        source='map ctrl+m launch --type=background play\ninclude carbon.conf\nfont_family Hack\n'
        after=c.kitty_include(source,'ocean-silk-current.conf','Ocean Silk')
        self.assertTrue(after.startswith(source));self.assertEqual(c.kitty_include(after,'ocean-silk-current.conf','Ocean Silk'),after)
        with self.assertRaises(RuntimeError):c.kitty_include(after+'# unknown moved block\n','ocean-silk-current.conf','Ocean Silk')
        with self.assertRaises(RuntimeError):c.kitty_include('include ocean-silk-current.conf\n'*2,'ocean-silk-current.conf','Ocean Silk')
    def test_embedded_existing_shell_hook_preserves_all_bytes(self):
        suffix=b'\n# Plum Afterglow prompt (appearance only)\n[ -r "$HOME/.config/plum-afterglow/prompt.bash" ] && . "$HOME/.config/plum-afterglow/prompt.bash"\n'
        raw=b'alias play=unchanged\n# byte \xff'+suffix+b'\n# current later preference\n'
        self.assertTrue(c.prompt_hook_presence(raw,suffix));self.assertEqual(raw,b'alias play=unchanged\n# byte \xff'+suffix+b'\n# current later preference\n')
        with self.assertRaises(RuntimeError):c.prompt_hook_presence(raw+suffix,suffix)
        with self.assertRaises(RuntimeError):c.prompt_hook_presence(raw.replace(b'] && .',b'] ; .'),suffix)
    def test_library_old_and_dynamic_wrappers_and_final_link(self):
        old='before\nreturn Gdk.RGBA(1/255,2/255,3/255,1)\nafter'
        self.assertEqual(c.replace_literal_rgba(old,[5,6,7]),'before\nreturn Gdk.RGBA(5/255,6/255,7/255,1)\nafter')
        dynamic='return Gdk.RGBA(channels[0],channels[1],channels[2],1)'
        self.assertEqual(c.replace_literal_rgba(dynamic,[5,6,7]),dynamic)
        with self.assertRaises(RuntimeError):c.replace_literal_rgba('unknown wrapper',[5,6,7])
        html='<head>\n<link rel="stylesheet" href="old.css">\n</head>\n<button onclick="play()">Play</button>'
        after=c.library_link(html,'candidate.css');self.assertIn('href="old.css"',after);self.assertIn('onclick="play()"',after)
        self.assertEqual(c.library_link(after,'candidate.css'),after)
if __name__=='__main__':unittest.main()
