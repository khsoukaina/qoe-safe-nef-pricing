3-D Pareto front of the QoD price plan (17h-24h, random admission)

Validation

- hours: 17h-24h
- levels: 8
- plans: 2097152
- max_switch: None
- dp_front: 5500
- dp_seconds: 2.44
- axes: ['profit', 'harm', 'dmos']
- color: p5
- rationing: random
- bruteforce_plans: 2097152
- bruteforce_seconds: 0.08
- choco_problems: 450
- choco_seconds: 12.7
- choco_front: 89
- max_gap_vs_dp: 0.0
- max_gap_vs_bruteforce: 0.0
- choco_hypervolume_share: 0.9634
- choco_points_on_exact_front: 89
- dissat_no_nef: 3.003981804906098

Constant plans and knee (no NEF: dissatisfied 3.004 %)

| Plan | Operator profit | Best-effort MOS loss (1e-3) | Δ mean MOS vs no NEF | 5th-percentile MOS (hourly) | Dissatisfied sessions, MOS < 3 (%) | Social welfare gain | On front | Levels per hour |
|---|---|---|---|---|---|---|---|---|
| Closed (no NEF) (all hours) | 0.000 | 0.000 | 0.000 | 3.443 | 3.004 | 0.000 | yes | closed x 7 |
| QoE-safe + gating (all hours) | 0.547 | 0.286 | 0.182 | 4.257 | 0.002 | 45.502 | yes | gate x 7 |
| QoE-safe (all hours) | 5.561 | 1.690 | 0.194 | 4.235 | 0.225 | 57.008 | yes | safe x 7 |
| Conformal quantile (all hours) | 3.212 | 2.486 | 0.195 | 4.216 | 0.334 | 55.614 | no | quant x 7 |
| Congestion w/o ext. (all hours) | 1.935 | 3.086 | 0.194 | 4.194 | 0.432 | 54.779 | no | cong x 7 |
| Flat commercial (all hours) | 17.557 | 0.379 | 0.144 | 4.057 | 0.002 | 37.208 | no | flat x 7 |
| Revenue max. (all hours) | 20.727 | 0.414 | 0.131 | 3.962 | 0.002 | 34.600 | yes | rev x 7 |
| **Knee (balanced)** | 18.383 | 0.390 | 0.156 | 4.100 | 0.002 | 40.216 | yes | 17h:mid 18h:rev 19h:flat 20h:flat 21h:flat 22h:rev 23h:mid |

Representative Pareto-optimal plans (sorted by profit)

| Plan | Operator profit | Best-effort MOS loss (1e-3) | Δ mean MOS vs no NEF | 5th-percentile MOS (hourly) | Dissatisfied sessions, MOS < 3 (%) | Social welfare gain | Levels per hour |
|---|---|---|---|---|---|---|---|
| #5496 | -0.000 | 0.044 | 0.037 | 3.576 | 3.004 | 8.826 | 17h:gate 18h:quant 19h:closed 20h:closed 21h:closed 22h:gate 23h:closed |
| #4812 | 2.988 | 0.177 | 0.108 | 3.930 | 1.412 | 27.282 | 17h:rev 18h:closed 19h:mid 20h:closed 21h:mid 22h:mid 23h:rev |
| #4125 | 5.589 | 0.120 | 0.076 | 3.747 | 2.153 | 19.157 | 17h:gate 18h:mid 19h:flat 20h:closed 21h:closed 22h:mid 23h:gate |
| #3425 | 7.466 | 0.279 | 0.143 | 4.085 | 0.002 | 36.028 | 17h:closed 18h:closed 19h:mid 20h:flat 21h:mid 22h:closed 23h:gate |
| #2749 | 9.624 | 0.195 | 0.061 | 3.676 | 1.594 | 16.121 | 17h:closed 18h:flat 19h:closed 20h:rev 21h:closed 22h:rev 23h:gate |
| #2062 | 11.634 | 0.304 | 0.151 | 4.104 | 0.002 | 38.488 | 17h:mid 18h:mid 19h:mid 20h:flat 21h:flat 22h:closed 23h:closed |
| #1375 | 12.934 | 0.251 | 0.106 | 3.857 | 0.853 | 27.591 | 17h:mid 18h:mid 19h:closed 20h:rev 21h:rev 22h:mid 23h:rev |
| #686 | 16.404 | 0.329 | 0.114 | 3.901 | 0.002 | 29.559 | 17h:gate 18h:closed 19h:rev 20h:rev 21h:flat 22h:closed 23h:closed |
| #0 | 20.727 | 0.414 | 0.131 | 3.962 | 0.002 | 34.600 | 17h:rev 18h:rev 19h:rev 20h:rev 21h:rev 22h:rev 23h:rev |
