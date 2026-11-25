#!/bin/bash
# Core container: TUN + data network, Open5GS configuration, subscribers, NFs, DN servers, NEF.
set -u
CORE_IP=${CORE_IP:-192.168.250.10}
MONGO_URI=${MONGO_URI:-mongodb://192.168.250.5/open5gs}
UE_COUNT=${UE_COUNT:-64}
LOG=/lab/results/core_logs
mkdir -p "$LOG" /var/log/open5gs

echo "[core] Open5GS version: $(open5gs-pcfd -v 2>&1 | head -1)"

# 1. UPF tunnel device and data-network address
ip tuntap add name ogstun mode tun 2>/dev/null || true
ip addr add 10.45.0.1/16 dev ogstun 2>/dev/null || true
ip link set ogstun up
ip link add dn0 type dummy 2>/dev/null || true
ip addr add 10.60.0.1/32 dev dn0 2>/dev/null || true
ip link set dn0 up
sysctl -qw net.ipv4.ip_forward=1

# 2. configuration (N2/N3 on the bridge address, MongoDB URI) and subscribers
python3 /lab/docker/core/patch_configs.py --core-ip "$CORE_IP" --mongo "$MONGO_URI" || exit 1
python3 /lab/provision/provision.py --mongo "$MONGO_URI" --count "$UE_COUNT" || exit 1

# 3. 5G core network functions (NRF first)
start_nf () {
  /usr/bin/open5gs-$1d -c /etc/open5gs/$1.yaml > "$LOG/$1.log" 2>&1 &
  echo "[core] started $1 (pid $!)"
}
start_nf nrf; sleep 2
start_nf scp; sleep 1
for nf in ausf udm udr pcf bsf nssf; do start_nf $nf; sleep 0.5; done
start_nf upf; sleep 1
start_nf smf; sleep 1
start_nf amf; sleep 1

# 4. application servers of the data network
python3 /lab/traffic/server.py --bind 10.60.0.1 > "$LOG/dn_servers.log" 2>&1 &

# 5. NEF (northbound :8080, southbound to NRF/BSF/PCF, enforcement on ogstun)
if [ "${NEF_AUTOSTART:-1}" = "1" ]; then
  sleep 3
  cd /lab && python3 -m nef.app ${NEF_ARGS:-} > "$LOG/nef.log" 2>&1 &
  echo "[core] NEF started, log: results/core_logs/nef.log"
fi
echo "[core] ready"
tail -f /dev/null
