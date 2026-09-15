# Sur le réseau — ne le faites pas sans le vouloir

L'atelier écoute sur `127.0.0.1` et **n'a aucune authentification**. C'est un
outil de poste de travail : il compte sur le fait d'être joignable depuis votre
seule machine, et rien d'autre ne l'oppose à qui atteint le port.

## Ce que peut faire quiconque l'atteint

- exécuter du SQL et du Jinja dbt quelconques sur votre entrepôt — c'est ce que
  fait une recipe [SQL](sql.md), et son aperçu ;
- écrire, renommer et supprimer les fichiers de vos projets dbt ;
- supprimer un dossier de projet entier ;
- lire vos profils dbt tels que dbt les résout.

## Ce que les parades couvrent, et ce qu'elles ne couvrent pas

L'atelier porte un contrôle du nom d'hôte, un contrôle de l'origine sur le
WebSocket, et le refus d'un corps JSON sans `Content-Type`. Ces trois parades
visent **une seule attaque** : une page web qui essaierait de parler à un
atelier *local* depuis votre navigateur.

> [!attention] Aucune de ces parades ne protège du réseau. Elles empêchent une page étrangère de piloter votre atelier ; elles n'empêchent personne d'ouvrir l'adresse s'il peut atteindre le port.

## Sortir de la boucle locale

`--host` hors boucle locale exige donc `--exposer`, qui dit que vous l'assumez.
Le plus souvent, ce n'est pas ce qu'il faut : pour joindre un atelier distant,
laissez-le sur `127.0.0.1` et passez par un tunnel.

```bash
ssh -L 8765:127.0.0.1:8765 la-machine   # puis http://127.0.0.1:8765 chez vous
```

Le tunnel donne exactement ce qu'on cherchait — l'atelier d'une machine
distante, dans votre navigateur — sans ouvrir de port à qui que ce soit
d'autre, et en réutilisant l'authentification SSH que vous avez déjà.
