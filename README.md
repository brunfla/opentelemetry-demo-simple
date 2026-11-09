# opentelemetry-demo-simple

Les values Helm de la démo OpenTelemetry et un workflow GitHub Actions pour les valider et les déployer sur le Minikube local `otel-demo`.

```text
values.yaml                  Configuration applicative
monitoring/values.yaml        Alertmanager, kube-state-metrics et règles d'alerte
monitoring/tests.yaml         Scénarios de test des alertes
monitoring/RUNBOOKS.md        Procédures d'intervention et notifications
scripts/check-monitoring.py  Validation et tests des règles rendues
scripts/smoke-alerting.py     Test de déclenchement et résolution sur le cluster
scripts/list-alerts.sh       Liste des alertes avec identifiants ou de toutes les règles
.github/workflows/helm.yml    Validation et déploiement
.gitignore
README.md
```

## Profil de test léger

Le fichier `deployment-profile` sélectionne le profil déployé après fusion sur
`main`. Il contient actuellement `test`. Pour retrouver la démo complète,
remplacer son contenu par `full` dans une PR. La CI valide les deux profils à
chaque changement ; le runner déploie uniquement celui choisi dans Git.

En mode `test`, seuls `quote` et une boucle BusyBox envoyant un POST toutes les
5 secondes restent côté application. Le collector conserve la conversion des
traces en métriques et les métriques kubelet. Prometheus, Alertmanager et
kube-state-metrics et Grafana restent actifs. Grafana utilise un réplica et son PVC
existant ; Jaeger et les autres composants de la boutique sont désactivés.
Les traces détaillées ne sont plus stockées, mais les alertes de latence et
d’erreurs continuent à fonctionner à partir des métriques de spans.

Les limites mémoire des conteneurs actifs totalisent **2528 Mio** sur un nœud,
Grafana inclus, auxquels s’ajoutent Kubernetes, containerd et Docker. La consommation
réelle est à mesurer après déploiement. Les limites ne sont pas une mesure de consommation et
ne plafonnent pas la mémoire du nœud. n8n, ses agents et les autres conteneurs
Docker de la machine ne sont pas inclus dans ce budget. La rétention Prometheus
passe à 6 heures / 512 Mo : les métriques plus anciennes seront purgées. Les PVC
Prometheus, Alertmanager et Grafana gardent leur taille et ne sont pas recréés.

