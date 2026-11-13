# Changelog

## v1.2.0 — 2026-11-13

### Webhook 17track temps réel, changement de transporteur et relance rapide des nouveaux colis ([#2](https://github.com/antvgr/hacs-suivi-colis/pull/2))

#### Nouveaux colis : plus de « Inconnu » pendant 30 min

17track met de quelques secondes à plusieurs minutes pour récupérer un nouveau numéro chez le transporteur. Le rafraîchissement juste après l'ajout arrivait trop tôt et le suivant n'avait lieu que 30 minutes plus tard.
- L'intégration interroge à nouveau 17track à 1, 3 et 10 minutes tant que le colis n'a pas de données. Les minuteries sont annulées au déchargement.
- La carte affiche « En attente » au lieu de « Inconnu ».

#### Webhook 17track (temps réel, optionnel)

- Nouvel écran d'options **Notifications en temps réel** : il active le webhook et affiche l'URL à coller dans le tableau de bord 17track. C'est un cloudhook Nabu Casa si disponible, sinon l'URL externe.
- Chaque appel est vérifié avec l'en-tête `sign` de 17track (SHA-256 de corps + "/" + clé). Un appel non signé reçoit 401.
- `TRACKING_UPDATED` met le colis à jour immédiatement. `TRACKING_STOPPED` le signale (« Suivi arrêté par 17track » sur la carte).
- L'interrogation toutes les 30 minutes reste active en secours.

#### Service `suivi_colis.change_carrier`

- Suit un colis chez un autre transporteur : liste prédéfinie ou n'importe quel code 17track.
- Utilise `carrier_old` d'après les données 17track. Les erreurs (limite de 5 changements, etc.) sont remontées à l'appelant.
- Transporteurs ajoutés : Mondial Relay, Relais Colis, DPD, GLS, FedEx, TNT et Vinted Go, dans le sélecteur de la carte et les icônes.

#### Migrations

- Entrée de configuration **2.1 → 2.2** : ajout de l'identifiant secret du webhook. C'est une version mineure, donc une version 2.x antérieure lit encore l'entrée. Une entrée v1 passe d'abord par 2.1.
- Les nouveaux champs des colis (`carrier_code`, `tracking_stopped`) ont une valeur par défaut, donc les colis enregistrés par 1.0.x et 1.1.0 se chargent sans changement.

<details>
<summary>Détail des 4 commits</summary>

##### Nouveautés

- 17track webhook and change_carrier service ([0a2425a](https://github.com/antvgr/hacs-suivi-colis/commit/0a2425a7ad1c428bc7df74388f1542d32e81433c))
- Card : redesign in Home Assistant style, keep package names ([2f37022](https://github.com/antvgr/hacs-suivi-colis/commit/2f370229057f3316520bc562186800dbb453d3ff))

##### Corrections

- Poll 17track again shortly after adding a package ([d1bd2ce](https://github.com/antvgr/hacs-suivi-colis/commit/d1bd2ce80fe289710a25947518131cb04dc1ced4))

##### Documentation

- Describe the new card in the README ([5bc4cae](https://github.com/antvgr/hacs-suivi-colis/commit/5bc4caee744e5a24596d129fcb78887c34228daf))

</details>

---

**Mise à jour** : HACS → Suivi de Colis → Mettre à jour, puis redémarrer Home Assistant et recharger la page du navigateur pour obtenir la nouvelle carte.
**Toutes les modifications** : https://github.com/antvgr/hacs-suivi-colis/compare/v1.1.0...v1.2.0

## v1.1.0 — 2026-11-12

### Plusieurs boîtes mail IMAP, formulaire complet et API 17track v2.4 (corrige #2) ([#1](https://github.com/antvgr/hacs-suivi-colis/pull/1))

Même contenu que la PR proposée au dépôt original : hello-lemon/hacs-suivi-colis#3.

#### Résumé

- **IMAP** : correctif du bug `starttls` (hello-lemon/hacs-suivi-colis#2), plusieurs boîtes mail, nouveau formulaire de connexion (préréglages fournisseur, SSL/TLS / STARTTLS / aucun, certificat, dossiers lus sur le serveur, options d'analyse) et migration automatique de l'ancienne configuration.
- **17track** : codes transporteurs corrigés (DHL était envoyé comme FedEx), erreurs de quota bien détectées, passage à l'API v2.4 avec traduction des événements, sous-statut, date de livraison estimée, heure réelle de livraison et capteur de quota.
- **Carte** : affiche le sous-statut, la livraison prévue et l'heure de livraison.
- **CI** : hassfest, HACS et 39 tests à chaque push ; release automatique avec changelog à chaque merge sur `main`.
- **Licence MIT**, icônes déplacées dans `brand/`.

<details>
<summary>Détail des 3 commits</summary>

##### Documentation

- Add MIT license ([ff73489](https://github.com/antvgr/hacs-suivi-colis/commit/ff734896c0a1086a410639a4ed244942da39151a))

##### Maintenance

- HACS/hassfest validation, tests and automatic releases ([346a931](https://github.com/antvgr/hacs-suivi-colis/commit/346a9312fa54dc1b4618e943050246e64937b9c0))

##### Autres changements

- Multiple IMAP mailboxes, reworked email setup and 17track API v2.4 ([53835e4](https://github.com/antvgr/hacs-suivi-colis/commit/53835e4f0cf852ab233b87b29628297368529403))

</details>

---

**Mise à jour** : HACS → Suivi de Colis → Mettre à jour, puis redémarrer Home Assistant et recharger la page du navigateur pour obtenir la nouvelle carte.
**Toutes les modifications** : https://github.com/antvgr/hacs-suivi-colis/commits/v1.1.0
