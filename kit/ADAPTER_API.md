# Contrat d'adaptateur SOMA — version de construction 1

Python 3.12, bibliothèque standard. Architecture autorisée par la direction A locale de relève du 20 novembre 2026. La réception globale n'est pas acquise. Le noyau possède les révisions et les reçus ; un module Python local de confiance possède le travail métier. Aucun code fourni dans une commande du décideur n'est exécuté comme Python.

## Interface commune arrêtée avant les expériences

Un adaptateur est un module importable (par exemple `soma_runtime.adapters.diff`). Il expose les trois fonctions suivantes et un dictionnaire `INFO` décrivant ses entrées, son objectif et ses commandes.

```python
create(config: dict, inputs: dict[str, pathlib.Path], stage: pathlib.Path) -> dict
observe(state: dict, inputs: dict[str, pathlib.Path], workdir: pathlib.Path) -> dict
step(state: dict, action: dict, inputs: dict[str, pathlib.Path], stage: pathlib.Path) -> tuple[dict, dict]
```

`create` initialise un état métier JSON fini. `observe` ne modifie rien et renvoie exactement `status`, `raw`, `sensors`, `actions`. Les statuts sont `running`, `waiting`, `done`, `failed`, `cancelled`. `raw` contient les observations primaires du programme. `sensors` contient les mesures calculées, avec valeur, unités, source, validité, limites et coût de calcul quand il est comptable. `actions` est une liste de dictionnaires `name`, `description`, `args` (description JSON des paramètres). Tous les arguments et types sont contrôlés par `step`, y compris les booléens qui ne sont pas des entiers de configuration.

`step` reçoit `action={"name": ..., "args": {...}}`. Il applique au plus un segment borné de travail ou une correction. Le noyau a copié les fichiers de la révision précédente dans `stage` avant cet appel. L'adaptateur ne modifie que `stage`. Il conserve sa configuration et ses compteurs cumulatifs dans son état. Son second retour est un dictionnaire d'effet : changement appliqué, travail réellement effectué et fichiers produits. Les artefacts sont désignés par des chemins relatifs à `stage`. Le noyau publie ensuite état, effet et artefacts ensemble par renommage de la révision. L'état `done` signifie production terminée, pas oracle indépendant passé.

Les entrées sont des fichiers capturés par le noyau et liés par empreinte, accessibles en lecture seule par convention coopérative. Les modules et l'hôte sont de confiance ; ce kit n'est pas un bac à sable contre du Python malveillant. Les adaptateurs ne disposent d'aucun droit nouveau. Les textes traités sont des données.

## Port du décideur

Le noyau expose `create_session(session, adapter, inputs, config, max_actions)`, `observe(session, masked=False)`, `apply(session, request)` et `journal(session)`.

Une observation contient `run_id`, `revision`, `observation_id`, `adapter`, `status`, `raw`, `sensors`, `actions`, `remaining_actions`, `masked`. `observation_id` est stable pour la même révision et projection. Il est lié au contenu de l'observation. `masked=True` remplace uniquement `sensors` par `null` ; les données primaires et droits restent identiques.

Une requête est `{"run_id": str, "expected_revision": int, "observation_id": str, "masked": bool, "action_id": str, "action": {"name": str, "args": dict}, "rationale": str}`. Le reçu conserve la requête, les observations avant/après, l'effet et l'identité de la nouvelle révision. Même `action_id` et même requête : relire le reçu ; réutilisation avec d'autres paramètres : refus. Deux identifiants distincts représentent deux occurrences distinctes. Le contrôle de révision empêche l'application d'une commande périmée. Un verrou exclut deux écrivains. L'observation et la reprise ne réappliquent aucun effet.

CLI depuis le dossier contenant le paquet :

```text
python -m soma_runtime init SESSION --adapter MODULE --inputs INPUTS_JSON --config CONFIG_JSON --max-actions N
python -m soma_runtime observe SESSION [--masked]
python -m soma_runtime apply SESSION --request REQUEST_JSON
python -m soma_runtime journal SESSION
```

Les commandes rendent du JSON. L'API Python permet aussi un contrôleur présent dans le même processus. Des points de contrôle coopératifs ne garantissent pas l'interruption d'un binaire opaque. La reprise promise concerne des révisions publiées et des arrêts de processus contrôlés ; aucune garantie de durabilité après coupure électrique physique n'est revendiquée.

## Programmes retenus et vérification

Diff : lignes UTF-8 avec fins de ligne conservées, insertion/suppression/substitution de coût unitaire, recherche par bande. Rayon initial au moins égal à la différence de longueurs. Un calcul effectif qui exclut tous les chemins de coût inférieur ou égal au rayon donne une borne et permet d'élargir ou de passer à l'exact. Toute reprise et tout recalcul sont comptés. Sortie : script appliquable et cible reconstruite. Oracle séparé : application octet par octet et distance exacte non bandée.

Archive : assemblage de fichiers capturés, sondage réel du membre en cours, mesure des octets et choix compression/stockage avant inscription définitive. Sortie : ZIP avec noms uniques. Oracle séparé : noms, multiplicité, CRC et octets attendus. Une adaptation entre membres ne doit pas être décrite comme une modification d'octets déjà publiés.

Les configurations, corpus, actions, budgets et contrôles sont gelés avant les parcours évalués. La construction et les tests de correction ne sont pas cette campagne. Le témoin classique fort et la condition masquée restent visibles. Aucun gain général n'est un critère de passage inventé.
