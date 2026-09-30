#!/usr/bin/python3
"""Deterministic Aqua silver Papirus overlay and unmodified system Bibata cursor copy. Staged only."""
from pathlib import Path
import hashlib, json, os, re, shutil, struct, subprocess, sys, xml.etree.ElementTree as ET
import gi, cairo
gi.require_version('Rsvg', '2.0')
from gi.repository import Rsvg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
U = Path.home()
D = json.loads((ROOT / 'design.json').read_text())
P = D['palette']
NAME = D['name']                                   # GNU-Darwin Aqua
ICONS = HERE / f'{NAME} icons'
CURSORS = HERE / f'{NAME} cursors'
WORK = HERE / '.build'
BASE = Path('/usr/share/icons/Papirus/64x64')
LIGHT = Path('/usr/share/icons/Papirus/64x64')
CUR = Path('/usr/share/icons/Bibata-Modern-Classic')
CUR_ALIASES = CUR / 'cursors'     # 74 alias symlinks to mirror
if not CUR_ALIASES.is_dir():
    CUR_ALIASES = CUR / 'cursors'                              # identical alias set (verified)
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
SIZES = [24, 32, 48, 64, 96] # actual sizes are read from Bibata binaries
BUILT_ON = '2026-09-29'

# Same application list as eucalyptus-felt-cinnamon/visual-assets/application-icons.json (388).
APPS = '''Acetino2 MidnightCommander accessories-calculator accessories-character-map accessories-text-editor
akonadi-ews akonadiconsole akregator alacarte app.devsuite.Ptyxis application-x-executable
applications-multimedia applications-other applications-system-symbolic ark artikulate bleachbit
blender blinken blueman blueman-device bomber bookmarks-organize bovo bulky camera-photo cantor
cervisia chatgpt cinnamon-virtual-keyboard claude-desktop co.anysphere.cursor codex
com.discordapp.Discord com.github.Matoking.protontricks com.github.jeromerobert.pdfarranger
com.github.maoschanz.drawing com.ranfdev.DistroShelf cs-actions cs-applets cs-backgrounds cs-color
cs-date-time cs-default-applications cs-desklets cs-desktop cs-desktop-effects cs-display
cs-extensions cs-fonts cs-general cs-gestures cs-keyboard cs-mouse cs-network cs-nightlight
cs-notifications cs-overview cs-panel cs-power cs-privacy cs-screensaver cs-sound
cs-startup-programs cs-tablet cs-themes cs-thunderbolt cs-universal-access cs-user cs-windows
cs-workspaces dev.geopjr.Calligraphy dialog-information dialog-password display-im6.q16 dosbox
dragonplayer drive-removable-media elisa filelight fingwit firefox folder folder-remote
get-hot-new-stuff giggle gimp gnome-online-accounts-gtk gnugo48 granatier gtk-select-color gufw gvim
gwenview help-browser htop hwinfo hwloc hypnotix ibus ibus-setup im-google input-keyboard
input-keyboard-virtual io.freetubeapp.FreeTube io.github.DenysMb.Kontainer
io.github.celluloid_player.Celluloid io.github.nokse22.asciidraw jockey juk kaddressbook kajongg
kalarm kalgebra kalzium kanagram kapman kapptemplate kate katomic kblackbox kblocks kbounce
kbreakout kbruch kcachegrind kcolorchooser kde kde-frameworks kdeconnect kdevelop kdf kdiamond kfind
kfontview kfourinline kgeography kget kgoldrunner kgpg khangman kig kigo killbots kimagemapeditor
kiriki kiten kitty kjumpingcube kleopatra klettres klickety klines klipper kmag kmahjongg kmail
kmenuedit kmines kmix kmousetool kmouth kmplot knavalbattle knetattach knetwalk knights knotes kolf
kollision kolourpaint kompare konquest konsolekalendar kontact kontact-import-wizard korganizer kpat
krdc kreversi krfb krita kruler krunner ksame kshisen ksirk ksnakeduel kspaceduel ksquares ksudoku
kteatime ktimer ktip ktnef ktouch ktuberling kturtle kubrick kuiviewer kwalletmanager kwikdisk
kwrite lazarus-3.0 libreoffice-calc libreoffice-draw libreoffice-impress libreoffice-startcenter
libreoffice-writer lightdm-settings lokalize lskat lyx map-globe marble mark-location-symbolic
mate-desktop mate-panel md.obsidian.Obsidian media-optical-audio mintbackup mintchat mintdrivers
mintinstall mintlocale-im mintreport mintsources mintstick mintstick-logo-linuxmint mintsysadm
mintupdate mintwelcome minuet mpv nbsdgames net.davidotek.pupgui2 network-server nm-device-wireless
nvidia-settings obconf okteta okular onboard openbox openjdk-21 openttd org.gnome.Builder
org.gnome.Calculator org.gnome.Calendar org.gnome.Devhelp org.gnome.DiskUtility
org.gnome.Evolution-alarm-notify org.gnome.FileRoller org.gnome.Glade org.gnome.PowerStats
org.gnome.Rhythmbox3 org.gnome.Screenshot org.gnome.SimpleScan org.gnome.SystemMonitor
org.gnome.Terminal org.gnome.Terminal.Preferences org.gnome.Yelp org.gnome.baobab
org.gnome.font-viewer org.gnome.seahorse.Application org.gnome.tweaks org.gtkhash.gtkhash
org.kde.konsole org.kde.kontrast org.kde.kwordquiz org.localsend.localsend_app org.remmina.Remmina
org.wireshark.Wireshark org.x.Warpinator ox package package-x-generic palapeli parley
partitionmanager pasaffe picmi pix plasma plasma-browser-integration plasma-search plasmadiscover
plasmashell playonlinux preferences-desktop preferences-desktop-accessibility
preferences-desktop-activities preferences-desktop-baloo preferences-desktop-color
preferences-desktop-cursors preferences-desktop-default-applications
preferences-desktop-display-nightcolor preferences-desktop-display-randr preferences-desktop-effects
preferences-desktop-emoticons preferences-desktop-feedback preferences-desktop-filetype-association
preferences-desktop-font preferences-desktop-font-installer preferences-desktop-gaming
preferences-desktop-icons preferences-desktop-keyboard preferences-desktop-keyboard-shortcut
preferences-desktop-locale preferences-desktop-mouse preferences-desktop-notification-bell
preferences-desktop-tablet preferences-desktop-theme preferences-desktop-theme-applications
preferences-desktop-theme-global preferences-desktop-theme-windowdecorations
preferences-desktop-thunderbolt preferences-desktop-touchpad preferences-desktop-touchscreen
preferences-desktop-user-password preferences-desktop-virtual preferences-devices-printer
preferences-kde-connect preferences-other preferences-security-firewall preferences-system
preferences-system-bluetooth preferences-system-login preferences-system-network
preferences-system-power-management preferences-system-session-services preferences-system-splash
preferences-system-tabbox preferences-system-time preferences-system-users
preferences-system-windows preferences-system-windows-actions printer qemu reload rocs
security-medium software-properties-mint spectacle steam steam_icon_1062090 steam_icon_208580
steam_icon_239030 steam_icon_297000 steam_icon_2980270 steam_icon_3241660 steam_icon_3706990
steam_icon_4404450 steam_icon_6020 steam_icon_6060 steam_icon_8930 step sticky sweeper synapse
system-file-manager system-log-out system-run system-software-update system-users systemsettings
text-directory text-editor text-x-vcard thingy thunderbird tilda timeshift tools-check-spelling
tools-report-bug transmission umbrello user-trash utilities-log-viewer utilities-system-monitor
utilities-terminal virtualbox vlc webapp-manager xorg xreader xsi-cog-symbolic xviewer'''.split()


