# Guide d'exécution (toutes les commandes)

Prérequis : Linux (Ubuntu 22.04 ou 24.04 testés côté conception), Docker avec le plugin compose,
4 cœurs et 8 Go de RAM au minimum, le module noyau `sctp`, et un accès Internet pour la
construction des images (PPA Open5GS, UERANSIM, pip, specs 3GPP sur GitHub).

```bash
cd ~/Downloads && unzip nef_core_lab.zip && cd nef_core_lab
sudo modprobe sctp            # NGAP (gNB <-> AMF) utilise SCTP
```

## Étape 0 — Construire et démarrer (environ 15 min la première fois)

```bash
./lab.sh build
./lab.sh up
sleep 60
./lab.sh status
```

Résultat attendu de `status` :
- 11 fonctions réseau marquées `up` ;
- le NEF répond (un JSON) ;
- `64 PDU sessions` ;
- le ping UE → 10.60.0.1 passe à 0 % de perte.

Les journaux sont dans `results/core_logs/` (amf.log, smf.log, pcf.log, nef.log, gnb.log, ue.log…).
Les fichiers créés par les conteneurs appartiennent à root : `sudo chown -R $USER results` si besoin.

## Étape 1 — Tests unitaires (1 min)

```bash
./lab.sh test
```

Les tests tournent avec un cœur simulé, sans trafic. Ils vérifient aussi la conformité de nos
messages aux specs 3GPP. Attendu : `10 passed`.

## Étape 2 — Prix de chaque politique (10 à 40 min selon la machine)

```bash
./lab.sh precompute
```

Sortie : `results/prices/prices.json`. Le script affiche les prix par créneau et le quantile conforme.
`./lab.sh precompute --quick` existe, mais il ne sert qu'à vérifier la chaîne.

## Étape 3 — Preuve de conformité de l'API et de la chaîne cœur (2 min)

```bash
./lab.sh conformance
```

Cette commande produit :
- `results/conformance/report.md` : chaque message northbound (TS 29.122) et southbound (TS 29.514,
  TS 29.521, TS 29.510) est validé contre les fichiers OpenAPI officiels de la Release 18. La ligne
  « NEF-side failures » doit valoir 0. Les réponses d'Open5GS sont signalées à part, à titre informatif ;
- `results/conformance/core_evidence.md` : à partir de la capture pcap, on retrouve les POST
  `npcf-policyauthorization/.../app-sessions`, les `PFCP Session Modification Request` contenant
  `Create QER`, et les messages NGAP ;
- `results/core_logs/core_signalling.pcap`, à ouvrir dans Wireshark pour une figure ou une annexe.

En cas d'échec, regarde `results/core_logs/pcf.log`. Le message « No PCC Rule in DB [QoS Index:x] »
signifie qu'il faut ajouter au type de média concerné une règle PCC avec ce 5QI dans
`provision/provision.py`, puis relancer `./lab.sh down && ./lab.sh up`.

## Étape 4 — Latence de décision réelle (environ 6 min)

```bash
./lab.sh latency --rates 5 10 20 50 100 --duration 30
./lab.sh figures --latency
```

La commande mesure deux configurations : NEF + vrai cœur, puis NEF seul (redémarré automatiquement en
`--southbound none`), ce qui isole le coût de la signalisation du cœur.
Figures : `results/figures/figT1_latency.png` et `tableT_latency.md` (p50, p95, p99).

## Étape 5 (recommandée) — Calibrer l'externalité sur le vrai plan utilisateur (environ 45 min)

```bash
./lab.sh externality --levels 0.5 0.7 0.85 1.0 1.15 --shares 0.25 0.5 --repeats 2
./lab.sh figures --externality
```

Sorties :
- `figT5_externality.png` : D_i(L) mesuré, comparé à la courbe du modèle ;
- `results/calibration_testbed.json`.

Pour rejouer toute la tarification avec le modèle calibré sur le banc (optionnel, à mentionner dans
l'article si tu le fais) :

```bash
docker compose -f docker/docker-compose.yml exec core bash -c \
  "cd /lab && NEF_LAB_CALIBRATION=results/calibration_testbed.json python3 pricing/precompute_prices.py"
```

## Étape 6 — Scénario horaire sur le vrai cœur (toute heure, tous régimes de charge)

Essai rapide (environ 15 min) :

```bash
./lab.sh scenario --quick --policies no_nef safe_strict quantile revenue_max --tag quick
./lab.sh figures --run results/testbed/quick
```

Campagne pour l'article (environ 4 à 5 h par règle d'admission ; elle peut tourner la nuit) :

```bash
./lab.sh scenario --policies no_nef safe_strict safe_gated quantile congestion_noext flat_rev revenue_max \
                  --rationing random --seeds 1 2 3 --tag hours_random
./lab.sh scenario --policies no_nef safe_strict safe_gated quantile revenue_max \
                  --rationing qoe_gain --seeds 1 2 3 --tag hours_qoegain
./lab.sh figures --run results/testbed/hours_random results/testbed/hours_qoegain
```

`no_nef` doit toujours figurer dans la liste : c'est la référence appariée (mêmes sessions, mêmes UE,
même demande). Les figures produites :
- `figT2_qoe_by_class_*` : Δ MOS mesuré par classe ;
- `figT3_who_gains_*` : sessions garanties contre toutes les autres, et part de mécontents ;
- `figT4_timeline_*` : prix, sessions accordées et MOS heure par heure ;
- `tableT_scoreboard_*.md` : le tableau final.

## Étape 7 — Amélioration du résultat de QoE en simulation (20 à 60 min)

```bash
./lab.sh sim
```

Figures dans `results/sim/` :
- `figQ1a_who_gains` : qui gagne, qui paie ;
- `figQ1b_gating` : stricte contre filtrage par classe ;
- `figQ1c_shift` : décalage temporel, φ.

Les chiffres sont dans `q1_key_numbers.json`. `./lab.sh sim --quick` sert à vérifier la chaîne.

## Ce qu'il faut m'envoyer pour l'analyse

1. `results/conformance/report.md` et `core_evidence.md` ;
2. `figT1_latency.png` et `tableT_latency.md` ;
3. `figT2`, `figT3` et `figT4`, ainsi que les `tableT_scoreboard_*.md` ;
4. `figT5_externality.png` et la sortie console de `fit_externality` ;
5. `results/sim/figQ1a`, `figQ1b`, `figQ1c` et `q1_key_numbers.json`.

## Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| `0 PDU sessions` | AMF injoignable en SCTP | `sudo modprobe sctp`, puis `./lab.sh logs amf` et `./lab.sh logs gnb` |
| UE enregistrés, mais le ping vers 10.60.0.1 échoue | adresse N3 | vérifier dans `smf.log` l'adresse GTP-U de l'UPF annoncée (elle doit valoir 192.168.250.10) |
| `503 SOUTHBOUND_FAILURE … bsf_discovery 404` | UE sans session PDU | utiliser une IP listée par `./lab.sh status` |
| `503 … pcf_create 403` | règle PCC absente pour le 5QI | voir l'étape 3 |
| `nrf_register` refusé | version d'Open5GS | sans effet sur le reste : le BSF est alors pris dans `core.bsf_uri` |
| MOS vidéo identiques partout | P.1203 non installé (modèle de repli) | le préciser dans l'article, ou installer `itu-p1203` dans le conteneur ue |
| Le script de scénario annonce plus de sessions que d'UE | demande tirée trop haute | augmenter `core.ue_count` et `UE_COUNT` (compose), puis `./lab.sh wipe && ./lab.sh up` |
