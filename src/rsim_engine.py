"""Samples a realistic look from the JSON profiles (no game dependency, testable outside the game).

Sampling order: origin -> hair -> eyes given hair -> skin lightness -> BMI -> body shape.
"""
import math

HAIR = ('red', 'blond', 'light_brown', 'dark_brown', 'black')
EYES = ('blue', 'inter', 'brown')
YOUTH_AGES = ('CHILD', 'TEEN')
UNDER_30 = ('CHILD', 'TEEN', 'YOUNGADULT')


def _weighted(rng, weights):
    items = [(k, float(v)) for k, v in weights.items() if not k.startswith('_') and float(v) > 0]
    total = sum(v for _, v in items)
    x = rng.random() * total
    for k, v in items:
        x -= v
        if x < 0:
            return k
    return items[-1][0]


def _norm(d, keys):
    vals = [max(0.0, float(d.get(k, 0))) for k in keys]
    s = sum(vals) or 1.0
    return [v / s for v in vals]


def _ipf(hair_p, eye_target, cond):
    """Joint hair x eye table: starts from P(hair)·P(eyes|hair), then fitted to the country's eye marginals."""
    joint = [[hair_p[i] * cond[i][j] for j in range(3)] for i in range(len(HAIR))]
    for _ in range(50):
        for j in range(3):
            col = sum(joint[i][j] for i in range(len(HAIR)))
            if col > 0:
                f = eye_target[j] / col
                for i in range(len(HAIR)):
                    joint[i][j] *= f
        for i in range(len(HAIR)):
            row = sum(joint[i])
            if row > 0:
                f = hair_p[i] / row
                joint[i] = [x * f for x in joint[i]]
    return [[x / (sum(r) or 1.0) for x in r] for r in joint]


