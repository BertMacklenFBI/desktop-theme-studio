#!/usr/bin/python3
"""Illustrative source-inspired composition only; not Cinnamon/live evidence."""
from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
HERE=Path(__file__).resolve().parent;D=json.loads((HERE.parent/'design.json').read_text());P=D['palette'];G=D['geometry']
im=Image.new('RGB',(1280,1024),P['desktop']);dr=ImageDraw.Draw(im)
font=ImageFont.truetype('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',13)
mono=ImageFont.truetype('/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf',13)
icons=HERE/(D['name']+' icons')
def tile(x,y,name):
 dr.rectangle((x,y,x+63,y+63),fill=P['accent']);dr.line((x,y+63,x,y,x+63,y),fill=P['elevated']);dr.line((x+63,y,x+63,y+63,x,y+63),fill=P['control_border'])
 p=icons/'64x64/apps'/(name+'.png')
 if p.exists():
  a=Image.open(p).convert('RGBA');a.thumbnail((48,48));im.paste(a,(x+(64-a.width)//2,y+(64-a.height)//2),a)
 else:
  # The actual vector remains in assets; illustration uses its short identity, never a fabricated icon.
  dr.text((x+3,y+25),name[:7],font=font,fill=P['selection_foreground'])
def window(box,title,active=True):
 x,y,w,h=box;dr.rectangle((x,y,x+w,y+h),fill=P['background'],outline=P['control_border']);dr.rectangle((x+1,y+1,x+w-1,y+21),fill=G['titlebar_active'] if active else G['titlebar_inactive']);ink=P['selection_foreground'] if active else P['foreground'];dr.text((x+w//2,y+4),title,font=font,anchor='mt',fill=ink)
 dr.rectangle((x+4,y+5,x+15,y+16),outline=ink);dr.line((x+w-17,y+6,x+w-7,y+16),fill=ink);dr.line((x+w-17,y+16,x+w-7,y+6),fill=ink)
 dr.text((x+8,y+29),'File   Edit   View   Tools   Window   Help',font=font,fill=P['foreground'])
window((440,0,774,932),'Document — illustrative layout',False);dr.rectangle((457,72,1198,902),fill=P['selection_foreground'])
window((0,0,430,148),'Utility controls',False)
for x,label in [(10,'Open'),(90,'Save'),(170,'Apply')]:
 dr.rectangle((x,65,x+65,88),fill=P['surface']);dr.line((x,88,x,65,x+65,65),fill=P['selection_foreground']);dr.line((x+65,65,x+65,88,x,88),fill=P['control_border']);dr.text((x+10,70),label,font=font,fill=P['foreground'])
window((0,154,990,520),'Workstation content',True);dr.rectangle((2,200,987,671),fill=P['desktop']);dr.text((18,220),'Black application canvas\n\nScientific content is application-owned.',font=mono,fill=P['selection_foreground'])
window((0,680,435,269),'Terminal',False);dr.rectangle((2,727,432,946),fill=P['terminal']);dr.text((12,742),'GNU-Darwin Workstation\n\nAppearance preview only\nOriginal commands and media preserved.',font=mono,fill=P['terminal_foreground'])
sequence=['preferences-desktop','cs-themes','firefox','libreoffice-startcenter','lyx','libreoffice-writer','blender','gimp','xreader','xviewer','org.x.Warpinator','accessories-text-editor','system-file-manager','kubrick','org.gnome.Terminal']
for i,n in enumerate(sequence):tile(1216,i*64,n)
for x,n in [(0,'gnu-darwin-workstation-menu'),(64,'org.gnome.Terminal'),(128,'libreoffice-writer'),(832,'system-file-manager')]:tile(x,960,n)
dr.text((490,984),'STATIC COMPOSITION — NOT A LIVE SESSION',font=font,fill=P['selection_foreground'])
im.save(HERE/'preview-static.png')
print('Wrote illustrative preview-static.png')
