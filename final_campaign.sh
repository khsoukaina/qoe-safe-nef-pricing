#!/bin/bash
# Campagne finale : prix calibrés sur le banc, conformité, scénario général et pic, simulation Q1, figures.
set -u
cd "$(dirname "$0")"
export NEF_LAB_CALIBRATION=results/calibration_testbed.json
step () { echo; echo "===== $(date '+%H:%M') $*"; }
step "1/8 prix (modèle calibré sur le banc)";        ./lab.sh precompute
step "2/8 nouveau NEF + santé des UE";             ./lab.sh nef-restart; sleep 5; ./lab.sh check
step "3/8 conformité finale (code définitif)";     ./lab.sh conformance
step "4/8 scénario général (tous régimes)";        ./lab.sh check; ./lab.sh scenario --policies no_nef no_nef#2 safe_mean safe_strict safe_gated_mean --slots 3 9 12 16 20 --slot-seconds 90 --seeds 1 2 3 --tag general
step "5/8 pic imprévu (régime saturé)";            ./lab.sh check; ./lab.sh scenario --policies no_nef safe_mean safe_strict --slots 12 20 --demand-mult 1.3 --slot-seconds 90 --seeds 1 2 3 --tag surge
step "6/8 simulation Q1 (qui gagne, filtrage, décalage)"; ./lab.sh sim
step "7/8 figures";                                ./lab.sh figures --run results/testbed/general results/testbed/surge
step "8/8 diagnostics";                            python3 analysis/diagnose.py results/testbed/general > results/testbed/general/diagnose.txt
                                                   python3 analysis/diagnose.py results/testbed/surge > results/testbed/surge/diagnose.txt
step "TERMINE"
