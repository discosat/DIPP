# Power cuts on the final build (2026-11-03)

Re-run decided after the 90 earlier cuts were found to predate daemon commit
`9cd72b0` (energy-halt path). Build `s5a-final6`, md5-checked on the board against
`staging/` (dipp `4a0cdbac…`, cost.seed `4454ea59…`, emulate_camera `bcd1bab2…`).
Final-policy scheduler environment as in `tests/board/run.sh`; HYBRID storage;
pipeline 1; clear-sky frames (`cs12_test_matchedkeep_00`). Driver
`runs/powercut-final-chain.sh` → `tools/c3_powercut.py`.

| Session | Log | Platform | Frames | Cuts | Cuts that hit queued work | Lost | Duplicated | Queue corrupt |
|---|---|---|---|---|---|---|---|---|
| A, yields | `powercut_final_yield.txt` | disco2-bol, 2× load, every 3rd frame urgent | 60 | 12 | 5 (ingest held 17/13/9/5/1 at start-up) | 0 | 0 | 0 |
| B, intended energy halts | `powercut_final_halt.txt` | dawndusk-3w7, start 0.05 Wh above floor | 30 | 12 | 2 (ingest held 7/3) | 0 | 0 | 0 |
| C, energy halts (2026-11-05) | `powercut_final_energy.txt` | dawndusk-3w7, start ~3 mWh above floor (soc0 0.45194) | 40 | 12 | 6 (partial queue held 12/10/8/6/4/2 halted batches at start-up) | 0 | 0 | 0 |

- **Loss is counted from the durable completion record** (`/usr/share/dipp/completed.log`),
  which the host snapshots before every cut. The "board-side" `IN_SCOPE_LOST` (A: 4, B: 2)
  greps the daemon's stdout log, which is not synced and loses lines in a cut. For B this
  was checked on the board afterwards: all 30 frames are in `completed.log`; the 2 missing
  are absent only from `pc.log` (`4614c42b…`, `48ea298f…`). Board files for B, md5-verified:
  `powercut_final_board/pc_final_halt.tgz`. A's board files were overwritten by B
  before they could be fetched.
- Recovery after every cut: login 26–37 s after power returned; daemon up 40–51 s; first
  frame selected at about 51 s when work was queued. 5 of 24 boots needed the 60 s
  retry cut (stopped in U-Boot).
- Yields to urgent frames: 6 (A) and 10 (B), each with a checkpoint.

**Session C closes the two gaps A and B left** (driver `runs/powercut-final-c.sh`; board files
`powercut_final_board/pc_final_energy.tgz`, md5-verified):
- **Energy halts:** 708 `SCHED_ENERGY_WAIT` lines and 67 halts; every cut that hit work found
  halted batches in the partially processed queue.
- **Resume from a checkpoint after a cut:** 17 resumes (progress 1 or 2) across 12 distinct
  batches, 2–3 per boot over boots 2–7; each of the 40 frames completed exactly once.
- Board-side `IN_SCOPE_LOST=3` are again log lines lost in a cut: all 40 frames are in
  `completed.log` (missing from `pc.log` only: `10dae43d…`, `228ff133…`, `83237339…`).
- Recovery: login 26–37 s after power; first frame selected 51–62 s after power. One boot
  needed the 60 s retry cut.

**Totals on the final build: 36 cuts, 130 frames, 0 lost, 0 duplicated, 0 corrupt queues;
13 cuts hit queued work (yields in A/B, halted batches in C).**

The drain step now counts the durable completion record (`tools/c3_powercut.py`); session C
counted the daemon log, which stopped at 37 of 40, so its drain waited the full 900 s idle window
before tallying. No result is affected: the tally reads `completed.log`.

**Sessions A and B alone did not exercise** (kept for the record):
- **Energy halts:** session B logged 0 `SCHED_ENERGY_WAIT` and 0 halts. Each boot resets
  the simulated battery to 41.625 Wh (floor 41.58 Wh). 30 frames at about 2 mWh each,
  recharged between frames, never reached the floor.
- **Resume from a checkpoint after a cut:** 0 in both sessions. Every yielded batch had
  finished before the next cut.
- Only 7 of the 24 cuts landed while work was queued; the rest hit an idle board.
