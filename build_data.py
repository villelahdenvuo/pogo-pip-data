#!/usr/bin/env python3
"""Builds v1/pokedata.json, Trainer Implant's game data, from PvPoke and the game master.

Usage: python3 build_data.py [--cache DIR] [--out FILE]

Sources (fetched over HTTPS unless already in the cache dir):
  - PvPoke gamemaster.min.json and overall rankings for Great/Ultra/Master
  - PokeMiners latest game master (base height/weight and their spreads)
  - PokémonDB GO Pokédex (each species' XXL height ceiling, for showcase scores)
"""
import argparse
import http.client
import json
import math
import os
import time
import urllib.request

PVPOKE = "https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data"
SOURCES = {
    "gm.json": f"{PVPOKE}/gamemaster.min.json",
    "r1500.json": f"{PVPOKE}/rankings/all/overall/rankings-1500.json",
    "r2500.json": f"{PVPOKE}/rankings/all/overall/rankings-2500.json",
    "r10000.json": f"{PVPOKE}/rankings/all/overall/rankings-10000.json",
    "gmpm.json": "https://raw.githubusercontent.com/PokeMiners/game_masters/master/latest/latest.json",
    # Per-species showcase size ceiling (sizescale = max XXL height); the game master doesn't carry it.
    "pdb.json": "https://pokemondb.net/json/go-pokedex",
    # Names for every national dex number, including ones not in GO yet.
    "species.json": "https://pokeapi.co/api/v2/pokemon-species?limit=1100",
}
XXL_CLASSES = (1.55, 1.75, 2.0)
LEAGUES = {"great": 1500, "ultra": 2500, "master": 10000}
FORM_TOKENS = {"alolan": "ALOLA", "galarian": "GALARIAN", "hisuian": "HISUIAN", "paldean": "PALDEA",
               # Minior's core comes in colours, all the same size; its plain form is the Meteor.
               "core": "GREEN"}
# Out in GO while PvPoke still has them unreleased: id -> the name the game shows.
RELEASED = {"minior_core": "Minior"}
SHADOW_ATK, SHADOW_DEF = 1.2, 0.8333333
# Movesets per species and type beyond its best, for rating one with the moves it knows.
MORE_SETS = 2
# The file's shape. The app reads v<FORMAT>/ and only one it knows; a change old apps can't read bumps it.
FORMAT = 1


