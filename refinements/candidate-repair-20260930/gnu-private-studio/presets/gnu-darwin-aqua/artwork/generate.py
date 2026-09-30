#!/usr/bin/python3
"""Original deterministic flowing GNU-Darwin artwork; no supplied artwork read or modified."""
from pathlib import Path
import json, hashlib
import numpy as np
from PIL import Image,ImageDraw,ImageFont
HERE=Path(__file__).resolve().parent
D=json.loads((HERE.parent/'design.json').read_text());P=D['palette'];R=D['rainbow']
def rgb(h):return tuple(int(h[i:i+2],16) for i in (1,3,5))
w,h=2880,1800
y,x=np.mgrid[0:h,0:w].astype(np.float32);x/=w;y/=h
base=np.array(rgb(P['desktop']),np.float32);light=np.array(rgb(R[1]),np.float32)
a=np.clip(.12+.36*(1-y)+.16*x,0,1)
arr=base+(light-base)*a[:,:,None]
for center,width,amount in [(.85,.032,.76),(.96,.045,.60),(1.10,.065,.42),(.69,.012,.26)]:
 curve=center-.37*x+.095*np.sin(x*4.8)
 band=np.exp(-((y-curve)/width)**2)*amount
 shadow=np.exp(-((y-curve-width*1.1)/(width*.45))**2)*.18
 arr=arr*(1-shadow[:,:,None])+base*shadow[:,:,None]
 arr=arr*(1-band[:,:,None])+light*band[:,:,None]
im=Image.fromarray(np.uint8(np.clip(arr,0,255)));d=ImageDraw.Draw(im)
font='/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'
d.text((165,175),'GNU-Darwin',font=ImageFont.truetype(font,74),fill=rgb(R[0]))
d.text((169,268),'A Q U A',font=ImageFont.truetype(font,23),fill=rgb(R[0]))
im.save(HERE/'wallpaper.png');im.resize((1440,900),Image.Resampling.LANCZOS).save(HERE/'preview.png')
for name,size in [('menu-logo',256),('plymouth-logo',720),('grub-logo',240)]:
 scale=3;s=size*scale;logo=Image.new('RGBA',(s,s));dr=ImageDraw.Draw(logo)
 dr.ellipse((s*.06,s*.06,s*.94,s*.94),fill=rgb(P['selection']),outline=rgb(R[0]),width=max(1,s//80))
 dr.arc((s*.1,s*.1,s*.9,s*.9),195,340,fill=rgb(P['accent']),width=max(2,s//30))
 f=ImageFont.truetype(font,int(s*.35));dr.text((s*.5,s*.49),'GD',font=f,fill=rgb(P['selection_foreground']),anchor='mm')
 logo.resize((size,size),Image.Resampling.LANCZOS).save(HERE/(name+'.png'))
files={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':list(Image.open(p).size)} for p in HERE.glob('*.png')}
(HERE/'manifest.json').write_text(json.dumps({'name':D['name'],'source':'Original mathematical flowing blue fields and original GD letter monogram; not an official logo','license':'CC0-1.0 original artwork','design_sha256':hashlib.sha256((HERE.parent/'design.json').read_bytes()).hexdigest(),'files':files},indent=2)+'\n')
print('wallpaper.png ready')
