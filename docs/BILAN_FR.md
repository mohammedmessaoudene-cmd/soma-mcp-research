# SOMA R4 - bilan du pilote controle

Preversion de recherche du 14 novembre 2026. Ce pilote est LOCAL, avec un
controleur deterministe et des quotas synthetiques. Aucun client industriel,
revenu, avantage universel, nouveaute mondiale ou qualification de production
n'est etabli. Les hypotheses et recettes sont figees dans spec/.

Les trois bras partagent le noyau PostgreSQL et les garanties. Le conventionnel
minimal propage exactement ses propres effets; le conventionnel enrichi recoit
les memes observations autorisees que SOMA. Les 45 sessions SDK correspondent
aux attentes litterales des cinq recettes et aux oracles. Les decisions et
observations normalisees SOMA/conventionnel enrichi sont equivalentes.

Tests noyau : 22. Tests R4 : 8. Aucun test ignore.
La reprise supplementaire tue le vrai serveur MCP apres commit avant reponse,
puis utilise un nouveau client et serveur pour rejouer la meme requete: un effet.
Ce n'est pas un test de coupure electrique. Les octets sont ceux des reponses SDK
reserialisees, pas les octets bruts ni des tokens. Aucun appel LLM n'est execute.

Une difference d'appels face aux recus minimaux mesure la disponibilite des
observations pour cette politique, pas un avantage algorithmique propre a SOMA.
La valeur de l'integration chez un client et l'utilite pour un agent LLM restent
a mesurer. Voir la note anglaise pour les hypotheses, droits et limites.

Code ID: `52ad3953d38ac310fd520feceda665c89b057bffacb2d54d775968cde4a058f7`

Design ID: `4d6a1ae6a76f73e75ba35e26019f5015d8e113e9a2ab1efd6c9dd370db80f302`

| Scenario | Arm | RPC/session (3 repetitions) | Read-state/session | Normalized response bytes/session |
|---|---|---|---|---|
| steady | conventional | [13, 13, 13] | [1, 1, 1] | [5937, 5937, 5937] |
| steady | soma | [13, 13, 13] | [1, 1, 1] | [9244, 9244, 9244] |
| steady | conventional_enriched | [13, 13, 13] | [1, 1, 1] | [9166, 9166, 9166] |
| peer_changes | conventional | [17, 17, 17] | [3, 3, 3] | [7219, 7219, 7219] |
| peer_changes | soma | [17, 17, 17] | [3, 3, 3] | [11036, 11036, 11036] |
| peer_changes | conventional_enriched | [17, 17, 17] | [3, 3, 3] | [10946, 10946, 10946] |
| replay_after_peer | conventional | [16, 16, 16] | [2, 2, 2] | [7039, 7039, 7039] |
| replay_after_peer | soma | [14, 14, 14] | [1, 1, 1] | [9962, 9962, 9962] |
| replay_after_peer | conventional_enriched | [14, 14, 14] | [1, 1, 1] | [9878, 9878, 9878] |
| unknown_budget | conventional | [14, 14, 14] | [12, 12, 12] | [5310, 5310, 5310] |
| unknown_budget | soma | [14, 14, 14] | [12, 12, 12] | [8840, 8840, 8840] |
| unknown_budget | conventional_enriched | [14, 14, 14] | [12, 12, 12] | [8756, 8756, 8756] |
| revocation | conventional | [13, 13, 13] | [1, 1, 1] | [5135, 5135, 5135] |
| revocation | soma | [13, 13, 13] | [1, 1, 1] | [7423, 7423, 7423] |
| revocation | conventional_enriched | [13, 13, 13] | [1, 1, 1] | [7369, 7369, 7369] |