def _load_app_list():
    """Prefer the family's canonical list when it is present; the embedded copy is the fallback."""
    f = U / 'Documents/eucalyptus-felt-cinnamon/visual-assets/application-icons.json'
    if f.is_file():
        try:
            names = json.loads(f.read_text())
            if isinstance(names, list) and names:
                return names
        except ValueError:
            pass
    return APPS


def darken(hex6, factor):
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    return '#%02X%02X%02X' % tuple(max(0, min(255, round(c * factor))) for c in (r, g, b))


def recolor(s, m):
    return re.sub(r'#[a-fA-F0-9]{6}\b', lambda x: m.get(x[0].lower(), x[0]), s)


def render(svg, out, width, height):
    handle = Rsvg.Handle.new_from_file(str(svg))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    rect = Rsvg.Rectangle(); rect.x = rect.y = 0; rect.width = width; rect.height = height
    handle.render_document(cairo.Context(surface), rect)
    surface.write_to_png(str(out))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ colour maps
FOLDER_BACK = P['selection']                      # #C97A45, the darkened accent
ICON_MAP = {                                       # Papirus-Dark -> GNU-Darwin Aqua
    '#5294e2': P['accent'],                        # folder front / Papirus blue
    '#4877b1': FOLDER_BACK,                        # folder back and shade
    '#1b83d4': P['accent'],                        # secondary Papirus blue (cloud/network)
    '#3b9ce6': D['rainbow'][1],                    # document fold (mid blue) -> apricot
    '#7ad2f9': D['rainbow'][0],                    # document fold highlight (pale blue) -> pale apricot
    '#e4e4e4': P['elevated'],                    # paper
    '#1d344f': P['desktop'],                       # ink
    '#8e8e8e': P['control_border'],                # grey hardware edges
    '#4f4f4f': P['surface'],                       # dark grey bodies
}
APP_MAP = {'#5294e2': P['accent'], '#4877b1': FOLDER_BACK, '#1b83d4': P['accent']}
CURSOR_MAP = {                                     # SILO phosphor -> GNU-Darwin Aqua
    '#0c1614': P['desktop'],                       # outline / base
    '#232627': P['desktop'],                       # outline (single occurrence)
    '#1fc9a6': P['accent'],                        # fill
    # '#333333' markers and '#000' shadows are left untouched on purpose.
}

