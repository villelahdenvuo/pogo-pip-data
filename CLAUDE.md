# pogo-pip-data

Trainer Implant's game data (`v1/pokedata.json`), built by `build_data.py` and refreshed daily by the
`Update game data` workflow. The app (private repo villelahdenvuo/pogo-pip) parses it in
`data/PokeData.kt` and fetches it in `data/HostedData.kt`.

- `build_data.py` lives only here. A change to the data's shape goes with a matching parser change in
  the app; one that apps already installed can't read bumps `FORMAT` (new `v<N>/` directory) and the
  app's `HostedData.FORMAT`.
- Keep the output deterministic: compact JSON, sorted where order doesn't matter, so the daily job
  only commits real changes.
- The app bundles a copy: `tools/fetch_data.py` in the app repo pulls the hosted file into its assets.
