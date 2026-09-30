#!/usr/bin/python3
"""Brief live visual verification, restoring the user's desktop visibility afterward."""
from pathlib import Path
import ast, json, os, subprocess, time
import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk
ROOT=LINEAGE_PRESET
OUT=ROOT/'verification'
def run(args):return subprocess.run(args,capture_output=True,text=True,timeout=10,check=True).stdout.strip()
def ev(code):
 r=run(['gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.Eval',code])
 if not r.startswith('(true,'):raise RuntimeError(r)
 return r
menu="imports.ui.appletManager.filterDefinitionsByUUID('menu@cinnamon.org')[0].applet.menu"
before=run(['xprop','-root','_NET_SHOWING_DESKTOP']).rsplit(' ',1)[-1]=='1'
menu_before='true' in ev(menu+'.isOpen').split(',',1)[-1]
pointer=Gdk.Display.get_default().get_default_seat().get_pointer()
screen,px,py=pointer.get_position()
try:
 pointer.warp(screen,0,200);Gdk.Display.get_default().flush()
 run(['wmctrl','-k','on']);ev(menu+'.close(); true');time.sleep(1)
 run(['scrot','--overwrite',str(OUT/'live-desktop.png')])
 ev(menu+'.open(); true');time.sleep(.6)
 run(['scrot','--overwrite',str(OUT/'live-menu.png')])
 ev(menu+'.close(); true')
finally:
 ev(menu+('.open(); true' if menu_before else '.close(); true'))
 run(['wmctrl','-k','on' if before else 'off'])
 pointer.warp(screen,px,py);Gdk.Display.get_default().flush()
print(json.dumps({'screenshots':[str(OUT/'live-desktop.png'),str(OUT/'live-menu.png')],'desktop_visibility_restored':True}))
