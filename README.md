# QoE-Safe Pricing of Quality on Demand at the 5G NEF

Code, data and scripts for the paper *"QoE-Safe Pricing of Quality on Demand at the 5G Network Exposure Function"*.

The repository contains:

- a **3GPP-conformant Network Exposure Function (NEF)** that prices Quality-on-Demand (QoD) requests
  (`AsSessionWithQoS`, TS 29.122) and enforces them through a real 5G core (Open5GS);
- the **pricing mechanism**: Stackelberg leader (NEF) and Nash followers (AFs), externality-aware
  welfare, conformal minimum safe price, marginal-gain gating and QoE-safe selection;
- the **simulation experiments** (conformal coverage, exact 3-D Pareto front with the Choco solver);
- the **testbed experiments** (load regimes, unforecast surge, decision latency, 3GPP conformance);
- the scripts that regenerate every figure and table of the paper.

---

## 1. What is real and what is emulated

| Component | Status | Details |
|---|---|---|
| 5G core | **real** | Open5GS ≥ 2.7.2: NRF, SCP, AUSF, UDM, UDR, PCF, BSF, NSSF, SMF, AMF, UPF |
| gNB and UEs | **real (software)** | UERANSIM v3.2.6: 64 registered UEs, one PDU session each |
| NEF | **real (this work)** | Northbound `AsSessionWithQoS` (TS 29.122); southbound NRF registration, BSF discovery `pcfBindings` (TS 29.521), `Npcf_PolicyAuthorization` Create/Update/Delete (TS 29.514) |
| QoS chain | **real** | NEF → BSF → PCF → SMF (policy update) → UPF (PFCP Session Modification with Create QER), visible in the pcap capture |
| Traffic | **real** | voice, gaming, adaptive video and web, from each UE to the DN server through gNB → N3 → UPF → N6 |
| Radio scheduler | **emulated** | neither UERANSIM nor the Open5GS UPF enforces a guaranteed bit rate: an HTB bottleneck on `ogstun` (capacity R_tb) enforces the granted GBRs, and best effort gets the rest |
| QoE | **measured → MOS** | E-model (ITU-T G.107) for voice, P.1203 mode 0 for video if installed, IQX for gaming, logarithmic model for web |
| AF demand | **model** | each AF computes its Nash-equilibrium share, then sends real requests to the NEF |
| Scale | **reduced** | the testbed is a 1/k sample of the cell with rates scaled by s; load ratios L/R, hence congestion and prices, are those of the model (R_tb = 56.25 Mbit/s at scale 1/20) |

---

## 2. Repository structure

```
config/lab.yaml               single configuration file (scale, core, NEF, QoS profiles, traffic, scenario)
labcommon.py                  configuration loader and access to the pricing model
vendor/nef_pricing/           pricing model and mechanism (equilibrium, conformal, QoE-safe, Kelly, Pareto)
pricing/precompute_prices.py  posted prices of every policy for every slot -> results/prices/prices.json
nef/                          the NEF: app.py (API), sbi.py (HTTP/2 to the core), mapping.py (TS 29.122 -> TS 29.514),
                              admission.py, enforcer.py (HTB), pricing.py (price-table lookup)
traffic/                      DN servers, UE-side traffic generators, measurement -> MOS
scenario/                     run_scenario.py (hourly scenario, any load regime), externality measurement and fit
latency/load_test.py          decision latency under load, with and without the core
conformance/                  3GPP OpenAPI specifications, active tests, validation, pcap evidence
sim/                          simulation experiments (conformal coverage, Pareto front, QoE analysis)
analysis/make_figures.py      testbed figures and tables
provision/                    subscriber and PCC-rule provisioning of the core
docker/                       docker compose: mongo, core, ue
tests/                        unit tests (simulated core, synthetic data)
lab.sh                        all testbed commands
```

---

## 3. Requirements

- Linux (Ubuntu 22.04 or 24.04), Docker with the compose plugin
- 4 CPU cores and 8 GB RAM at least
- kernel module `sctp` (NGAP between gNB and AMF)
- Internet access to build the images (Open5GS PPA, UERANSIM, pip, 3GPP specifications on GitHub)
- for the Pareto experiment only: `pip3 install pychoco plotly` inside the core container

---

## 4. Quick start

```bash
sudo modprobe sctp
./lab.sh build          # ~15 min the first time
./lab.sh up
sleep 60
./lab.sh status         # expected: 11 NFs up, NEF answers, 64 PDU sessions, UE -> DN ping OK
./lab.sh check          # expected: 64/64 healthy UEs
./lab.sh test           # unit tests, simulated core, no traffic
```

If `status` reports 0 PDU sessions after a previous run, restart the stack:
`./lab.sh down && ./lab.sh up`, wait about 90 s, then run `./lab.sh status && ./lab.sh check` again.

Files written by the containers belong to root: run `sudo chown -R $USER results` if needed.

---

## 5. Reproducing the paper

Run the steps in this order. Durations are indicative.

