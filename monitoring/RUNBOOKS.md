# Alertes de la démo OpenTelemetry

Prometheus évalue les règles toutes les 30 secondes. Alertmanager les regroupe,
et Grafana les affiche dans **Alerting → Alert rules**, source **Prometheus**.
La source **Alertmanager** permet de consulter les groupes et de créer des silences.
Les règles restent gérées dans Git, pas dans l'interface Grafana.

Le récepteur `n8n-incidents` envoie les alertes du namespace `otel-demo`, hors
`Watchdog`, au webhook local `http://host.docker.internal:5678/webhook/otel-alerts`.
Les résolutions sont également envoyées. L’en-tête `X-API-Key` est lu depuis
`/etc/alertmanager/n8n/webhook-token`, monté en lecture seule à partir du Secret
existant `incident-n8n-webhook`. Sa valeur reste hors de Git et n’est pas créée
par le chart. Le déploiement nécessite ce Secret avec la clé `webhook-token`.
Les alertes d’autres namespaces, notamment `kube-system`, restent dans Grafana
via le récepteur `grafana-only`, sans envoi à n8n. Ce périmètre préserve le routage
qui existait sur le cluster avant son intégration dans Git.

`Watchdog` est une alerte informative permanente, pas un incident. Son récepteur
est distinct de n8n. Une panne complète du nœud nécessite un contrôle extérieur
au cluster et un récepteur externe pour son heartbeat.

