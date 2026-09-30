#!/usr/bin/python3
"""Solid palette-black workspace; preserve all supplied artwork and Fastfetch files."""
from pathlib import Path
from PIL import Image
import json,hashlib,subprocess
HERE=Path(__file__).resolve().parent;D=json.loads((HERE.parent/'design.json').read_text());P=D['palette']
Image.new('RGB',(2880,1800),P['desktop']).save(HERE/'wallpaper.png')
Image.new('RGB',(1440,900),P['desktop']).save(HERE/'preview.png')
# Preserve the exact recovered GNUstep icon shape/colors as an optional menu asset,
# clearly attributed as Window Maker artwork, never presented as a GNU-Darwin logo.
src=HERE.parents[2]/'proposals/gnu-darwin-archive/windowmaker-assets/extracted/usr/share/WindowMaker/Icons/GNUstepGlow.xpm'
subprocess.run(['/usr/bin/convert',str(src),'-strip','-define','png:exclude-chunks=date,time',str(HERE/'menu-logo.png')],check=True)
files={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':list(Image.open(p).size)} for p in HERE.glob('*.png')}
(HERE/'manifest.json').write_text(json.dumps({'name':D['name'],'wallpaper_source':'New solid palette black, matching uncovered reference pixels; no branding','menu_source':str(src),'menu_source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'menu_author':'Banlu Kemiyatorn, 2000','menu_license':'WTFPL-1; see desktop icon copyright','historical_limit':'Modern Window Maker package artwork; not proven identical to 2002 screenshot bytes','files':files},indent=2)+'\n')
print('Workstation wallpaper ready')
