# Methodology

## 1. How the game generates a Sim

Generated Sims (townies, NPCs, service Sims, random households) are created by the Python call
`SimSpawner.create_sim_infos`, which hands the actual work to the native function
`_cas.generate_household` in `Simulation_x64.dll`. We disassembled that path with Ghidra
(game version 1.128.90). What the native generator does:

- **Skin tone.** It picks one tone from the catalogue of all installed skin tones that pass an
  age/species filter. The pick is a uniform random index followed by a linear probe; one modder
  (CmarNYC, TS4 Skininator) reports that tones with more "archetype" tags are picked more often.
  Either way there is no notion of population: the result follows the composition of the palette.
- **Palette composition.** The December 2020 update (patch 1.68) added more than 100 tones to
  improve representation in Create-a-Sim. We classified the 136 official human tones by the
  lightness L* of their CAS swatch (sRGB -> CIELAB): 14 white (L* >= 79), 7 light (73-78),
  12 light brown (63-72), 31 medium brown (45-62), 72 dark (< 45). About 15% of the palette is
  white or light, and counting archetype tags gives about 12% of the weight. In our in-game test,
  about 3 of 35 vanilla adults were light-skinned.
- **Hair and eye colour.** Each CAS part (hair, eye colour) is drawn from its own catalogue,
  independently of the skin. This is why dark-skinned Sims with blond or red hair or green eyes
  are common, while such combinations are very rare in real populations.
- **Body shape.** Two body axes (fat/thin, muscle/bony) are each set to the mean of three uniform
  random numbers, a narrow bell curve centred on "average", then usually replaced by the body of a
  random CAS preset with ±0.05 noise. No country, age or sex statistics are involved.

The game also has a bug in this code: the probe loop never ends when it starts at index 0 and no
catalogue entry passes the filter, which freezes the game (see the README).

## 2. What the mod changes

The mod lets the native generator run, then rewrites the look of the new Sims before they are
used anywhere:

| Trait | How it is set | Game API |
|---|---|---|
| Skin tone | Tone picked with Gaussian weights around a target swatch lightness for the origin group | `sim_info.skin_tone` |
| Hair colour | Official hair part carrying the colour tag (`HairColor_*`), same length when possible, texture drawn per group | outfits + genetics |
| Beard, eyebrows | Same colour tag as the hair, only if the Sim already has one | outfits + genetics |
| Eye colour | Official part carrying the eye colour tag (`EyeColor_*`) | outfits + genetics |
| Body shape | BMI -> silhouette -> body sliders | `sim_info.physique` |

Parts are replaced in every outfit and in the Sim's genetic data, so children inherit them.

## 3. Where the numbers come from

The profiles were built from a literature and statistics review. Every block of
`data/rsim_profiles.json` carries its source and status: **[S]** sourced, **[I]** computed or
interpolated from sources, **[D]** design choice.

### Origins

Official statistics where ethnicity is recorded (ONS Census 2021, US Census 2020). Elsewhere, the
best available proxy, which is birthplace of the person or of their parents: INSEE 2023 and the
TeO2 survey (France), Destatis Mikrozensus 2024 (Germany), CBS 2025 (Netherlands), SCB 2024
(Sweden), INE 2025 (Spain), ISTAT 2025 (Italy), GUS (Poland). These were converted to broad
appearance groups (European, North African and Middle Eastern, Sub-Saharan African, East, South and
Southeast Asian, Latin American, mixed). The conversion is an estimate [I] (±1 to 3 points);
people of mixed parentage and third generations are mostly invisible in these figures.
Minorities are younger than average, so the share of non-European origins is raised by a factor
of 1.6 for Sims under 30 [D].

The `europe` profile is the population-weighted average of the eight European profiles.

### Hair and eye colour

- Frequencies: Sulem et al. 2007 (Iceland n=2,986, Netherlands n=1,214), UK Biobank via Morgan et
  al. 2018 (n=343,234), Ambroa-Conde et al. 2024 (Spain n=380), Mengel-From et al. 2009 (Denmark,
  Scotland), Lock-Andersen et al. 1998 (Denmark).
- Hair x eye association: the Netherlands Twin Register 2004 cross-table (n=3,619) and Bolk 1908
  (n≈479,000), via Lin et al. 2016. Eyes are drawn given hair, then the table is refitted (iterative
  proportional fitting) to each country's eye frequencies. Redheads keep the measured distribution.
- No published frequency table exists for France, Italy, Sweden or Poland, or for the Maghreb:
  those values are interpolated or design choices and are marked as such.
- Non-European groups: CANDELA (Adhikari et al. 2019, n=6,236, Latin America), Turkey (n=149) and
  Pakistan (n=893) iris studies, Crawford et al. 2017 for African skin variation.
- Children: about 70% of blond toddlers darken by age 6-13, so blond is boosted for children
  and teens.

### Skin tone

No study links measured skin reflectance to the game's tones, so target lightness values per group
are design choices [D], calibrated by eye on the sorted palette (`data/tones_sheet.png`).
Redheads only get the lightest tones (MC1R: red hair occurs with phototypes I and II).

### Body shape

- BMI classes (<18.5, 18.5-25, 25-30, 30-35, 35-40, >=40) by country, sex and age from NCD-RisC
  2024 (measured height and weight, modelled estimates). Children and teens: NCD-RisC 2024 with
  WHO definitions.
- BMI is converted to a body silhouette with the Stunkard scale calibrated by Parzer et al. 2021,
  then to the game's fat/thin slider [D].
- Muscle: share of adults doing muscle-strengthening exercise at least twice a week (Eurostat
  EHIS 2019).

## 4. Limits

- Self-reported pigmentation is not comparable across countries: people rate themselves against
  their neighbours. Measured data exist only for a few populations.
- The swatch colour is a proxy for the rendered skin.
- Faces still come from EA's presets; eye shape is not handled yet.
- The mapping from BMI to the game's body sliders has to be checked visually.
