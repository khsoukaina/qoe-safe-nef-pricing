#!/usr/bin/env bash
# Fetch the official 3GPP OpenAPI specifications (Release 18) used by the conformance check.
# Source: https://github.com/jdegre/5GC_APIs (mirror of the 3GPP CT4/CT3 OpenAPI files).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p specs
if [ ! -d specs/5GC_APIs ]; then
  git clone --depth 1 --branch Rel-18 https://github.com/jdegre/5GC_APIs specs/5GC_APIs
fi
git -C specs/5GC_APIs rev-parse HEAD > specs/COMMIT
echo "3GPP OpenAPI specs at commit $(cat specs/COMMIT)"
ls specs/5GC_APIs | grep -E "TS29122_AsSessionWithQoS|TS29514_Npcf_PolicyAuthorization|TS29521_Nbsf_Management|TS29510_Nnrf_NF(Management|Discovery)"
