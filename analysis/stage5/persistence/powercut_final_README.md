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

**Not exercised:**
- **Energy halts:** session B logged 0 `SCHED_ENERGY_WAIT` and 0 halts. Each boot resets
  the simulated battery to 41.625 Wh (floor 41.58 Wh). 30 frames at about 2 mWh each,
  recharged between frames, never reached the floor.
- **Resume from a checkpoint after a cut:** 0 in both sessions. Every yielded batch had
  finished before the next cut.
- Only 7 of the 24 cuts landed while work was queued; the rest hit an idle board.
