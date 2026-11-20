# SOMA — kit de programmes instrumentés

Ce kit permet à un agent de lire des mesures mathématiques pendant le travail d'un programme Python coopératif, puis de lui demander une correction autorisée. Le programme conserve l'effet de la commande et poursuit la tâche. La livraison globale est en cours de réception ; les résultats historiques restent dans le dossier SOMA.

Il utilise Python 3.12 et sa bibliothèque standard. Depuis ce dossier, exécuter `py -3.12 -m soma_runtime --help`. Aucune base de données, aucun téléchargement de modèle et aucun service distant ne sont nécessaires au fonctionnement local. Le décideur se branche sur le port JSON ; les exemples classiques ne sont pas des appels à un modèle IA.

## Observer et agir

### Livrer une paire de répertoires

Depuis ce dossier, donner deux répertoires de fichiers ordinaires et un troisième chemin neuf. Les deux entrées et la destination ont des racines disjointes. Le parent de la destination doit exister.

```powershell
py -3.12 -B workflow.py run D:\donnees\avant D:\donnees\apres D:\livraisons\essai01
py -3.12 -B workflow.py replay D:\donnees\avant D:\livraisons\essai01\result\patch D:\livraisons\rejeu01
```

Avec `run`, le contrôleur classique poursuit dans la bande admissible, bascule vers la matrice complète quand la continuation devient impossible et choisit DEFLATE si le préfixe compressé est strictement plus petit que le préfixe brut. Le cas vide utilise STORE. Ce parcours local ne sollicite aucun modèle.

Après succès, ouvrir `result/report.json`. Le dossier `result/patch` est autonome avec la source d'origine. Le lanceur l'a déjà appliqué dans `result/reconstructed`. Les fichiers de cette reconstruction ont alimenté l'adaptateur d'archive. Le ZIP réellement créé, `result/bundle.zip`, est relu, ses méthodes contrôlées et ses membres écrits dans `result/extracted`. Le contrôle final compare chaque octet et tous les chemins de fichiers avec la cible figée. La publication de `result` intervient après ces vérifications. En cas d'échec, les états disponibles restent dans le dossier neuf pour diagnostic. Un autre essai emploie une nouvelle destination.

La commande `replay` applique uniquement le patch à une source conforme. Elle ne nécessite ni le dossier cible ni une inférence. Un rejeu de commandes conservées sur de nouvelles révisions est également une reproduction hors modèle. Les preuves des sessions de modèles et de la campagne historique sont gérées séparément.

Le [profil de livraison](WORKFLOW.md) décrit le port JSON par lignes, le domaine, les mesures communes, les limites et la comptabilité. L'interface de session présentée ci-dessous reste disponible pour connecter directement un programme.

Écrire un fichier `inputs.json` qui associe des noms aux fichiers source, et un fichier `config.json` pour le programme. Choisir un dossier de session neuf. Exemple pour le diff :

```json
{"source": "chemin/avant.txt", "target": "chemin/apres.txt"}
```

```json
{"rows_per_step": 128, "cell_budget": 1000000}
```

Le rayon initial doit être au moins égal à la différence des nombres de lignes. Il est préférable de l'omettre pour utiliser le défaut adapté aux entrées. Le budget de cellules limite le travail métier, et le plafond d'actions limite les commandes. Ces valeurs sont des configurations locales à choisir selon la tâche, pas des garanties universelles.

```powershell
py -3.12 -m soma_runtime init ma_session --adapter soma_runtime.adapters.diff --inputs inputs.json --config config.json --max-actions 40
py -3.12 -m soma_runtime observe ma_session
py -3.12 -m soma_runtime apply ma_session --request commande.json
py -3.12 -m soma_runtime journal ma_session
```

La commande contient les identifiants de l'observation courante, un `action_id` nouveau pour cette occurrence, une action offerte et son motif. Le reçu renvoie l'effet et l'observation suivante. Après perte de réponse, relire le journal ou représenter la même requête et le même identifiant ; cela relit le reçu. Une nouvelle commande avec une vieille révision est refusée. Les artefacts sont sous le `artifact_root` indiqué, relatif à la session. L'état `done` indique la fin du programme ; l'oracle distinct doit encore vérifier sa sortie.

Le [contrat d'adaptateur](ADAPTER_API.md) définit le port Python et le schéma complet. Il permet de raccorder un autre programme local sans modifier le noyau. Les entrées sont capturées au début de la session et contrôlées par empreinte. Le kit contrôle ses effets gérés par révision ; il ne confine pas un module Python malveillant.

## Deux programmes

**Diff exact.** Le programme compare deux textes UTF-8, sans normaliser leurs fins de ligne. Il traite les lignes par étapes dans une bande. Quand le calcul exclut le seuil choisi, l'agent peut élargir la bande ou reprendre en mode exact. Les cellules abandonnées et recalculées sont comptées. `patch.json` décrit le script et `reconstructed.txt` contient la cible reconstruite. La recherche par bande est une technique classique, présentée notamment par [Ukkonen (1985)](https://www.cs.helsinki.fi/u/ukkonen/InfCont85.PDF).

**Archive.** Le programme comprime réellement un préfixe du membre en cours. Il expose ses tailles et sa distribution d'octets. L'agent choisit ensuite compression ou stockage avant l'inscription du membre dans `bundle.zip`. La mesure ne garantit pas la compressibilité du reste du fichier. Les méthodes correspondent aux possibilités de [zipfile dans Python 3.12](https://docs.python.org/3.12/library/zipfile.html). La configuration `members` associe chaque nom d'entrée à un chemin relatif unique dans le ZIP ; `probe_bytes`, `chunk_bytes` et `read_budget` bornent les lectures.

## Réception et limites

Les tests de construction, les parcours du modèle, les témoins classiques et les oracles ont des rôles distincts. Les résultats exposé/masqué permettent de décrire les décisions observées ; ils ne prouvent pas à eux seuls une causalité du résumé ni une supériorité générale. La réception globale doit également vérifier un raccordement par un autre intégrateur sur une copie propre.

Le kit vise des points de contrôle coopératifs sous Python local. Il n'interrompt pas arbitrairement un binaire opaque. Les tests de reprise concernent un arrêt de processus et la visibilité des révisions ; ils ne certifient pas une coupure électrique physique. Aucune qualification industrielle, affiliation institutionnelle ou adoption indépendante n'est annoncée.
