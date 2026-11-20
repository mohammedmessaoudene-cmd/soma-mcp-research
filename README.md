# SOMA — kit de programmes instrumentés

Ce kit local relie des mesures mathématiques prises dans un programme, les commandes d'un agent, leur application contrôlée et la vérification de la sortie. Python 3.12 et sa bibliothèque standard suffisent. Le même noyau équipe un calcul de différences exactes et une fabrication d'archive. Un troisième module de réconciliation de stocks illustre le raccordement par le port public.

## Utiliser le kit

Depuis `kit`, donner deux répertoires de fichiers ordinaires et une destination neuve dont le parent existe :

```powershell
py -3.12 -B workflow.py run D:\donnees\avant D:\donnees\apres D:\livraisons\essai01
```

Le dossier `result` contient un patch rejouable, les fichiers reconstruits, leur ZIP, une extraction vérifiée et le rapport JSON. Le mode par défaut est un contrôleur classique, sans modèle IA. Pour connecter un modèle ou un autre décideur, ajouter `--controller stdio` et suivre le [port JSON](kit/WORKFLOW.md). Le paquet ne contient pas d'hôte ou de clé de modèle. Une décision persistante peut s'appliquer aux fichiers suivants ; le port retourne les observations et les effets.

Pour vérifier la livraison hors modèle depuis une copie propre, exécuter à la racine du paquet :

```powershell
py -3.12 -B reproduce.py D:\receptions\soma01
```

Cette commande vérifie le manifeste, copie le kit dans une destination neuve, teste les deux programmes, produit et rejoue une livraison sur les exemples, puis contrôle le troisième module et ses refus/reprises. Elle préserve les journaux. Elle ne rejoue aucune inférence ni ne mesure une amélioration d'un modèle.

## Résultats et limites

La campagne initiale a employé deux participants modèles, l'un avec les résumés calculés, l'autre avec les mêmes données primaires sans ces résumés. Ils ont fait les mêmes choix. Le diff exact et le patch minimal sont identiques au classique direct, avec 273 364 cellules. Les ZIP modèle font 263 434 octets ; le classique direct produit 251 536 octets et évite 65 536 octets de sondages. Aucun gain supplémentaire du résumé n'est établi.

Sur le coordinateur final de répertoires, un parcours modèle a livré les trois fichiers attendus en trois décisions, avec vérification du patch, de la reconstruction et du ZIP. Un second parcours a échoué après une erreur de recopie d'identifiant ; le port a refusé la commande sans l'appliquer. L'échec est conservé. Les correctifs ultérieurs du port ont leurs vérifications et leurs rejeux déterministes distincts.

Les comparaisons décrivent ces parcours ; elles ne mesurent pas un effet moyen et ne prouvent ni nouveauté générale, ni supériorité. Pour les tâches qui suivent une politique claire et stable, les résultats présents soutiennent l'usage du contrôleur classique disponible. Le kit sert à instrumenter et commander des programmes coopératifs lorsque cette interface est utile.

Le domaine porte sur des fichiers ordinaires locaux, avec limites de taille, calcul, actions et stockage. Les lignes du diff sont séparées par LF, octets et fins conservés ; les autres contenus admis peuvent être remplacés intégralement sans prétention de distance minimale. Les répertoires vides, métadonnées NTFS, concurrence hostile, effets distants et coupure électrique physique ne sont pas qualifiés. Le coût logique mesuré est distingué des E/S physiques, du RSS et du coût total d'un modèle, non mesurés. Voir [domaine, coûts et reprise](kit/WORKFLOW.md).

Les revues ont été réalisées avec des assistants IA distincts. Elles ne constituent pas une validation institutionnelle ou humaine indépendante. La décision de réception exacte figure dans `RECEPTION.json`. La licence de réutilisation n'est pas déclarée par le propriétaire ; voir [NOTICE](kit/NOTICE.md). Le paquet n'est pas présenté comme open source.

La réception globale est favorable pour ce périmètre local : A provient de ChatGPT externe ; B a été menée par un agent natif distinct, selon la relève prévue par le mandat après indisponibilité du canal externe. Aucun avis B externe ChatGPT n’est revendiqué. La revue technique distincte a contrôlé le candidat corrigé. Les fichiers du kit restent ceux de la réception.

La reproduction publique couvre le kit et les exemples fournis. Les documents et journaux privés de la comparaison historique restent hors du paquet ; ses résultats sont rapportés séparément, sans prétendre les reproduire par cette commande.
