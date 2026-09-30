#!/usr/bin/python3
"""Stage source-attributed Window Maker icon aliases; never install or alter originals."""
from pathlib import Path
import json,hashlib,shutil,subprocess
from PIL import Image,ImageDraw
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
D=json.loads((ROOT/'design.json').read_text());NAME=D['name'];P=D['palette']
PKG=ROOT.parents[1]/'proposals/gnu-darwin-archive/windowmaker-assets/extracted'
SRC=PKG/'usr/share/WindowMaker/Icons';OUT=HERE/(NAME+' icons');CUR=HERE/(NAME+' cursors')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if OUT.exists():shutil.rmtree(OUT)
(OUT/'64x64/apps').mkdir(parents=True)
# Exact functional aliases; modern package provenance, not claimed 2002 originals.
MAP={
 'defaultterm.tiff':(['org.gnome.Terminal','utilities-terminal','terminal'],'terminal','Marco van Hylckama Vlieg, 1997','attribution'),
 'Mozilla.png':(['firefox','web-browser'],'browser','Banlu Kemiyatorn, 2000','WTFPL-1'),
 'staroffice2.tiff':(['libreoffice-startcenter'],'office','Marco van Hylckama Vlieg, 1997','attribution'),
 'write.tiff':(['accessories-text-editor','org.x.editor','libreoffice-writer'],'document/text editor','Marco van Hylckama Vlieg, 1997','attribution'),
 'wilber.tiff':(['gimp'],'image editor','Marco van Hylckama Vlieg, 1997','attribution'),
 'pdf.tiff':(['xreader','application-pdf'],'PDF reader','Marco van Hylckama Vlieg, 1997','attribution'),
 'Drawer.tiff':(['system-file-manager','nemo','folder'],'file manager','Window Maker contributors; full package copyright retained','GPL-2+'),
 '../../GNUstep/System/Applications/WPrefs.app/WPrefs.xpm':(['cs-themes'],'appearance settings','Banlu Kemiyatorn, 2000','WTFPL-1'),
 'xv.tiff':(['xviewer'],'image viewer','Marco van Hylckama Vlieg, 1997','attribution'),
 'GNUstepGlow.xpm':(['gnu-darwin-workstation-menu','preferences-desktop'],'menu/settings icon','Banlu Kemiyatorn, 2000','WTFPL-1')}
records=[]
for name,(aliases,function,author,license) in MAP.items():
 src=SRC/name
 for alias in aliases:
  dest=OUT/'64x64/apps'/(alias+'.png')
  subprocess.run(['/usr/bin/convert',str(src),'-strip','-define','png:exclude-chunks=date,time',str(dest)],check=True)
  records.append({'icon':alias,'function':function,'source':str(src),'source_sha256':sha(src),'output_sha256':sha(dest),'author':author,'license':license,'transformation':'lossless format conversion; shape and colors retained; toolkit scales to48px','historical_identity':'modern packaged source; not verified screenshot bytes'})
# Unknown historical functions keep truthful installed modern identities.
(OUT/'scalable/apps').mkdir(parents=True)
for icon in ['blender','lyx','org.x.Warpinator','gvim','kubrick']:
 src=Path('/usr/share/icons/Papirus/64x64/apps')/(icon+'.svg')
 assert src.is_file(),str(src)
 shutil.copy2(src,OUT/'scalable/apps'/(icon+'.svg'))
 records.append({'icon':icon,'function':'installed modern application','source':str(src),'source_sha256':sha(src),'license':'Papirus GPL-3; COPYRIGHT-Papirus retained','historical_identity':'modern installed substitution, not claimed legacy application'})
shutil.copy2(PKG/'usr/share/doc/wmaker-common/copyright',OUT/'COPYRIGHT-WindowMaker')
if Path('/usr/share/doc/papirus-icon-theme/copyright').exists():shutil.copy2('/usr/share/doc/papirus-icon-theme/copyright',OUT/'COPYRIGHT-Papirus')
(OUT/'index.theme').write_text(f'[Icon Theme]\nName={NAME} icons\nComment=Recovered classic Window Maker icons with documented application aliases\nInherits=Adwaita,hicolor\nDirectories=64x64/apps,scalable/apps\n\n[64x64/apps]\nSize=64\nType=Fixed\nContext=Applications\n\n[scalable/apps]\nSize=64\nType=Scalable\nMinSize=16\nMaxSize=256\nContext=Applications\n')
(OUT/'SOURCE.md').write_text('# GNU-Darwin Workstation icons\n\nRecovered modern Ubuntu Window Maker0.96 package assets, not proven identical2002 screenshot bytes. Icons retain original shape and color; TIFF/XPM converted losslessly toPNG. Application aliases are deliberate function mappings; Mozilla stands for the installed Firefox browser, StarOffice for LibreOffice Start Center, write for Writer and the text editor, WPrefs for Cinnamon appearance settings, and xv for the image viewer. Blender retains its installed identity. Detailed records in ../assets-report.json.\n\nCredit Marco van Hylckama Vlieg(1997) for defaultterm/staroffice2/write/wilber/pdf; distribution or modification permitted with attribution. Credit Banlu Kemiyatorn(2000), WTFPL-1, for Mozilla/GNUstepGlow. Other assets follow package GPL-2+ coverage; full copyright retained. No assertion that these are official GNU-Darwin logos.\n')
if CUR.exists():shutil.rmtree(CUR)
CUR.mkdir()
(CUR/'index.theme').write_text(f'[Icon Theme]\nName={NAME} cursors\nComment=Installed Bibata Modern Classic fallback; modern substitution\nInherits=Bibata-Modern-Classic\n')
(CUR/'cursor.theme').write_text((CUR/'index.theme').read_text())
(CUR/'SOURCE.md').write_text('Inherits installed /usr/share/icons/Bibata-Modern-Classic. Modern practical cursor substitute; no copied cursor binaries and no historical identity claim.\n')
canvas=Image.new('RGB',(len(MAP)*80,88),P['desktop']);draw=ImageDraw.Draw(canvas)
for i,(name,(aliases,*_)) in enumerate(MAP.items()):
 draw.rectangle((i*80+8,8,i*80+71,71),fill=P['accent']);im=Image.open(OUT/'64x64/apps'/(aliases[0]+'.png')).convert('RGBA');im.thumbnail((48,48));canvas.paste(im,(i*80+16,16),im)
canvas.save(HERE/'preview-assets.png')
report={'name':NAME,'design_sha256':sha(ROOT/'design.json'),'icons':records,'count':len(records),'cursor':'Installed Bibata-Modern-Classic inherited without copies','applied':False}
(HERE/'assets-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Built',len(records),'documented icon aliases')
