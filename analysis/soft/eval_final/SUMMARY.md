# Soft-deadline utility V_G on the P2 board runs (eval_final, seed 1), 2026-10-15

METRIC.md addendum 2026-10-15. V_G: a late frame keeps q * max(0, 1 - (lateness/D)/G).
Source: analysis/soft/eval_final/runs.csv (campaign_utility.py on analysis/parsed/eval_final).

| cell | metric | low | med | high | deadline-only | final (v2) | best static | final regret |
|---|---|---|---|---|---|---|---|---|
| C3 | U | .791 | .949 | .590 | .814 | .963 | med | -.015 |
| C3 | V_G0.5 / 1 / 2 | .791 | .949 | .788 / .880 / .931 | .974 / .980 / .983 | .963 | med | -.015 |
| C4 | U | .791 | .949 | .403 | .962 | .933 | med | +.015 |
| C4 | V_G0.5 / 1 / 2 | .791 | .949 | .501 / .646 / .799 | .966 | .933 | med | +.015 |
| C5 | U | .791 | .336 | .134 | .166 | .798 | low | -.007 |
| C5 | V_G0.5 / 1 / 2 | .791 | .411 / .513 / .687 | .164 / .200 / .281 | .421 / .594 / .693 | .798 | low | -.007 |
C1, C2: nothing late for any policy, V_G = U. Urgent-hard variant within .016 of soft.

Lateness of the late frames (lateness / own deadline): deadline-only C3 41 late,
median .02, max .25 (finishes just after the deadline); high C3 median .24;
high C4 median .58, max 14.6; deadline-only C5 median .30; med C5 median .79.

Findings: the final policy is never late, so V_G = U for it. Best static and the
final policy's regret are unchanged at every G. Deadline-only overtakes the final
policy in C3 under V_G (+.011 to +.020): its 41 misses are near-misses. In C5
no soft grace rescues the overloading policies (best .69 vs final .80).
