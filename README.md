# nef_core_lab — tarification dynamique de la QoS exposée par un vrai NEF, devant un vrai cœur 5G

Ce dossier est **indépendant** de `nef_pricing_lab` : il ne le modifie pas, ne lit pas ses résultats
et écrit uniquement dans `nef_core_lab/results/`. Les mathématiques convenues (leader de Stackelberg,
équilibre de Nash des AF, sélection QoE-safe stricte, quantile conforme, admission et rationnement,
modèles QoE UIT) sont **importées** d'une copie figée du paquet de simulation, `vendor/nef_pricing/`,
et non réécrites. Si le banc ne donne rien d'exploitable, les résultats de simulation restent valables tels quels.

## Ce qui est réel, ce qui est émulé

| Élément | Statut | Détail |
|---|---|---|
| Cœur 5G | **réel** | Open5GS ≥ 2.7.2 : NRF, SCP, AUSF, UDM, UDR, PCF, BSF, NSSF, SMF, AMF, UPF |
| gNB + UE | **réels (logiciels)** | UERANSIM v3.2.6 : 64 UE enregistrés, une session PDU chacun |
| NEF | **réel** (ce travail) | API northbound AsSessionWithQoS (TS 29.122) ; southbound : enregistrement NRF, découverte du BSF, `pcfBindings` (TS 29.521), `Npcf_PolicyAuthorization` Create/Update/Delete (TS 29.514) |
| Chaîne de QoS | **réelle** | NEF → BSF → PCF → SMF (mise à jour de politique) → UPF (modification PFCP avec Create QER), visible dans la capture pcap |
| Trafic | **réel** | voix, jeu, vidéo adaptative, web, de chaque UE vers le serveur DN via gNB → N3 → UPF → N6 |
| Ordonnanceur radio | **émulé** | ni UERANSIM ni l'UPF d'Open5GS n'appliquent un débit garanti : un goulot HTB sur `ogstun` (R_tb) applique les GBR accordés, et le best-effort prend le reste |
| QoE | **mesurée → MOS** | mêmes modèles que la simulation : E-model G.107, P.1203 mode 0 si installé, IQX jeu, web logarithmique |
| Demande des AF | **modèle** | chaque AF calcule sa part à l'équilibre de Nash (paquet figé), puis envoie de vraies requêtes |
| Échelle | **réduite** | le banc est un échantillon 1/k de la cellule, avec des débits × s. Les rapports L/R, donc la congestion et les prix, sont identiques à la simulation |

## Arborescence

```
config/lab.yaml              configuration unique (échelle, cœur, NEF, profils QoS, trafic, scénario)
labcommon.py                 chargement de la configuration + accès au modèle figé
vendor/nef_pricing/          copie figée du paquet de simulation (NE PAS MODIFIER)
pricing/precompute_prices.py prix affichés de chaque politique pour les créneaux choisis (toute heure)
nef/                         le NEF : app.py (API), sbi.py (HTTP/2 vers le cœur), mapping.py (29.122 → 29.514),
                             admission.py, enforcer.py (HTB), pricing.py
traffic/                     serveurs DN, générateurs de trafic côté UE, mesure → MOS
scenario/                    run_scenario.py (scénario horaire générique, toute heure), measure/fit_externality.py (calibration sur le banc)
latency/load_test.py         latence de décision sous charge, avec et sans cœur
conformance/                 specs 3GPP officielles, tests actifs, validation OpenAPI, preuve pcap
sim/exp_q1_quality.py        amélioration du résultat de QoE (simulation) : qui gagne, filtrage par classe, décalage temporel
analysis/make_figures.py     figures et tableaux du banc (en anglais)
docker/                      docker compose : mongo, core, ue
tests/                       tests unitaires (cœur simulé, sans expérience)
lab.sh                       toutes les commandes
```

## Politiques comparées

| Clé | Description |
|---|---|
| `no_nef` | pas d'exposition (référence appariée) |
| `safe_strict` | **proposée**. QoE-safe stricte : MOS moyen et MOS du 5e centile ≥ sans NEF (η = 0, aucun paramètre libre), prix plancher = quantile conforme |
| `safe_gated` | **nouvelle**. `safe_strict` réservée aux classes dont le test de gain marginal est ≥ 0 ; les autres classes sont fermées |
| `safe_mean_eta` | QoE-safe sur la moyenne seule, η = 0,01 (un point de la frontière) |
| `quantile` | prix d'équilibre au quantile conforme de la demande (P(surcharge) ≤ ε) |
| `congestion_noext` | tarification de congestion qui ignore l'externalité best-effort |
| `flat_rev` | prix commercial fixe sur la journée (maximise le profit) |
| `revenue_max` | maximise le revenu par créneau |

Règles d'admission : `random` (premier arrivé), `priority` (sensibilité par unité de ressource, fenêtre de 1,5 s)
et `qoe_gain` (**nouvelle** : gain marginal de QoE de la classe par unité de ressource, puis priorité).

Voir `GUIDE.md` pour les commandes, dans l'ordre.