def load(cache, name):
    path = os.path.join(cache, name)
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        print("fetching", SOURCES[name])
        req = urllib.request.Request(SOURCES[name], headers={"User-Agent": "Mozilla/5.0"})
        # A cut-off download is retried, and never left behind as a half file in the cache.
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = resp.read()
                break
            except (OSError, http.client.HTTPException):
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        with open(path + ".part", "wb") as f:
            f.write(body)
        os.replace(path + ".part", path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def size_table(gmpm):
    """dex -> list of (form, heightM, weightKg, heightStd)."""
    out = {}
    for entry in gmpm:
        ps = entry.get("data", {}).get("pokemonSettings")
        tid = entry.get("templateId", "")
        if not ps or not tid.startswith("V") or "pokedexHeightM" not in ps:
            continue
        dex = int(tid[1:5])
        out.setdefault(dex, []).append(
            (ps.get("form") or "", ps["pokedexHeightM"], ps["pokedexWeightKg"], ps.get("heightStdDev"),
             ps.get("weightStdDev"))
        )
    return out


def find_size(sizes, dex, species_id):
    rows = sizes.get(dex)
    if not rows:
        return None
    tokens = species_id.replace("_shadow", "").split("_")[1:]
    if "mega" in tokens or "primal" in tokens:
        return None
    for t in tokens:
        want = FORM_TOKENS.get(t, t.upper())
        for form, *rest in rows:
            if form.endswith("_" + want):
                return rest
    for form, *rest in rows:
        if form == "" or form.endswith("_NORMAL"):
            return rest
    return rows[0][1:]


def xxl_table(pdb):
    """(dex, formalias) -> XXL ceiling as a multiple of average height: 1.55, 1.75 or 2.0."""
    out = {}
    for e in (pdb.values() if isinstance(pdb, dict) else pdb):
        try:
            ratio = float(e["sizescale"]) / float(e["height"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        out[(int(e["national"]), e.get("formalias") or "")] = min(XXL_CLASSES, key=lambda c: abs(c - ratio))
    return out


def find_xxl(xxl, dex, species_id):
    tokens = species_id.replace("_shadow", "").split("_")[1:]
    for t in tokens:
        if (dex, t) in xxl:
            return xxl[(dex, t)]
    if (dex, "") in xxl:
        return xxl[(dex, "")]
    # Species that only come in named forms (East/West Sea Gastrodon, Vivillon patterns): the forms
    # share a size class, so any will do; when they don't agree, the most common one.
    forms = [v for (d, _), v in xxl.items() if d == dex]
    return max(set(forms), key=forms.count) if forms else None


def traits(stats, shadow, moveset, moves, cp):
    """Subset of PvPoke's generateTraits() that only needs ranking data."""
    pros, cons = [], []
    atk = stats["atk"] * (SHADOW_ATK if shadow else 1)
    bulk = stats["def"] * stats["hp"] * (SHADOW_DEF if shadow else 1)
    scale = {1500: [12500, 14000, 17000, 23000], 2500: [19000, 22000, 25000, 31000],
             10000: [27000, 30000, 35000, 39000]}[cp]
    if bulk <= scale[0]:
        cons.append("Glass Cannon" if atk * atk > bulk else "Glassy")
    elif bulk <= scale[1]:
        cons.append("Less Bulky")
    elif bulk >= scale[3]:
        pros.append("Extremely Bulky")
    elif bulk >= scale[2]:
        pros.append("Bulky")
    fast = moves.get(moveset[0]) if moveset else None
    charged = [moves[m] for m in moveset[1:] if m in moves]
    if fast and charged and fast.get("energyGain"):
        cheapest = min(c["energy"] for c in charged)
        speed = math.ceil(cheapest * 2 / fast["energyGain"]) * fast["cooldown"] / 1000
        if speed <= 12:
            pros.append("Spammy")
        elif speed >= 19:
            cons.append("Slow")
        if fast["cooldown"] == 500:
            pros.append("Agile")
        elif fast["cooldown"] >= 2000:
            cons.append("Clumsy")
    return pros, cons


ITEM_NAMES = {
    "ITEM_SUN_STONE": "Sun Stone", "ITEM_KINGS_ROCK": "King's Rock", "ITEM_METAL_COAT": "Metal Coat",
    "ITEM_DRAGON_SCALE": "Dragon Scale", "ITEM_UP_GRADE": "Up-Grade", "ITEM_GEN4_EVOLUTION_STONE": "Sinnoh Stone",
    "ITEM_GEN5_EVOLUTION_STONE": "Unova Stone", "ITEM_OTHER_EVOLUTION_STONE_A": "Magnetic Lure",
    "ITEM_TROY_DISK_MOSSY": "Mossy Lure", "ITEM_TROY_DISK_GLACIAL": "Glacial Lure",
    "ITEM_TROY_DISK_MAGNETIC": "Magnetic Lure", "ITEM_TROY_DISK_RAINY": "Rainy Lure",
    "ITEM_TROY_DISK_SPARKLY": "Golden Lure",
}


def item_name(item):
    return ITEM_NAMES.get(item, item.replace("ITEM_", "").replace("_", " ").title())


def pretty(slug):
    special = {"mr-mime": "Mr. Mime", "mime-jr": "Mime Jr.", "mr-rime": "Mr. Rime", "type-null": "Type: Null",
               "ho-oh": "Ho-Oh", "porygon-z": "Porygon-Z", "farfetchd": "Farfetch'd", "sirfetchd": "Sirfetch'd",
               "nidoran-f": "Nidoran♀", "nidoran-m": "Nidoran♂", "flabebe": "Flabébé"}
    return special.get(slug, " ".join(w.capitalize() for w in slug.split("-")))


def how_to_evolve(parent_name, branch):
    parts = [f"Evolve {parent_name}"]
    if branch.get("candyCost"):
        parts.append(f"{branch['candyCost']} candy")
    if branch.get("evolutionItemRequirement"):
        parts.append(item_name(branch["evolutionItemRequirement"]))
    if branch.get("lureItemRequirement"):
        parts.append("at a " + item_name(branch["lureItemRequirement"]))
    if branch.get("kmBuddyDistanceRequirement"):
        parts.append(f"walk {branch['kmBuddyDistanceRequirement']:g} km as buddy")
    elif branch.get("mustBeBuddy"):
        parts.append("as buddy")
    if branch.get("onlyDaytime"):
        parts.append("by day")
    if branch.get("onlyNighttime"):
        parts.append("at night")
    if branch.get("genderRequirement"):
        parts.append(branch["genderRequirement"].replace("GENDER_", "").lower() + " only")
    if branch.get("questDisplay"):
        parts.append("evolution quest")
    if branch.get("noCandyCostViaTrade"):
        parts.append("free if traded")
    return " · ".join(parts)


TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]


def raid_table(gm, gmpm, top=30):
    """Raid attackers per type, by the community's estimated rating: ER = (DPS^3 x TDO)^(1/4).

    Level 40, 15/15/15, against a neutral boss with 180 defense that's weak to the type (x1.6 for
    moves of that type), taking 900/defense damage per second. Shadows get x1.2 attack, x5/6
    defense. The comprehensive-DPS formula is GamePress's: fast/charged cycle plus energy from
    damage taken. Return and Frustration are left out.
    """
    pve = {}
    for e in gmpm:
        ms = e.get("data", {}).get("moveSettings")
        if not ms or "_MOVE_" not in e.get("templateId", ""):
            continue
        pve[str(ms["movementId"])] = {
            "type": ms.get("pokemonType", "").replace("POKEMON_TYPE_", "").lower(),
            "power": ms.get("power", 0.0),
            "dur": ms.get("durationMs", 1000) / 1000.0,
            "energy": ms.get("energyDelta", 0),
        }

    def move(mid):
        return pve.get(mid) or pve.get(mid + "_FAST")

    cpm40 = 0.7903001
    out = {t: [] for t in TYPES}
    for p in gm["pokemon"]:
        if not p.get("released"):
            continue
        sid = p["speciesId"]
        shadow = sid.endswith("_shadow")
        b = p["baseStats"]
        atk = (b["atk"] + 15) * cpm40 * (1.2 if shadow else 1.0)
        dfn = (b["def"] + 15) * cpm40 * (5 / 6 if shadow else 1.0)
        hp = int((b["hp"] + 15) * cpm40)
        y = 900.0 / dfn
        types = p.get("types", [])
        elite = set(p.get("eliteMoves", []))
        fasts = [m for m in p.get("fastMoves", []) if move(m)]
        charged = [m for m in p.get("chargedMoves", []) if move(m) and m not in ("RETURN", "FRUSTRATION")]
        for t in TYPES:
            best = None
            sets = []
            for c in charged:
                cm = move(c)
                if cm["type"] != t:
                    continue
                for f in fasts:
                    fm = move(f)
                    def dmg(m):
                        stab = 1.2 if m["type"] in types else 1.0
                        eff = 1.6 if m["type"] == t else 1.0
                        return 0.5 * m["power"] * (atk / 180.0) * stab * eff + 0.5
                    fd, fe, fdur = dmg(fm), max(fm["energy"], 0.01), fm["dur"]
                    cd, ce, cdur = dmg(cm), max(-cm["energy"], 1), cm["dur"]
                    fdps, feps, cdps, ceps = fd / fdur, fe / fdur, cd / cdur, ce / cdur
                    dps0 = (fdps * ceps + cdps * feps) / (ceps + feps)
                    x = 0.5 * ce + 0.5 * fe
                    dps = dps0 + (cdps - fdps) / (ceps + feps) * (0.5 - x / hp) * y
                    dps = max(dps, fdps)
                    tdo = dps * hp / y
                    er = (dps ** 3 * tdo) ** 0.25
                    sets.append((round(er, 2), f, c))
                    if best is None or er > best["er"]:
                        best = {"id": sid, "name": p["speciesName"], "f": f, "c": c, "er": round(er, 2),
                                "dps": round(dps, 2), "tdo": round(tdo, 1)}
                        if f in elite or c in elite:
                            best["elite"] = 1
            if best:
                # The next best movesets too, for rating one of yours with the moves it knows.
                best["more"] = [{"f": f, "c": c, "er": er} for er, f, c in sorted(sets, key=lambda x: (-x[0], x[1], x[2]))
                                if (f, c) != (best["f"], best["c"])][:MORE_SETS]
                out[t].append(best)
    # Every species' best moveset per type, for rating your own at their real level and IVs:
    # {speciesId: [{t, f, c, er}, ...]}, only types where it's at least half the best.
    best = {t: max((r["er"] for r in out[t]), default=1) for t in TYPES}
    every = {}
    for t in TYPES:
        for r in out[t]:
            if r["er"] >= best[t] * 0.5:
                # Best first per type: apps that read one moveset take the first.
                every.setdefault(r["id"], []).append({"t": t, "f": r["f"], "c": r["c"], "er": r["er"]})
                every[r["id"]] += [{"t": t, **m} for m in r["more"] if m["er"] >= best[t] * 0.25]
    for t in TYPES:
        for r in out[t]:
            del r["more"]
        out[t] = sorted(out[t], key=lambda r: -r["er"])[:top]
    return out, every


EXTRA_DMAX = {"zacian_hero", "zamazenta_hero"}


def max_table(gm, gmpm):
    """Pokémon that can Dynamax (and Gigantamax) per the game master, with what Max battles use:
    level-40 hundo stats, types, and fast moves with their types (a Max Attack takes the fast
    move's type). G-Max ones are marked; their Max moves hit harder."""
    e = next(x for x in gmpm if x["templateId"] == "BREAD_POKEMON_SCALING_SETTINGS")
    dmax, gmax = set(), set()
    for v in e["data"]["breadPokemonScalingSettings"]["visualSettings"]:
        for f in v.get("pokemonFormData", [{}]):
            form = f.get("pokemonForm", v["pokemonId"]).lower().replace("_normal", "")
            for vd in f.get("visualData", []):
                mode = vd.get("breadMode")
                if mode == "BREAD_MODE":
                    dmax.add(form)
                elif mode == "BREAD_DOUGH_MODE":
                    gmax.add(form)
    # Newer ones are marked per species instead, in their extended settings.
    for x in gmpm:
        ext = x.get("data", {}).get("pokemonExtendedSettings")
        if not ext:
            continue
        form = (ext.get("form") or ext["uniqueId"]).lower().replace("_normal", "")
        for o in ext.get("breadOverrides", []):
            if o.get("breadMode") == "BREAD_MODE":
                dmax.add(form)
            elif o.get("breadMode") == "BREAD_DOUGH_MODE":
                gmax.add(form)
    ptype = {}
    for x in gmpm:
        ms = x.get("data", {}).get("moveSettings")
        if ms and "_MOVE_" in x.get("templateId", ""):
            ptype[str(ms["movementId"])] = ms.get("pokemonType", "").replace("POKEMON_TYPE_", "").lower()
    cpm40 = 0.7903001
    rows = []
    for p in gm["pokemon"]:
        sid = p["speciesId"]
        # The scaling list only has those that needed their own model scale; add known others.
        if not p.get("released") or (sid not in dmax and sid not in gmax and sid not in EXTRA_DMAX):
            continue
        b = p["baseStats"]
        fast = []
        for m in p.get("fastMoves", []):
            t = ptype.get(m) or ptype.get(m + "_FAST")
            if t:
                fast.append([m, t])
        # The types it can hit with, fast and charged: as a boss, what a tank has to take.
        hits = sorted({t for m in p.get("fastMoves", []) + p.get("chargedMoves", []) for t in [ptype.get(m) or ptype.get(m + "_FAST")] if t})
        rows.append({
            "id": sid, "name": p["speciesName"], "types": p.get("types", []),
            "atk": round((b["atk"] + 15) * cpm40, 2), "def": round((b["def"] + 15) * cpm40, 2),
            "hp": int((b["hp"] + 15) * cpm40), "fast": fast, "gmax": 1 if sid in gmax else 0, "hits": hits,
        })
    return rows


def dex_table(pdb, gmpm, species):
    """Every national dex number: name, in GO or not, class, and how to get it by evolving."""
    # In GO when any regular form is: some species' default form has a name of its own
    # (Deoxys "normal", Shaymin "land", Hoopa "confined"). Megas and Gigantamax don't count.
    in_go = {}
    for e in (pdb.values() if isinstance(pdb, dict) else pdb):
        alias = e.get("formalias") or ""
        if alias in ("mega", "mega-x", "mega-y", "gmax", "gigantamax") or alias.startswith("mega"):
            continue
        n = int(e["national"])
        if not alias or n not in in_go:
            in_go[n] = e["name"]
    names = {i + 1: pretty(r["name"]) for i, r in enumerate(species["results"])}
    by_id, cls = {}, {}
    settings = []
    for entry in gmpm:
        ps = entry.get("data", {}).get("pokemonSettings")
        tid = entry.get("templateId", "")
        if not ps or not tid.startswith("V"):
            continue
        dex = int(tid[1:5])
        by_id.setdefault(ps["pokemonId"], dex)
        if ps.get("pokemonClass"):
            cls[dex] = ps["pokemonClass"].replace("POKEMON_CLASS_", "").lower()
        settings.append((dex, ps))
    evolves = {}
    for dex, ps in settings:
        for b in ps.get("evolutionBranch", []):
            target = by_id.get(b.get("evolution"))
            if target and target not in evolves:
                evolves[target] = (dex, b)
    rows = []
    for n in range(1, len(names) + 1):
        row = {"n": n, "name": in_go.get(n, names[n])}
        if n not in in_go:
            row["out"] = 1
        if n in cls:
            row["cls"] = cls[n]
        if n in evolves:
            pdex, b = evolves[n]
            row["from"] = pdex
            row["how"] = how_to_evolve(in_go.get(pdex, names.get(pdex, str(pdex))), b)
        rows.append(row)
    return rows


def short_note(text, limit=140):
    if not text:
        return None
    first = text.split(". ")[0].strip()
    if not first.endswith("."):
        first += "."
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(__file__), ".cache"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), f"v{FORMAT}", "pokedata.json"))
    args = ap.parse_args()

    gm = load(args.cache, "gm.json")
    for p in gm["pokemon"]:
        if p["speciesId"] in RELEASED:
            p["released"], p["speciesName"] = True, RELEASED[p["speciesId"]]
    sizes = size_table(load(args.cache, "gmpm.json"))
    xxl = xxl_table(load(args.cache, "pdb.json"))
    moves = {m["moveId"]: m for m in gm["moves"]}

    species = []
    for p in gm["pokemon"]:
        if not p.get("released"):
            continue
        sid = p["speciesId"]
        tags = p.get("tags", [])
        if "mega" in tags or sid.endswith("_mega") or "_mega_" in sid or "primal" in sid:
            continue
        fam = p.get("family", {})
        legacy = list(p.get("legacyMoves", []))
        if "shadoweligible" in tags:
            legacy.append("RETURN")
        if sid.endswith("_shadow"):
            legacy.append("FRUSTRATION")
        elite = set(p.get("eliteMoves", []))
        entry = {
            "id": sid,
            "name": p["speciesName"],
            "dex": p["dex"],
            "atk": p["baseStats"]["atk"],
            "def": p["baseStats"]["def"],
            "hp": p["baseStats"]["hp"],
            "elite": sorted(elite),
            "noTm": sorted(set(m for m in legacy if m not in elite)),
            # Read off the detail screen to tell apart forms with identical stats (Alolan Muk).
            "types": [t for t in p["types"] if t != "none"],
        }
        if sid in RELEASED:
            # Not in PvPoke's rankings yet: the PiP says so rather than calling it unranked.
            entry["unrated"] = 1
        if fam.get("parent"):
            entry["parent"] = fam["parent"]
        if fam.get("evolutions"):
            entry["evolutions"] = fam["evolutions"]
        size = find_size(sizes, p["dex"], sid)
        if size:
            entry["h"], entry["w"] = size[0], size[1]
            if size[2]:
                entry["hsd"] = size[2]
            if size[3]:
                entry["wsd"] = size[3]
        ceiling = find_xxl(xxl, p["dex"], sid)
        if ceiling:
            entry["xxl"] = ceiling
        species.append(entry)

    known = {s["id"] for s in species}
    # Moves a TM can't teach (Elite TM or legacy): the toggle offers the best one that it can.
    special = {s["id"]: set(s["elite"]) | set(s["noTm"]) for s in species}
    rankings = {}
    for league, cp in LEAGUES.items():
        table = {}
        for i, r in enumerate(load(args.cache, f"r{cp}.json")):
            sid = r["speciesId"]
            if sid not in known:
                continue
            pros, cons = traits(r["stats"], sid.endswith("_shadow"), r["moveset"], moves, cp)
            row = {"rank": i + 1, "score": r["score"], "moves": r["moveset"]}
            # For each Elite TM or legacy move in the set, the most used move of its kind (fast or charged) in
            # PvPoke's simulations that a TM can teach and isn't already in the set.
            hard = special.get(sid, set())

            def by_use(kind):
                return sorted(r.get("moves", {}).get(kind, []), key=lambda m: -(m.get("uses") or 0))

            alt = {}
            for i, m in enumerate(r["moveset"]):
                if m not in hard:
                    continue
                pool = by_use("fastMoves" if i == 0 else "chargedMoves")
                pick = next((c["moveId"] for c in pool
                             if c["moveId"] not in hard and c["moveId"] not in r["moveset"] and c["moveId"] not in alt.values()), None)
                if pick:
                    alt[m] = pick
            if alt:
                row["alt"] = alt
            # PvPoke's key wins and losses: what the team builder checks members against.
            wins = [m["opponent"] for m in r.get("matchups", [])[:5]]
            losses = [m["opponent"] for m in r.get("counters", [])[:5]]
            if wins:
                row["wins"] = wins
            if losses:
                row["losses"] = losses
            if pros:
                row["pros"] = pros
            if cons:
                row["cons"] = cons
            note = short_note(r.get("editorNotes"))
            if note:
                row["note"] = note
            table[sid] = row
        rankings[league] = table

    used = {m for t in rankings.values() for r in t.values() for m in r["moves"] + list(r.get("alt", {}).values())}
    gmpm = load(args.cache, "gmpm.json")
    dex = dex_table(load(args.cache, "pdb.json"), gmpm, load(args.cache, "species.json"))

    raid, raid_all = raid_table(gm, gmpm)
    used |= {r[k] for rows in raid.values() for r in rows for k in ("f", "c")}
    used |= {r[k] for rows in raid_all.values() for r in rows for k in ("f", "c")}
    used |= {m for r in max_table(gm, gmpm) for m, _ in r["fast"]}

    # Megas' own types: a mega boosts its team's attacks of these types the most.
    mega_types = {p["speciesId"]: [t for t in p.get("types", []) if t != "none"] for p in gm["pokemon"]
                  if p.get("released") and ("_mega" in p["speciesId"] or p["speciesId"].endswith("_primal"))}
    # Species whose megas reach level 4 (Super Mega): they fight 2 levels higher.
    super_mega = sorted({e["data"]["megaEvoLevelSettings"]["pokemonId"].lower() for e in gmpm
                         if e.get("templateId", "").startswith("MEGA_EVOLUTION_LEVEL_4_")})

    out = {
        "source": f"PvPoke gamemaster {gm.get('timestamp', '')}".strip(),
        "format": FORMAT,
        "raid": raid,
        "raidAll": raid_all,
        "max": max_table(gm, gmpm),
        "dex": dex,
        "species": species,
        "moves": {m: moves[m]["name"] for m in sorted(used) if m in moves},
        "rankings": rankings,
        "megaTypes": mega_types,
        "superMega": super_mega,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {args.out}: {len(species)} species, "
          + ", ".join(f"{k}={len(v)}" for k, v in rankings.items())
          + f", dex={len(dex)} ({sum(1 for d in dex if 'out' not in d)} in GO)")


if __name__ == "__main__":
    main()