class Engine:
    def __init__(self, data):
        self.data = data
        self.settings = data['settings']
        cond_src = data['european_eyes_given_hair']
        self._cond = [_norm(dict(zip(EYES, cond_src[h])), EYES) for h in HAIR]
        self._eu_cache = {}

    def profiles(self):
        return sorted(self.data['profiles'])

    # --- origin -------------------------------------------------------------------------
    def _origin(self, profile, age, rng):
        weights = {k: float(v) for k, v in profile['origins'].items() if not k.startswith('_')}
        if age in UNDER_30:
            boost = float(self.settings.get('minority_boost_under_30', 1.0))
            weights = {k: (v if k == 'european' else v * boost) for k, v in weights.items()}
        return _weighted(rng, weights)

    # --- hair and eyes -----------------------------------------------------------------
    def _hair_weights(self, base, age):
        w = {k: float(base.get(k, 0)) for k in HAIR}
        boost = {'CHILD': self.settings.get('child_blond_boost', 1.0),
                 'TEEN': self.settings.get('teen_blond_boost', 1.0)}.get(age, 1.0)
        if boost != 1.0 and w['blond'] > 0:
            extra = w['blond'] * (boost - 1.0)
            taken = min(extra, w['light_brown'])
            w['blond'] += taken
            w['light_brown'] -= taken
        return w

    def _european_traits(self, profile_name, eu, age, gender, rng):
        hair_w = self._hair_weights(eu['hair'], age)
        hair = _weighted(rng, hair_w)
        key = (profile_name, gender, age)
        if key not in self._eu_cache:
            eyes_target = _norm(eu['eyes_F' if gender == 'FEMALE' else 'eyes_M'], EYES)
            table = _ipf(_norm(hair_w, HAIR), eyes_target, self._cond)
            # Redheads keep the measured eye distribution (MC1R, barely country dependent): no refitting.
            table[HAIR.index('red')] = self._cond[HAIR.index('red')]
            self._eu_cache[key] = table
        row = self._eu_cache[key][HAIR.index(hair)]
        eyes = _weighted(rng, dict(zip(EYES, row)))
        return hair, eyes

    def _group_traits(self, group, age, rng):
        hair = _weighted(rng, self._hair_weights(group['hair'], age))
        eyes = _weighted(rng, group['eyes'])
        return hair, eyes

    def _group(self, name):
        return self.data['groups'][name]

    # --- body shape ----------------------------------------------------------------------
    def _bmi(self, profile, age, gender, rng):
        table = profile['bmi']['F' if gender == 'FEMALE' else 'M']
        probs = table.get(age) or table['ADULT']
        k = _weighted(rng, {str(i): p for i, p in enumerate(probs)})
        lo, hi = self.data['bmi_classes']['bounds'][int(k)]
        return lo + (hi - lo) * rng.random()

    def _silhouette(self, bmi, gender):
        means = self.data['silhouettes']['F' if gender == 'FEMALE' else 'M']
        if bmi <= means[0]:
            return 1.0 - (means[0] - bmi) / (means[1] - means[0])
        for s in range(1, len(means)):
            if bmi <= means[s]:
                return s + (bmi - means[s - 1]) / (means[s] - means[s - 1])
        return float(len(means))

    def _fat_axis(self, silhouette):
        neutral = float(self.settings.get('neutral_silhouette', 3.5))
        if silhouette >= neutral:
            return min(1.0, (silhouette - neutral) / (9.0 - neutral)), 0.0
        return 0.0, min(1.0, (neutral - silhouette) / (neutral - 0.0))

    def _youth_fat(self, profile, age, gender, rng):
        ow, ob, thin = profile['youth']['{}_{}'.format(age, 'F' if gender == 'FEMALE' else 'M')]
        x = rng.random() * 100
        if x < ob:
            return 0.5 + 0.35 * rng.random(), 0.0, 'obese'
        if x < ow:
            return 0.2 + 0.25 * rng.random(), 0.0, 'overweight'
        if x < ow + thin:
            return 0.0, 0.3 + 0.3 * rng.random(), 'thin'
        return 0.08 * rng.random(), 0.12 * rng.random(), 'normal'

    def _muscle(self, profile, age, gender, heavy, rng):
        p = float(profile['muscle']['p'])
        p += 0.05 if gender == 'MALE' else -0.03
        p *= {'YOUNGADULT': 1.4, 'ADULT': 1.0, 'ELDER': 0.5, 'TEEN': 0.8, 'CHILD': 0.0}.get(age, 1.0)
        if rng.random() < p:
            return (0.25 + 0.45 * rng.random()) * (1.0 - 0.6 * heavy), 0.0
        if heavy < 0.15 and rng.random() < 0.3:
            return 0.0, 0.3 * rng.random()
        return 0.1 * rng.random() * (1.0 - heavy), 0.0

    # --- full sample ------------------------------------------------------------------
    def sample(self, profile_name, age, gender, rng, origin=None):
        """age: Age enum name (CHILD, TEEN, YOUNGADULT, ADULT, ELDER); gender: MALE / FEMALE."""
        profile = self.data['profiles'][profile_name]
        if origin is None:
            origin = self._origin(profile, age, rng)
        sd_default = float(self.settings.get('skin_sd_default', 5.0))
        if origin == 'mixed':
            others = {k: v for k, v in profile['origins'].items() if not k.startswith('_') and k not in ('european', 'mixed')}
            other = _weighted(rng, others)
            parents = ['european', other]
            source = parents[int(rng.random() * 2)]
            mu_eu = profile['european']['skin_L'][0]
            mu_other = self._group(other)['skin_L'][0]
            skin_mu, skin_sd = (mu_eu + mu_other) / 2.0, 8.0
        else:
            source = origin
            if origin == 'european':
                skin_mu, skin_sd = profile['european']['skin_L']
            else:
                skin_mu, skin_sd = self._group(origin)['skin_L']
        if source == 'european':
            hair, eyes = self._european_traits(profile_name, profile['european'], age, gender, rng)
        else:
            hair, eyes = self._group_traits(self._group(source), age, rng)
        if hair == 'red':
            skin_mu += float(self.settings.get('redhead_skin_shift', 4.0))
            skin_sd = min(skin_sd, 3.0)
        result = {'origin': origin, 'hair': hair, 'eyes': eyes, 'skin_mu': skin_mu, 'skin_sd': skin_sd or sd_default,
                  'archetype': self.archetype(source)}
        textures = self.data.get('hair_texture', {}).get(source)
        if textures:
            result['hair_texture'] = _weighted(rng, textures)
        self._add_body(result, profile, age, gender, rng)
        return result

    def _add_body(self, result, profile, age, gender, rng):
        if age in YOUTH_AGES:
            heavy, lean, cls = self._youth_fat(profile, age, gender, rng)
            result['bmi_class'] = cls
        else:
            bmi = self._bmi(profile, age, gender, rng)
            result['bmi'] = round(bmi, 1)
            heavy, lean = self._fat_axis(self._silhouette(bmi, gender))
        fit, bony = self._muscle(profile, age, gender, heavy, rng)
        result.update(heavy=round(heavy, 3), lean=round(lean, 3), fit=round(fit, 3), bony=round(bony, 3))

    def sample_household(self, profile_name, members, rng):
        """members: list of (age, gender). Adults share one origin; children inherit from a random adult.

        Children get 'parents' (indexes of the adults) so the caller can blend the skin tones it picked.
        """
        profile = self.data['profiles'][profile_name]
        adults = [i for i, (age, _) in enumerate(members) if age not in YOUTH_AGES]
        origin = self._origin(profile, 'ADULT', rng) if adults else None
        looks = [None] * len(members)
        for i in adults:
            age, gender = members[i]
            looks[i] = self.sample(profile_name, age, gender, rng, origin=origin)
        for i, (age, gender) in enumerate(members):
            if looks[i] is not None:
                continue
            if not adults:
                looks[i] = self.sample(profile_name, age, gender, rng)
                continue
            parent = looks[adults[int(rng.random() * len(adults))]]
            other = looks[adults[int(rng.random() * len(adults))]]
            hair = parent['hair']
            boost = {'CHILD': self.settings.get('child_blond_boost', 1.0),
                     'TEEN': self.settings.get('teen_blond_boost', 1.0)}.get(age, 1.0)
            if hair == 'light_brown' and rng.random() < (boost - 1.0) * 0.3:
                hair = 'blond'
            look = {'origin': parent['origin'], 'hair': hair, 'eyes': other['eyes'],
                    'skin_mu': parent['skin_mu'], 'skin_sd': 2.0, 'parents': adults,
                    'archetype': parent.get('archetype')}
            if 'hair_texture' in parent:
                look['hair_texture'] = parent['hair_texture']
            self._add_body(look, profile, age, gender, rng)
            looks[i] = look
        return looks

    def archetype(self, group):
        """EA archetype tag matching an origin group (None when the data has no mapping)."""
        return self.data.get('group_archetypes', {}).get(group)

    def pick_tone(self, tones, mu, sd, rng, archetype=None):
        """tones: list of (id, L, archetypes). Weighted pick, Gaussian weights centred on mu.

        Lightness comes from the population data; among tones of similar lightness, those EA tagged
        for the Sim's archetype are favoured: the tags carry the undertone (pale or golden) that L* lacks.
        """
        boost = float(self.settings.get('archetype_tone_boost', 1.0))
        weights = {}
        for tone in tones:
            tone_id, lightness = tone[0], tone[1]
            w = math.exp(-((lightness - mu) ** 2) / (2.0 * sd * sd))
            if archetype and len(tone) > 2 and archetype in tone[2]:
                w *= boost
            if w > 1e-6:
                weights[str(tone_id)] = w
        if not weights:
            best = min(tones, key=lambda t: abs(t[1] - mu))
            return best[0]
        return int(_weighted(rng, weights))
