#!/usr/bin/python3
"""Read-only Muffin6.6.3 frame StyleContext color regression. Needs coordinator display; maps no widgets."""
from pathlib import Path
import json,sys
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
if not Gtk.init_check()[0]:sys.exit('Display required by GTK StyleContext; run in coordinator serialized display. No widgets are created.')
HERE=Path(__file__).resolve().parent;D=json.loads((HERE.parent/'design.json').read_text())
provider=Gtk.CssProvider();provider.load_from_path(str(HERE/D['name']/'gtk-3.0/gtk.css'))
contexts=[]
# Mirrors Muffin6.6.3 create_style_context: copy parent path before adding new node.
for typ,name,classes in [(Gtk.Window,'window',['background','ssd']),(Gtk.Window,'decoration',[]),(Gtk.HeaderBar,'headerbar',['titlebar','horizontal','default-decoration']),(Gtk.Label,'label',['title'])]:
 parent=contexts[-1] if contexts else None
 path=parent.get_path().copy() if parent else Gtk.WidgetPath.new()
 i=path.append_type(typ);path.iter_set_object_name(i,name)
 for cls in classes:path.iter_add_class(i,cls)
 context=Gtk.StyleContext();context.set_parent(parent);context.set_path(path);context.add_provider(provider,Gtk.STYLE_PROVIDER_PRIORITY_SETTINGS);contexts.append(context)
result={}
for name,state,expected in [('focused',Gtk.StateFlags.NORMAL,D['palette']['selection_foreground']),('inactive',Gtk.StateFlags.BACKDROP,D['palette']['foreground'])]:
 for c in contexts:c.set_state(state)
 rgba=contexts[-1].get_color(state);value='#%02X%02X%02X'%tuple(round(v*255) for v in [rgba.red,rgba.green,rgba.blue])
 result[name]={'actual':value,'expected':expected,'pass':value==expected}
print(json.dumps(result,indent=2))
if not all(v['pass'] for v in result.values()):sys.exit(1)
