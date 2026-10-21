# pogo-pip-data

Game data for Trainer Implant, rebuilt daily from public sources and served from this repo.

The app bundles a copy of `v1/pokedata.json`. Once a day it checks
`https://raw.githubusercontent.com/villelahdenvuo/pogo-pip-data/main/v1/pokedata.json`
and, when it's newer, uses that from the next start. Nothing about the user is sent.

## Sources

- [PvPoke](https://github.com/pvpoke/pvpoke): game master and Great/Ultra/Master overall rankings
- [PokeMiners game masters](https://github.com/PokeMiners/game_masters): base sizes, raid and Max data
- [PokémonDB](https://pokemondb.net/go/pokedex): XXL height ceilings for showcases
- [PokéAPI](https://pokeapi.co): names for every Pokédex number

## Build

```sh
python3 build_data.py            # writes v1/pokedata.json, downloads cached in .cache/
rm -rf .cache && python3 build_data.py   # fresh sources
```

The `Update game data` workflow runs this daily (and by hand from the Actions tab) and commits only
when the output changed.

## Format

`v1/` is format 1, and the file carries `"format": 1`. A change that apps already out there can't read
goes to a new `v2/` with `FORMAT = 2`; old apps keep reading `v1/` (which then stops updating) or their
bundled copy. Additions that old apps simply ignore stay in the same format.