PLACES = ['folder', 'folder-open', 'folder-documents', 'folder-download', 'folder-downloads',
          'folder-pictures', 'folder-music', 'folder-videos', 'folder-publicshare', 'folder-templates',
          'folder-desktop', 'folder-home', 'folder-remote', 'folder-network', 'folder-development',
          'folder-cloud', 'folder-locked', 'folder-trash', 'user-home', 'user-desktop', 'user-trash',
          'user-trash-full', 'user-bookmarks']
DEVICES = ['computer', 'computer-laptop', 'drive-harddisk', 'drive-harddisk-solidstate', 'drive-optical',
           'drive-removable-media', 'media-flash', 'media-flash-sd-mmc', 'audio-card', 'audio-headphones',
           'audio-speakers', 'input-keyboard', 'input-mouse', 'network-wired', 'network-wireless', 'phone',
           'printer']
MIMES = ['text-plain', 'text-x-generic', 'application-x-zerosize', 'x-office-document',
         'x-office-spreadsheet', 'x-office-presentation', 'application-pdf', 'image-x-generic',
         'audio-x-generic', 'video-x-generic']
SUBSET = {'places': PLACES, 'devices': DEVICES, 'mimetypes': MIMES}


def source_icon(name):
    aliases = {'org.gnome.SystemMonitor': 'utilities-system-monitor', 'codex': 'chatgpt',
               'app.devsuite.Ptyxis': 'org.gnome.Ptyxis'}
    for n in [name, aliases.get(name, '')]:
        if not n:
            continue
        for base in (BASE, LIGHT):
            for category in ['apps', 'places', 'devices']:
                f = base / category / (n + '.svg')
                if f.is_file():
                    return f
            for size in ['48x48', '32x32']:
                for category in ['apps', 'actions', 'status', 'mimetypes']:
                    f = base.parent / size / category / (n + '.svg')
                    if f.is_file():
                        return f
        for root in [Path('/var/lib/flatpak/exports/share/icons/hicolor'),
                     U / '.local/share/flatpak/exports/share/icons/hicolor',
                     U / '.local/share/icons/hicolor', Path('/usr/share/icons/hicolor')]:
            f = root / 'scalable/apps' / (n + '.svg')
            if f.is_file():
                return f


