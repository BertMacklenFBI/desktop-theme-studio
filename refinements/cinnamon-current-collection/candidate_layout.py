"""Strict authored panel compositions for candidate private sessions.

This module returns settings data only. It never changes a session. Collection
activation remains blocked until the separate panel ownership adapter and
current release evidence are complete.
"""
from __future__ import annotations
import re
import math


def settings(profile):
    layout = profile.get('candidate_layout')
    if not isinstance(layout, dict):
        raise ValueError('Candidate requires an explicit authored panel composition')
    kind = layout.get('kind')
    if kind == 'two-panel':
        fields = {'kind', 'top_height', 'bottom_height'}
        panels = [(1, 'top', layout.get('top_height')), (2, 'bottom', layout.get('bottom_height'))]
        primary, dock = 1, 2
    elif kind in ('single-top', 'single-bottom'):
        fields = {'kind', 'height'}
        panels = [(1, 'top' if kind == 'single-top' else 'bottom', layout.get('height'))]
        primary = dock = 1
    else:
        raise ValueError('Unsupported candidate panel composition: ' + str(kind))
    if set(layout) != fields:
        raise ValueError('Unknown or missing candidate layout fields')
    if any(type(height) is not int or not 16 <= height <= 200 for _, _, height in panels):
        raise ValueError('Candidate panel height is outside reviewed bounds')
    island = profile.get('island_uuid', 'nothing-island@desktop-theme-studio')
    if not isinstance(island, str) or not re.fullmatch(r'[A-Za-z0-9._-]+@[A-Za-z0-9._-]+', island):
        raise ValueError('Unsafe candidate Island identity')
    # IDs are confined to the disposable session. Host IDs are never reused.
    applets = [f'panel{primary}:left:0:menu@cinnamon.org:901',
               f'panel{primary}:left:1:workspace-switcher@cinnamon.org:902',
               f'panel{primary}:right:0:{island}:903',
               f'panel{primary}:right:1:notifications@cinnamon.org:904',
               f'panel{primary}:right:2:sound@cinnamon.org:905',
               f'panel{dock}:center:0:grouped-window-list@cinnamon.org:906']
    if kind == 'two-panel':
        applets[2] = f'panel{primary}:center:0:{island}:903'
    return {'enabled-extensions': [], 'enabled-desklets': [],
            'panels-enabled': [f'{pid}:0:{edge}' for pid, edge, _ in panels],
            'panels-height': [f'{pid}:{height}' for pid, _, height in panels],
            'panels-autohide': [f'{pid}:false' for pid, _, _ in panels],
            'enabled-applets': applets, 'next-applet-id': 907}


def assert_native(measured, profile):
    expected = settings(profile)
    panels = measured.get('panels', [])
    if len(panels) != len(expected['panels-height']):
        raise RuntimeError('Native candidate panel count differs from authored composition')
    by_id = {row['id']: row for row in panels}
    if len(by_id) != len(panels) or measured.get('screen') != [2880, 1800]:
        raise RuntimeError('Native panel identity or framebuffer evidence is incomplete')
    for row in expected['panels-height']:
        pid, height = map(int, row.split(':'))
        found = by_id.get(pid)
        if (not found or type(found.get('height')) not in (int,float)
                or not math.isfinite(found['height']) or abs(found['height'] - height) > 1
                or found.get('mapped') is not True):
            raise RuntimeError('Native candidate panel allocation differs from authored composition')
        position, size = found.get('position'), found.get('size')
        if (not isinstance(position,list) or not isinstance(size,list) or len(position)!=2 or len(size)!=2
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in position+size)):
            raise RuntimeError('Native candidate panel bounds are incomplete')
        edge = next(value.split(':')[2] for value in expected['panels-enabled'] if value.startswith(str(pid)+':'))
        expected_y = 0 if edge == 'top' else 1800-height
        # Authored shell CSS can inset a panel symmetrically (Moonstone uses
        # 12px side margins). The composition declares its edge and height;
        # preserve that artwork/style geometry while verifying containment.
        if (position[0]<-1 or size[0]<=0 or position[0]+size[0]>2881
                or abs(position[0]+size[0]/2-1440)>1
                or abs(position[1]-expected_y)>1 or abs(size[1]-height)>1):
            raise RuntimeError('Native candidate panel placement differs from authored edge')
    return expected
