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

### EA's archetype tags

EA tags CAS content with nine "archetypes" (tag category 69): African, MiddleEastern, Asian,
Caucasian, SouthAsian, NorthAmerican, Latin, Island and NativeAmerican. What carries them, in the
game's own files (version 1.128.90):

| Content | Tagged | What the tags say |
|---|---|---|
| Skin tones | 142 of 180 | Broad lightness ranges, but at equal lightness Asian and Caucasian tones are pale, Middle Eastern, South Asian and Latin tones golden |
| Face presets (eyes, nose, mouth) | 316 of 1,425 human presets reserved to 1-3 archetypes | Asian eyelids, African noses and lips, etc. |
| Hair colours | most colour swatches | Black fits everyone, dark brown Caucasian/Latin/Middle Eastern/North American, blond, red and light brown Caucasian only |
| Eye colours | almost all | Light eye colours are not tagged Asian or South Asian |

A tuning file, `Client_TagRandomizer`, lists for each randomized item the tag categories that
must stay consistent within one Sim. Several items carry the archetype category (69), with hair
colour or eye colour categories next to it. The native function that applies it
(`0x0f6f86f0` in Ghidra) draws one tag per category per Sim and reuses it for every item.

We traced which items the townie generator actually requests, with hardware breakpoints on the
game while it generated three vanilla Sims (game version 1.128.90):

| Item | Tag categories | Requested by the generator |
|---|---|---|
| 4, 5, 6, 7 | clothing style (66, 76, 107) | yes, many times |
| 17 | 75 | yes |
| 1 | archetype (69) + occult (109) | yes, twice for the three Sims |
| 0 | archetype (69) + occult (109) | never |
| 2 | archetype (69) + 75 | never |
| 8 | archetype (69) + 72 | never |
| 9 | archetype (69) | never |

So the generator uses the system mainly to coordinate outfits. It does draw an archetype, but the items
that would apply it to the other traits are never requested, and hair colour, eye colour and face
are drawn without it. Item names are not in the game's Python or tuning; the roles above are
inferred from their tag categories. The Create-a-Sim randomizer runs in the client
(`TS4_x64.exe`, which has its own copy of this tuning) and did not keep the archetype consistent
either in our test (a dark-skinned Sim with blond hair). The mod applies the archetype itself: see
section 2.

Hairstyles are also shipped as one mesh per body frame: every style, masculine or feminine,
exists for both frames, so the generator's catalogue offers men long feminine styles. Neither the
internal name (`ymHair_...` / `yfHair_...`) nor the age/gender field gives the style's gender: they
only say which frame the mesh fits. Create-a-Sim's gender filter follows two tags of category 111
instead: 1529 (suits masculine Sims) and 1530 (suits feminine Sims); unisex styles carry both. The
mod applies the same rule to hair and beards (an adult man keeps about 6,000 of the 12,500 hair
parts), and for parts without these tags (most eyebrows) falls back to the internal name
(`tools/build_parts.py`).

The game also has a bug in this code: the probe loop never ends when it starts at index 0 and no
catalogue entry passes the filter, which freezes the game (see the README).

## 2. What the mod changes

### Where it hooks in

| Game path | Used for | Mod |
|---|---|---|
| `SimSpawner.create_sim_infos` -> `generate_household` | Townies (Sim filters), NPCs and service Sims, household templates, neighbourhood population, family tree, cheats | Profile applied to the whole batch right after generation |
| `generate_random_siminfo` (imported by `adoption_service` and `matchmaking_service`) | Sims offered for adoption, dating app profiles | Profile applied to the temporary Sim; the game copies its look onto the real Sim later |
| `generate_offspring` | Newborns | Not changed: babies inherit from their parents, and the mod writes its changes into the genetic data so children of generated Sims inherit them |
| `generate_occult_siminfo` | Occult forms | Not changed |
| Create-a-Sim randomizer | Player-made Sims | Not reachable from Python |

