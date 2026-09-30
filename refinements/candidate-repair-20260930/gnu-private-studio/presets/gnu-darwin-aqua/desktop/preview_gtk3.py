#!/usr/bin/python3
"""Read-only offscreen GTK3 render of the built GNU-Darwin Aqua theme -> desktop/preview-gtk3.png.
No toplevel is mapped and no settings are written: GTK_THEME/XDG_DATA_HOME are process-local and the theme is exposed
through a temporary themes/ symlink. Usage: python3 preview_gtk3.py [output.png]"""
import os,sys,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;D=json.loads((HERE.parent/'design.json').read_text());NAME=D['name']
if os.environ.get('TG_PREVIEW_CHILD')!='1':
    out=sys.argv[1] if len(sys.argv)>1 else str(HERE/'preview-gtk3.png')
    with tempfile.TemporaryDirectory(prefix='tg-preview-') as tmp:
        (Path(tmp)/'themes').mkdir();(Path(tmp)/'themes'/NAME).symlink_to(HERE/NAME)
        env=dict(os.environ,XDG_DATA_HOME=tmp,GTK_THEME=NAME,TG_PREVIEW_CHILD='1')
        sys.exit(subprocess.run([sys.executable,'-u',__file__,out],env=env).returncode)
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GLib
out=sys.argv[1]
if not Gtk.init_check()[0]:
    sys.exit('GTK preview unavailable: no display; coordinator must use serialized private display.')
st=Gtk.Settings.get_default();st.set_property('gtk-decoration-layout',D['geometry']['button_layout']);st.set_property('gtk-font-name',D['typography']['ui_family']+' '+str(D['typography']['ui_size_pt']))
def build(title):
    w=Gtk.OffscreenWindow();w.set_default_size(620,440)
    outer=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
    # Gtk.OffscreenWindow ignores set_titlebar, so a .titlebar box with real titlebutton classes stands in for the CSD header.
    hb=Gtk.Box(spacing=6);hb.get_style_context().add_class('titlebar');hb.set_border_width(4);outer.add(hb)
    hb.pack_start(Gtk.Button(label='Library'),False,False,0)
    tl=Gtk.Label(label=title);tl.get_style_context().add_class('title');hb.set_center_widget(tl)
    for cls,icon in [('close','window-close-symbolic'),('minimize','window-minimize-symbolic'),('maximize','window-maximize-symbolic')]:
        b=Gtk.Button();b.add(Gtk.Image.new_from_icon_name(icon,Gtk.IconSize.MENU));b.get_style_context().add_class('titlebutton');b.get_style_context().add_class(cls);hb.pack_start(b,False,False,0)
    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12);box.set_border_width(20);outer.add(box)
    mb=Gtk.MenuBar()
    for n in ['File','Edit','View']:mi=Gtk.MenuItem(label=n);mi.set_submenu(Gtk.Menu());mb.append(mi)
    box.add(mb)
    l=Gtk.Label();l.set_markup(title+' | <a href="x">accent link</a> and <span foreground="#535A64">muted</span> text');l.set_xalign(0);box.add(l)
    box.add(Gtk.Entry(placeholder_text='Search your desktop'))
    e2=Gtk.Entry(text='selected text sample');box.add(e2)
    row=Gtk.Box(spacing=8)
    row.add(Gtk.Button(label='Normal'));b=Gtk.Button(label='Suggested');b.get_style_context().add_class('suggested-action');row.add(b)
    b=Gtk.Button(label='Destructive');b.get_style_context().add_class('destructive-action');row.add(b)
    d=Gtk.Button(label='Disabled');d.set_sensitive(False);row.add(d)
    t=Gtk.ToggleButton(label='Checked');t.set_active(True);row.add(t);box.add(row)
    row=Gtk.Box(spacing=16)
    c=Gtk.CheckButton(label='Checked');c.set_active(True);row.add(c);row.add(Gtk.CheckButton(label='Unchecked'))
    r=Gtk.RadioButton.new_with_label_from_widget(None,'Radio');r.set_active(True);row.add(r)
    sw=Gtk.Switch();sw.set_active(True);row.add(sw);row.add(Gtk.Switch());box.add(row)
    sc=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,100,1);sc.set_value(65);box.add(sc)
    pb=Gtk.ProgressBar();pb.set_fraction(0.6);box.add(pb)
    nb=Gtk.Notebook()
    tv=Gtk.TreeView(model=Gtk.ListStore(str));tv.append_column(Gtk.TreeViewColumn('Rows',Gtk.CellRendererText(),text=0))
    for s in ['Alpha row','Selected row','Gamma row']:tv.get_model().append([s])
    tv.get_selection().select_path(Gtk.TreePath.new_from_indices([1]))
    nb.append_page(tv,Gtk.Label(label='List'));nb.append_page(Gtk.Label(label='Second'),Gtk.Label(label='Other'));box.add(nb)
    ib=Gtk.InfoBar();ib.set_message_type(Gtk.MessageType.WARNING);ib.get_content_area().add(Gtk.Label(label='Warning infobar'));box.add(ib)
    w.add(outer);w.show_all();e2.select_region(0,8)
    return w
w=build(NAME)
def snap():
    pb=w.get_pixbuf();pb.savev(out,'png',[],[]);print('saved',out,pb.get_width(),pb.get_height());Gtk.main_quit();return False
GLib.timeout_add(700,snap);GLib.timeout_add_seconds(15,Gtk.main_quit);Gtk.main()
