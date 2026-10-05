"""Random Sim preview: spawns N generated Sims around the active Sim, vanilla or with a realistic profile.

rsim.spawn [count=35] [age=adult|young_adult|teen|child|elder|mix|family] [profile=default|vanilla|auto|europe|...]
    vanilla: the game's own generator, untouched
    auto:    generated exactly like a townie, through the automatic mode hook (tests auto_apply)
    family:  households of two adults and two children (tests household coherence)
rsim.profiles  lists the available profiles
rsim.freeze    disables autonomy on every preview Sim
rsim.reroll    applies the profile to generated Sims already in the save (dry run unless 'confirm')
rsim.clear     permanently deletes every preview Sim

Profiles are read from rsim_profiles.json and rsim_tones.json, packed inside the .ts4script; a copy
placed next to the .ts4script takes precedence.
rsim.cfg chooses the default profile ("default" in rsim.spawn) and whether custom content may be used.
"""
import configparser
import importlib
import json
import os
import random
import time
import traceback
import zipfile

import services
import sims4.commands
import sims4.math
import terrain
from autonomy.settings import AutonomyState
from cas import cas
from objects import ALL_HIDDEN_REASONS
from protocolbuffers import Outfits_pb2
from sims.household_enums import HouseholdChangeOrigin
from sims.occult.occult_enums import OccultType
from sims.outfits.outfit_enums import BodyType
from sims.sim_info_types import Age, Gender, Species
from sims.sim_spawner import SimCreator, SimSpawner
from sims.sim_spawner_enums import SimInfoCreationSource
from tag import Tag

from rsim_engine import Engine

LAST_NAME = 'Preview'
LEGACY_LAST_NAMES = ('Apercu',)
LOG_NAME = 'rsim.log'
SPACING = 1.6
COLUMNS = 7
AGES = {
    'adult': Age.ADULT,
    'young_adult': Age.YOUNGADULT,
    'teen': Age.TEEN,
    'child': Age.CHILD,
    'elder': Age.ELDER,
}
MIX = (Age.CHILD, Age.TEEN, Age.YOUNGADULT, Age.YOUNGADULT, Age.ADULT, Age.ADULT, Age.ELDER)
SUPPORTED_AGES = (Age.CHILD, Age.TEEN, Age.YOUNGADULT, Age.ADULT, Age.ELDER)
HAIR_LENGTH_TAGS = ('HairLength_Short', 'HairLength_Medium', 'HairLength_Long')
PHYSIQUE_HEAVY, PHYSIQUE_FIT, PHYSIQUE_LEAN, PHYSIQUE_BONY = 0, 1, 2, 3


