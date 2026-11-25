# Tarification QoE-safe de la QoD au NEF — code, données et rapport technique

Ce dépôt accompagne l’article *QoE-Safe Pricing of Quality on Demand at the 5G Network Exposure Function* (soumis à IEEE/IFIP NOMS 2027). Il contient :

- un **NEF conforme 3GPP** qui fixe le prix de chaque requête QoD (`AsSessionWithQoS`, TS 29.122) devant un vrai cœur 5G Open5GS ;
- le **mécanisme de tarification** : jeu de Stackelberg (NEF meneur, AF suiveuses en équilibre de Nash), prix minimum sûr par prédiction conforme, test de gain marginal et règle de sélection QoE-safe ;
- les **expériences** de l’article : banc réel, couverture de la garantie conforme, front de Pareto exact (Choco) ;
- le **rapport technique** avec les preuves complètes (`docs/technical_report.pdf`).

---

## 1. Ce qui est réel, ce qui est émulé

| Élément | Statut | Détail |
|---|---|---|
| Cœur 5G | réel | Open5GS ≥ 2.7.2 : NRF, SCP, AUSF, UDM, UDR, PCF, BSF, NSSF, SMF, AMF, UPF |
| gNB et UE | réels (logiciels) | UERANSIM v3.2.6 : 64 UE enregistrés, une session PDU chacun |
| NEF | réel (ce travail) | API northbound `AsSessionWithQoS` (TS 29.122) ; southbound : découverte du BSF (TS 29.521) et `Npcf_PolicyAuthorization` (TS 29.514) |
| Chaîne de QoS | réelle | NEF → BSF → PCF → SMF → UPF (modification PFCP avec *Create QER*), visible dans la capture pcap |
| Trafic | réel | voix, jeu, vidéo adaptative et web, de chaque UE vers le serveur DN |
| Ordonnanceur radio | émulé | goulot HTB/netem sur `ogstun` : la capacité R est partagée entre débits garantis et best effort |
| QoE | mesurée puis convertie en MOS | E-model (G.107), P.1203 mode 0 si installé, IQX pour le jeu, modèle logarithmique pour le web |
| Échelle | réduite | échantillon 1/20 de la cellule (R = 56,25 Mbit/s) ; les rapports L/R, donc la congestion et les prix, sont conservés |

---

## 2. Arborescence

```
config/lab.yaml                 configuration unique (échelle, cœur, NEF, profils QoS, trafic, scénario)
labcommon.py                    chargement de la configuration et accès au modèle
vendor/nef_pricing/             modèle et mathématiques du mécanisme (ne pas modifier)
pricing/precompute_prices.py    prix affichés de chaque politique, par créneau horaire
nef/                            le NEF : API (app.py), SBI HTTP/2 (sbi.py), 29.122 → 29.514 (mapping.py),
                                admission (admission.py), application HTB (enforcer.py), lecture des prix (pricing.py)
traffic/                        serveurs DN, générateurs de trafic côté UE, mesure → MOS
scenario/                       scénarios horaires sur le banc, mesure de l’externalité
latency/                        latence de décision sous charge, avec et sans cœur
conformance/                    spécifications 3GPP officielles, validation OpenAPI, preuve pcap
sim/exp_conformal_coverage.py   couverture de la garantie conforme (4 lois de demande)
sim/exp_pareto3d_choco.py       front de Pareto exact des plans de prix du soir (Choco)
sim/exp_q1_quality.py           analyses complémentaires de QoE en simulation
analysis/make_figures.py        figures et tableaux du banc
docker/                         docker compose : mongo, core, ue
tests/                          tests unitaires (sans expérience)
docs/technical_report.pdf       preuves complètes
lab.sh                          toutes les commandes du banc
```

---

## 3. Prérequis

