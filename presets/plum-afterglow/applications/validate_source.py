#!/usr/bin/env python3
"""Read-only schema/palette/contrast/source validation; no UI or live writes."""
import base64,importlib.util,json,re,xml.etree.ElementTree as ET
from pathlib import Path
from gi.repository import Gio,GLib
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('app',HERE/'adapter.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
d=json.loads((HERE.parent/'design.json').read_text());t=d['tokens'];plan=m.plan()
for action in plan['actions']:
 if action['kind']=='gsetting':
  schema=Gio.SettingsSchemaSource.get_default().lookup(action['schema'].split(':')[0],True)
  assert schema.get_key(action['key']).range_check(GLib.Variant.parse(None,action['after'],None,None))
 if action['kind']=='file' and action['path'].endswith('.xml'):ET.fromstring(base64.b64decode(action['after']))
files={a['path']:base64.b64decode(a['after']) for a in plan['actions'] if a['kind']=='file'}
kitty=files[str(Path.home()/'.config/kitty/plum-afterglow.conf')].decode()
assert 'font_size 11.0' in kitty and 'window_padding_width 12' in kitty and 'background_opacity 1.0' in kitty
logo=(HERE.parent/'artwork/menu-logo.png').read_bytes()
assert files[str(Path.home()/'.config/fastfetch/logos/hues/macklenmobile-logo-plum-afterglow.png')]==logo
assert files[str(Path.home()/'Documents/Codex/2026-09-05/le/outputs/nocturne-studio/music-widget/ui/fastfetch-logo.png')]==logo
source=(HERE/'receiver/saimoom.yuck').read_text()
for handler in ('media seek {}','media previous','media toggle','media next'):assert source.count(handler)==1
assert source.count(':active {s.media.player != ""}')==3
assert 'native_visibility.py show && scripts/ui hide' in source
windows=(HERE/'receiver/eww.yuck').read_text()
assert windows.count('(deflisten')==1 and '(defpoll' not in windows
assert windows.count(':anchor "bottom center"')==12
assert windows.count(':y "-66px"')==12
colors=[]
def lum(color):
 values=[int(color[i:i+2],16)/255 for i in (1,3,5)];values=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in values];return sum(a*b for a,b in zip(values,[.2126,.7152,.0722]))
for fg,bg in [('foreground','background'),('foreground','elevated'),('foreground','selection'),('muted','surface'),('accent','surface'),('sage','surface'),('background','accent'),('background','sage')]:
 a,b=lum(t[fg]),lum(t[bg]);ratio=(max(a,b)+.05)/(min(a,b)+.05);assert ratio>=4.5,(fg,bg,ratio);colors.append([fg,bg,round(ratio,2)])
print(json.dumps({'status':'staged-source-validated','theme':d['name'],'actions':len(plan['actions']),'paths':len(plan['touched_paths']),'logo_sha256':m.digest(logo),'contrast_pairs':colors,'geometry':'all 12 windows bottom-center y-66; music/dashboard width860','gui_launched':False},indent=2))
