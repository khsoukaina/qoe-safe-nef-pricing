"""Patch the stock Open5GS configuration (/etc/open5gs/*.yaml) in place, comments kept:
  * AMF NGAP server address  -> core bridge address (gNB in the UE container)
  * UPF GTP-U server address -> core bridge address (N3 from the gNB)
  * db_uri                   -> MongoDB container
Everything else keeps the Open5GS defaults (PLMN 999/70, TAC 1, SST 1, loopback SBI, NRF
127.0.0.10, SCP 127.0.0.200, PCF 127.0.0.13, BSF 127.0.0.15, UE pool 10.45.0.0/16).
"""
import argparse
import glob
import re
import sys


def replace_first_address_after(text, anchor, new_ip):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{anchor}:\s*$", line):
            ind = len(line) - len(line.lstrip())
            for j in range(i + 1, min(i + 12, len(lines))):
                l2 = lines[j]
                if l2.strip() and (len(l2) - len(l2.lstrip())) <= ind and not l2.lstrip().startswith("#"):
                    break
                m = re.match(r"^(\s*-?\s*address:\s*)(\S+)(.*)$", l2)
                if m:
                    lines[j] = f"{m.group(1)}{new_ip}{m.group(3)}"
                    return "\n".join(lines), m.group(2)
    return text, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-ip", required=True)
    ap.add_argument("--mongo", required=True)
    ap.add_argument("--dir", default="/etc/open5gs")
    a = ap.parse_args()
    ok = True
    for fn, anchor in ((f"{a.dir}/amf.yaml", "ngap"), (f"{a.dir}/upf.yaml", "gtpu")):
        t = open(fn).read()
        t2, old = replace_first_address_after(t, anchor, a.core_ip)
        if old is None:
            print(f"[patch] could not find '{anchor}: ... address:' in {fn}: edit it by hand", file=sys.stderr)
            ok = False
        else:
            open(fn, "w").write(t2)
            print(f"[patch] {fn}: {anchor} address {old} -> {a.core_ip}")
    for fn in glob.glob(f"{a.dir}/*.yaml"):
        t = open(fn).read()
        t2 = re.sub(r"^(\s*db_uri:\s*)\S+", rf"\g<1>{a.mongo}", t, flags=re.M)
        if t2 != t:
            open(fn, "w").write(t2)
            print(f"[patch] {fn}: db_uri -> {a.mongo}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