- Linux (Ubuntu 22.04 ou 24.04), Docker avec le plugin compose ;
- 4 cœurs et 8 Go de RAM au minimum ;
- le module noyau `sctp` (utilisé par NGAP entre le gNB et l’AMF) ;
- un accès Internet lors de la première construction des images.

Pour les expériences de simulation hors conteneur : Python ≥ 3.10 et

```bash
pip install -r requirements-core.txt pychoco plotly
```

---

## 4. Démarrage rapide

```bash
sudo modprobe sctp
./lab.sh build          # environ 15 min la première fois
./lab.sh up
sleep 60
./lab.sh status         # attendu : 11 fonctions réseau « up », le NEF répond, 64 sessions PDU
./lab.sh test           # tests unitaires et conformité des messages
./lab.sh precompute     # prix de chaque politique -> results/prices/prices.json
```

Les conteneurs écrivent en tant que root : si besoin, `sudo chown -R $USER results`.

---

## 5. Reproduire les résultats de l’article

| Résultat de l’article | Commande | Sortie |
|---|---|---|
| Conformité 3GPP (0 échec côté NEF) | `./lab.sh conformance` | `results/conformance/report.md`, `core_evidence.md` |
| Latence de décision | `./lab.sh latency --rates 5 10 20 50 100 --duration 30` puis `./lab.sh figures --latency` | `results/figures/figT1_latency.png` |
| Externalité mesurée D_i(L) | `./lab.sh externality --levels 0.5 0.7 0.85 1.0 1.15 --shares 0.25 0.5 --repeats 2` puis `./lab.sh figures --externality` | `results/figures/figT5_externality.png` |
| QoE par régime de charge (acheteurs, non-acheteurs, tous) | `./lab.sh scenario --policies no_nef safe_strict safe_gated … --rationing random --seeds 1 2 3 --tag <tag>` puis `./lab.sh figures --run results/testbed/<tag>` | `results/figures/figT6_regimes_random.png` |
| Couverture de la garantie conforme | `python3 sim/exp_conformal_coverage.py` | `results/sim/figC1_coverage`, `figC2_cost_of_safety`, `figC3_surge_timeline` |
| Front de Pareto exact (Choco) | `python3 sim/exp_pareto3d_choco.py` (après `./lab.sh precompute`) | `results/sim/` (front 3-D et carte des plans) |

`no_nef` doit toujours figurer dans les politiques d’un scénario : c’est la référence appariée (mêmes sessions, mêmes UE, même demande). Chaque script de simulation accepte `--quick`, qui vérifie la chaîne en quelques minutes sans reproduire les chiffres de l’article.

---

## 6. Politiques comparées

| Clé | Description |
|---|---|
| `no_nef` | sans exposition (référence) |
| `safe_mean` | QoE-safe : le MOS moyen ne baisse pas par rapport à `no_nef` |
| `safe_strict` | QoE-safe stricte : MOS moyen et MOS du 5e centile ne baissent pas |
| `safe_gated` | `safe_strict`, réservée aux classes dont le test de gain marginal est positif |
| `quantile` | prix minimum sûr seul (quantile conforme, P(surcharge) ≤ ε) |
| `congestion_noext` | prix de congestion qui ignore l’externalité sur le best effort |
| `flat_rev` | prix commercial fixe sur la journée |
| `revenue_max` | prix qui maximise le revenu par créneau |

Règles d’admission quand la demande dépasse la capacité : `random` (tirage aléatoire des requêtes admises), `priority` (par sensibilité) et `qoe_gain` (par gain marginal de QoE).

---

## 7. Citation

```bibtex
@inproceedings{qoesafe_nef_2027,
  title     = {{QoE}-Safe Pricing of Quality on Demand at the {5G} Network Exposure Function},
  author    = {Anonymous},
  booktitle = {IEEE/IFIP Network Operations and Management Symposium (NOMS)},
  year      = {2027},
  note      = {under review}
}
```

---

## 8. Licence

Voir le fichier `LICENSE`.
