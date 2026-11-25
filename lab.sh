#!/usr/bin/env bash
# nef_core_lab helper: every command runs inside the containers (docker compose).
# Usage: ./lab.sh <command> [args]     (run ./lab.sh help)
set -euo pipefail
cd "$(dirname "$0")"
DC="docker compose -f docker/docker-compose.yml"
# NEF_LAB_CALIBRATION=results/calibration_testbed.json ./lab.sh <cmd>  -> model calibrated on the testbed
CAL=""
[ -n "${NEF_LAB_CALIBRATION:-}" ] && CAL="-e NEF_LAB_CALIBRATION=${NEF_LAB_CALIBRATION}" && echo "[lab] calibration: ${NEF_LAB_CALIBRATION}"
cmd=${1:-help}; shift || true
case "$cmd" in
  build)        $DC build ;;
  up)           sudo modprobe sctp 2>/dev/null || true; $DC up -d; echo "wait ~60 s, then: ./lab.sh status" ;;
  down)         $DC down ;;
  wipe)         $DC down -v ;;
  logs)         tail -n 40 results/core_logs/${1:-nef}.log ;;
  status)
    echo "--- core NFs";  $DC exec core bash -c 'for p in nrf scp ausf udm udr pcf bsf nssf smf amf upf; do pgrep -f open5gs-${p}d >/dev/null && echo "  $p up" || echo "  $p DOWN"; done'
    echo "--- NEF";      $DC exec core curl -s http://127.0.0.1:8080/lab/v1/state || echo "  NEF not answering"
    echo; echo "--- UEs";  $DC exec ue bash -c 'echo "  $(ip -o link show | grep -c uesimtun) PDU sessions"; ip -4 -o addr show | grep uesimtun | head -3'
    echo "--- user plane (UE -> UPF -> DN)"; $DC exec ue bash -c 'ip=$(ip -4 -o addr show uesimtun0 | awk "{print \$4}" | cut -d/ -f1); ping -c 3 -I $ip 10.60.0.1 | tail -2' ;;
  shell-core)   $DC exec core bash ;;
  shell-ue)     $DC exec ue bash ;;
  nef-restart)  $DC exec core bash -c "pkill -f 'python3 -m nef[.]app' || true"; sleep 1
                $DC exec -d core bash -c "cd /lab && python3 -m nef.app $* > results/core_logs/nef.log 2>&1"
                sleep 4; tail -n 3 results/core_logs/nef.log ;;
  test)         $DC exec core bash -c "cd /lab && bash conformance/fetch_specs.sh >/dev/null && NEF_SPECS=/lab/conformance/specs/5GC_APIs python3 -m pytest -q tests/" ;;
  precompute)   $DC exec $CAL core bash -c "cd /lab && python3 pricing/precompute_prices.py $*" ;;
  conformance)
    $DC exec core bash -c "cd /lab && bash conformance/fetch_specs.sh"
    SINCE=$(date +%s)
    UE=$($DC exec -T ue bash -c 'ip -4 -o addr show uesimtun0 | awk "{print \$4}" | cut -d/ -f1' | tr -d '\r')
    $DC exec -d core bash -c "cd /lab && bash conformance/capture_core.sh 40"
    sleep 2
    $DC exec ue bash -c "cd /lab && python3 conformance/active_tests.py --nef http://192.168.250.10:8080 --ue $UE" || true
    sleep 40
    $DC exec -T core bash -c "cd /lab && python3 conformance/validate.py --since $SINCE; python3 conformance/core_evidence.py results/core_logs/core_signalling.pcap" ;;
  latency)
    $DC exec ue bash -c "cd /lab && python3 latency/load_test.py --label core $*"
    ./lab.sh nef-restart --southbound none
    $DC exec ue bash -c "cd /lab && python3 latency/load_test.py --label none $*"
    ./lab.sh nef-restart ;;
  externality)  $DC exec ue bash -c "cd /lab && python3 scenario/measure_externality.py $*"
                $DC exec core bash -c "cd /lab && python3 scenario/fit_externality.py" ;;
  check)        $DC exec ue bash -c "cd /lab && python3 scenario/check_ues.py $*" ;;
  restart-all)  $DC restart core; sleep 25; $DC restart ue; echo "wait ~90 s, then: ./lab.sh status && ./lab.sh check" ;;
  scenario)     $DC exec $CAL ue bash -c "cd /lab && python3 scenario/run_scenario.py $*" ;;
  sim)          $DC exec $CAL core bash -c "cd /lab && python3 sim/exp_q1_quality.py $*" ;;
  figures)      $DC exec $CAL core bash -c "cd /lab && python3 analysis/make_figures.py $*" ;;
  help|*)
    sed -n '2,4p' "$0"
    echo "commands: build up status check restart-all down wipe logs [nf] shell-core shell-ue nef-restart [args] test precompute [--quick]"
    echo "          conformance latency [--rates ...] externality scenario [args] sim [--quick] figures [args]" ;;
esac
