# molonari-pipeline : du brut de Toulouse aux données prêtes pour COMPUTO

Deux sources brutes (V1 : exports HOBO déposés à la main, V2 : messages LoRa reçus du serveur de Toulouse) sont
rangées par sampling point dans `raw_data/`, puis transformées de façon reproductible en `processed_data/`, que
COMPUTO (modèle direct, MCMC) peut lire. Un manifeste par point permet de prouver que le calcul se rejoue à l'identique.

## Architecture

```
MOLONARI-DATA/                                  (le dépôt de données ; ici « data_root »)
├── lab/lab.json                                laboratoire virtuel : appareils -> sampling point -> calibration
├── lab/hardware/                               configuration matérielle d'origine (capteurs de pression, tiges, thermistances)
├── raw_data/
│   ├── Point034/
│   │   ├── v2_lora/2026-11-09.jsonl            messages reçus, écrits par le receiver (un fichier par jour)
│   │   ├── v1_hobo/P_measures.csv, T_measures.csv   exports déposés, copiés sans modification
│   │   ├── legacy_csv/pressure.csv, temperature.csv exports des loggers 2015-2023, copiés tels quels
│   │   ├── meta/                               geometrie.txt, fichiers d'info, noms des fichiers d'origine
│   │   └── ingest.json                         empreinte de chaque fichier brut
│   ├── _unrouted/                              messages d'appareils inconnus du laboratoire
│   └── _invalid/                               messages illisibles
├── reference_data/Point034/                    résultats historiques de Molonaviz, pour CONTRÔLER la conversion
├── calibration/<point>/RECORDS.csv             expériences de calibration thermique (carte SD)
└── processed_data/
    └── Point034/
        ├── data/measures.csv                   Date, Temp1..4, TempBed, Pressure
        └── manifest.json                       entrées, calibration, paramètres, code, environnement, sorties
```

| Programme | Rôle | Déterministe ? |
|---|---|---|
| receiver (`receiver.py`) | écoute Toulouse, range chaque message dans `raw_data/<point>/` d'après le laboratoire | non : il écoute un flux en direct |
| import V1 (`datarepo.import_v1`) | copie des exports HOBO dans `raw_data/<point>/v1_hobo/` | oui |
| pipeline (`run_point`, `run_all`) | `raw_data/<point>/` -> `processed_data/<point>/` | oui |
| COMPUTO | lit `processed_data/<point>/` et vérifie le hash du manifeste | (hors de ce dépôt) |

Le brut est la frontière du déterminisme : tout ce qui est écrit dans `processed_data` doit pouvoir être recalculé
à l'identique à partir de `raw_data` et de `lab.json`.

```
RawV1 ─┐
        ├─> Converted ─> Ready ─> processed_data/<point>/
RawV2 ─┘
```

## Utilisation

```bash
python -m raw_to_processed import-v1   DATA_ROOT Point034 dossier_hobo/     # dépose P_measures.csv et T_measures.csv
python -m raw_to_processed ingest-file DATA_ROOT messages.jsonl             # range des messages déjà collectés
python -m raw_to_processed run         DATA_ROOT [Point034 ...]             # raw_data -> processed_data
python -m raw_to_processed verify      DATA_ROOT                            # le brut est-il intact ?
python -m raw_to_processed listen      DATA_ROOT config.json                # écoute le broker (voir plus bas)
python -m raw_to_processed import-dataanalysis MOLONARI1D/dataAnalysis DATA_ROOT   # données historiques + laboratoire
python -m raw_to_processed validate    DATA_ROOT [Point034 ...]             # écart avec les résultats de Molonaviz
python -m raw_to_processed records     RECORDS.csv [sortie.xlsx]            # RECORDS.csv -> Excel (nécessite openpyxl)
```

`--lab chemin/lab.json` remplace l'emplacement par défaut (`DATA_ROOT/lab/lab.json`).

Règles du brut :
- une ligne reçue n'est jamais modifiée ; le fichier d'un jour est l'ensemble trié des messages de ce jour, donc
  l'ordre et le nombre de livraisons ne changent pas les octets (donc pas le hash) ;
- un fichier V1 déjà présent et différent n'est jamais écrasé ;
- `run` refuse de traiter un point dont le brut ne correspond plus à `ingest.json` (`--no-verify` pour passer outre) ;
- si un point a du V1 et du V2, il faut choisir (`--source v1` ou `v2`).

## Lancer les tests et les démonstrations

