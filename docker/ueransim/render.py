"""Render the UERANSIM gNB / UE configuration (Open5GS defaults: PLMN 999/70, TAC 1, SST 1)."""
import os

core = os.environ.get("CORE_IP", "192.168.250.10")
gnb = os.environ.get("GNB_IP", "192.168.250.20")

open("/tmp/gnb.yaml", "w").write(f"""mcc: '999'
mnc: '70'
nci: '0x000000010'
idLength: 32
tac: 1
linkIp: {gnb}
ngapIp: {gnb}
gtpIp: {gnb}
amfConfigs:
  - address: {core}
    port: 38412
slices:
  - sst: 1
ignoreStreamIds: true
""")

open("/tmp/ue.yaml", "w").write(f"""supi: 'imsi-999700000000001'
mcc: '999'
mnc: '70'
protectionScheme: 0
homeNetworkPublicKey: '5a8d38864820197c3394b92613b20b91633cbd897119273bf8e4a6f4eec0a650'
homeNetworkPublicKeyId: 1
routingIndicator: '0000'
key: '465B5CE8B199B49FAA5F0A2EE238A6BC'
op: 'E8ED289DEABA952E4CB592F3F0D3FBC0'
opType: 'OPC'
amf: '8000'
imei: '356938035643803'
imeiSv: '4370816125816151'
gnbSearchList:
  - {gnb}
uacAic:
  mps: false
  mcs: false
uacAcc:
  normalClass: 0
  class11: false
  class12: false
  class13: false
  class14: false
  class15: false
sessions:
  - type: 'IPv4'
    apn: 'internet'
    slice:
      sst: 1
configured-nssai:
  - sst: 1
default-nssai:
  - sst: 1
integrity:
  IA1: true
  IA2: true
  IA3: true
ciphering:
  EA1: true
  EA2: true
  EA3: true
integrityMaxRate:
  uplink: 'full'
  downlink: 'full'
""")
print("[ue] rendered /tmp/gnb.yaml and /tmp/ue.yaml")
