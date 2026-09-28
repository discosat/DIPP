# Power profile — SI-DISCOII-DR-001 Fig 7-3

The battery simulator's solar generation (`DIPP_BATT_PROFILE`), digitised from
the DISCO-II design report, SI-DISCOII-DR-001 Issue 1.0 (2023-03-29), §7.2.1.

```sh
python3 tools/digitise_fig7_3.py --pdf "../SI-DISCO-II Satellite Design Report v1.0-1.pdf" \
    -o analysis/power-profile/
python3 tools/make_power_profile.py --digitised analysis/power-profile/fig7-3_digitised.csv \
    --orbit-avg-w 17.3 --conversion 0.95 -o analysis/power-profile/disco2_fig7-3_bol.profile
python3 tools/make_power_profile.py --digitised analysis/power-profile/fig7-3_digitised.csv \
    --orbit-avg-w 17.3 --degrade 0.92833 --conversion 0.95 \
    -o analysis/power-profile/disco2_fig7-3_eol.profile
```

| File | What |
|---|---|
| `fig7-3_digitised.csv` | per-panel and total W against phase of the plot window; PDF and image SHA-256 in the header |
| `fig7-3_overlay.png` | the digitised values redrawn (cyan, white = total) over the source figure |
| `disco2_fig7-3_bol.profile` | `disco2-bol.env`. Fig 7-3 at the report's own **17.3 W** panel output (×1.0068 removes the 0.8 % digitisation error), × 0.95 power point tracker = **16.435 W delivered** |
| `disco2_fig7-3_eol.profile` | `disco2-eol.env`. × 27.2/29.3, the report's 5-year cell-efficiency loss: 16.06 W panel, **15.257 W delivered** |

Profiles hold power **delivered to the bus**: the report's 17.3 W is solar-cell
output ("14.4 cells each 1.2 W"; Table 5: 2.409 V × 0.5029 A = 1.21 W per
cell), and the MPPT-P4 between the cells and the battery is 95 % efficient
(§5.1.2).

## How it was read

The figure is a Grafana screenshot embedded at 1691 × 531 px (page 39, third
image), extracted with `pdfimages` at native resolution. Each series is read
by line colour, column by column; lines hidden under another are
interpolated across the gap. The axis calibration comes from the gridlines.
The window is one orbit: every series ends where it starts.

## Checks against the report's own numbers

| | Digitised | Report |
|---|---|---|
| Orbit average | 17.17 W | 17.3 W (§7.2.1) |
| Energy per orbit | 27.31 Wh | 27.5 Wh (Fig 7-4) |
| Deployables peak | 19.19 W | 19.2 W ("9.6 W on each deployable panel") |
| Body-panel peaks | 9.61 W | 9.6 W |
| Eclipse | 35.96 min | not stated; β = 0 geometry for the §7.2.1 orbit gives 35.6 min |

The generation is not flat: 23–30 W across the sunlit arc (budget scaling),
dipping where the deployables' incidence crosses over.

## Which orbit average, and why not Table 6's 15.82 W

The report prints three generation figures:

- **17.3 W / 27.5 Wh per orbit** — §7.2.1's simulation, the one this figure
  plots and the digitisation reproduces; also 14.4 cells × 1.2 W. **Default.**
- **16.06 W** — not printed, but fully derived from the report: 17.3 W × the
  cell-efficiency loss it states for 5 years (29.3 % → 27.2 %, §7.2). **EOL arm.**
- **15.82 W** — Table 6, with no derivation, in a section marked "not
  updated". No factor the report states reproduces it (EOL gives 16.06 W,
  the 95 % MPPT efficiency 16.4 W). **Not used.**

## The other constants this settles (experiments/platforms/disco2-*.env)

- **Orbit** 95.43 min (mean motion 15.09 rev/day). **Eclipse** read from the
  profile (35.8 min; β = 0 geometry gives 35.6). There is no built-in
  rectangular generation any more: a platform without a curve gets an
  exact-edge rectangular *file* from `make_power_profile.py --rect`. (The old
  25.4 W sunlit figure was Table 6's 15.82 W spread over a 36 min eclipse.)
- **Bus load** 13.07 W is Table 6's total and **includes the cameras**
  (1.03 W). The report has **no line for the processing board.**
- **Processing board, per burst.** The board's measured idle floor
  (3.03–3.30 W after a boot) is almost all of the margin the report leaves
  (16.435 − 13.07 = 3.37 W delivered at BOL; 2.19 W at EOL) and was charged
  nowhere. It is now charged at 3.30 W while the board has work and for 60 s
  after, then switched off, as the PDU and flight planner allow. `always` is
  the counterfactual: 16.37 W against 16.435 W leaves 0.07 W at BOL before any
  processing energy, and is short by 1.1 W at EOL.
- **Payload safety margin**, derived because the report gives no cut-off
  (decided 2026-09-28): the payload may spend at most **one orbit's surplus**
  — what the arrays deliver over an orbit beyond the platform's own load —
  so one orbit of generation always puts it back and the battery is full
  again; plus a **2 % reserve** against the payload drawing more than
  predicted. `margin = C·(1 + 0.02) − (P̄_gen − P_bus)·T_orbit`:
  BOL 93.84 − 5.35 = **88.5 Wh (96.2 % SoC)**, EOL 90.4 Wh (98.2 %). Every
  term but the reserve comes from the report; no depth-of-discharge figure
  from outside is needed, because cycling stays as shallow as the platform's
  own eclipse swing (7.7 Wh, 8.4 %) plus one orbit's surplus.
