#!/bin/bash
# Capture the real core signalling during a run (inside the core container):
#   SBI  (HTTP/2, tcp/7777, loopback)  NEF->NRF/BSF/PCF and PCF->SMF (SM policy update)
#   PFCP (udp/8805)                    SMF->UPF session modification (new QoS flow, QER)
#   NGAP (sctp/38412)                  AMF->gNB PDU session resource modify
# Usage: bash conformance/capture_core.sh 120   (seconds) -> results/core_logs/core_signalling.pcap
DUR=${1:-120}
OUT=/lab/results/core_logs/core_signalling.pcap
mkdir -p "$(dirname "$OUT")"
echo "capturing $DUR s -> $OUT"
timeout "$DUR" tcpdump -i any -s 0 -w "$OUT" 'tcp port 7777 or udp port 8805 or sctp port 38412' || true
echo "done: python3 conformance/core_evidence.py $OUT"