L'environnement s'installe avec [pixi](https://pixi.sh), dans ce dépôt :

```bash
pixi install          # Python 3.12, pandas 3, numpy 2, protobuf, paho-mqtt et raw_to_processed en mode éditable
pixi run test         # les tests, sans réseau
pixi run demo         # V1 et V2 convergent à partir d'une même vérité synthétique
pixi run demo-repo    # le dépôt de données complet, avec receiver et import V1
```

`pixi.lock` fixe l'environnement exact. Les commandes `python -m raw_to_processed ...` ci-dessus se lancent dans cet
environnement avec `pixi run python -m raw_to_processed ...`. Dans l'arbre
[MOLONARI-computo](https://github.com/flipoyo/MOLONARI-computo), `pixi install` à la racine de l'arbre installe ce
paquet avec les autres, et `pixi run test-all` lance tous leurs tests.
Facultatifs : `git` (hash d'arbre) et ComplexGitSync (`CGS_SRC=<chemin>/ComplexGitSync/src`, avec `tomli_w`) pour vérifier que le JSON canonique est
identique au sien. Sans eux, les tests concernés sont ignorés.

## Contenu

| Fichier | Rôle |
|---|---|
| `raw_to_processed/lab.py` | Laboratoire virtuel : appareils, sampling points, calibrations, paramètres |
| `raw_to_processed/receiver.py` | Routage des messages par appareil, écriture du brut, `listen` (MQTT) |
| `raw_to_processed/datarepo.py` | Disposition du dépôt, intégrité du brut, import V1, `run_point`, `run_all` |
| `raw_to_processed/pipeline.py` | Les étapes et `process` (écrit les sorties et le manifeste) |
| `raw_to_processed/operators.py` | Fonctions pures portées de Molonaviz (conversions, dates, nettoyage) |
| `raw_to_processed/states.py` | Un type par état : `RawV1`, `RawV2`, `Converted`, `Ready`, `Calibration` |
| `raw_to_processed/readers.py` | Lecture des exports HOBO et codec Protobuf `SensorData` écrit à la main |
| `raw_to_processed/canonical.py` | JSON canonique, racine de Merkle d'un dossier, hash d'arbre git, CSV canonique |
| `raw_to_processed/dataanalysis.py` | Import de `MOLONARI1D/dataAnalysis` : brut, laboratoire, références |
| `raw_to_processed/validate.py` | Compare `processed_data` aux résultats historiques de Molonaviz |
| `raw_to_processed/thermal.py` | Calibration thermique par filtre (intégration de `calib.py`) |
| `raw_to_processed/records.py` | Lecture de RECORDS.csv (intégration de `records_to_excel.py`) |
| `raw_to_processed/virtual_lab.py`, `fixtures.py` | Vérité synthétique écrite en V1 et V2 ; faux dépôts de données, calibration synthétique |
| `config.example.json` | Modèle de configuration du receiver (la vraie ne va jamais dans git) |

## D'où viennent les opérateurs (dépôt MOLONARI1D)

| Ici | Dans Molonaviz |
|---|---|
| `adc_to_volt`, `int_to_iso8601` | `Molonaviz/src/receiver/decoder.py` |
| `volt_to_kelvin` | `backend/SPointCoordinator.py::calibrate_temperature` |
| `volt_to_pressure` | `frontend/dialogsCleanup.py::convertVoltagePressure` |
| `detect_date_format` | `utils/general.py::convertDates` |
| `iqr_outlier_mask`, `zscore_outlier_mask`, `outside_window_mask` | `frontend/dialogsCleanup.py` |
| `Calibration.from_pressure_sensor_csv` | lit `data/Laboratory/pressure_sensors/P*.csv` |

## Données historiques (dataAnalysis), contrôle et calibration thermique

### Import et source `legacy_csv`

`import-dataanalysis` range chaque point de `dataAnalysis/raw_data/pointNN_*/` dans `raw_data/Point0NN/legacy_csv/`
(`pressure.csv` et `temperature.csv`, **octets inchangés**) et copie `configuration/` dans `lab/hardware/`. Le
laboratoire `lab/lab.json` est construit à partir de ces fichiers et porte, par point, ce qui a été constaté dans les
fichiers : format des dates (un format, ou deux quand un fichier change de format en cours de route), décalage horaire
annoncé par l'en-tête du logger (`GMT+02:00`), `timestamps: strict` ou `intersection` quand les deux fichiers n'ont
pas les mêmes instants, capteur de pression. Un point qu'on ne sait pas importer est signalé, pas ignoré en silence.
Le lecteur (`read_legacy_csv`) accepte les variantes rencontrées : une ou deux lignes de titre, virgule ou
tabulation, colonne `#` présente ou non, colonnes vides, ligne « Fin de fichier ». Il ne convertit rien : les valeurs
sont déjà en °C et en volts. Quand le fuseau est inconnu (`utc_offset_hours: null`), l'heure d'origine est conservée
et le fichier de sortie n'a pas de suffixe `Z`.

### Contrôle de la conversion

`validate` compare `processed_data` aux fichiers `*_cleaned.csv` de dataAnalysis (`reference_data/`) sur les instants
communs. Résultat sur les 11 points de dataAnalysis :

| Point | Capteur | Verdict | Remarque |
|---|---|---|---|
| 014, 048 | P507, P606 | conforme | identique à l'arrondi près (pression à 5e-6) |
| 034, 036, 046, 047 | P508 | proche | pression à 5e-5 – 9e-5 près, températures exactes |
| 013 | P505 | écarts | `TempBed` et pression ne correspondent pas (voir ci-dessous) |
| 035 | P508 | écarts | idem, pression à 0,2 près ; changer de capteur (P505) ne change rien |
| 015, 052, 053 | P506, P052, P053 | pas de référence | converti, non contrôlé |

Ce que le contrôle a révélé :
- **Les références de 46, 47 et 48 ont des dates fausses** : les jours 1 à 12 de chaque mois sont écrits avec le mois
  avant le jour (« 07-01-2019 » pour le 1er juillet). L'ancienne chaîne confondait jour et mois ; c'est le piège de
  `convertDates` signalé plus haut. `validate` les répare par continuité et le dit (1056, 1049 et 6336 dates).
- **Les constantes de calibration de l'ancienne chaîne ne sont pas exactement celles de `configuration/`** : l'écart de
  pression est linéaire en température (≈ 2,8e-6 · T pour P508, résidu 5e-6), ce qui correspond à un `dU/dT`
  d'environ 4e-6 V/°C de différence. L'écart reste très en dessous de l'incertitude de mesure (Sigma_Meas_P = 0,01 V).
- **Points 13 et 35 : les deux loggers (pression et température) n'ont pas les mêmes horodatages.** La chaîne actuelle
  les aligne par instant. L'ancienne semble avoir associé les mesures par position : sur le point 13, décaler la
  série de pression de 6 lignes redonne exactement `TempBed` de la référence. Sur le point 35, aucun décalage ne
  suffit. **À trancher avec ceux qui connaissent les loggers** : quelle est la bonne association, et les horloges
  sont-elles synchronisées ?
- **Point 34 : le fichier de pression change de format de date au milieu** (12 h AM/PM puis 24 h, après passage par un
  tableur) ; **point 48 : après une remise à zéro du logger les dates ont des secondes** (14:06:21).
- **Le pipeline ne retire pas les valeurs aberrantes par défaut** (point 53 : jusqu'à 124 °C ; point 14 : 45 °C pour
  `TempBed`) et ne coupe pas au début de la mesure : les références, elles, ont été nettoyées. Les clés `rules` et
  `window` du laboratoire existent ; elles ne sont pas renseignées.
- Les fichiers `info` se contredisent sur le capteur du point 35 (`p508` ou `P505`) ; l'import suit `geometrie.txt`
  (`--sensor Point035=P505` pour imposer l'autre).

### Calibration thermique (`calib.py`)

`thermal.py` intègre la méthode de filtre : un filtre du premier ordre relie la température externe (rivière) à la
température interne du capteur, puis `k · (Ti − T_ref)` est retiré de la pression. Le calibrage lit une table dans le
dépôt de données (clé `thermal_calibration` du laboratoire, voir `lab.py`), au format RECORDS.csv avec la
correspondance de colonnes écrite dans le laboratoire (jamais devinée), ou un tableau aux colonnes de `calib.py`.

Différences avec `calib.py`, à valider avec ses auteurs :
- même modèle, résolu en forme linéaire (moindres carrés exacts) au lieu de `scipy.optimize.least_squares` : même
  optimum (un test le vérifie quand scipy est disponible), mais déterministe et sans dépendance à scipy ;
- le coefficient `a` est ramené au pas de temps des données de terrain (`a' = a ** (dt_terrain / dt_calibration)`) :
  c'est une approximation, `calib.py` supposait des pas identiques ;
- la correction se fait avant le nettoyage (elle exige des mesures régulières) et s'ajoute au terme `dU/dT · TempBed`
  de la formule de pression de Molonaviz : **à vérifier qu'elle ne compte pas deux fois la même dérive** ;
- la pression de la table de calibration doit être dans la même unité que `Pressure` en sortie ;
- la lecture d'un fichier Excel en dur à la fin de `calib.py` a été retirée.
Le manifeste enregistre le modèle ajusté, le pas de temps, le fichier de calibration et son SHA-256.

## Écarts voulus par rapport à Molonaviz

1. `adc_to_volt(0)` vaut 0 V (Molonaviz : NaN, à cause de `if not value`).
2. Les températures sont en °C dans `Converted` et `Ready`. Le chemin receveur de Molonaviz stocke des Kelvin alors
   que le nettoyage et `Compute.py` supposent des °C ; avec les coefficients du capteur P520, une température en
   Kelvin décale la pression d'environ 0,12.
3. Les dates sont lues avec un format et un décalage horaire explicites, enregistrés dans le manifeste.
4. Les messages en double (même appareil, même date) sont retirés (`dedupe`, activé par défaut, enregistré).
5. La précision canonique (3 décimales pour les températures, 5 pour la tension) est imposée dès l'état `Converted`.

## Ce que les tests prouvent, et ce qu'ils ne prouvent pas

Prouvent (60 tests, sans réseau) :
- les messages sont rangés par appareil et par jour de message, sans modification, sans doublon, et dans un ordre
  qui ne dépend pas de l'ordre d'arrivée ;
- un appareil inconnu ou un message illisible est conservé à part, jamais perdu ni traité ;
- une altération du brut est détectée et bloque le traitement ;
- deux points, l'un alimenté en V1 et l'autre en V2 par la même vérité synthétique, produisent le même
  `processed_data` (même hash) ; une relance sur une copie redonne le même manifeste ;
- changer la calibration du laboratoire change les sorties et l'empreinte du laboratoire, pas celle du brut ;
- le codec Protobuf produit et lit les mêmes octets que la bibliothèque officielle.

Ne prouvent pas :
- **La calibration thermique n'a été essayée que sur des données synthétiques** : le vrai fichier de calibration
  (`test_5h_calibration.xlsx`) et le rôle de ses colonnes ne sont pas dans le dépôt. Il faut dire quelle colonne est
  la température externe, l'interne et la pression avant de l'activer sur un vrai point.
- **`listen` n'a jamais tourné contre un vrai broker.** Les champs de `config.example.json` et le sujet MQTT sont
  des hypothèses (sujet ChirpStack habituel) à confirmer avec l'administrateur.
- La convergence V1/V2 est vraie par construction (vérité exprimée en comptes du convertisseur). Avec des données
  réelles, un logger a son propre décalage : il faudra une tolérance.
- Les opérateurs n'ont pas été comparés à la sortie réelle de Molonaviz ni testés sur `Point034`.
- Je n'ai pas résolu comment Molonaviz lit les exports HOBO bruts (`StudyHandler.importSPoint` renomme les colonnes
  avec 3 et 5 noms alors que `Point034` a une ligne de titre et plus de colonnes) ; le lecteur suit `Point034`.
- Une seule calibration de thermistance (`beta`, `V_ref`) sert aux cinq voies de température d'un point.
- Le laboratoire est un JSON dans ce prototype ; la spec cible un `lab.yaml` dans MOLONARI-TWIN.
- Git gère mal les gros fichiers : à terme (point ouvert 7 de la spec), `raw_data` devra sortir de git ou passer par
  un stockage dédié. Côté `.gitattributes`, déclarer les fichiers de données en `-text` pour que git ne modifie
  jamais leurs fins de ligne.

## Questions ouvertes

- Où vit le laboratoire (DATA, TWIN, HARDWARE) et qui peut le modifier ?
- Que hache Toulouse à l'envoi : le message JSON, la charge utile décodée, un fichier ?
- Le broker garde-t-il les messages pendant que le receiver est éteint (sinon, il faut un historique côté serveur) ?
- Quel fuseau pour les exports HOBO (l'en-tête de `Point034` annonce GMT+01:00, Molonaviz l'ignore) ?
- D'où vient `TempBed` côté V2 (`insertion_molonaviz.py` pose la question en commentaire) ?
- `raw_data` et `processed_data` : deux dépôts ou un seul ? (la granularité de la racine de Merkle de ComplexGitSync
  est le dépôt ; la spec cible les place dans MOLONARI-DATA, avec les mêmes droits de lecture.)

## Version

La version suit [SemVer](https://semver.org). `[workspace] version` de `pixi.toml` est la référence ;
`scripts/bump_version.py`, le même dans chaque dépôt de l'arbre MOLONARI-computo, la recopie dans chaque fichier qui
la reprend (`pyproject.toml`, `raw_to_processed/__init__.py`) :

```sh
pixi run bump-version minor --dry-run   # affiche le plan, n'écrit rien
pixi run bump-version patch             # ou minor, major ; --stage rc ajoute une étiquette de pré-version
pixi run check-version                  # chaque copie porte la version de pixi.toml (vérifié par GitHub Actions)
```

## Assistance par LLM

Une partie de ce dépôt (son environnement pixi et ce README) a été écrite avec l'aide de grands modèles de langage,
principalement Claude (Anthropic) avec Claude Code, utilisés comme service payant sous la direction du responsable
du projet, Nicolas Flipo. Ils sont mentionnés ici plutôt que crédités comme co-auteurs.
