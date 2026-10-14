#!/usr/bin/env python3
"""Stage 5a tables in analysis/parse_runs.py.   python3 tests/test_parse_stage5.py

A synthetic console in the daemon's exact formats: a routine frame (scene 1)
starts a 4 s stage at t=10 s; an urgent frame (scene 2) is SENT at t=12 s
(deadline 32 s, SLO 20 s) but, the daemon reading its queue only between
batches, INGESTED at t=14 s -- blocked 2 s from arrival -- and its MARK is
read later still; a third frame (scene 3, sent 12.5 s) is ingested with it;
the routine batch then halts for 3 s on energy.
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "analysis"))
import parse_runs  # noqa: E402

S = 1_000_000
CONSOLE = f"""SIPP build: test
BATTERY_CONFIG capacity_wh=92.00 soc0=0.950 wh0=87.400 bus_w=13.07 gen_avg_w=16.435 orbit_min=95.430 eclipse_min=35.80 phase0=0.185 orbit_surplus_wh=5.351 margin_wh=88.49 margin_src=derived reserve_frac=0.020 profile=x.profile profile_samples=360 board_mode=burst board_idle_w=3.30 board_hold_s=60.0
BATTERY ts_us={9*S} sim_t_s=9.00 wh=87.4000 soc=0.9500 phase=0.1866 state=SUNLIT p_gen_w=20.00 board=off board_wh=0.0000
MARK ts_us={10*S - 1000} scene=1 sim_t_s=10.00 phase=0.1867
INGEST ts_us={10*S} uuid=aaa pipeline=2 num_images=1 deadline_us={310*S} slo_us={300*S} ingest_depth=1 partial_depth=0
SCHED_STATE ts_us={10*S} state=busy ingest_depth=1 partial_depth=0 battery_wh=87.4000
EXEC ts_us={14*S} uuid=aaa pipeline=2 module_index=0 module=libdemosaic param_id=0 num_images=1 batch_bytes=1 cached=0 n_input=1 cost_src=point latency_pred_us=4000000 latency_meas_us={4*S} energy_pred_uwh=100 energy_debited_uwh=100.000 battery_wh=87.300000 deadline_us={310*S} slack_us=1
INGEST ts_us={14*S + 1000} uuid=bbb pipeline=2 num_images=1 deadline_us={32*S} slo_us={18*S} ingest_depth=1 partial_depth=1
INGEST ts_us={14*S + 2000} uuid=ddd pipeline=2 num_images=1 deadline_us={312*S + S // 2} slo_us={298*S} ingest_depth=2 partial_depth=1
MARK ts_us={14*S + 3000} scene=2 sim_t_s=14.00 phase=0.1871
MARK ts_us={14*S + 4000} scene=3 sim_t_s=14.00 phase=0.1872
EXEC ts_us={15*S} uuid=bbb pipeline=2 module_index=0 module=libdemosaic param_id=0 num_images=1 batch_bytes=1 cached=0 n_input=1 cost_src=point latency_pred_us=1000000 latency_meas_us={1*S} energy_pred_uwh=25 energy_debited_uwh=25.000 battery_wh=87.290000 deadline_us={32*S} slack_us=1
Pipeline fully executed successfully uuid=bbb ts_us={15*S} deadline_us={32*S}
SCHED_HALT ts_us={15*S} uuid=aaa progress=1 battery_wh=88.4000 margin_wh=88.49 ingest_depth=0 partial_depth=1
SCHED_STATE ts_us={15*S} state=halted ingest_depth=0 partial_depth=1 battery_wh=88.4000
BATTERY_FF ts_us={16*S} adv_s=60.0 sim_t_before_s=16.00 sim_t_after_s=76.00 wh_before=88.4000 wh_after=88.6000 phase_after=0.1980 state=SUNLIT board_wh=0.0550
SCHED_RESUME ts_us={18*S} uuid=aaa progress_from=1 progress_now=2 halted_s=3.000 battery_wh=88.6000
EXEC ts_us={20*S} uuid=aaa pipeline=2 module_index=1 module=libtile param_id=1 num_images=1 batch_bytes=1 cached=0 n_input=1 cost_src=point latency_pred_us=2000000 latency_meas_us={2*S} energy_pred_uwh=50 energy_debited_uwh=50.000 battery_wh=88.550000 deadline_us={310*S} slack_us=1
Pipeline fully executed successfully uuid=aaa ts_us={20*S} deadline_us={310*S}
INGEST_DROP ts_us={21*S} uuid=ccc pipeline=2 num_images=1 deadline_us={41*S} reason=queue_full ingest_depth=16
BATTERY_EMPTY ts_us={22*S} sim_t_s=90.00 board=on -- the scenario exceeds the platform
"""
FEED = """TRACE_START wall=1.0 trace=x.trace
FEED scene=1 datatake=S2A:1-1 class=routine slo_s=300 t_s=0.000 target_sim_s=10.0 sim_now_s=10.0 late_s=0.0 wall=2
FEED scene=2 datatake=S2A:1-1 class=urgent slo_s=20 t_s=2.000 target_sim_s=12.0 sim_now_s=12.0 late_s=0.0 wall=4
FEED scene=3 datatake=S2A:1-1 class=routine slo_s=300 t_s=2.500 target_sim_s=12.5 sim_now_s=12.5 late_s=0.0 wall=4.5
TRACE_END frames=2 late_frames=0 advanced_s=60.000 tail_advanced_s=0 end_state=idle wall=9
"""

fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    fails += 0 if ok else 1


with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "console.log"), "w").write(CONSOLE)
    open(os.path.join(d, "feed.log"), "w").write(FEED)
    row, execs, _, (batches, battery, events) = parse_runs.collect(d)

b = {x["uuid"]: x for x in batches}
check("four batches incl. the dropped one", set(b) == {"aaa", "bbb", "ccc", "ddd"}, list(b))
check("MARKs read after their INGESTs still pair by order",
      b["bbb"].get("scene") == 2 and b["ddd"].get("scene") == 3, (b["bbb"].get("scene"), b["ddd"].get("scene")))
check("arrival = deadline - SLO (12 s), not the late MARK; ingest delay 2 s", b["bbb"]["arrival_ts_us"] == 12 * S
      and b["bbb"]["ingest_delay_us"] == 2 * S + 1000, (b["bbb"]["arrival_ts_us"], b["bbb"]["ingest_delay_us"]))
check("classes from feed.log via MARK", b["aaa"].get("cls") == "routine" and b["bbb"].get("cls") == "urgent",
      (b["aaa"].get("cls"), b["bbb"].get("cls")))
check("dropped batch has no scene (no MARK before it)", b["ccc"]["dropped"] == 1 and "scene" not in b["ccc"])
check("urgent blocked 2 s by the running routine stage", b["bbb"]["blocking_us"] == 2 * S, b["bbb"]["blocking_us"])
check("routine not blocked", b["aaa"]["blocking_us"] == 0, b["aaa"]["blocking_us"])
check("urgent latency 3 s from arrival, met", b["bbb"]["latency_us"] == 3 * S and b["bbb"]["deadline_met"] == 1,
      b["bbb"].get("latency_us"))
check("urgent waited 2 s before its first stage", b["bbb"]["wait_us"] == 2 * S, b["bbb"].get("wait_us"))
check("routine halted once for 3 s", b["aaa"]["n_halts"] == 1 and b["aaa"]["halted_s"] == 3.0,
      (b["aaa"]["n_halts"], b["aaa"]["halted_s"]))
check("energy debited summed per batch", b["aaa"]["energy_debited_uwh"] == 150.0, b["aaa"]["energy_debited_uwh"])
check("battery rows: BATTERY + BATTERY_FF", [x["tag"] for x in battery] == ["BATTERY", "BATTERY_FF"],
      [x["tag"] for x in battery])
check("events include HALT/RESUME/MARK/DROP/EMPTY",
      {"SCHED_HALT", "SCHED_RESUME", "MARK", "INGEST_DROP", "BATTERY_EMPTY", "SCHED_STATE"} <= {x["tag"] for x in events})
check("run: urgent met 1.0, routine met 0.5 (unfinished counts as missed)",
      row.get("urgent_met_frac") == 1.0 and row.get("routine_met_frac") == 0.5,
      (row.get("urgent_met_frac"), row.get("routine_met_frac")))
check("run: 1 dropped, 1 unfinished (ddd never completed)", row.get("batches_dropped") == 1
      and row.get("batches_unfinished") == 1, (row.get("batches_dropped"), row.get("batches_unfinished")))
check("run: battery min over BATTERY and BATTERY_FF", row.get("battery_wh_min") == 87.4, row.get("battery_wh_min"))
check("run: config carried", row.get("cfg_margin_wh") == 88.49 and row.get("cfg_board_mode") == "burst")
check("run: BATTERY_EMPTY counted (prose after -- ignored)", row.get("battery_empty") == 1)
check("run: feed TRACE_END carried", row.get("feed_frames") == 2 and row.get("feed_end_state") == "idle")
check("run: urgent blocking max 2 s", row.get("urgent_blocking_max_s") == 2.0, row.get("urgent_blocking_max_s"))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
