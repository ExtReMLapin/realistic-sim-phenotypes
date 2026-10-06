"""Builds rsim_parts.json: the design gender of EA's hair, beard and eyebrow parts.

Since the 2016 gender customization update, EA ships every hairstyle for both body frames: a
feminine style (internal name yfHair_...) also exists as a part flagged for male Sims, and the
other way round. The game's catalogue therefore offers feminine hairstyles to men; Create-a-Sim
only hides them behind its "Masculine" filter, which follows the design gender. That gender is
only recorded in the part's internal name (age letter, then m, f or u), so it is read here.

Usage: python build_parts.py [output_dir]   (Python 3.8+, the game does not need to run)
"""
import glob
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbpfx import entries, read  # noqa: E402

GAME = os.environ.get('TS4_GAME_DIR', r'C:\Program Files\EA Games\The Sims 4')
T_CASP = 0x034AEECB
BODY_TYPES = {2: 'hair', 28: 'facial_hair', 34: 'eyebrows'}
NAME = re.compile(r'^[a-z]([mfu])[A-Z]')


def name_and_body_type(data):
    """Internal name and body type of a CASP resource (versions 26 to 52)."""
    version = struct.unpack_from('<I', data, 0)[0]
    pos = 12
    length = data[pos]
    pos += 1
    if length & 0x80:
        length = (length & 0x7f) | (data[pos] << 7)
        pos += 1
    name = data[pos:pos + length].decode('utf-16-be', 'replace')
    pos += length + 14
    width, fmt = (6, '<HI') if version >= 0x2b else (4, '<HH')
    # The flags before the tag list and the fields after it vary between versions: find the tag
    # count, then the body type and age/gender fields, by checking that the values are consistent.
    for skip in range(72):
        start = pos + skip
        if start + 4 > len(data):
            break
        count = struct.unpack_from('<I', data, start)[0]
        end = start + 4 + width * count
        if count > 400 or end + 12 > len(data):
            continue
        tags = [struct.unpack_from(fmt, data, start + 4 + width * k) for k in range(count)]
        if any(c > 1000 or c == 0 for c, _ in tags):
            continue
        for gap in range(8, 48):
            at = end + gap
            if at + 12 > len(data):
                break
            body_type, _, age_gender = struct.unpack_from('<iiI', data, at)
            if 0 < body_type < 200 and age_gender & 0xff and (age_gender & 0xff00) in (0x1000, 0x2000, 0x3000):
                return name, body_type
    return name, None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    genders = {'masculine': set(), 'feminine': set()}
    seen = set()
    for path in glob.glob(os.path.join(GAME, '**', '*.package'), recursive=True):
        try:
            for f, t, g, i, off, fs, ms, comp in entries(path):
                if t != T_CASP or i in seen or comp == 0xffe0:
                    continue
                data = read(f, off, fs, ms, comp)
                if not data:
                    continue
                seen.add(i)
                name, body_type = name_and_body_type(data)
                match = NAME.match(name)
                if body_type in BODY_TYPES and match and match.group(1) != 'u':
                    genders['masculine' if match.group(1) == 'm' else 'feminine'].add(i)
        except Exception:
            pass
    table = {'_format': 'instance ids of EA hair, beard and eyebrow parts by design gender (from the internal name); '
                        'parts not listed are unisex or custom content',
             'masculine': sorted(genders['masculine']), 'feminine': sorted(genders['feminine'])}
    with open(os.path.join(out, 'rsim_parts.json'), 'w', encoding='utf-8') as f:
        json.dump(table, f, separators=(',', ':'))
    print('parts read: {}; masculine: {}, feminine: {}'.format(len(seen), len(genders['masculine']),
                                                             len(genders['feminine'])))


if __name__ == '__main__':
    main()