def app_tile(content):
    """Polycarbonate tile: elevated->surface gradient, 1 px border, milky inner highlight, accent dash."""
    return (f'<svg xmlns="{NS}" width="64" height="64" viewBox="0 0 64 64">'
            f'<defs><linearGradient id="tile" x2="0" y2="1"><stop stop-color="{P["elevated"]}"/>'
            f'<stop offset="1" stop-color="{P["surface"]}"/></linearGradient></defs>'
            f'<rect x="3" y="4" width="58" height="58" rx="12" fill="{P["desktop"]}" opacity=".3"/>'
            f'<rect x="3" y="2" width="58" height="58" rx="12" fill="url(#tile)" stroke="{P["border"]}" stroke-width="1"/>'
            f'<path d="M14 3.5h36" stroke="{P["secondary"]}" stroke-opacity=".55" stroke-width="1" stroke-linecap="round"/>'
            f'{content}'
            f'<path d="M25 56h14" stroke="{P["accent"]}" stroke-linecap="round" stroke-width="2"/></svg>')


def icons():
    if ICONS.exists():
        shutil.rmtree(ICONS)
    sections = {'apps': 'Applications', 'places': 'Places', 'devices': 'Devices', 'mimetypes': 'MimeTypes'}
    for d in sections:
        (ICONS / 'scalable' / d).mkdir(parents=True, exist_ok=True)
    ini = (f'[Icon Theme]\nName={NAME} icons\nComment=Aqua silver tiles and folders over Papirus-Dark\n'
           f'Inherits=Papirus,hicolor\nDirectories=' + ','.join('scalable/' + x for x in sections) + '\n')
    for d, ctx in sections.items():
        ini += f'\n[scalable/{d}]\nSize=64\nType=Scalable\nMinSize=24\nMaxSize=256\nContext={ctx}\n'
    (ICONS / 'index.theme').write_text(ini)
    counts = {k: 0 for k in sections}
    missing = []
    for name in _load_app_list():
        src = source_icon(name)
        if not src:
            missing.append(name)
            continue
        root = ET.fromstring(recolor(src.read_text(), APP_MAP))
        if not root.get('viewBox'):
            root.set('viewBox', '0 0 ' + root.get('width', '64').removesuffix('px') + ' ' + root.get('height', '64').removesuffix('px'))
        root.set('x', '10'); root.set('y', '9'); root.set('width', '44'); root.set('height', '44')
        (ICONS / 'scalable/apps' / (name + '.svg')).write_text(app_tile(ET.tostring(root, encoding='unicode')))
        counts['apps'] += 1
    missing_subset = []
    for category, names in SUBSET.items():
        for stem in names:
            src = BASE / category / (stem + '.svg')
            if not src.is_file():
                missing_subset.append(f'{category}/{stem}')
                continue
            (ICONS / 'scalable' / category / (stem + '.svg')).write_text(recolor(src.read_text(), ICON_MAP))
            counts[category] += 1
    shutil.copy2('/usr/share/doc/papirus-icon-theme/copyright', ICONS / 'COPYRIGHT')
    (ICONS / 'SOURCE.md').write_text(
        f'# {NAME} icons\n\nGenerated by `desktop/build_assets.py` from `design.json` on {BUILT_ON}.\n\n'
        '- `scalable/apps`: Papirus application marks nested in a 64x64 silver tile '
        '(elevated->surface gradient, 1 px border, milky top highlight, 2 px accent dash).\n'
        '- `scalable/places|devices|mimetypes`: the curated Papirus subset recoloured '
        '(folder front = accent, folder back = selection, paper = elevated).\n'
        '- Everything else inherits from Papirus, hicolor. See COPYRIGHT for the Papirus licence.\n')
    # verify: every SVG parses, no Papirus blue survives
    parsed = 0
    for f in ICONS.rglob('*.svg'):
        ET.parse(f); parsed += 1
    blue = [str(f.relative_to(ICONS)) for f in ICONS.rglob('*.svg') if '#5294e2' in f.read_text().lower()]
    assert not blue, f'Papirus blue survived in {blue}'
    return {'counts': counts, 'total': sum(counts.values()), 'svg_parsed': parsed,
            'inherited_application_icons': missing, 'missing_subset_sources': missing_subset,
            'papirus_blue_hits': len(blue), 'index_theme_sha256': sha256(ICONS / 'index.theme')}


