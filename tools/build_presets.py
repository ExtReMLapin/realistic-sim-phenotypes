"""Builds rsim_presets.json: the game's CAS face presets (CASPreset 0xEAA32ADD) with their archetype tags.

A CAS preset is a face or body shape (sculpts + slider modifiers) for one region, such as the eye
shape. EA tags many of them with the archetypes they suit (tag category 69). Only the game's own
packages are read, so the table never contains custom content.

Usage: python build_presets.py [output_dir]   (Python 3.8+, the game does not need to run)
"""
import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbpfx import entries, read  # noqa: E402

GAME = os.environ.get('TS4_GAME_DIR', r'C:\Program Files\EA Games\The Sims 4')
T_PRESET = 0xEAA32ADD
ARCHETYPE_CATEGORY = 69
# Occult tags (category 109). Aliens and werewolves are human species with an occult form: their
# face presets (black alien eyes, werewolf muzzle) carry their occult tag and not the human one.
OCCULT_CATEGORY, OCCULT_HUMAN = 109, 1310
# Tag values of Archetype_* (category 69). The order follows the tag tuning; confirmed in game with tag.Tag.
ARCHETYPES = {73: 'african', 74: 'middle_eastern', 75: 'asian', 76: 'caucasian', 88: 'south_asian',
              89: 'north_american', 312: 'latin', 2206: 'island', 2996: 'native_american'}
# CAS regions of the presets (SimRegion), as far as they are known.
REGIONS = {0: 'eyes', 1: 'nose', 2: 'mouth', 3: 'cheeks', 4: 'chin', 5: 'jaw', 6: 'forehead', 8: 'brows',
           9: 'ears', 10: 'head', 12: 'face', 28: 'body'}
HUMAN = 1


def parse(data):
    """CASPreset v12: header, sculpts, modifiers, a few flags (and physique), then the tag list."""
    version, age_gender, frame, species, region = struct.unpack_from('<IIIII', data, 0)
    if version != 12:
        return None
    pos = 40
    count = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    sculpts = list(struct.unpack_from('<%dQ' % count, data, pos))
    pos += 8 * count
    count = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    modifiers = []
    for _ in range(count):
        key, amount = struct.unpack_from('<Qf', data, pos)
        modifiers.append([key, round(amount, 4)])
        pos += 12
    tags = tag_list(data)
    if tags is None:
        return None
    archetypes = sorted({ARCHETYPES[v] for c, v in tags if c == ARCHETYPE_CATEGORY and v in ARCHETYPES})
    occults = {v for c, v in tags if c == OCCULT_CATEGORY}
    return {'human_form': not occults or OCCULT_HUMAN in occults,'age_gender': age_gender, 'frame': frame, 'species': species, 'region': region,
            'sculpts': sculpts, 'modifiers': modifiers, 'archetypes': archetypes}


def tag_list(data):
    # The tag list closes the resource: a count, then (uint16 category, uint32 value) pairs.
    for width, fmt in ((6, '<HI'), (4, '<HH')):
        for n in range(1, 60):
            start = len(data) - 4 - width * n
            if start < 0:
                break
            if struct.unpack_from('<I', data, start)[0] == n:
                tags = [struct.unpack_from(fmt, data, start + 4 + width * k) for k in range(n)]
                if all(c < 2000 and v < 200000 for c, v in tags):
                    return tags
    if len(data) >= 4 and struct.unpack_from('<I', data, len(data) - 4)[0] == 0:
        return []
    return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    presets = {}
    for path in glob.glob(os.path.join(GAME, '**', '*.package'), recursive=True):
        try:
            for f, t, g, i, off, fs, ms, comp in entries(path):
                if t != T_PRESET or i in presets:
                    continue
                preset = parse(read(f, off, fs, ms, comp))
                # Occult presets are kept, flagged: never drawn, but their sculpts and sliders are
                # cleared from a face region before a preset is applied.
                if preset is not None and preset['species'] == HUMAN and preset['region'] in REGIONS:
                    presets[i] = preset
        except Exception:
            pass
    rows = []
    for preset_id, p in sorted(presets.items()):
        rows.append([preset_id, p['age_gender'], p['frame'], REGIONS[p['region']], p['sculpts'], p['modifiers'],
                     p['archetypes'], int(p['human_form'])])
    table = {'_format': 'id, age_gender, frame, region, sculpts, modifiers [key, amount], archetypes, '
                        'human_form (0: alien or werewolf preset, never drawn)',
             'archetypes': sorted(ARCHETYPES.values()), 'presets': rows}
    with open(os.path.join(out, 'rsim_presets.json'), 'w', encoding='utf-8') as f:
        json.dump(table, f, separators=(',', ':'))
    print('presets: {}, human form: {}, with 1 to 3 archetypes: {}'.format(
        len(rows), sum(r[7] for r in rows), sum(1 for r in rows if r[7] and 1 <= len(r[6]) <= 3)))


if __name__ == '__main__':
    main()