Les 31 règles restent présentes. La disponibilité HTTP, l’absence de métriques
applicatives et la chute de trafic ciblent `quote` ; les services volontairement
arrêtés ne déclenchent pas d’alertes de télémétrie manquante. Le routage n8n existant
est conservé. Consulter [Prometheus](http://localhost:9090/alerts), ou ouvrir
Alertmanager avec :

```bash
kubectl --context otel-demo -n otel-demo port-forward service/alertmanager 9093:9093
```

Puis ouvrir `http://localhost:9093`. Pour Grafana :

```bash
kubectl --context otel-demo -n otel-demo port-forward service/grafana 3000:80
```

Ouvrir `http://localhost:3000/grafana/`. La boutique reste indisponible dans ce
mode. Pour un test de déclenchement/résolution après déploiement :

```bash
python3 scripts/smoke-alerting.py
```

Ce test envoie de vraies notifications à n8n. Pour tester sans notification,
utiliser les scénarios promtool de la CI.

## Cycle de modification

1. Créer une branche, modifier les values ou la supervision, pousser et ouvrir une pull request vers `main`.
2. GitHub exécute `helm lint --strict`, `helm template`, les tests `promtool` et la validation `amtool` sur un runner hébergé par GitHub.
3. Fusionner la pull request : GitHub déploie sur Minikube avec `helm upgrade --install` depuis le runner local.
4. Helm attend que les ressources soient prêtes. Si une mise à jour échoue, `--rollback-on-failure` restaure la révision précédente.

Un push direct sur `main` suit aussi ces étapes de validation et déploiement. Les changements du workflow déclenchent également le pipeline. Un changement du README seul ne lance rien. Pour relancer manuellement : **Actions → Helm CI/CD → Run workflow**, sur `main`.

Les pull requests utilisent uniquement le runner GitHub. Les déploiements sur `main` sont sérialisés ; un nouveau push n'annule pas un déploiement en cours.

## Configuration

| Paramètre | Valeur |
| --- | --- |
| Chart officiel | `opentelemetry-demo` |
| Version du chart | `0.42.6` |
| Version de Helm | `v4.3.1` |
| Release / namespace / contexte Kubernetes | `otel-demo` |
| Environnement GitHub | `local` |
| Labels du runner de déploiement | `self-hosted`, `linux`, `x64`, `otel-demo` |
| Variable GitHub `KUBECONFIG_PATH` | Chemin absolu du kubeconfig sur la machine du runner |

Les versions et la cible se trouvent dans le workflow. Le chart est téléchargé depuis son dépôt officiel à chaque exécution.

`values.yaml` reprend les valeurs effectivement installées le 2 novembre 2026. Cette variante désactive notamment Kafka, accounting, fraud-detection, OpenSearch, les services IA et telemetry-docs. Grafana utilise Prometheus et Jaeger. La configuration du collector accompagne cette variante sans Kafka ni OpenSearch.

Les adresses, NodePorts et le certificat **public** du kubelet correspondent au Minikube existant. Les images `latest` et `IfNotPresent` reprennent la configuration locale ; un changement de values ne construit aucune image. Le kubeconfig et les clés privées restent sur la machine du runner.

## Alertes

Le déploiement applique `values.yaml`, puis `monitoring/values.yaml`. Prometheus
évalue 31 règles couvrant disponibilité, erreurs, latence, état Kubernetes et
santé de la supervision. Les erreurs et la latence couvrent 12 services ; une
chute de trafic est détectée sur frontend et checkout. Le cluster est surveillé
sur tous les namespaces : pods bloqués, redémarrages/OOM, réplicas manquants,
volumes en attente, mémoire, throttling CPU et ressources du nœud. Les règles
et leurs limites sont détaillées dans les runbooks. kube-state-metrics collecte l'état des ressources ;
Alertmanager regroupe les alertes. Prometheus (4 Gi), Grafana (1 Gi) et Alertmanager
(1 Gi) utilisent des volumes persistants de la StorageClass par défaut du cluster.
Ces volumes survivent aux remplacements des pods ; ils ne constituent pas une
sauvegarde extérieure à Minikube.
La rétention Prometheus est limitée à 7 jours ou 3 Go de blocs, selon la première
limite atteinte, afin de garder de l'espace pour son journal d'écriture.

Dans Grafana, ouvrir **Alerting → Alert rules** et sélectionner **Prometheus**.
La source **Alertmanager** permet de gérer les silences. Le récepteur
`n8n-incidents` transmet les alertes du namespace `otel-demo` (hors `Watchdog`)
au webhook n8n local, y compris leur résolution. Le Secret Kubernetes existant
`incident-n8n-webhook`, clé `webhook-token`, fournit l’authentification et reste
hors de Git. Les autres namespaces restent consultables dans Grafana sans
notification n8n. `Watchdog` reste toujours active ; la surveillance d'une panne
totale nécessite un récepteur de heartbeat extérieur au cluster.

Voir [les procédures, seuils et instructions de notification](monitoring/RUNBOOKS.md).

Pour consulter les alertes avec `kubectl` et `jq` (lecture seule) :

```bash
./scripts/list-alerts.sh          # Alertes dans Alertmanager, avec leur fingerprint
./scripts/list-alerts.sh --rules  # Toutes les règles Prometheus et leur état
./scripts/list-alerts.sh --json   # Détails complets : labels, annotations, silences…
```

Le contexte et le namespace par défaut sont `otel-demo` ; utiliser `--context`
et `--namespace` pour les changer. Les alertes encore `pending` dans Prometheus
ne sont pas envoyées à Alertmanager. La colonne `ETAT_AM` indique l'état de
notification (`active`, `suppressed` ou `unprocessed`), pas l'état de la règle
Prometheus (`inactive`, `pending` ou `firing`). Ce n'est pas un historique.

Chaque instance reçue par Alertmanager a un `fingerprint` calculé à partir de
l'ensemble de ses labels. `alertname` désigne le nom de l'alerte ; deux pods
touchés par la même règle peuvent donc avoir deux fingerprints différents.
Le même ensemble de labels conserve son fingerprint lors d'une nouvelle panne :
pour distinguer les occurrences, associer `fingerprint` et `startsAt`.
Voir [les champs des alertes Alertmanager](https://prometheus.io/docs/alerting/latest/notifications/#alert).

Pour tester le trajet complet et la résolution sans arrêter un service :

```bash
python3 scripts/smoke-alerting.py
```

Ce test prend environ 4 à 7 minutes et crée puis supprime un déploiement temporaire.
Il envoie une notification réelle à n8n et peut déclencher le traitement d’un
incident. Il est distinct de la CI de validation, qui n'accède pas au cluster.

## Runner local

Le runner doit tourner sur la machine qui accède au contexte Kubernetes `otel-demo`, avec `kubectl`, Python 3 et PyYAML `6.0.3` disponibles. GitHub installe la version de Helm déclarée dans le workflow.

Pour enregistrer un runner : **Settings → Actions → Runners → New self-hosted runner**, puis suivre les commandes GitHub et ajouter le label `otel-demo`. Le dépôt est privé.

Configurer le chemin du kubeconfig depuis la machine locale :

```bash
gh variable set KUBECONFIG_PATH \
  --repo brunfla/opentelemetry-demo-simple \
  --body /home/bruno/.kube/config
```

Le runner préparé pour ce dépôt est installé dans `/home/bruno/code/.runners/opentelemetry-demo-simple`. S'il est arrêté, le relancer dans un terminal :

```bash
cd /home/bruno/code/.runners/opentelemetry-demo-simple
./run.sh
```

La machine, Minikube et le runner doivent rester démarrés pour déployer. L'enregistrement du runner persiste ; son processus devra être relancé après un redémarrage de la machine. La CI de validation fonctionne même lorsque le runner local est arrêté.

## Vérifier localement

Avec Helm `v4.3.1`, à la racine du dépôt :

```bash
mkdir -p .helm
helm pull opentelemetry-demo \
  --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.42.6 --untar --untardir .helm
python3 scripts/prepare-profile.py --output .helm/profile-values.yaml
helm lint .helm/opentelemetry-demo --values .helm/profile-values.yaml --strict
helm template otel-demo .helm/opentelemetry-demo \
  --namespace otel-demo --values .helm/profile-values.yaml > .helm/rendered.yaml
python3 scripts/check-monitoring.py .helm/rendered.yaml --profile "$(cat deployment-profile)"
# En mode test :
python3 scripts/check-test-profile.py .helm/rendered.yaml
```

La validation des alertes nécessite Python 3, PyYAML `6.0.3` et Docker. Elle
utilise les versions de Prometheus et Alertmanager des manifests rendus.
Les binaires locaux correspondants peuvent être indiqués via `PROMTOOL` et `AMTOOL`
pour exécuter les validations sans Docker.

Pour annuler une modification, créer un commit qui rétablit les anciennes values, puis le pousser sur `main`. Le pipeline les redéploiera.

Références : [GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [runner local](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners), [Helm upgrade](https://helm.sh/docs/helm/helm_upgrade/).
