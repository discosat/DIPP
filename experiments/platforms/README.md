# Platform presets

One file per platform: everything the battery simulator needs that belongs
to the spacecraft (and the processing board), as `KEY=value` lines that
`run.sh --platform <file>` sources. The simulator has no defaults, so a run
names its platform explicitly and `board.json` records the file's md5.

Initial conditions are per run, not per platform: `run.sh --soc0 --phase0`.

| File | What |
|---|---|
| `disco2-bol.env` | DISCO-II, beginning of life: 16.435 W delivered orbit average |
| `disco2-eol.env` | DISCO-II after 5 years: 15.257 W delivered |

A new platform is a new file: its capacity, bus load (excluding the board),
orbit period and a profile from `tools/make_power_profile.py` (a digitised
curve, or `--rect` from its sunlit power and eclipse). Staging copies these
files and `analysis/power-profile/*.profile` into the run-set.
