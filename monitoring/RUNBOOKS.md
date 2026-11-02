# Alertes de la démo OpenTelemetry

Prometheus évalue les règles toutes les 30 secondes. Alertmanager les regroupe,
et Grafana les affiche dans **Alerting → Alert rules**, source **Prometheus**.
La source **Alertmanager** permet de consulter les groupes et de créer des silences.
Les règles restent gérées dans Git, pas dans l'interface Grafana.

Le récepteur `grafana-only` ne contient volontairement aucun canal externe :
aucun email ou webhook n'est envoyé. `Watchdog` est une alerte informative
permanente, pas un incident. Une panne complète du nœud nécessite un contrôle
extérieur au cluster et un récepteur externe pour son heartbeat.

Les seuils initiaux (5 % d'erreurs, p95 > 1 s, au moins 10 appels sur 5 minutes)
sont adaptés à une démo à faible trafic. Ils doivent être réévalués à partir du
trafic et des objectifs du service avant un usage en production. Les métriques
de spans supposent que la collecte des traces reste représentative du trafic.

## Vérifications communes

```bash
kubectl --context otel-demo -n otel-demo get pods,deployments,daemonsets,statefulsets
kubectl --context otel-demo -n otel-demo get events --sort-by=.lastTimestamp
kubectl --context otel-demo -n otel-demo logs deployment/prometheus -c prometheus-server --tail=100
```

Utiliser les labels de l'alerte pour cibler le pod, le déploiement ou le nœud.
Consulter le dashboard **Demo Dashboard** et les traces Jaeger du service.
Lors d'une maintenance, créer un silence limité dans le temps et documenté.
Un diagnostic de cause doit précéder un redémarrage ou un changement de seuil.

## WebstoreUnavailable

La sonde HTTP du collector ne reçoit plus de réponse 2xx de `frontend-proxy`,
ou les échantillons disparaissent. Le dernier résultat est pris sur deux minutes
pour éviter qu'un ancien code HTTP 200 masque les nouveaux échecs. Sans aucun
échantillon dans cette fenêtre, la sonde est considérée en échec, avec les mêmes
labels : la disparition d'une sonde déjà en erreur ne résout pas son alerte.

Vérifier les pods et logs de `frontend-proxy` et `frontend`, leurs EndpointSlices,
puis accéder à la boutique. Examiner également `CollectorTelemetryMissing` :
une perte de collecte peut rendre la sonde muette sans panne de la boutique.

## ApplicationHighErrorRate

Plus de 5 % des spans **serveur** de `frontend` ou `checkout` ont le statut ERROR,
avec au moins 10 appels sur 5 minutes, pendant 5 minutes. Les spans client et
internes ne comptent pas dans ce ratio.

Ouvrir les traces en erreur du service indiqué. Examiner le dernier déploiement,
les feature flags de la démo et les dépendances (`payment`, `cart`, base de données).
En cas de régression identifiée, rétablir la configuration ou la version précédente.

## ApplicationHighLatency

Le p95 des spans serveur dépasse 1 seconde pendant 5 minutes, avec au moins
10 appels sur la fenêtre. Les buckets utilisés sont en **millisecondes**.

Chercher le segment lent dans Jaeger, puis vérifier ressources, throttling,
files d'attente et dépendances. Les services à faible trafic nécessitent
des fenêtres plus longues ou des sondes synthétiques avant de réduire ce minimum.

## ApplicationTelemetryMissing

Les séries de spans serveur de `frontend` ou `checkout` sont absentes depuis
5 minutes, puis la condition persiste encore 2 minutes.

Vérifier le service, le générateur de trafic, l'instrumentation et le pipeline
`span_metrics` du collector. Avec le générateur désactivé ou aucun appel après
un redémarrage, ce signal peut être attendu : utiliser un silence ciblé.

## KubeDeploymentUnavailable

Un déploiement du namespace `otel-demo` désire au moins un réplica, mais aucun
n'est disponible depuis 3 minutes. Les déploiements volontairement réduits à zéro
ne déclenchent pas cette alerte.

```bash
kubectl --context otel-demo -n otel-demo describe deployment NOM_DEPLOIEMENT
kubectl --context otel-demo -n otel-demo describe pod NOM_POD
kubectl --context otel-demo -n otel-demo logs NOM_POD --all-containers --tail=100
```

Examiner les readiness probes, erreurs de téléchargement d'image, ressources
insuffisantes, sélecteurs de nœuds et volumes non montés.

## KubePodCrashLooping

Un conteneur est régulièrement en `CrashLoopBackOff`. Vérifier l'état de sortie,
les événements, les limites mémoire et les logs de l'instance précédente :

```bash
kubectl --context otel-demo -n otel-demo logs NOM_POD -c NOM_CONTENEUR --previous --tail=100
```

## KubeNodeNotReady

Le nœud est NotReady depuis 3 minutes. Vérifier `kubectl describe node NOM_NOEUD`,
le runtime, le kubelet et la connectivité avec l'API. Sur ce cluster à un seul
nœud, une panne totale arrête aussi Prometheus : le heartbeat extérieur est
nécessaire pour couvrir ce cas.

## KubeNodePressure

Examiner la condition `MemoryPressure`, `DiskPressure` ou `PIDPressure` portée
par l'alerte. Identifier les consommateurs de ressources et l'espace disque
disponible avant toute suppression. Les données applicatives ne doivent pas
être supprimées pour acquitter une alerte.

## KubeCollectorUnavailable

Le DaemonSet `otel-collector-agent` possède moins de pods prêts que prévu.
Cette vérification provient de kube-state-metrics, indépendamment du collector.
Vérifier le placement des pods, les événements, la configuration et les logs :

```bash
kubectl --context otel-demo -n otel-demo logs daemonset/otel-collector-agent --tail=100
```

## CollectorTelemetryMissing

Prometheus ne reçoit plus les métriques internes du collector depuis au moins
2 minutes, et la condition persiste 2 minutes supplémentaires. Vérifier le pod,
le receiver OTLP de Prometheus, la résolution DNS et les erreurs d'export.
Une disparition de métriques ne signifie pas un retour à la normale.

## CollectorQueueSaturated

La file de l'exporter indiqué dépasse 80 % de sa capacité depuis 5 minutes.
Vérifier le backend (Prometheus ou Jaeger), les timeouts et les erreurs réseau.
Augmenter la file sans corriger le backend ne résout pas le problème.

## MonitoringTargetDown

Prometheus ne collecte plus `kube-state-metrics` ou `alertmanager`, ou leur cible
disparaît. Vérifier **Prometheus → Targets**, les services, EndpointSlices et pods.
Une panne d'Alertmanager peut empêcher la réception de ses propres alertes :
Prometheus et Grafana permettent encore de les consulter si ceux-ci fonctionnent.

## PrometheusRuleEvaluationFailures

Ouvrir **Prometheus → Rules**, relever l'erreur exacte et examiner les logs.
Vérifier les requêtes, les limites de ressources et les séries utilisées.
Exécuter la validation locale avant de corriger et redéployer.

## PrometheusConfigReloadFailed

Prometheus continue d'utiliser son ancienne configuration. Vérifier les logs
du conteneur `prometheus-server-configmap-reload`, puis ceux de `prometheus-server`.
Corriger la configuration dans Git et redéployer ; le reloader recharge les
ConfigMaps sans nécessiter un redémarrage du serveur.

## Watchdog

Cette alerte `info` doit toujours être active. Elle vérifie le trajet
Prometheus → Alertmanager et fournit un signal à raccorder à un récepteur de
heartbeat **hors de ce cluster**. En mode `grafana-only`, aucun service externe
ne vérifie son absence : la couverture d'une panne totale reste à configurer.

## Tests

Les scénarios dans `tests.yaml` couvrent le déclenchement, les périodes d'attente,
la résolution, le faible trafic et l'absence de données. Le script
`scripts/check-monitoring.py` valide les règles **rendues par Helm** avec la
version de `promtool` correspondant à l'image déployée, puis la configuration
Alertmanager avec la version d'`amtool` embarquée dans son image.

Après déploiement, exécuter :

```bash
python3 scripts/smoke-alerting.py
```

Le script crée un déploiement temporaire impossible à placer. Il attend
`KubeDeploymentUnavailable` dans Prometheus, Alertmanager et via la source de
données Grafana, supprime ce déploiement, puis vérifie la résolution. Il ne
modifie aucun service applicatif. Compter environ 4 à 7 minutes. Si un canal
externe est ajouté plus tard, ce test peut y envoyer une vraie notification.

## Ajouter un canal de notification

Modifier `prometheus.alertmanager.config.receivers` et les routes dans
`monitoring/values.yaml`. Conserver le récepteur séparé `watchdog`. Prévoir une
route par sévérité ou équipe, des notifications de résolution (`send_resolved`)
et vérifier la réception réelle.

Les identifiants SMTP et URL de webhook contenant un secret doivent être fournis
par un Secret Kubernetes monté avec `alertmanager.extraSecretMounts`, ou par une
configuration Alertmanager stockée dans un Secret. Ne pas les committer dans les
values. Pour un webhook, Alertmanager supporte `url_file` ; pour SMTP,
`auth_password_file`. La destination et ces secrets dépendent du canal choisi.

Références : [Prometheus](https://prometheus.io/docs/practices/alerting/),
[Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/),
[tests de règles](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/),
[Grafana et Alertmanager](https://grafana.com/docs/grafana/latest/datasources/alertmanager/).
