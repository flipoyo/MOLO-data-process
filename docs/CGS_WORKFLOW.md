# Workflow MOLONARI dans un Operating Space CGS

Depuis la racine du bootstrap (où `data/` est le dépôt MOLO-data-light) :

```bash
pixi install

# 1. Importer les exports HOBO V1 (P_measures.csv et T_measures.csv)
pixi run molonari import-data Point034 ./incoming/Point034/

# 2. Importer une calibration instrumentale depuis un CSV de capteur de pression
pixi run molonari import-calibration ./incoming/P508_T002.csv --id P508_T002

# 3. Affecter cette calibration au point
pixi run molonari assign-calibration Point034 P508_T002

# 4. Traiter les données brutes
pixi run molonari process Point034

# 5. Vérifier l'intégrité des données brutes
pixi run molonari verify Point034
```

Les commandes utilisent `--data-root data` par défaut. Ajouter `--data-root chemin` si le dépôt de données n'est pas dans `./data`.

## Format du CSV de calibration

Le CSV doit contenir les lignes `Intercept`, `dU/dH` et `dU/dT`, avec leur valeur dans la deuxième colonne. Les séparateurs virgule et point-virgule sont acceptés. Exemple :

```csv
Intercept;1.210344
dU/dH;-1.462495
dU/dT;0.001474
```

Les paramètres de thermistance `beta` et `v_ref` prennent par défaut les valeurs 3834 K et 3,3 V ; les options `--beta` et `--v-ref` permettent de les préciser.

L'import copie le CSV sans le modifier dans `data/calibration/instrumental/<ID>.csv`, ajoute les paramètres à `data/lab/lab.json` et conserve l'empreinte SHA-256 de la source. Un identifiant déjà présent n'est jamais écrasé.

Le traitement produit `data/processed_data/<POINT>/data/measures.csv` et `manifest.json`. Le manifeste enregistre la calibration effectivement utilisée. Cette calibration instrumentale est distincte de la calibration thermique, qui se configure avec `python -m raw_to_processed thermal-configure data`.
