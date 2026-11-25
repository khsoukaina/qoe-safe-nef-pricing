#!/bin/bash
# UE container: wait for the core, start the gNB until NG Setup succeeds, then attach the UEs
# in small batches (a registration storm of 64 UEs can crash nr-gnb: "Segmentation fault").
# A watchdog restarts gNB + UEs if the gNB dies during attachment (up to 4 attempts).
set -u
LOG=/lab/results/core_logs
mkdir -p "$LOG"
UE_COUNT=${UE_COUNT:-64}
UE_BATCH=${UE_BATCH:-8}
CORE_IP=${CORE_IP:-10.100.200.10}
BIN=/opt/UERANSIM/build
python3 /lab/docker/ueransim/render.py

ntun () { ip -o link show | grep -c uesimtun || true; }

# 1. wait until the core is fully up (the NEF starts after the AMF in the core entrypoint)
for i in $(seq 1 120); do
  python3 -c "import urllib.request;urllib.request.urlopen('http://$CORE_IP:8080/lab/v1/state',timeout=2)" 2>/dev/null && break
  sleep 2
done
echo "[ue] core reachable (NEF answers)"
sleep 2

start_gnb () {
  for attempt in $(seq 1 10); do
    pkill -f nr-gnb 2>/dev/null; sleep 1
    $BIN/nr-gnb -c /tmp/gnb.yaml >> "$LOG/gnb.log" 2>&1 &
    GNB_PID=$!
    for i in $(seq 1 15); do
      grep -q "NG Setup procedure is successful" "$LOG/gnb.log" && { echo "[ue] gNB connected to AMF (pid $GNB_PID)"; return 0; }
      sleep 1
    done
    echo "[ue] NG Setup not successful (attempt $attempt)"; tail -n 2 "$LOG/gnb.log"
  done
  return 1
}

attach_all () {
  pkill -f nr-ue 2>/dev/null; pkill -f nr-gnb 2>/dev/null; sleep 3
  : > "$LOG/gnb.log"; : > "$LOG/ue.log"
  start_gnb || return 1
  sleep 2
  local i=0
  while [ "$i" -lt "$UE_COUNT" ]; do
    local n=$(( UE_COUNT - i < UE_BATCH ? UE_COUNT - i : UE_BATCH ))
    local first=$(( i + 1 ))
    local supi="imsi-99970$(printf '%010d' "$first")"
    sed "s/imsi-999700000000001/$supi/" /tmp/ue.yaml > "/tmp/ue_$first.yaml"
    $BIN/nr-ue -c "/tmp/ue_$first.yaml" -n "$n" >> "$LOG/ue.log" 2>&1 &
    for k in $(seq 1 40); do
      kill -0 "$GNB_PID" 2>/dev/null || { echo "[ue] gNB died during batch starting at UE $first"; return 1; }
      [ "$(ntun)" -ge $(( i + n )) ] && break
      sleep 1
    done
    echo "[ue] batch $first..$(( i + n )): $(ntun) tunnels up"
    i=$(( i + n ))
    sleep 1
  done
  sleep 3
  kill -0 "$GNB_PID" 2>/dev/null || { echo "[ue] gNB died after attachment"; return 1; }
  return 0
}

for attempt in 1 2 3 4; do
  if attach_all; then break; fi
  echo "[ue] attachment attempt $attempt failed, retrying"
  sleep 5
done
echo "[ue] $(ntun) / $UE_COUNT PDU sessions up"
bash /lab/docker/ueransim/ue_routing.sh
echo "[ue] ready"

# 2. watchdog: report (in docker logs) if the gNB dies later during a run
while true; do
  sleep 10
  if ! pgrep -f nr-gnb >/dev/null; then
    echo "[ue] WARNING: nr-gnb is not running any more (see results/core_logs/gnb.log); run: docker compose -f docker/docker-compose.yml restart ue"
    sleep 50
  fi
done
