"""Builds the tables read by the rsim mod (run again whenever your CC changes).

- rsim_tones.json: every skin tone (TONE 0x0354796A) with its CAS swatch lightness L*,
  a "human" flag (plausible skin hue) and a "cc" flag.
- rsim_cc.json:    instance ids of the CAS parts (CASP 0x034AEECB) found in the Mods folder.

Usage: python build_tables.py [output_dir]   (Python 3.8+, the game does not need to run)
"""
import colorsys
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbpfx import entries, read  # noqa: E402

# Set TS4_GAME_DIR and TS4_MODS_DIR when the game or the Mods folder is elsewhere.
GAME = os.environ.get('TS4_GAME_DIR', r'C:\Program Files\EA Games\The Sims 4')
MODS = os.environ.get('TS4_MODS_DIR', os.path.join(os.path.expanduser('~'), 'Documents', 'Electronic Arts',
                                                   'The Sims 4', 'Mods'))
T_TONE, T_CASP = 0x0354796A, 0x034AEECB


def swatch(data):
    # The swatch colour (ARGB, preceded by a count of 1) sits near the end of the resource.
    tail = data[-60:]
    for k in range(len(tail) - 5, -1, -1):
        if tail[k] == 1 and tail[k + 4] == 0xFF:
            b, g, r = tail[k + 1], tail[k + 2], tail[k + 3]
            return r, g, b
    return None


def lightness(r, g, b):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    y = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 116 * y ** (1 / 3) - 16 if y > 0.008856 else 903.3 * y


def is_human(r, g, b):
    h, _, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return (h <= 0.14 or h >= 0.95) and s >= 0.08


def scan(packages, want_tones, want_parts):
    tones, parts = {}, set()
    for path in packages:
        try:
            for f, t, g, i, off, fs, ms, comp in entries(path):
                if t == T_TONE and want_tones and i not in tones:
                    data = read(f, off, fs, ms, comp)
                    rgb = swatch(data) if data else None
                    if rgb:
                        tones[i] = rgb
                elif t == T_CASP and want_parts:
                    parts.add(i)
        except Exception:
            pass
    return tones, parts


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    game_pkgs = glob.glob(os.path.join(GAME, '**', '*.package'), recursive=True)
    mod_pkgs = glob.glob(os.path.join(MODS, '**', '*.package'), recursive=True)
    game_tones, game_parts = scan(game_pkgs, True, True)
    mod_tones, cc_parts = scan(mod_pkgs, True, True)
    cc_parts -= game_parts  # overrides of official parts stay usable
    rows = []
    for source, tones in (('game', game_tones), ('cc', {k: v for k, v in mod_tones.items() if k not in game_tones})):
        for tone_id, (r, g, b) in sorted(tones.items()):
            rows.append({'id': tone_id, 'L': round(lightness(r, g, b), 1), 'rgb': '#%02x%02x%02x' % (r, g, b),
                         'human': is_human(r, g, b), 'cc': source == 'cc'})
    with open(os.path.join(out, 'rsim_tones.json'), 'w', encoding='utf-8') as f:
        json.dump({'tones': rows}, f, separators=(',', ':'))
    with open(os.path.join(out, 'rsim_cc.json'), 'w', encoding='utf-8') as f:
        json.dump({'cas_parts': sorted(cc_parts)}, f, separators=(',', ':'))
    print('tones: {} official, {} CC; CC parts: {}'.format(len(game_tones), len(rows) - len(game_tones), len(cc_parts)))


if __name__ == '__main__':
    main()