Les seuils initiaux (5 % d'erreurs, p95 > 1 s, au moins 10 appels sur 5 minutes)
sont adaptés à une démo à faible trafic. Ils doivent être réévalués à partir du
trafic et des objectifs du service avant un usage en production. Les métriques
de spans supposent que la collecte des traces reste représentative du trafic.


## Couverture et limites

Les erreurs et la latence couvrent les spans serveur de `frontend`,
`frontend-proxy`, `checkout`, `cart`, `payment`, `product-catalog`, `currency`,
`shipping`, `quote`, `email`, `ad` et `image-provider`. Les noms et les histogrammes
ont été vérifiés dans Prometheus. L’absence de spans et la chute de trafic sont
surveillées pour les parcours centraux `frontend` et `checkout` ; une absence de
trafic sur les autres services ne déclenche pas à elle seule une alerte.

Les objets Kubernetes sont surveillés dans **tous les namespaces**, notamment
`kube-system`. Les valeurs de saturation proviennent du kubelet via cAdvisor
(conteneurs) et du receiver OpenTelemetry `kubeletstats` (nœuds). La collecte des
PVC est activée pour détecter les demandes de volume bloquées. Les séries
`kubelet_volume_stats_*` ne sont pas exposées sur ce Minikube : le seuil disque
couvre le système de fichiers du nœud, pas le remplissage individuel de chaque PVC.
Sur Docker, cette mesure dépend du stockage exposé au nœud Minikube et ne couvre
pas tous les disques de la machine hôte.

Ce sont des seuils explicites, complétés par une baisse de trafic par rapport
à une référence récente ; ce n’est pas un modèle d’apprentissage automatique.
Les délais limitent le bruit des rollouts courts. Les seuils doivent être ajustés
après observation de la démo, en particulier quand des feature flags simulent des
pannes. Les compteurs et les percentiles peuvent manquer de données quand la
collecte est interrompue : les alertes de télémétrie signalent ces angles morts.

Alertmanager inhibe uniquement `KubeDeploymentReplicasMismatch` lorsqu’un
`KubeDeploymentUnavailable` concerne le **même déploiement et namespace**.
Une alerte critique ne masque plus tous les avertissements du même service.
Les labels absents étant traités comme des valeurs vides par
[Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/#inhibit_rule),
les règles d’inhibition doivent rester ciblées.

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

Plus de 5 % des spans **serveur** des services surveillés ont le statut ERROR,
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

Un déploiement, dans tout namespace (y compris `kube-system`), désire au moins un réplica, mais aucun
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

Prometheus ne collecte plus une cible essentielle : `kube-state-metrics`,
`alertmanager`, `kubernetes-api-servers`, `kubernetes-nodes` ou
`kubernetes-nodes-cadvisor`. Une disparition complète de l’un de ces jobs est
également détectée après deux minutes sans échantillon, puis deux minutes de délai. Vérifier **Prometheus → Targets**, les services, EndpointSlices et pods.
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
heartbeat **hors de ce cluster**. Le récepteur `watchdog` n’est pas raccordé à
un service externe ; n8n ne reçoit pas cette alerte. La couverture d’une panne
totale reste donc à configurer.

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
modifie aucun service applicatif. Compter environ 4 à 7 minutes. Avec le routage
actuel, ce test envoie une vraie notification à n8n et peut créer un incident.
Les tests de routage d’`amtool` exécutés en CI sont locaux et n’envoient rien.

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

## ApplicationTrafficDrop

Le débit est inférieur à 20 % de la référence de 30 minutes décalée de 10 minutes, depuis 10 minutes. La référence contient au moins 100 appels. Vérifier le générateur de trafic et le parcours utilisateur.

Comparer le débit actuel au générateur de trafic et aux traces. Une baisse volontaire de charge peut déclencher cette alerte : utiliser un silence pendant ces essais. La référence est une fenêtre de 30 minutes décalée de 10 minutes et doit contenir au moins 100 appels. Un historique partiel suffit si ce minimum est atteint. Si les séries disparaissent entièrement, consulter `ApplicationTelemetryMissing`. Après une longue baisse, la référence finit par baisser aussi ; ce signal détecte une rupture, pas une indisponibilité permanente.

## KubeDeploymentReplicasMismatch

Le nombre de réplicas disponibles reste inférieur au nombre désiré pendant 10 minutes. Vérifier le rollout et les readiness probes.

Comparer réplicas désirés, disponibles et mis à jour avec `kubectl describe deployment`. Examiner rollout, readiness, scheduling et quota. Un scale-to-zero ne déclenche pas cette règle. Une indisponibilité totale reste couverte par la règle critique plus rapide.

## KubeStatefulSetReplicasMismatch

Des réplicas restent indisponibles depuis 5 minutes. Vérifier les pods et les volumes, notamment ceux d’Alertmanager.

Examiner le StatefulSet, ses pods ordonnés et les PVC. Pour Alertmanager, vérifier aussi la cible Prometheus. Un scale-to-zero volontaire ne déclenche pas cette règle.

## KubeDaemonSetUnavailable

Le nombre de pods prêts reste inférieur au nombre attendu depuis 5 minutes. Examiner les composants système et le placement.

Examiner `kubectl get daemonsets -A`, puis les événements et taints des nœuds concernés. Cette règle inclut les composants système comme kube-proxy. Le collector possède sa règle critique dédiée et est exclu ici.

## KubePodNotReady

Un pod Pending, Running ou Unknown reste non prêt pendant 10 minutes. Les pods terminés et en cours de suppression sont exclus.

Examiner `kubectl describe pod` dans le namespace de l’alerte : scheduling, readiness, init-containers, volumes et ressources. Les pods Succeeded/Failed et ceux qui ont un timestamp de suppression sont exclus pour éviter le bruit des jobs et des suppressions.

## KubeContainerWaiting

Une erreur d’image ou de configuration bloque un conteneur ou init-container depuis 5 minutes. Vérifier image, registre, Secrets et ConfigMaps.

Lire la raison et les événements du pod. Vérifier le tag, l’existence de l’image, les credentials du registre et les Secrets/ConfigMaps référencés. Les init-containers sont inclus. ContainerCreating seul n’est pas une erreur d’image.

## KubeContainerRestarts

Au moins 3 redémarrages estimés sur 15 minutes, pendant 2 minutes. Examiner les logs précédents et les probes.

Lire les logs avec `--previous` et vérifier les probes. Le compteur est évalué avec `increase`, qui prend en compte les resets et extrapole les limites de fenêtre ; le nombre est une estimation. Un redémarrage isolé n’est pas une tempête de redémarrages.

## KubeContainerOOMKilled

La dernière terminaison est OOMKilled et date de moins de 10 minutes. Vérifier consommation, limite mémoire et pression du nœud.

Examiner la dernière terminaison, les limites mémoire et les événements. Le timestamp de terminaison limite l’alerte aux dix dernières minutes : une ancienne valeur OOMKilled ne doit pas rester active indéfiniment. Le signal concerne les conteneurs principaux, pas les init-containers. Ne pas augmenter systématiquement la limite sans chercher une fuite ou une pression mémoire globale.

## KubeContainerMemoryNearLimit

Le working set dépasse 90 % de la limite mémoire depuis 10 minutes. Les conteneurs sans limite positive sont exclus.

Comparer le working set à la limite du conteneur et à son évolution. Vérifier heap, caches et limites. Les conteneurs sans limite positive sont exclus ; le signal du nœud couvre la saturation globale. Le working set ne prédit pas tous les OOM.

## KubeContainerCPUThrottling

Plus de 25 % des périodes CPU sont limitées sur 5 minutes, pendant 10 minutes. Corréler avec latence et limites CPU avant de les modifier.

Comparer la proportion de périodes CPU limitées avec le p95 et la charge. Ce ratio n’est pas un pourcentage de CPU utilisé. Examiner les limites CPU avant de les augmenter ; une activité normale peut produire du throttling sans incident utilisateur.

## KubeNodeCPUHigh

La consommation CPU dépasse 90 % de la capacité du nœud depuis 15 minutes. Vérifier les principaux consommateurs.

Comparer les cœurs consommés (`k8s_node_cpu_usage`) à la capacité CPU du même nœud. Identifier les pods consommateurs et vérifier la capacité allouée à Minikube. La jointure utilise le nom du nœud ; les labels de pod du collector ne doivent pas casser le calcul.

## KubeNodeMemoryHigh

Le working set représente plus de 90 % du total working set et mémoire disponible depuis 10 minutes.

La proportion est working set / (working set + mémoire disponible) issue de kubeletstats. Examiner les pods consommateurs, la mémoire accordée à Minikube et `MemoryPressure`. Vérifier aussi la mémoire de l’hôte Docker si nécessaire.

## KubeNodeDiskSpaceLow

Moins de 10 % d’espace disponible sur le système de fichiers du nœud depuis 10 minutes. Vérifier images, logs et volumes persistants.

Examiner l’espace du système de fichiers du nœud, les images inutilisées, les journaux et la rétention Prometheus. Identifier les données avant tout nettoyage. Ce signal ne mesure pas la capacité restante de chaque PVC et ne surveille pas l’ensemble des disques de l’hôte.

## KubePersistentVolumeClaimPending

Un PVC reste Pending depuis 10 minutes. Vérifier StorageClass, provisioner et événements du volume.

Examiner `kubectl describe pvc` dans le namespace concerné, la StorageClass par défaut et le provisioner. Sur Minikube, vérifier le storage-provisioner et les événements de scheduling. Les volumes Bound ne déclenchent pas cette règle.

## KubeNodeResourceTelemetryMissing

Au moins une métrique CPU, mémoire ou disque manque depuis 5 minutes. Vérifier kubeletstats et le collector ; les seuils de saturation peuvent être aveugles.

L’inventaire des nœuds vient de kube-state-metrics. Pour chaque nœud connu, la règle vérifie la présence récente des cinq séries CPU, mémoire et disque nécessaires. Vérifier kubeletstats, les droits et le pipeline métriques. Une panne de kube-state-metrics relève de `MonitoringTargetDown`. Une disparition partielle des métriques cAdvisor reste à diagnostiquer dans Targets et les logs ; le contrôle `up` ne vérifie pas chaque famille de métriques.

## CollectorExportFailures

Des échecs d’export sont observés sur 5 minutes et la condition persiste 2 minutes. Vérifier le backend et les erreurs permanentes ou réessayées.

Examiner l’exporter indiqué et les logs du collector. Les échecs de spans, métriques et logs sont additionnés. Ils peuvent être réessayés : l’alerte ne prouve pas à elle seule une perte définitive. Corréler avec `CollectorQueueSaturated`, les erreurs réseau et la disponibilité du backend.

## Conflit de propriété Helm et modifications manuelles

Helm 4 utilise l’application côté serveur. Une modification avec `kubectl patch`
peut devenir propriétaire d’un champ et bloquer ensuite l’upgrade, ainsi que son
rollback. Le 6 novembre 2026, le champ `data.alertmanager.yml` appartenait à
`kubectl-patch` après l’ajout du webhook n8n hors de Git. Le montage de son Secret
avait lui aussi été ajouté au StatefulSet par un patch.

Avant toute reprise de propriété, sauvegarder le ConfigMap et inspecter
`metadata.managedFields` ainsi que la configuration effective. Réintégrer les
modifications utiles dans les values, en particulier routes et montages de
Secrets. Le token n’a pas sa place dans ces sauvegardes ni dans Git.

La réparation ponctuelle du ConfigMap a conservé exactement la configuration
YAML interprétée : seule sa sérialisation a changé pour permettre à Helm de
reprendre le champ via une application ciblée avec `--field-manager=helm` et
`--force-conflicts`. Vérifier ensuite le propriétaire et l’égalité de la
configuration interprétée. Le workflow normal n’active pas le forçage des
conflits sur l’ensemble de la release. Un échec du rollback peut laisser des
ressources partiellement mises à jour : après la fusion de la correction,
vérifier le statut Helm, les pods, les 31 règles et la configuration chargée.

Les futures modifications d’Alertmanager doivent passer par les values et une
PR pour éviter de recréer cette divergence. Références :
[conflits d’application Kubernetes](https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts),
[options d’upgrade Helm](https://docs.helm.sh/docs/helm/helm_upgrade/).
