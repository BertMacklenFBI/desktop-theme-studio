#!/usr/bin/env python3
"""Bounded transaction tests. Every destination, template dir and alternative is a temporary fake."""
import importlib.util, tempfile, pathlib, unittest, json, base64, copy, os
spec=importlib.util.spec_from_file_location('staged_system',pathlib.Path(__file__).with_name('system.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
PNG=m.PNG+b'fixture'
GRUB_TEMPLATE='# @theme_name@ (@slug@)\ndesktop-color: "@desktop@"\nitem_color = "@foreground@"\nselected_item_color = "@selection_foreground@"\n'
SCRIPT_TEMPLATE='// @theme_name@\nWindow.SetBackgroundTopColor(@desktop_rgb@);\ntitle = Image.Text("@theme_name@", @foreground_rgb@);\n// @OPTIONAL_LOGO@\nbar = @selection_rgb@;\n'
DESCRIPTOR_TEMPLATE='[Plymouth Theme]\nName=@theme_name@\nModuleName=script\n[script]\nImageDir=/usr/share/plymouth/themes/@slug@\nScriptFile=/usr/share/plymouth/themes/@slug@/@slug@.script\n'
class Fixture(unittest.TestCase):
    """Temporary destinations plus fake alternatives/rebuild; optional fake asset/preset trees."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.tmp.name)
        self.original={k:getattr(m,k) for k in ('STATE','GREETER','OVERRIDE','PLYMOUTHD','TREES','PRESET','HERE','GRUB_FONT_FALLBACKS','node','alternatives','run','rebuild')}
        m.STATE=self.root/'state'; m.STATE.mkdir()
        m.GREETER=self.root/'greeter.conf'; m.OVERRIDE=self.root/'grub.cfg'; m.PLYMOUTHD=self.root/'etc-plymouth'/'plymouthd.conf'
        m.TREES=tuple(str(self.root/name) for name in ('theme','icons','cursors','backgrounds','grub','plymouth'))
        realnode=m.node
        def node(p):
            n=realnode(p)
            if n['kind']!='missing': n.update(uid=0,gid=0)
            return n
        m.node=node
        self.alt={'status':'auto','value':'/old.plymouth','entries':{'/old.plymouth':100}}
        m.alternatives=lambda:copy.deepcopy(self.alt)
        def run(*argv):
            if argv[1]=='--install': self.alt['entries'][argv[4]]=int(argv[5])
            if argv[1]=='--set': self.alt.update(status='manual',value=argv[3])
            if argv[1]=='--remove': self.alt['entries'].pop(argv[3],None)
            if argv[1]=='--auto': self.alt.update(status='auto',value=max(self.alt['entries'],key=self.alt['entries'].get))
            return ''
        m.run=run; m.rebuild=lambda *args:None
    def tearDown(self):
        for k,v in self.original.items(): setattr(m,k,v)
        self.tmp.cleanup()
    def operation(self,p,content):
        return dict(path=str(p),planned_before=m.node(p),after=dict(kind='file',sha256=m.sha(content),mode=0o644,uid=0,gid=0),content=base64.b64encode(content).decode())
    def stage(self,background=True,font=True,templates=True,wallpaper=True,plymouthd=True):
        """Fake preset (design.json copy, artwork, desktop trees) and fake system/ asset dirs."""
        design=(m.PRESET/'design.json').read_bytes()
        m.PRESET=self.root/'preset'; m.HERE=m.PRESET/'system'
        (m.PRESET/'artwork').mkdir(parents=True); (m.PRESET/'design.json').write_bytes(design)
        if wallpaper: (m.PRESET/'artwork'/'wallpaper.png').write_bytes(PNG+b'wall')
        (m.PRESET/'artwork'/'plymouth-logo.png').write_bytes(PNG+b'logo')
        for part in (m.NAME,m.NAME+' icons',m.NAME+' cursors'):
            d=m.PRESET/'desktop'/part; d.mkdir(parents=True); (d/'index.theme').write_text('[Icon Theme]\nName='+part+'\nInherits=hicolor\n')
        g=m.HERE/'grub'; (g/'icons').mkdir(parents=True); (g/'__pycache__').mkdir()
        for name in ('logo.png','select_c.png','select_e.png','menu_c.png','bar_c.png','hl_c.png','icons/ubuntu.png'): (g/name).write_bytes(PNG+name.encode())
        if background: (g/'background.png').write_bytes(PNG+b'scene')
        if font: (g/'font.pf2').write_bytes(b'FILE\x00fixture')
        (g/'generate.py').write_text('# generator'); (g/'mockup.png').write_bytes(PNG); (g/'README.md').write_text('# grub'); (g/'__pycache__'/'x.pyc').write_bytes(b'x'); (g/'.gitkeep').write_bytes(b'')
        p=m.HERE/'plymouth'; (p/'mockups').mkdir(parents=True); (p/'__pycache__').mkdir()
        for name in ('progress-track.png','progress-fill.png','lock.png'): (p/name).write_bytes(PNG+name.encode())
        (p/'mockups'/'boot.png').write_bytes(PNG); (p/'generate.py').write_text('#'); (p/'check.py').write_text('#'); (p/'README.md').write_text('# plymouth'); (p/'__pycache__'/'y.pyc').write_bytes(b'y')
        if plymouthd: (p/'plymouthd.conf').write_text('[Daemon]\nTheme='+m.SLUG+'\nDeviceScale=1\n')
        if templates:
            (g/'theme.txt.in').write_text(GRUB_TEMPLATE); (p/(m.SLUG+'.script.in')).write_text(SCRIPT_TEMPLATE); (p/'theme.plymouth.in').write_text(DESCRIPTOR_TEMPLATE)
        m.GRUB_FONT_FALLBACKS=(self.root/'no-such-unicode.pf2',self.root/'fallback-unicode.pf2'); (self.root/'fallback-unicode.pf2').write_bytes(b'FILE\x00system')
        return {op['path']:op for op in m.build()[0]}
class Transactions(Fixture):
    def test_apply_restore_original_and_absent(self):
        m.GREETER.write_bytes(b'original'); new=self.root/'theme'/'new.css'
        result=m.apply([self.operation(m.GREETER,b'themed'),self.operation(new,b'new')]); folder=pathlib.Path(result['receipt']).parent
        self.assertEqual(m.GREETER.read_bytes(),b'themed'); self.assertEqual(self.alt['value'],m.PLY)
        m.restore(folder)
        self.assertEqual(m.GREETER.read_bytes(),b'original'); self.assertFalse(new.exists()); self.assertEqual(self.alt['status'],'auto'); self.assertEqual(self.alt['value'],'/old.plymouth')
    def test_manual_prior_alternative_restored_exactly(self):
        self.alt={'status':'manual','value':'/lavender.plymouth','entries':{'/old.plymouth':100,'/lavender.plymouth':50}}
        result=m.apply([self.operation(m.GREETER,b'themed')]); folder=pathlib.Path(result['receipt']).parent
        self.assertEqual(self.alt['value'],m.PLY); self.assertEqual(self.alt['entries'][m.PLY],50)
        m.restore(folder)
        self.assertEqual(self.alt,{'status':'manual','value':'/lavender.plymouth','entries':{'/old.plymouth':100,'/lavender.plymouth':50}})
    def test_external_conflict_refuses_all_restore(self):
        m.GREETER.write_bytes(b'original'); new=self.root/'theme'/'new.css'
        result=m.apply([self.operation(m.GREETER,b'themed'),self.operation(new,b'new')]); folder=pathlib.Path(result['receipt']).parent
        m.GREETER.write_bytes(b'external')
        with self.assertRaisesRegex(RuntimeError,'conflict'): m.restore(folder)
        self.assertEqual(new.read_bytes(),b'new')
    def test_corrupt_backup_refuses_all_restore(self):
        m.GREETER.write_bytes(b'original'); new=self.root/'theme'/'new.css'
        result=m.apply([self.operation(m.GREETER,b'themed'),self.operation(new,b'new')]); folder=pathlib.Path(result['receipt']).parent
        (folder/'0.backup').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError,'backup'): m.restore(folder)
        self.assertEqual(m.GREETER.read_bytes(),b'themed'); self.assertTrue(new.exists())
    def test_rebuild_failure_rolls_back(self):
        m.GREETER.write_bytes(b'original'); calls=[]
        def rebuild(*args):
            calls.append(1)
            if len(calls)==1: raise RuntimeError('injected build error')
        m.rebuild=rebuild
        with self.assertRaisesRegex(RuntimeError,'configuration restored'): m.apply([self.operation(m.GREETER,b'themed')])
        self.assertEqual(m.GREETER.read_bytes(),b'original'); self.assertEqual(self.alt['status'],'auto')
    def test_late_plan_conflict_prevents_writes(self):
        m.GREETER.write_bytes(b'original'); op=self.operation(m.GREETER,b'themed'); m.GREETER.write_bytes(b'external')
        with self.assertRaisesRegex(RuntimeError,'planning'): m.apply([op])
        self.assertEqual(m.GREETER.read_bytes(),b'external')
    def test_greeter_preserves_nonappearance(self):
        text='[Greeter]\nshow-a11y=true\nenable-hidpi=on\ntheme-name=old\nonscreen-keyboard-layout=/usr/share/onboard/layouts/Whiteboard_wide.onboard\n[Other]\nautologin-user=keep\n'
        patched=m.greeter_patch(text).decode()
        for value in ('show-a11y=true','enable-hidpi=on','autologin-user=keep','theme-name=GNU-Darwin Aqua','onscreen-keyboard-layout=/usr/share/onboard/layouts/Whiteboard_wide.onboard'): self.assertIn(value,patched)
        self.assertNotIn('theme-name=old',patched)
    def test_greeter_cursor_theme_size_key(self):
        patched=m.greeter_patch('[Greeter]\ncursor-theme-size=24\ntheme-name=old\n').decode()
        self.assertIn('cursor-theme-size=24\n',patched); self.assertNotIn('cursor-theme-size=28',patched)
        self.assertIn('cursor-theme-name=GNU-Darwin Aqua cursors\n',patched)
        self.assertIn('icon-theme-name=GNU-Darwin Aqua icons\n',patched)
        self.assertIn('background='+m.TREES[3]+'/wallpaper.png\n',patched)
        self.assertIn('background-color=#265098\n',patched); self.assertIn('draw-user-backgrounds=false\n',patched); self.assertIn('font-name=Liberation Sans 11\n',patched)
    def test_target_escape_rejected(self):
        with self.assertRaises(ValueError): m.target(str(self.root/'theme'/'..'/'bad'))
        with self.assertRaisesRegex(ValueError,'outside'): m.target(str(self.root/'etc-plymouth'/'other.conf'))
        self.assertEqual(m.target(str(m.PLYMOUTHD)),m.PLYMOUTHD)
    def test_foreign_receipt_is_refused(self):
        result=m.apply([self.operation(m.GREETER,b'themed')]);folder=pathlib.Path(result['receipt']).parent
        receipt=folder/'transaction.json';data=json.loads(receipt.read_text());data['preset_id']='another-theme';receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'another preset'):m.restore(folder)
        self.assertEqual(m.GREETER.read_bytes(),b'themed')
    def test_new_apply_refuses_unfinished_recovery(self):
        folder=m.STATE/'unfinished';folder.mkdir();(folder/'transaction.json').write_text('{"status":"recovery-required"}')
        with self.assertRaisesRegex(RuntimeError,'incomplete system recovery'):m.apply([self.operation(m.GREETER,b'themed')])
        self.assertFalse(m.GREETER.exists())
    def test_symlinked_source_tree_is_refused(self):
        design=(m.PRESET/'design.json').read_bytes()
        m.PRESET=self.root/'preset';(m.PRESET/'desktop').mkdir(parents=True)
        (m.PRESET/'design.json').write_bytes(design)
        external=self.root/'external';external.mkdir()
        (m.PRESET/'desktop'/m.NAME).symlink_to(external,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'regular directory'):m.build()
    def test_plymouthd_conf_created_then_deleted_on_restore(self):
        self.assertFalse(m.PLYMOUTHD.parent.exists())
        result=m.apply([self.operation(m.PLYMOUTHD,b'[Daemon]\nTheme=gnu-darwin-aqua\nDeviceScale=1\n')]); folder=pathlib.Path(result['receipt']).parent
        journal=json.loads((folder/'transaction.json').read_text())
        self.assertIn(str(m.PLYMOUTHD.parent),journal['created_directories']); self.assertEqual(journal['operations'][0]['before'],{'kind':'missing'})
        self.assertTrue(m.PLYMOUTHD.is_file())
        m.restore(folder)
        self.assertFalse(m.PLYMOUTHD.exists()); self.assertFalse(m.PLYMOUTHD.parent.exists())
    def test_plymouthd_conf_backed_up_and_restored_when_present(self):
        m.PLYMOUTHD.parent.mkdir(); m.PLYMOUTHD.write_bytes(b'[Daemon]\nTheme=previous\n')
        result=m.apply([self.operation(m.PLYMOUTHD,b'[Daemon]\nTheme=gnu-darwin-aqua\nDeviceScale=1\n')]); folder=pathlib.Path(result['receipt']).parent
        journal=json.loads((folder/'transaction.json').read_text())
        self.assertNotIn(str(m.PLYMOUTHD.parent),journal['created_directories']); self.assertEqual((folder/'0.backup').read_bytes(),b'[Daemon]\nTheme=previous\n')
        self.assertIn(b'DeviceScale=1',m.PLYMOUTHD.read_bytes())
        m.restore(folder)
        self.assertEqual(m.PLYMOUTHD.read_bytes(),b'[Daemon]\nTheme=previous\n'); self.assertTrue(m.PLYMOUTHD.parent.is_dir())
class AssetContract(Fixture):
    def test_static_asset_inventory(self):
        self.stage(); inventory=m.static_assets()
        expected={'grub/logo.png','grub/select_c.png','grub/select_e.png','grub/menu_c.png','grub/bar_c.png','grub/hl_c.png','grub/icons/ubuntu.png','grub/background.png','grub/font.pf2',
                  'plymouth/progress-track.png','plymouth/progress-fill.png','plymouth/lock.png'}
        self.assertEqual(set(inventory),expected)
        for excluded in ('grub/theme.txt.in','grub/generate.py','grub/mockup.png','grub/README.md','grub/__pycache__/x.pyc','grub/.gitkeep',
                         'plymouth/mockups/boot.png','plymouth/plymouthd.conf','plymouth/check.py','plymouth/theme.plymouth.in','plymouth/'+m.SLUG+'.script.in','plymouth/README.md'):
            self.assertNotIn(excluded,inventory)
    def test_build_installs_statics_verbatim_and_renders_templates(self):
        ops=self.stage(); grub=m.TREES[4]; ply=m.TREES[5]
        self.assertEqual(base64.b64decode(ops[grub+'/icons/ubuntu.png']['content']),PNG+b'icons/ubuntu.png')
        self.assertEqual(base64.b64decode(ops[grub+'/font.pf2']['content']),b'FILE\x00fixture')
        self.assertEqual(base64.b64decode(ops[ply+'/lock.png']['content']),PNG+b'lock.png')
        theme=base64.b64decode(ops[grub+'/theme.txt']['content']).decode()
        self.assertIn('# GNU-Darwin Aqua (gnu-darwin-aqua)',theme); self.assertIn('desktop-color: "#265098"',theme); self.assertIn('selected_item_color = "#FFFFFF"',theme); self.assertNotIn('@',theme)
        descriptor=base64.b64decode(ops[ply+'/'+m.SLUG+'.plymouth']['content']).decode()
        self.assertIn('Name=GNU-Darwin Aqua',descriptor); self.assertIn('ScriptFile=/usr/share/plymouth/themes/gnu-darwin-aqua/gnu-darwin-aqua.script',descriptor)
        self.assertNotIn(ply+'/mockups/boot.png',ops); self.assertNotIn(ply+'/plymouthd.conf',ops)
        self.assertEqual(base64.b64decode(ops[str(m.PLYMOUTHD)]['content']),b'[Daemon]\nTheme='+m.SLUG.encode()+b'\nDeviceScale=1\n')
        self.assertEqual(base64.b64decode(ops[str(m.OVERRIDE)]['content']).decode().splitlines()[-1],'GRUB_THEME="'+grub+'/theme.txt"')
        self.assertEqual(base64.b64decode(ops[m.TREES[3]+'/wallpaper.png']['content']),PNG+b'wall'); self.assertEqual(base64.b64decode(ops[ply+'/wallpaper.png']['content']),PNG+b'wall')
        self.assertEqual(m.build.info['static_assets'],{'grub':9,'plymouth':3})
    def test_rgb_floats_four_decimals(self):
        self.assertEqual(m.rgb_floats('#1A1D21'),'0.1020, 0.1137, 0.1294')
        self.assertEqual(m.rgb_floats('#FFFFFF'),'1.0000, 1.0000, 1.0000')
        ops=self.stage(); script=base64.b64decode(ops[m.TREES[5]+'/'+m.SLUG+'.script']['content']).decode()
        self.assertIn('Window.SetBackgroundTopColor(0.1490, 0.3137, 0.5961);',script)
        self.assertIn('Image.Text("GNU-Darwin Aqua", 0.1255, 0.1373, 0.1569)',script)
        self.assertIn('bar = 0.1412, 0.3725, 0.6588;',script); self.assertNotIn('@',script)
    def test_templates_follow_shared_palette(self):
        self.stage(); design=m.design_spec(); design['tokens']['foreground']='#654321'; design['tokens']['desktop']='#123456'
        assets=m.render_assets(design)
        self.assertIn(b'#123456',assets['grub/theme.txt']); self.assertIn(b'#654321',assets['grub/theme.txt'])
        self.assertIn(b'0.0706, 0.2039, 0.3373',assets['plymouth/'+m.SLUG+'.script'])
        self.assertIn(b'background-color=#123456',m.greeter_patch('[Greeter]\n',design))
    def test_leftover_token_is_an_error(self):
        self.stage(); (m.HERE/'grub'/'theme.txt.in').write_text(GRUB_TEMPLATE+'color = "@no_such_key@"\n')
        with self.assertRaisesRegex(ValueError,'Unresolved template token in grub/theme.txt.in: @no_such_key@'): m.render_assets(m.design_spec())
        (m.HERE/'grub'/'theme.txt.in').write_text(GRUB_TEMPLATE+'text = "user@host"\n')
        with self.assertRaisesRegex(ValueError,'Unresolved'): m.build()
    def test_missing_templates_reported_not_raised(self):
        ops=self.stage(templates=False); missing=m.build()[1]
        self.assertEqual(len([x for x in missing if x.endswith('.in')]),3); self.assertNotIn(m.TREES[4]+'/theme.txt',ops)
        with self.assertRaisesRegex(FileNotFoundError,'Missing template inputs'): m.render_assets(m.design_spec())
    def test_grub_background_vs_wallpaper_fallback(self):
        ops=self.stage(background=True)
        self.assertIn(m.TREES[4]+'/background.png',ops); self.assertNotIn(m.TREES[4]+'/wallpaper.png',ops); self.assertIn('bespoke',m.build.info['grub_background'])
        self.tearDown(); self.setUp(); ops=self.stage(background=False)
        self.assertNotIn(m.TREES[4]+'/background.png',ops); self.assertEqual(base64.b64decode(ops[m.TREES[4]+'/wallpaper.png']['content']),PNG+b'wall'); self.assertIn('fallback',m.build.info['grub_background'])
    def test_grub_font_fallback(self):
        ops=self.stage(font=False)
        self.assertEqual(base64.b64decode(ops[m.TREES[4]+'/font.pf2']['content']),b'FILE\x00system'); self.assertIn('fallback',m.build.info['grub_font'])
    def test_logo_is_opt_in(self):
        ops=self.stage(); ply=m.TREES[5]
        self.assertNotIn(ply+'/logo.png',ops); self.assertNotIn(b'Image("logo.png")',base64.b64decode(ops[ply+'/'+m.SLUG+'.script']['content']))
        ops={op['path']:op for op in m.build(logo='default')[0]}
        self.assertEqual(base64.b64decode(ops[ply+'/logo.png']['content']),PNG+b'logo')
        script=base64.b64decode(ops[ply+'/'+m.SLUG+'.script']['content']).decode(); self.assertIn('Image("logo.png")',script); self.assertNotIn('@',script)
        supplied=self.root/'supplied.png'; supplied.write_bytes(PNG+b'supplied')
        ops={op['path']:op for op in m.build(logo=str(supplied))[0]}; self.assertEqual(base64.b64decode(ops[ply+'/logo.png']['content']),PNG+b'supplied')
        (self.root/'bad.png').write_bytes(b'not png')
        with self.assertRaisesRegex(ValueError,'PNG'): m.build(logo=str(self.root/'bad.png'))
    def test_missing_inputs_listed(self):
        self.stage(wallpaper=False,plymouthd=False); missing=m.build()[1]
        self.assertTrue(any(x.endswith('artwork/wallpaper.png') for x in missing)); self.assertTrue(any(x.endswith('plymouth/plymouthd.conf') for x in missing))
    def test_icon_fallbacks_checked(self):
        self.stage(); (m.PRESET/'desktop'/(m.NAME+' icons')/'index.theme').write_text('[Icon Theme]\nInherits=Papirus-Dark,Papirus,hicolor,No-Such-Fallback\n')
        missing=m.prerequisites([])
        self.assertIn('system icon fallback: No-Such-Fallback',missing); self.assertNotIn('system icon fallback: hicolor',missing)
class Identity(unittest.TestCase):
    def test_destinations_use_new_identity(self):
        self.assertEqual(m.NAME,'GNU-Darwin Aqua'); self.assertEqual(m.SLUG,'gnu-darwin-aqua')
        self.assertIn(m.NAME,m.TREES[0]); self.assertIn(m.SLUG,m.PLY)
        self.assertEqual(m.OVERRIDE.name,'zz-desktop-theme-studio.cfg'); self.assertEqual(str(m.PLYMOUTHD),'/etc/plymouth/plymouthd.conf')
        self.assertEqual(str(m.STATE),'/var/lib/desktop-theme-studio/gnu-darwin-aqua')
        for value in (str(m.STATE),m.PLY,*m.TREES): self.assertNotIn('macintosh',value.lower())
    def test_canonical_schema_is_read_without_modifying_it(self):
        source=m.PRESET/'design.json'; before=source.read_bytes(); design=m.design_spec()
        self.assertEqual(design['id'],'gnu-darwin-aqua'); self.assertEqual(design['cursor_size'],24)
        self.assertEqual(design['tokens']['selection_foreground'],design['palette']['selection_foreground'])
        self.assertEqual(design['typography']['interface'],'Liberation Sans 11')
        self.assertEqual(source.read_bytes(),before)
    def test_template_values_cover_every_palette_key(self):
        values=m.template_values(m.design_spec())
        for key in m.IDENTITY['palette']: self.assertIn(key,values); self.assertIn(key+'_rgb',values)
        self.assertEqual(values['theme_name'],'GNU-Darwin Aqua'); self.assertEqual(values['slug'],'gnu-darwin-aqua')
if __name__=='__main__': unittest.main()
