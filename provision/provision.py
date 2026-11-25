"""Provision the Open5GS subscribers (MongoDB, schema used by Open5GS 2.7 / its WebUI).

UEs: IMSI 999700000000001 ... (count), K / OPc = UERANSIM defaults, DNN 'internet', SST 1,
session AMBR 1 Gbit/s (the UPF must not limit anything: the bottleneck is the emulated
scheduler). Two PCC-rule templates per subscriber, 5QI 1 (AUDIO) and 5QI 2 (VIDEO): the
Open5GS PCF needs a rule with the 5QI of the media type to accept an
Npcf_PolicyAuthorization request ("No PCC Rule in DB [QoS Index:x]"). They carry no flow, so
they act only as templates (ARP); the flows and bit rates come from the NEF request.

Usage:  python provision/provision.py --mongo mongodb://192.168.250.5/open5gs --count 64
"""
from __future__ import annotations

import argparse
import sys
import time

from bson import ObjectId
from pymongo import MongoClient

K = "465B5CE8B199B49FAA5F0A2EE238A6BC"
OPC = "E8ED289DEABA952E4CB592F3F0D3FBC0"
PLMN = "99970"


def br(value, unit):
    return {"value": value, "unit": unit}          # unit: 0 bps, 1 Kbps, 2 Mbps, 3 Gbps


def pcc_template(qi, arp_pl):
    return {"_id": ObjectId(), "flow": [],
            "qos": {"index": qi, "arp": {"priority_level": arp_pl, "pre_emption_capability": 2,
                                         "pre_emption_vulnerability": 2},
                    "mbr": {"downlink": br(100, 2), "uplink": br(100, 2)},
                    "gbr": {"downlink": br(100, 2), "uplink": br(100, 2)}}}


def subscriber(imsi):
    return {
        "schema_version": 1, "imsi": imsi, "msisdn": [], "imeisv": [], "mme_host": [], "mm_realm": [],
        "purge_flag": [],
        "security": {"k": K, "op": None, "opc": OPC, "amf": "8000"},
        "ambr": {"downlink": br(1, 3), "uplink": br(1, 3)},
        "slice": [{"_id": ObjectId(), "sst": 1, "default_indicator": True, "session": [{
            "_id": ObjectId(), "name": "internet", "type": 3,
            "qos": {"index": 9, "arp": {"priority_level": 8, "pre_emption_capability": 1,
                                        "pre_emption_vulnerability": 1}},
            "ambr": {"downlink": br(1, 3), "uplink": br(1, 3)},
            "pcc_rule": [pcc_template(1, 2), pcc_template(2, 4)]}]}],
        "access_restriction_data": 32, "network_access_mode": 0, "subscriber_status": 0,
        "operator_determined_barring": 0, "subscribed_rau_tau_timer": 12, "__v": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mongo", default="mongodb://127.0.0.1/open5gs")
    ap.add_argument("--count", type=int, default=64)
    ap.add_argument("--first", type=int, default=1)
    a = ap.parse_args()
    for attempt in range(30):
        try:
            cli = MongoClient(a.mongo, serverSelectionTimeoutMS=2000)
            cli.admin.command("ping")
            break
        except Exception as e:
            print(f"[provision] waiting for MongoDB ({e.__class__.__name__})")
            time.sleep(2)
    else:
        sys.exit("[provision] MongoDB unreachable")
    db = cli.get_default_database()
    for n in range(a.first, a.first + a.count):
        imsi = f"{PLMN}{n:010d}"
        db.subscribers.replace_one({"imsi": imsi}, subscriber(imsi), upsert=True)
    print(f"[provision] {a.count} subscribers ready ({PLMN}{a.first:010d} ...), "
          f"total in DB: {db.subscribers.count_documents({})}")


if __name__ == "__main__":
    main()
