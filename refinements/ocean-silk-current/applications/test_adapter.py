"""Current-state plans projected into temporary files; no live writes or GUI calls."""
import base64
from collections import defaultdict
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import adapter as a

class AppearanceTests(unittest.TestCase):
    def fixture(self, root):
        home=Path(root)/'home';home.mkdir()
        paths=['.config/kitty/kitty.conf','.config/konsolerc','.config/tilda/config_0',
               '.var/app/app.devsuite.Ptyxis/config/glib-2.0/settings/keyfile',
               '.var/app/app.devsuite.Ptyxis/config/gtk-4.0/gtk.css',
               '.config/fastfetch/config.jsonc','.config/fastfetch/logos/hues/palettes.json','.config/fastfetch/.theme',
               '.config/fastfetch/modes.py','.config/fastfetch/long.jsonc','.config/fastfetch/apply-hue-colors.py',
               '.config/plum-afterglow/prompt.bash','.nanorc','.config/kdeglobals','.config/kritarc',
               '.config/GIMP/2.10/gimprc','.config/libreoffice/4/user/registrymodifications.xcu',
               '.config/cava/config','.bashrc','.local/share/flatpak/overrides/global']
        paths += [str(p.relative_to(a.HOME)) for p in (a.HOME/'.local/share/konsole').glob('*.profile')]
        paths += ['.local/share/cinnamon/extensions/gTile@shuairan/'+name for name in ['stylesheet.css','5.4/stylesheet.css','3.8/stylesheet.css']]
        paths += [a.DASH_REL+'/'+name for name in ['appearance.json','ui/index.html','native_music_flow.py','dashboard.py','ui/app.js','backend/media.py','backend/music.py']]
        for relative in paths:
            source=a.HOME/relative
            if source.is_file():
                target=home/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        logo=Path(root)/'fixture-logo.png'
        logo.write_bytes((a.PRESET.parents[1]/'refinements/macintosh-soft-evergreen/artwork/menu-logo.png').read_bytes())
        return home,logo

    def test_full_file_plan_apply_restore_and_protected_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            home,logo=self.fixture(tmp)
            state=a.plan(home,settings=False,logo_path=logo)
            self.assertGreater(len(state['actions']),80)
            self.assertGreaterEqual(len(state['protected_edits']),2)
            for action in state['actions']:
                self.assertTrue(Path(action['path']).is_relative_to(home),action)
                self.assertEqual(a.current(action),action['before'])
            originals={path:Path(path).read_bytes() if Path(path).exists() else None for path in state['touched_paths']}
            a.validate(state)
            with patch.object(a,'ensure_closed'):
                for action in state['actions']:
                    a.write(action,action['after']);action['applied']=True
                a.validate(state)
                self.assertTrue(all(a.current(action)==action['after'] for action in state['actions']))
                html=(home/a.DASH_REL/'ui/index.html').read_text()
                self.assertEqual(html.count('href="ocean-silk-current.css"'),1)
                self.assertEqual(html.rsplit('</head>',1)[0].splitlines()[-1],'<link rel="stylesheet" href="ocean-silk-current.css">')
                hook=(home/a.DASH_REL/'native_music_flow.py').read_text()
                surface=a.engine.load(a.PRESET/'design.json')['palette']['surface']
                self.assertTrue('return Gdk.RGBA(channels[0],channels[1],channels[2],1)' in hook or 'Gdk.RGBA('+','.join(str(v)+'/255' for v in a.engine.rgb(surface))+',1)' in hook)
                for entry in state['protected_edits']:
                    self.assertEqual(a.engine.digest(Path(entry['path']).read_bytes()),entry['after_sha256'])
                # Projected source and behavior invariants detect unrelated edits.
                p=home/a.DASH_REL/'native_music_flow.py';saved=p.read_text();p.write_text(saved+'\n# unexpected change\n')
                with self.assertRaises(RuntimeError):a.validate(state)
                p.write_text(saved)
                a.engine.restore(state,Path(tmp)/'receipt.json')
            for path,data in originals.items():
                if data is None:self.assertFalse(Path(path).exists(),path)
                else:self.assertEqual(Path(path).read_bytes(),data,path)

    def test_fastfetch_updater_and_long_keep_identity_and_custom_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home,logo=self.fixture(tmp);state=a.plan(home,settings=False,logo_path=logo)
            for action in state['actions']:
                if '/.config/fastfetch/' in action.get('path',''):a.write(action,action['after'])
            root=home/'.config/fastfetch'
            spec=importlib.util.spec_from_file_location('fixture_hues',root/'apply-hue-colors.py');hue=importlib.util.module_from_spec(spec);spec.loader.exec_module(hue)
            hue.CONFIG_PATH=str(root/'config.jsonc');hue.PALETTES_PATH=str(root/'logos/hues/palettes.json');hue.HUES_DIR=str(root/'logos/hues')
            before=(root/'config.jsonc').read_bytes()
            with patch('sys.argv',['apply-hue-colors.py','ocean-silk-current']):hue.main();hue.main()
            self.assertEqual((root/'config.jsonc').read_bytes(),before)
            spec=importlib.util.spec_from_file_location('fixture_modes',root/'modes.py');modes=importlib.util.module_from_spec(spec);spec.loader.exec_module(modes)
            current=json.loads(before);long=modes.build(current,json.loads((root/'long.jsonc').read_text()),json.loads((root/'logos/hues/palettes.json').read_text()))
            self.assertEqual(long['logo'],current['logo'])
            self.assertTrue(current['logo']['source'].endswith('macklenmobile-logo-ocean-silk-current.png'))
            self.assertTrue(any(m.get('format')=='86.75-309' for m in long['modules'] if isinstance(m,dict)))

    def test_running_apps_preflight_happens_before_transaction(self):
        with patch.object(a,'ensure_closed',side_effect=RuntimeError('krita is running')) as closed:
            with self.assertRaisesRegex(RuntimeError,'krita'):a.preflight({'requires_closed':['krita']})
            closed.assert_called_once_with('krita')

    def test_generated_styles_use_canonical_palette(self):
        d=a.engine.load(a.PRESET/'design.json');t=d['palette'];colors=d['terminal_ansi']
        for text in [a.render.library(t),a.render.sourceview(t),a.render.kde_scheme(t),a.render.kitty(t,colors)]:
            self.assertNotIn('#67537D',text);self.assertNotIn('#EBE9E1',text)
        self.assertEqual(len(colors),16)
        self.assertIn('Name=Ocean-Silk',a.render.ptyxis(t,colors))

if __name__=='__main__':unittest.main()
