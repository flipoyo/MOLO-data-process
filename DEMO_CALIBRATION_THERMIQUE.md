# Démonstration de calibration thermique avec cinq mesures fictives

Cette démonstration permet de parcourir le véritable assistant de déclaration thermique et de modifier le
`lab/lab.json` d'un dépôt **MOLO-data-light local**.

## Lancer

Depuis le dépôt `MOLO-data-process`, branche `CGS-MOLO` :

```bash
pixi run python demo_calibration_thermique.py
```

Le programme demande le chemin local du dépôt `MOLO-data-light`. Il lit ensuite le fichier :

```text
calibration/thermal/demo_5_mesures/RECORDS.csv
```

Le jeu contient exactement cinq lignes artificielles. Pour cette démonstration, les colonnes sont :
- `Value_Temperature_2` : température externe ;
- `Value_Temperature_3` : température interne ;
- `Value_Pressure` : pression.

Après avoir tapé `DEMO`, l'assistant demande l'identifiant de génération matérielle, le point d'échantillonnage à associer et la confirmation finale. Pour le fichier fourni, choisir le format `records` et reprendre la correspondance des colonnes affichée par le programme.

## Protection du laboratoire local

Le programme agit sur le véritable `lab/lab.json` du dépôt indiqué. Avant d'écrire, il crée une copie horodatée
`lab/lab.json.bak-AAAAmmjj-HHMMSS`. L'écriture n'a lieu qu'après validation des données et confirmation dans l'assistant.

**Important :** les cinq valeurs sont fictives et ne constituent pas une calibration physique. Cette démonstration sert
à tester le parcours utilisateur et la mise à jour du JSON, pas à corriger des données de terrain réelles.