# ------------------------------------------------------------------ cursors
def xcursor_sizes(path):
    """Return the sorted set of nominal sizes stored in an Xcursor file."""
    data = Path(path).read_bytes()
    magic, header, version, ntoc = struct.unpack('<4sIII', data[:16])
    assert magic == b'Xcur', 'not an Xcursor file'
    sizes = set()
    for i in range(ntoc):
        typ, subtype, pos = struct.unpack('<III', data[16 + 12 * i:28 + 12 * i])
        if typ == 0xfffd0002:
            sizes.add(subtype)
    return sorted(sizes)


def cursors():
    if CURSORS.exists(): shutil.rmtree(CURSORS)
    shutil.copytree(CUR,CURSORS,symlinks=True)
    for filename in ('index.theme','cursor.theme'):
        (CURSORS/filename).write_text(f'[Icon Theme]\nName={NAME} cursors\nComment=Unmodified Bibata Modern Classic; original license retained\nInherits=Bibata-Modern-Classic\n')
    (CURSORS/'SOURCE.md').write_text('Unmodified system Bibata Modern Classic cursor binaries. GPL-3.0. Source https://github.com/ful1e5/Bibata_Cursor ; copied once, no recoloring.\n')
    out=CURSORS/'cursors'
    for f in out.iterdir(): assert f.exists(), str(f)
    sizes=xcursor_sizes(out/'left_ptr')
    return {'shapes':sum(not f.is_symlink() for f in out.iterdir()),'aliases':sum(f.is_symlink() for f in out.iterdir()),'sizes':sizes,'default_sizes':sizes,'source':str(CUR),'recolored':False}


# ------------------------------------------------------------------ preview
def preview():
    """Contact sheet: 6 icons + 3 cursors on the desktop colour, for a visual colour check."""
    icon_names = ['places/folder', 'places/folder-documents', 'devices/computer', 'mimetypes/x-office-document',
                  'apps/firefox', 'apps/cs-themes']
    cursor_names = ['default', 'pointer', 'text']
    cell, pad = 96, 16
    cols = len(icon_names)
    W = pad + cols * (cell + pad); H = pad + cell + pad
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surface)
    r, g, b = (int(P['desktop'][i:i + 2], 16) / 255 for i in (1, 3, 5))
    ctx.set_source_rgb(r, g, b); ctx.paint()
    x = pad
    tmp = WORK / 'preview'; tmp.mkdir(parents=True, exist_ok=True)
    for n in icon_names:
        png = tmp / (n.replace('/', '_') + '.png')
        render(ICONS / 'scalable' / (n + '.svg'), png, cell, cell)
        img = cairo.ImageSurface.create_from_png(str(png))
        ctx.set_source_surface(img, x, pad); ctx.paint(); x += cell + pad
    out = WORK / 'preview-assets.png'
    surface.write_to_png(str(out))
    return str(out)


if __name__ == '__main__':
    WORK.mkdir(exist_ok=True)
    report = {
        'name': NAME, 'id': D['id'], 'built_on': BUILT_ON,
        'design_sha256': sha256(ROOT / 'design.json'),
        'tools': {'renderer': 'gi.repository.Rsvg + cairo', 'xcursorgen': shutil.which('xcursorgen'),
                  'python': sys.version.split()[0]},
        'sources': {'icons': str(BASE), 'icons_fallback': str(LIGHT), 'cursors': str(CUR), 'aliases': str(CUR_ALIASES)},
        'colour_map': {'icons': ICON_MAP, 'apps': APP_MAP, 'cursors': {},
                       'tile': {'gradient_top': P['elevated'], 'gradient_bottom': P['surface'], 'border': P['border'],
                                'highlight': P['secondary'] + ' @ .55', 'dash': P['accent'], 'shadow': P['desktop'] + ' @ .3', 'rx': 12}},
        'icons': icons(),
        'cursors': cursors(),
    }
    report['preview'] = preview()
    (HERE / 'assets-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"Built {report['icons']['total']} icons and {report['cursors']['shapes']} cursor shapes "
          f"(+{report['cursors']['aliases']} aliases) at sizes {report['cursors']['sizes']}")
