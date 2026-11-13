# Suivi de Colis

Intégration Home Assistant pour suivre vos colis avec l'[API 17track](https://api.17track.net/en/doc) : plus de 1 200 transporteurs (Colissimo, Chronopost, Mondial Relay, DHL, UPS, Amazon, Cainiao…), ajout manuel ou détection automatique depuis vos emails, notifications en temps réel et carte Lovelace intégrée.

- [Fonctionnalités](#fonctionnalités)
- [Installation](#installation)
- [Configuration](#configuration)
- [Paramètres de l'intégration](#paramètres-de-lintégration)
- [Entités](#entités)
- [Carte Lovelace](#carte-lovelace)
- [Services](#services)
- [Automatisations](#automatisations)
- [Dépannage](#dépannage)
- [Désinstallation](#désinstallation)
- [Développement](#développement)

## Fonctionnalités

- **Un capteur par colis**, avec le statut (traduit dans l'interface), le statut détaillé (douane, destinataire absent, adresse invalide, retour à l'expéditeur, perdu…), le transporteur, le lieu, la date de livraison prévue, l'heure réelle de livraison et l'historique complet.
- **Capteurs de synthèse** : colis en cours, livraisons du jour, colis à retirer, prochaine livraison, problème de livraison.
- **Événement à chaque changement de statut**, pour vos automatisations.
- **Ajout manuel** depuis la carte ou par service, avec un **nom** et un transporteur facultatifs.
- **Détection depuis les emails** (facultatif) : une ou plusieurs boîtes IMAP, chaque boîte pouvant être ajoutée, modifiée, suspendue ou supprimée à tout moment.
- **Temps réel** (facultatif) : 17track prévient Home Assistant dès qu'un colis change, au lieu d'attendre la prochaine vérification.
- **Événements en français** quand le transporteur fournit une traduction officielle.
- **Carte Lovelace** au style de Home Assistant, en thème clair ou sombre.
- **Archivage automatique** des colis livrés après un délai réglable.

## Installation

### Avec HACS (recommandé)

1. HACS → menu ⋮ → **Dépôts personnalisés** → ajouter `https://github.com/hello-lemon/hacs-suivi-colis`, catégorie **Intégration**.
2. Rechercher **Suivi de Colis** dans HACS et l'installer.
3. Redémarrer Home Assistant.

Les mises à jour sont ensuite proposées par HACS. Home Assistant **2025.1** ou plus récent est nécessaire.

### Manuelle

Copier le dossier `custom_components/suivi_colis/` dans le dossier `config/custom_components/` de Home Assistant, puis redémarrer.

## Configuration

**Paramètres → Appareils et services → Ajouter une intégration → Suivi de Colis.**

### 1. Clé API 17track (obligatoire)

1. Créer un compte sur [api.17track.net](https://api.17track.net/register).
2. Dans le tableau de bord 17track : **Settings → Security → API Key**, copier la clé.
3. La coller dans Home Assistant. Elle est vérifiée immédiatement.

**Quota** : chaque **nouveau** numéro enregistré consomme 1 crédit 17track, et les mises à jour d'un colis déjà suivi sont gratuites. Depuis le 7 janvier 2026, 17track n'offre plus 100 suivis gratuits chaque mois : un nouveau compte reçoit une dotation unique de 200 numéros. Le capteur **Quota 17track restant** permet de surveiller le solde.

### 2. Boîtes mail (facultatif)

À la fin de l'installation, un menu propose **Ajouter une boîte mail** ou **Terminer**. Aucune boîte n'est obligatoire : sans boîte, les colis s'ajoutent à la main. Vous pourrez en ajouter, en modifier ou en supprimer plus tard dans les [paramètres](#paramètres-de-lintégration).

Pour chaque boîte :

1. **Fournisseur** : préremplit le serveur, le port et le chiffrement. Choix possibles : Gmail, Outlook, Yahoo, iCloud, Orange, Free, SFR, La Poste, GMX, IONOS, OVH, Proton Mail Bridge, ou « Autre » pour tout saisir.
2. **Connexion**, testée dès la validation, avec un message d'erreur précis (identifiants refusés, certificat, port, serveur introuvable, délai dépassé…) :
   - nom affiché, serveur et port (vide = port standard) ;
   - chiffrement : **SSL/TLS** (993), **STARTTLS** (143) ou **aucun** ;
   - vérification du certificat, à désactiver seulement pour un serveur local au certificat auto-signé ;
   - identifiant, mot de passe et délai d'attente.
3. **Analyse** :
   - **dossiers** à analyser, choisis dans la liste lue sur le serveur (plusieurs possibles, ou saisie libre) ;
   - **boîte dédiée** : analyse tous les emails et pas seulement ceux des transporteurs connus. Idéal pour une adresse qui ne reçoit que des avis d'expédition ;
   - période analysée en jours et nombre maximum d'emails par dossier ;
   - uniquement les non lus, et/ou marquer comme lus les emails où un numéro a été trouvé (sinon les emails ne sont jamais modifiés) ;
   - boîte **active** ou **suspendue**.

> Gmail, Yahoo et iCloud exigent un **mot de passe d'application** : le mot de passe du compte est refusé. Les comptes Microsoft (Outlook, Hotmail) n'acceptent plus que l'authentification OAuth2, qui n'est pas prise en charge pour l'instant.

## Paramètres de l'intégration

**Paramètres → Appareils et services → Suivi de Colis → Configurer** ouvre un menu :

| Menu | Rôle |
|------|------|
| **Paramètres généraux** | Délai d'archivage des colis livrés (jours, 0 = jamais) et intervalle de vérification des emails (minutes). |
| **Ajouter une boîte mail** | Même assistant qu'à l'installation. |
| **Modifier une boîte mail** | Toutes les options d'une boîte. Laisser le mot de passe vide pour le conserver. |
| **Supprimer des boîtes mail** | Retire une ou plusieurs boîtes. Leur capteur de diagnostic disparaît, et les colis déjà détectés sont conservés. |
| **Notifications en temps réel** | Active le webhook 17track et affiche l'URL à copier (voir ci-dessous). |

### Notifications en temps réel (facultatif)

Sans webhook, l'intégration interroge 17track toutes les 30 minutes, et plus souvent juste après l'ajout d'un colis. Avec le webhook, 17track prévient Home Assistant à chaque changement :

1. **Configurer → Notifications en temps réel** : activer l'option et copier l'URL affichée.
2. La coller dans le [tableau de bord 17track](https://api.17track.net/admin/settings), section **WebHook**, puis enregistrer et cliquer sur le bouton de test.

Home Assistant doit être joignable depuis Internet. Avec Nabu Casa, l'URL est créée automatiquement ; sinon il faut une URL externe (**Paramètres → Système → Réseau**). Chaque appel est vérifié avec la signature 17track : connaître l'URL ne suffit pas pour envoyer de fausses données. La vérification toutes les 30 minutes reste active en secours.

## Entités

Toutes les entités sont regroupées sous l'appareil **Suivi de Colis**, visible sur la page de l'intégration.

### Colis

Un capteur par colis en cours, nommé d'après le nom que vous lui avez donné, ou d'après son numéro sinon. L'identifiant d'entité reste basé sur le numéro (par exemple `sensor.6a12345678901`).

- **État** : `info_received`, `in_transit`, `out_for_delivery`, `available_for_pickup`, `delivered`, `delivery_failure`, `exception`, `expired`, `not_found` ou `unknown`. Ces états sont affichés traduits dans l'interface.
- **Attributs** :
  - `tracking_number`, `package_name`, `carrier` ;
  - `sub_status` : statut détaillé 17track, par exemple `DeliveryFailure_NoBody` ;
  - `info_text`, `location` ;
  - `estimated_delivery_from` / `estimated_delivery_to`, `delivered_at` ;
  - `events` : l'historique, qui n'est pas enregistré dans la base de données pour ne pas la faire grossir ;
  - `tracking_stopped` : 17track a arrêté le suivi.

Un colis livré est archivé (et son capteur retiré) après le délai choisi dans les paramètres.

### Synthèse

| Entité | Type | Description |
|--------|------|-------------|
| Colis en cours | capteur (nombre) | Colis non livrés. Liste des colis en attribut `packages`. |
| Livraisons aujourd'hui | capteur (nombre) | Colis en cours de livraison. |
| Colis à retirer | capteur (nombre) | Colis disponibles en point relais ou en bureau de poste. |
| Prochaine livraison | capteur (horodatage) | Date prévue la plus proche, avec le colis concerné en attributs. |
| Problème de livraison | capteur binaire (problème) | Allumé si un colis est en échec, en anomalie, introuvable, expiré ou n'est plus suivi. |
| Changement de statut | entité événement | Se déclenche à chaque changement de statut d'un colis. |

### Diagnostic

| Entité | Description |
|--------|-------------|
| Quota 17track restant | Crédits restants. Total, utilisés et utilisés aujourd'hui en attributs. |
| Dernière actualisation | Dernière mise à jour réussie depuis 17track. Dernière notification webhook et dernière vérification des emails en attributs. |
| Boîte mail *nom* | Une par boîte : connectée ou non lors de la dernière vérification. Date, erreur éventuelle et nombre de numéros trouvés en attributs. |

## Carte Lovelace

La carte est installée avec l'intégration : sur un tableau de bord, **Ajouter une carte** puis rechercher **Suivi de Colis**. Elle suit le thème de Home Assistant (clair, sombre ou personnalisé).

- **Badges de résumé** : livraisons du jour, colis à retirer, en route, problèmes.
- **Colis regroupés par urgence** : action requise, en route, livrés. Chaque ligne affiche le statut détaillé, le transporteur et le lieu, et à droite la date de livraison prévue ou le statut.
- **Clic sur un colis** : historique complet, numéro de suivi, transporteur, dernière mise à jour et bouton **Supprimer**.
- **Bouton +** : ajouter un colis avec son numéro, un nom (facultatif) et le transporteur (détection automatique par défaut).
- **Archiver les livrés** en bas de la carte.

```yaml
type: custom:suivi-colis-card
title: Colis          # facultatif
show_delivered: true  # facultatif : afficher les colis livrés
```

Après une mise à jour de l'intégration, rechargez la page du navigateur pour obtenir la nouvelle version de la carte.

## Services

| Service | Description |
|---------|-------------|
| `suivi_colis.add_package` | Suivre un numéro, avec un nom et un transporteur facultatifs. |
| `suivi_colis.remove_package` | Arrêter le suivi d'un colis et supprimer son capteur. |
| `suivi_colis.change_carrier` | Suivre un colis chez un autre transporteur (transporteur mal détecté). 17track limite à 5 changements par colis. |
| `suivi_colis.refresh` | Mettre à jour tous les colis immédiatement. |
| `suivi_colis.archive_delivered` | Archiver tous les colis livrés. |

```yaml
action: suivi_colis.add_package
data:
  tracking_number: "XA123456789FR"
  friendly_name: "Clavier MX Keys"
  carrier: "chronopost"   # facultatif, détecté automatiquement
```

```yaml
action: suivi_colis.change_carrier
data:
  tracking_number: "CH514085194DE"
  carrier: "dhl_paket"     # ou carrier_code: 7041 pour n'importe quel transporteur 17track
```

**Transporteurs** : Colissimo, Chronopost, Colis Privé, Mondial Relay, Relais Colis, DPD, GLS, UPS, FedEx, TNT, DHL, Amazon, Vinted Go et Cainiao peuvent être choisis directement. Sans choix, 17track détecte le transporteur, ce qui couvre les 1 200+ transporteurs qu'il prend en charge. Pour un transporteur absent de la liste, `change_carrier` accepte son code 17track (`carrier_code`).

## Automatisations

L'événement `suivi_colis_status_changed` est émis à chaque changement de statut d'un colis. Ses données : `tracking_number`, `name`, `carrier`, `old_status`, `new_status`, `sub_status`, `info` et `location`. L'entité **Changement de statut** reçoit le même événement et peut servir de déclencheur depuis l'éditeur visuel.

Être prévenu quand un colis part en livraison :

```yaml
triggers:
  - trigger: event
    event_type: suivi_colis_status_changed
    event_data:
      new_status: out_for_delivery
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.name }} arrive aujourd'hui ({{ trigger.event.data.carrier }})"
```

Être prévenu d'un problème de livraison (remplacez l'entité par celle de votre installation) :

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.suivi_de_colis_probleme_de_livraison
    to: "on"
actions:
  - action: notify.notify
    data:
      message: >
        Problème de livraison :
        {{ state_attr(trigger.entity_id, 'packages') | map(attribute='name') | join(', ') }}
```

## Dépannage

| Symptôme | Explication |
|----------|-------------|
| Un nouveau colis reste **« En attente »** | 17track met de quelques secondes à quelques minutes pour interroger le transporteur. L'intégration vérifie de nouveau après 1, 3 et 10 minutes. |
| Statut **Introuvable** | Le transporteur ne connaît pas encore le numéro, ou il a été mal détecté : essayez `suivi_colis.change_carrier`. |
| **Suivi arrêté** | 17track arrête un suivi après 30 jours sans nouvelles, ou 15 jours après la livraison. |
| Boîte mail **déconnectée** | L'attribut `error` du capteur de la boîte donne la raison. Vérifiez le mot de passe d'application et le chiffrement dans **Configurer → Modifier une boîte mail**. |
| Le webhook ne reçoit rien | Vérifiez que l'URL est accessible depuis Internet et utilisez le bouton de test du tableau de bord 17track. L'attribut `last_push` du capteur **Dernière actualisation** indique la dernière notification reçue. |
| Quota épuisé | Les nouveaux colis sont refusés (message dans le journal) ; les colis déjà suivis continuent d'être mis à jour. |

Journaux détaillés : sur la page de l'intégration, menu ⋮ → **Activer le journal de débogage**.

## Désinstallation

1. **Paramètres → Appareils et services → Suivi de Colis** → menu ⋮ → **Supprimer**. Les entités sont retirées, et le suivi continue chez 17track jusqu'à son arrêt automatique.
2. Si le webhook était activé, retirer l'URL du tableau de bord 17track.
3. Désinstaller depuis HACS (ou supprimer `custom_components/suivi_colis/`), puis redémarrer.

## Mise à jour depuis une version précédente

La configuration est migrée automatiquement :
- **1.0.x** : la boîte mail unique devient la première boîte de la liste. Une option « SSL » décochée devient STARTTLS.
- **1.1.0** : ajout de l'identifiant du webhook, qui reste désactivé tant que vous ne l'activez pas.

Les colis déjà suivis sont conservés, avec les mêmes identifiants d'entité. Ils apparaissent désormais sous l'appareil **Suivi de Colis**.

## Développement

### Tests

```bash
pip install -r requirements_test.txt
pytest
```

### Intégration continue

À chaque push et pull request, et une fois par semaine, GitHub Actions lance :
- **Hassfest** : validation officielle Home Assistant (manifest, traductions, services) ;
- **HACS** : validation du dépôt pour HACS ;
- **Tests** : la suite `pytest`.

### Versions

Chaque merge sur `main` publie automatiquement une version : tag `vX.Y.Z`, release GitHub utilisée par HACS, `CHANGELOG.md` et `manifest.json` mis à jour.
- Si la version du `manifest.json` a été augmentée à la main dans la PR, c'est elle qui est publiée.
- Sinon, elle dépend des messages de commit depuis la dernière version :
  - `feat: …` → version mineure (1.**2**.0) ;
  - `type!: …` ou `BREAKING CHANGE` → version majeure (**2**.0.0) ;
  - le reste (`fix: …`, `docs: …`…) → correctif (1.1.**1**).

Les notes de version (release GitHub et `CHANGELOG.md`) reprennent le titre et la description de chaque pull request fusionnée, puis les commits poussés directement sur `main`, classés par type (nouveautés, corrections, documentation, maintenance). Les changements incompatibles (`BREAKING CHANGE:` en pied de commit) et une nouvelle version minimale de Home Assistant sont signalés en tête. La description d'une PR sert donc de note de version : sections « Tests » ou « Vérifications » exclues.

```bash
python .github/scripts/release.py --notes-for v1.2.0     # afficher les notes d'une version
python .github/scripts/release.py --rebuild-changelog    # régénérer CHANGELOG.md
```

## Licence

MIT, voir [LICENSE](LICENSE).