Batches skipped on purpose (by creation source): pregnancy, cloning, relatives built from a
parent's genetics (`stayover relative`), adoption copies, reincarnation, and premade Sims created
from a template (`resource_key`). Occult Sims are skipped as well.

Sims generated together share one origin; children and teens copy the hair, eye colour and hair
texture of an adult of the batch, and get a skin tone close to the average of the adults.

### Sims already in a save

The automatic mode only acts at creation time. `rsim.reroll confirm` applies the profile to
existing Sims, a whole household at a time, using the creation source the game stores for every
Sim in the save. A household is rerolled only if every member was generated by the game (Sim
filter, neighbourhood population or household template). Played households, the active
household, and any household containing a Sim made in Create-a-Sim, premade by EA, downloaded from
the Gallery, born, adopted or cloned are left alone. Without `confirm` the command only counts.

### What is written on the Sim

The mod lets the native generator run, then rewrites the look of the new Sims before they are
used anywhere:

| Trait | How it is set | Game API |
|---|---|---|
| Skin tone | Tone picked with Gaussian weights around a target swatch lightness for the origin group | `sim_info.skin_tone` |
| Hair colour | Official hair part carrying the colour tag (`HairColor_*`), same length when possible, texture drawn per group | outfits + genetics |
| Beard, eyebrows | Same colour tag as the hair, only if the Sim already has one | outfits + genetics |
| Eye colour | Official part carrying the eye colour tag (`EyeColor_*`) | outfits + genetics |
| Skin undertone | Among tones of similar lightness, tones EA tagged for the Sim's archetype weigh 3x more | `sim_info.skin_tone` |
| Face shape | Eyes, nose and mouth: one EA face preset per region, drawn among the presets tagged for the Sim's archetype (`face_presets` in the cfg) | `sim_info.facial_attributes` + genetics |
| Body shape | BMI -> silhouette -> body sliders | `sim_info.physique` + genetics |

Parts, face presets and body shape are also written in the Sim's genetic data, so children inherit
them. Each origin group maps to one archetype (`group_archetypes` in the profiles file; Southeast
Asians use Asian, Polynesians Island). Face presets are drawn among every preset carrying the tag,
so a group keeps its variety, but presets reserved to other archetypes are never used: a European
Sim no longer gets East Asian eyelids.

A preset is only drawn when it fits the Sim on three counts, each of which breaks the face otherwise:

- age and gender (the preset's age/gender field);
- body frame: most presets suit both genders but are shaped for the masculine or the feminine
  frame, and a preset made for the other frame tears the mesh (split lips, eye corners); the frame
  is a gender option trait, by default the Sim's gender;
- human form: aliens and werewolves are human Sims with an occult form, so their face presets are
  filed under the human species; they carry their occult tag (category 109) instead of the human
  one and give black patches around the eyes and mouth. They are never drawn, but what they set is
  cleared from a region before a preset is applied, which repairs a Sim that got one.

Before a preset is applied, every sculpt and slider that any preset of that region can set is
removed from the Sim, so presets do not pile up.

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
- Polynesian group: hair colour, hair form and eye colour from Sullivan 1921 (Samoa) and 1922 (Tonga),
  Bishop Museum Memoirs, visual chart observations on about 300 people [S]. Skin from Colmenares et
  al. 2013 (n=12, back skin) [I]. No data was found for Maori, Native Hawaiians or French Polynesia.
- Native American group: skin placed from Jablonski & Chaplin 2000 (reflectance of Kaingang, Guarani,
  Andean and Amazonian samples) and Ruiz-Linares et al. 2014 (CANDELA "Native" subgroups) [I]. No
  hair or eye colour frequency is published for low-admixture Indigenous Americans: those values are
  design choices [D].
- Neither group is used by a country profile yet; they serve EA's Island and NativeAmerican
  archetypes in `rsim.facepreset`.
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
