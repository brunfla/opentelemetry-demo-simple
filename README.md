# opentelemetry-demo-simple

Les values Helm de la démo OpenTelemetry et un workflow GitHub Actions pour les valider et les déployer sur le Minikube local `otel-demo`.

```text
values.yaml                  Configuration applicative
monitoring/values.yaml        Alertmanager, kube-state-metrics et règles d'alerte
monitoring/tests.yaml         Scénarios de test des alertes
monitoring/RUNBOOKS.md        Procédures d'intervention et notifications
scripts/check-monitoring.py  Validation et tests des règles rendues
scripts/smoke-alerting.py     Test de déclenchement et résolution sur le cluster
.github/workflows/helm.yml    Validation et déploiement
.gitignore
README.md
```

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
Pour tester le trajet complet et la résolution sans arrêter un service :

```bash
python3 scripts/smoke-alerting.py
```

Ce test prend environ 4 à 7 minutes et crée puis supprime un déploiement temporaire.
Il envoie une notification réelle à n8n et peut déclencher le traitement d’un
incident. Il est distinct de la CI de validation, qui n'accède pas au cluster.

## Runner local

Le runner doit tourner sur la machine qui accède au contexte Kubernetes `otel-demo`, avec `kubectl` disponible. GitHub installe la version de Helm déclarée dans le workflow.

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
helm lint .helm/opentelemetry-demo \
  --values values.yaml --values monitoring/values.yaml --strict
helm template otel-demo .helm/opentelemetry-demo \
  --namespace otel-demo --values values.yaml \
  --values monitoring/values.yaml > .helm/rendered.yaml
python3 scripts/check-monitoring.py .helm/rendered.yaml
```

La validation des alertes nécessite Python 3, PyYAML `6.0.3` et Docker. Elle
utilise les versions de Prometheus et Alertmanager des manifests rendus.
Les binaires locaux correspondants peuvent être indiqués via `PROMTOOL` et `AMTOOL`
pour exécuter les validations sans Docker.

Pour annuler une modification, créer un commit qui rétablit les anciennes values, puis le pousser sur `main`. Le pipeline les redéploiera.

Références : [GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [runner local](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners), [Helm upgrade](https://helm.sh/docs/helm/helm_upgrade/).
