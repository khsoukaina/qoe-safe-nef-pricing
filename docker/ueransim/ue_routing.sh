#!/bin/bash
# Source-based routing: a socket bound to a UE address leaves through that UE's tunnel.
# (UERANSIM normally installs these rules itself; this makes it idempotent and explicit.)
n=0
for dev in $(ip -o link show | awk -F': ' '{print $2}' | grep '^uesimtun'); do
  ip=$(ip -4 -o addr show dev "$dev" | awk '{print $4}' | cut -d/ -f1)
  [ -z "$ip" ] && continue
  idx=${dev#uesimtun}
  tbl=$((1000 + idx))
  ip rule show | grep -q "from $ip lookup" || ip rule add from "$ip" lookup "$tbl" pref $((2000 + idx))
  ip route replace default dev "$dev" table "$tbl"
  n=$((n + 1))
done
echo "[ue] source routing set for $n UE tunnels"