| Step | Command | Output | Paper |
|---|---|---|---|
| Prices of every policy (10–40 min) | `./lab.sh precompute` | `results/prices/prices.json` | Sec. IV |
| 3GPP conformance (2 min) | `./lab.sh conformance` | `results/conformance/report.md`, `core_evidence.md`, `core_signalling.pcap` | Sec. V |
| Decision latency (6 min) | `./lab.sh latency --rates 5 10 20 50 100 --duration 30` then `./lab.sh figures --latency` | `figT1_latency`, `tableT_latency.md` | Sec. VI |
| Externality calibration (45 min) | `./lab.sh externality --levels 0.5 0.7 0.85 1.0 1.15 --shares 0.25 0.5 --repeats 2` then `./lab.sh figures --externality` | `figT5_externality`, `results/calibration_testbed.json` | Sec. V |
| Load regimes on the real core (4–5 h) | see below | `figT6_regimes_*`, `tableT_regimes_*.md` | Sec. VI |
| Conformal coverage (simulation) | see below | `figC1_coverage`, `figC2_cost_of_safety`, `figC3_surge_timeline`, `tableC_coverage.md` | Sec. VI |
| Exact 3-D Pareto front (simulation) | see below | `figP3_pareto3d_choco`, `figP3a_pareto3d`, `.html` | Sec. VI |

### Load regimes and unforecast surge (testbed)

```bash
./lab.sh scenario --policies no_nef safe_strict safe_gated quantile congestion_noext flat_rev revenue_max \
                  --rationing random --seeds 1 2 3 --tag hours_random
./lab.sh scenario --policies no_nef safe_strict safe_gated \
                  --rationing random --demand-mult 1.3 --seeds 4 5 6 --tag surge
./lab.sh figures --run results/testbed/hours_random results/testbed/surge
```

`no_nef` must always be in the list: it is the paired reference (same sessions, same UEs, same demand).
`--demand-mult 1.3` multiplies the realised demand while the NEF still prices on its forecast, which emulates an unforecast surge.

### Simulation experiments (inside the core container)

```bash
docker compose -f docker/docker-compose.yml exec core bash
cd /lab
python3 sim/exp_conformal_coverage.py              # add --quick for a smoke test (< 1 min)
pip3 install pychoco plotly                        # once
python3 sim/exp_pareto3d_choco.py                  # add --quick for a smoke test
```

Both scripts accept `--replot` (coverage) or `--plot-only` (Pareto) to redraw the figures from the cached results in seconds. To use the model calibrated on the testbed, prefix the commands with `NEF_LAB_CALIBRATION=results/calibration_testbed.json`.

---

## 6. Policies compared

| Key | Description |
|---|---|
| `no_nef` | no exposure (paired reference) |
| `safe_mean` | QoE-safe rule on the mean MOS only, minimum safe price from conformal prediction |
| `safe_strict` | **proposed**: mean MOS and 5th-percentile MOS no lower than without NEF, minimum safe price from conformal prediction |
| `safe_gated` | **proposed**: `safe_strict` posted only to the classes that pass the marginal-gain test; the other classes are closed |
| `quantile` | clearing price at the conformal quantile of demand, P(overload) ≤ ε |
| `congestion_noext` | congestion pricing that ignores the best-effort externality |
| `flat_rev` | fixed commercial price over the day (profit-maximising) |
| `revenue_max` | revenue-maximising price per slot |

Admission rules when demand exceeds capacity: `random` (excess requests rejected at random), `priority` (sensitivity per unit of resource) and `qoe_gain` (marginal QoE gain of the class per unit of resource).

---

## 7. Mechanism in brief

For each slot, offline:

1. **Response surfaces**: AF equilibrium for every price and demand scale on a grid.
2. **Minimum safe price**: clearing price at the conformal quantile of past demand, so that P(overload) ≤ ε without any assumption on the demand law (`vendor/nef_pricing/conformal.py`).
3. **Marginal-gain gating**: classes whose sale lowers the aggregate MOS at the forecast load are closed (`vendor/nef_pricing/qoe_safe.py`).
4. **QoE-safe selection**: the price that maximises expected welfare subject to the mean and 5th-percentile MOS constraints; QoD is closed if no price qualifies.

Online, for each `AsSessionWithQoS` request, the NEF reads the price table, checks admission and forwards the request through BSF → PCF → SMF → UPF. Pricing and admission take at most 0.21 ms.

The full proofs of the results stated in the paper are in `docs/technical_report.pdf`.

---

## 8. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `0 PDU sessions` | AMF unreachable over SCTP, or stale core state | `sudo modprobe sctp`; `./lab.sh down && ./lab.sh up`; check `./lab.sh logs amf` and `./lab.sh logs gnb` |
| UEs registered but ping to 10.60.0.1 fails | N3 address | check in `smf.log` that the announced UPF GTP-U address is 10.100.200.10 |
| `503 SOUTHBOUND_FAILURE … bsf_discovery 404` | UE without a PDU session | use an IP listed by `./lab.sh status` |
| `503 … pcf_create 403` | no PCC rule for the 5QI | add the rule in `provision/provision.py`, then `./lab.sh down && ./lab.sh up` |
| identical video MOS everywhere | P.1203 not installed (fallback model) | install `itu-p1203` in the ue container |

---

## 9. Citation

```bibtex
@inproceedings{qoesafe_nef,
  title     = {{QoE}-Safe Pricing of Quality on Demand at the {5G} Network Exposure Function},
  author    = {Anonymous},
  booktitle = {Under review},
  year      = {2026}
}
```

## 10. Licence

To be added.