def _mod_dir():
    # __file__ is .../Mods/<folder>/rsim.ts4script/rsim_preview.pyc
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(line):
    try:
        with open(os.path.join(_mod_dir(), LOG_NAME), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _debug(line):
    # Step markers: the last one written tells which step was running if the game freezes.
    if _Data.engine is None or _Data.engine.settings.get('debug_log', True):
        _log('  [step] ' + line)


# --- data ----------------------------------------------------------------------------------

class _Data:
    engine = None
    tones = None
    tags = {}
    catalogs = {}


def _read_config():
    config = {'profile': 'europe'}
    parser = configparser.ConfigParser(inline_comment_prefixes=(';', '#'))
    try:
        with open(os.path.join(_mod_dir(), 'rsim.cfg'), encoding='utf-8') as f:
            parser.read_file(f)
    except OSError:
        return config
    if parser.has_section('population'):
        section = parser['population']
        config['profile'] = section.get('profile', config['profile']).strip().lower()
        if 'official_only' in section:
            config['official_only'] = section.getboolean('official_only')
        if 'auto_apply' in section:
            config['auto_apply'] = section.getboolean('auto_apply')
    return config


def _read_json(name):
    """A copy next to the .ts4script wins (for players editing profiles); otherwise use the one packed inside it.

    CurseForge only accepts .ts4script, .package, .cfg, .txt and .binary files in Mods archives.
    """
    loose = os.path.join(_mod_dir(), name)
    if os.path.isfile(loose):
        with open(loose, encoding='utf-8') as f:
            return json.load(f)
    archive = os.path.dirname(os.path.abspath(__file__))
    with zipfile.ZipFile(archive) as z:
        return json.loads(z.read(name).decode('utf-8'))


def _load():
    if _Data.engine is not None:
        return _Data.engine
    engine = Engine(_read_json('rsim_profiles.json'))
    config = _read_config()
    engine.settings['default_profile'] = config['profile']
    for key in ('official_only', 'auto_apply'):
        if key in config:
            engine.settings[key] = config[key]
    _Data.tones = _read_json('rsim_tones.json')['tones']
    _Data.engine = engine
    return engine


def _tag(name):
    if name not in _Data.tags:
        try:
            _Data.tags[name] = int(Tag[name])
        except Exception:
            _Data.tags[name] = None
    return _Data.tags[name]


def _is_official_part(part_id):
    # Every EA CAS part has a 32-bit instance id; CC tools generate 64-bit ids (high bit set).
    # Checked on 1.128.90: 112,312 of 112,312 official parts below 2**32, 1 of 105,548 CC parts.
    return part_id < 0x100000000


def _catalog(sim_info, body_type):
    """{part_id: set(tags)} of the parts of this body type valid for the Sim's age and gender."""
    key = (int(body_type), int(sim_info.age), int(sim_info.gender))
    if key not in _Data.catalogs:
        parts = cas.get_catalog_casparts_by_bodytype(sim_info=sim_info._base, include_tags=[], exclude_tags=[],
                                                     bodytypes=[body_type], show_all_variants=True, rewards_parts=[])
        official_only = _Data.engine.settings.get('official_only', True)
        catalog = {}
        for part_id, value in parts.items():
            part_id = int(part_id)
            if official_only and not _is_official_part(part_id):
                continue
            tags = value[0] if isinstance(value, tuple) else value
            catalog[part_id] = set(int(t) for t in tags)
        _Data.catalogs[key] = catalog
    return _Data.catalogs[key]


# --- applying a look to a Sim -----------------------------------------------------------------

def _current_part(sim_info, body_type):
    outfits = sim_info.save_outfits()
    for outfit in outfits.outfits:
        types = outfit.body_types_list.body_types
        for i, bt in enumerate(types):
            if bt == body_type:
                return outfit.parts.ids[i]
    return None


def _pick_part(sim_info, body_type, wanted_tag_names, keep_tag_names, rng, required_tag_name=None,
               only_if_tagged=None):
    """Pick a part carrying one of the wanted tags.

    keep_tag_names: tags kept from the current part when possible (e.g. hair length).
    required_tag_name: tag required when possible (e.g. hair texture), dropped if it leaves no candidate.
    only_if_tagged: do nothing unless the current part carries one of these tags (e.g. no beard).
    """
    wanted = set(t for t in (_tag(n) for n in wanted_tag_names) if t is not None)
    if not wanted:
        return None
    catalog = _catalog(sim_info, body_type)
    current = _current_part(sim_info, body_type)
    current_tags = catalog.get(int(current), set()) if current is not None else set()
    if only_if_tagged is not None:
        gate = set(t for t in (_tag(n) for n in only_if_tagged) if t is not None)
        if not (current_tags & gate):
            return None
    candidates = [p for p, tags in catalog.items() if tags & wanted]
    if not candidates:
        return None
    required = _tag(required_tag_name) if required_tag_name else None
    if required is not None:
        narrowed = [p for p in candidates if required in catalog[p]]
        if narrowed:
            candidates = narrowed
    if keep_tag_names:
        keep = set(t for t in (_tag(n) for n in keep_tag_names) if t is not None)
        current_keep = current_tags & keep
        if current_keep:
            same_look = [p for p in candidates if current_keep <= catalog[p]]
            if same_look:
                candidates = same_look
    return rng.choice(candidates)


def _replace_part_everywhere(sim_info, body_type, part_id):
    outfits = sim_info.save_outfits()
    changed = 0
    for outfit in outfits.outfits:
        types = outfit.body_types_list.body_types
        for i, bt in enumerate(types):
            if bt == body_type and outfit.parts.ids[i] != part_id:
                outfit.parts.ids[i] = part_id
                changed += 1
    if changed:
        sim_info.load_outfits(outfits)
    try:
        genetic = Outfits_pb2.GeneticData()
        genetic.ParseFromString(sim_info._base.genetic_data)
        for part in genetic.parts_list.parts:
            if part.body_type == body_type:
                part.id = part_id
        sim_info._base.genetic_data = genetic.SerializeToString()
    except Exception as exc:
        _log('genetics not updated (body type {}): {!r}'.format(int(body_type), exc))
    return changed


def _set_physique(sim_info, look):
    values = (sim_info.physique or '').split(',')
    while len(values) < 4:
        values.append('0')
    values[PHYSIQUE_HEAVY] = '{:.3f}'.format(look['heavy'])
    values[PHYSIQUE_LEAN] = '{:.3f}'.format(look['lean'])
    values[PHYSIQUE_FIT] = '{:.3f}'.format(look['fit'])
    values[PHYSIQUE_BONY] = '{:.3f}'.format(look['bony'])
    sim_info.physique = ','.join(values)


def _usable_tones(engine):
    official_only = engine.settings.get('official_only', True)
    excluded = set(engine.settings.get('excluded_tones', ()))
    return [(t['id'], t['L']) for t in _Data.tones
            if t['human'] and t['id'] not in excluded and not (official_only and t['cc'])]


def _eligible(sim_info):
    if sim_info.species != Species.HUMAN or sim_info.age not in SUPPORTED_AGES:
        return False
    occult = getattr(sim_info, 'occult_types', OccultType.HUMAN)
    return occult == OccultType.HUMAN


def apply_look(sim_info, look, rng, skin_mu=None):
    """Write a sampled look onto a Sim info: skin tone, body shape, hair, beard, eyebrows, eye colour."""
    engine = _load()
    tones = _usable_tones(engine)
    mu = look['skin_mu'] if skin_mu is None else skin_mu
    sim_info.skin_tone = engine.pick_tone(tones, mu, look['skin_sd'], rng)
    sim_info.skin_tone_val_shift = float(engine.settings.get('skin_val_shift', 0.0))
    _set_physique(sim_info, look)
    if sim_info.age != Age.ELDER:
        hair_tags = engine.data['hair_tags'][look['hair']]
        all_hair_colors = [t for tags in engine.data['hair_tags'].values() if isinstance(tags, list) for t in tags]
        hair = _pick_part(sim_info, BodyType.HAIR, hair_tags, HAIR_LENGTH_TAGS, rng,
                          required_tag_name=look.get('hair_texture'))
        if hair is not None:
            _replace_part_everywhere(sim_info, BodyType.HAIR, hair)
        look['hair_part'] = hair
        # Beard and eyebrows get the hair colour, only when the Sim already has a coloured one.
        for body_type in (BodyType.FACIAL_HAIR, BodyType.EYEBROWS):
            part = _pick_part(sim_info, body_type, hair_tags, (), rng, only_if_tagged=all_hair_colors)
            if part is not None:
                _replace_part_everywhere(sim_info, body_type, part)
    eyes = _pick_part(sim_info, BodyType.EYECOLOR, engine.data['eye_tags'][look['eyes']], (), rng)
    if eyes is not None:
        _replace_part_everywhere(sim_info, BodyType.EYECOLOR, eyes)
    look['eye_part'] = eyes
    return look


def apply_profile(sim_info, profile_name, rng):
    engine = _load()
    if not _eligible(sim_info):
        return None
    look = engine.sample(profile_name, sim_info.age.name, sim_info.gender.name, rng)
    return apply_look(sim_info, look, rng)


def apply_profile_to_household(sim_infos, profile_name, rng):
    """Apply a profile to Sims generated together: shared origin, children resembling an adult."""
    engine = _load()
    members = [s for s in sim_infos if _eligible(s)]
    if not members:
        return []
    looks = engine.sample_household(profile_name, [(s.age.name, s.gender.name) for s in members], rng)
    lightness = {t['id']: t['L'] for t in _Data.tones}
    for sim_info, look in zip(members, looks):
        if 'parents' not in look:
            apply_look(sim_info, look, rng)
    for sim_info, look in zip(members, looks):
        if 'parents' in look:
            parent_l = [lightness.get(members[i].skin_tone) for i in look['parents']]
            parent_l = [l for l in parent_l if l is not None]
            apply_look(sim_info, look, rng, skin_mu=sum(parent_l) / len(parent_l) if parent_l else None)
    return list(zip(members, looks))


# --- automatic mode: every Sim the game generates ------------------------------------------------

# Sims whose look the game overwrites or must keep: newborns, clones, relatives built from a
# parent's genetics, adoption and reincarnation copies, and this mod's own preview command.
SKIPPED_SOURCES = ('pregnancy', 'cloning', 'stayover relative', 'adoption', 'reincarnation', 'rsim_preview')


class _Hook:
    original = None
    suppressed = 0


def _after_create(sim_creators, sim_infos, creation_source):
    if _Hook.suppressed or not sim_infos:
        return
    source = str(creation_source or '')
    if any(source.startswith(s) for s in SKIPPED_SOURCES):
        return
    engine = _load()
    if not engine.settings.get('auto_apply', True):
        return
    profile = engine.settings.get('default_profile', 'europe')
    if profile not in engine.data['profiles']:
        return
    creators = list(sim_creators or ())
    generated = [s for i, s in enumerate(sim_infos)
                 if i >= len(creators) or getattr(creators[i], 'resource_key', None) is None]
    applied = apply_profile_to_household(generated, profile, random.Random())
    _debug('auto: profile {} applied to {} of {} Sims ({})'.format(profile, len(applied), len(sim_infos), source))


def _create_sim_infos(cls, sim_creators, *args, **kwargs):
    result = _Hook.original(cls, sim_creators, *args, **kwargs)
    try:
        _after_create(sim_creators, result[0] if result else None, kwargs.get('creation_source', ''))
    except Exception:
        _log('auto mode error:\n' + traceback.format_exc())
    return result


def _install_hook():
    if _Hook.original is None:
        _Hook.original = SimSpawner.create_sim_infos.__func__
        SimSpawner.create_sim_infos = classmethod(_create_sim_infos)


_install_hook()


# --- dating app and adoption: generate_random_siminfo -----------------------------------------

class _BaseAdapter:
    """View of a native BaseSimInfo exposing what apply_look needs (adoption and dating app Sims)."""

    occult_types = OccultType.HUMAN

    def __init__(self, base):
        self._base = base

    @property
    def id(self):
        return self._base.sim_id

    @property
    def age(self):
        return Age(self._base.age)

    @property
    def gender(self):
        return Gender(self._base.gender)

    @property
    def species(self):
        return Species.HUMAN if int(self._base.species) == int(Species.HUMAN) else None

    @property
    def skin_tone(self):
        return self._base.skin_tone

    @skin_tone.setter
    def skin_tone(self, value):
        self._base.skin_tone = value

    @property
    def skin_tone_val_shift(self):
        return self._base.skin_tone_val_shift

    @skin_tone_val_shift.setter
    def skin_tone_val_shift(self, value):
        self._base.skin_tone_val_shift = value

    @property
    def physique(self):
        return self._base.physique

    @physique.setter
    def physique(self, value):
        self._base.physique = value

    def save_outfits(self):
        outfits = Outfits_pb2.OutfitList()
        outfits.ParseFromString(self._base.outfits)
        return outfits

    def load_outfits(self, outfits):
        self._base.outfits = outfits.SerializeToString()


def _after_random_siminfo(base):
    if _Hook.suppressed:
        return
    engine = _load()
    if not engine.settings.get('auto_apply', True):
        return
    profile = engine.settings.get('default_profile', 'europe')
    if profile not in engine.data['profiles']:
        return
    sim = _BaseAdapter(base)
    if _eligible(sim):
        apply_profile(sim, profile, random.Random())
        _debug('auto: profile {} applied to random Sim info {}'.format(profile, sim.id))


def _wrap_random_siminfo(original):
    def generate_random_siminfo(base, *args, **kwargs):
        result = original(base, *args, **kwargs)
        try:
            _after_random_siminfo(base)
        except Exception:
            _log('auto mode error (random Sim info):\n' + traceback.format_exc())
        return result
    generate_random_siminfo.rsim_wrapped = True
    return generate_random_siminfo


def _install_random_siminfo_hooks():
    # Both modules import the native function by name, so each module attribute is wrapped.
    for module_name in ('adoption.adoption_service', 'services.matchmaking_service'):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        original = getattr(module, 'generate_random_siminfo', None)
        if original is not None and not getattr(original, 'rsim_wrapped', False):
            module.generate_random_siminfo = _wrap_random_siminfo(original)


_install_random_siminfo_hooks()


# --- rerolling Sims that already exist in the save --------------------------------------------

# Sims the game generated itself, and creation sources that must never be touched.
GENERATED_SOURCES = (SimInfoCreationSource.FILTER | SimInfoCreationSource.NEIGHBORHOOD_POPULATION_SERVICE
                     | SimInfoCreationSource.HOUSEHOLD_TEMPLATE)
PROTECTED_SOURCES = (SimInfoCreationSource.PRE_MADE | SimInfoCreationSource.CAS_INITIAL
                     | SimInfoCreationSource.CAS_REENTRY | SimInfoCreationSource.GALLERY
                     | SimInfoCreationSource.PREGNANCY | SimInfoCreationSource.ADOPTION
                     | SimInfoCreationSource.CLONED)


def _creation_flags(sim_info):
    source = getattr(sim_info, 'creation_source', None)
    source = getattr(source, 'creation_source', source)  # SimInfoCreationSourceData wraps the flags
    try:
        return int(source)
    except (TypeError, ValueError):
        return 0


def _rerollable_households():
    active = services.active_household()
    households = []
    for household in services.household_manager().values():
        if household is active or household.is_played_household:
            continue
        members = list(household)
        if not members:
            continue
        flags = [_creation_flags(s) for s in members]
        # Whole household or nothing: a single hand-made, born or adopted member protects it.
        if any(f & PROTECTED_SOURCES or not f & GENERATED_SOURCES for f in flags):
            continue
        if any(s.last_name in (LAST_NAME,) + LEGACY_LAST_NAMES for s in members):
            continue
        households.append((household, members))
    return households


@sims4.commands.Command('rsim.reroll', command_type=sims4.commands.CommandType.Live)
def rsim_reroll(confirm: str = '', profile: str = 'default', _connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        engine = _load()
    except Exception as exc:
        output('rsim: cannot read the JSON files ({!r})'.format(exc))
        return False
    if profile == 'default':
        profile = engine.settings.get('default_profile', 'europe')
    if profile not in engine.data['profiles']:
        output('rsim: unknown profile {} ({})'.format(profile, ', '.join(engine.profiles())))
        return False
    households = _rerollable_households()
    sims = sum(len(m) for _, m in households)
    if confirm.lower() != 'confirm':
        output('rsim: {} generated households ({} Sims) can be rerolled with profile {}. '
               'Played households, hand-made, premade, born and adopted Sims are left alone. '
               'Save first, then run: rsim.reroll confirm'.format(len(households), sims, profile))
        return True
    rng = random.Random()
    changed = errors = 0
    _log('# reroll {}: {} households, profile {}'.format(time.strftime('%Y-%m-%d %H:%M:%S'), len(households), profile))
    for household, members in households:
        try:
            for sim_info, look in apply_profile_to_household(members, profile, rng):
                sim_info.resend_physical_attributes()
                changed += 1
        except Exception:
            errors += 1
            _log('reroll error on household {}:\n{}'.format(household.id, traceback.format_exc()))
    output('rsim: {} Sims rerolled with profile {}, {} errors (details in {})'.format(changed, profile, errors, LOG_NAME))
    return True


# --- commands ---------------------------------------------------------------------------------

def _freeze(sim_info):
    """Disable autonomy so the preview Sim stays put and never uses objects on the lot."""
    sim = sim_info.get_sim_instance(allow_hidden_flags=ALL_HIDDEN_REASONS)
    if sim is None:
        return False
    sim.autonomy_settings.set_setting(AutonomyState.DISABLED, sim.get_autonomy_settings_group())
    return True


def _preview_sim_infos():
    names = (LAST_NAME,) + LEGACY_LAST_NAMES
    return [s for s in services.sim_info_manager().values() if s.last_name in names]


def _grid_positions(origin, count):
    for k in range(count):
        row, col = divmod(k, COLUMNS)
        x = origin.x + (col - (COLUMNS - 1) / 2) * SPACING
        z = origin.z + 2.5 + row * SPACING
        yield sims4.math.Vector3(x, terrain.get_terrain_height(x, z), z)


@sims4.commands.Command('rsim.spawn', command_type=sims4.commands.CommandType.Live)
def rsim_spawn(count: int = 35, age: str = 'adult', profile: str = 'default', _connection=None):
    output = sims4.commands.CheatOutput(_connection)
    client = services.client_manager().get(_connection)
    active = client.active_sim if client is not None else None
    if active is None:
        output('rsim: no active Sim')
        return False
    age = age.lower()
    if age not in ('mix', 'family') and age not in AGES:
        output('rsim: unknown age {} (adult, young_adult, teen, child, elder, mix, family)'.format(age))
        return False
    profile = profile.lower()
    if profile not in ('vanilla', 'auto'):
        try:
            engine = _load()
        except Exception as exc:
            output('rsim: cannot read the JSON files ({!r})'.format(exc))
            return False
        if profile == 'default':
            profile = engine.settings.get('default_profile', 'europe')
        if profile not in engine.data['profiles']:
            output('rsim: unknown profile {} ({})'.format(profile, ', '.join(engine.profiles())))
            return False
    rng = random.Random()
    household_manager = services.household_manager()
    situation_manager = services.get_zone_situation_manager()
    _log('# batch {}: {} Sims, age {}, profile {}'.format(time.strftime('%Y-%m-%d %H:%M:%S'), count, age, profile))
    _log('sim_id\tage\tgender\tskin_tone\tphysique\torigin\thair\ttexture\teyes\tbmi\tstatus')
    if profile != 'auto':
        _Hook.suppressed += 1
    try:
        _spawn_batch(client, active, count, age, profile, rng, household_manager, situation_manager, output)
    finally:
        if profile != 'auto':
            _Hook.suppressed -= 1
    return True


FAMILY = (Age.ADULT, Age.ADULT, Age.CHILD, Age.TEEN)


def _spawn_batch(client, active, count, age, profile, rng, household_manager, situation_manager, output):
    spawned = errors = not_shown = 0
    positions = list(_grid_positions(active.position, count))
    # The automatic mode skips this mod's own source name; 'auto' uses a neutral one to go through it.
    source = 'rsim_test_auto' if profile == 'auto' else 'rsim_preview'
    while positions:
        if age == 'family':
            ages = FAMILY[:len(positions)]
        else:
            ages = (random.choice(MIX) if age == 'mix' else AGES[age],)
        try:
            household = household_manager.create_household(client.account)
            _debug('generating {} Sim(s) ({})'.format(len(ages), ', '.join(a.name for a in ages)))
            sim_infos, _ = SimSpawner.create_sim_infos(
                [SimCreator(age=a, last_name=LAST_NAME) for a in ages], household=household, account=client.account,
                zone_id=0, creation_source=source, household_change_origin=HouseholdChangeOrigin.CHEAT_SIMS_SPAWN)
        except Exception:
            errors += 1
            positions = positions[len(ages):]
            _log('generation error:\n' + traceback.format_exc())
            continue
        looks = {}
        if profile not in ('vanilla', 'auto'):
            _debug('applying profile {} to {} Sim(s)'.format(profile, len(sim_infos)))
            try:
                looks = {s.id: look for s, look in apply_profile_to_household(sim_infos, profile, rng)}
            except Exception:
                errors += 1
                _log('profile error:\n' + traceback.format_exc())
        for sim_info in sim_infos:
            if not positions:
                break
            position = positions.pop(0)
            shown = False
            try:
                situation_manager.add_debug_sim_id(sim_info.id)
                _debug('spawning {}'.format(sim_info.id))
                shown = bool(SimSpawner.spawn_sim(sim_info, position, is_debug=True))
                if shown:
                    _freeze(sim_info)
            except Exception:
                errors += 1
                _log('spawn error on {}:\n{}'.format(sim_info.id, traceback.format_exc()))
            if shown:
                spawned += 1
            else:
                not_shown += 1
            look = looks.get(sim_info.id) or {}
            _log('\t'.join(str(v) for v in (
                sim_info.id, sim_info.age.name, sim_info.gender.name, sim_info.skin_tone, sim_info.physique,
                look.get('origin', ''), look.get('hair', ''), look.get('hair_texture', ''), look.get('eyes', ''),
                look.get('bmi', look.get('bmi_class', '')), 'spawned' if shown else 'NOT SPAWNED')))
    output('rsim: {} Sims spawned, {} not spawned, {} errors, profile {} (details in {}; rsim.clear to remove)'.format(
        spawned, not_shown, errors, profile, LOG_NAME))
    return True


@sims4.commands.Command('rsim.freeze', command_type=sims4.commands.CommandType.Live)
def rsim_freeze(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    frozen = sum(1 for sim_info in _preview_sim_infos() if _freeze(sim_info))
    output('rsim: autonomy disabled on {} preview Sims'.format(frozen))
    return True


@sims4.commands.Command('rsim.profiles', command_type=sims4.commands.CommandType.Live)
def rsim_profiles(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        engine = _load()
    except Exception as exc:
        output('rsim: cannot read the JSON files ({!r})'.format(exc))
        return False
    default = engine.settings.get('default_profile')
    for name in engine.profiles():
        output('{}{}: {}'.format(name, ' (default)' if name == default else '', engine.data['profiles'][name].get('label', name)))
    return True


@sims4.commands.Command('rsim.clear', command_type=sims4.commands.CommandType.Live)
def rsim_clear(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    in_world = removed = 0
    for sim_info in _preview_sim_infos():
        sim = sim_info.get_sim_instance(allow_hidden_flags=ALL_HIDDEN_REASONS)
        if sim is not None:
            # The Sim info is only deleted once the Sim has actually left the world.
            sim.schedule_destroy_asap(post_delete_func=lambda *_, si=sim_info: _remove_sim_info(si),
                                      source=sim, cause='rsim.clear')
            in_world += 1
        else:
            _remove_sim_info(sim_info)
            removed += 1
    output('rsim: {} Sims deleted, {} being removed from the world'.format(removed, in_world))
    return True


def _remove_sim_info(sim_info):
    try:
        if services.sim_info_manager().get(sim_info.id) is None:
            return
        household = sim_info.household
        sim_info.remove_permanently()
        household_manager = services.household_manager()
        if household is not None and not len(household) and household_manager.get(household.id) is not None:
            household_manager.remove(household)
    except Exception:
        _log('delete error on {}:\n{}'.format(sim_info.id, traceback.format_exc()))
