# Repères pour reprendre ce dépôt

## Projet et sources de vérité

Ce dépôt configure une démo OpenTelemetry sur le Minikube local `otel-demo`,
avec Helm et GitHub Actions. Il contient les values et les outils de supervision ;
le code des services vient du chart et des images upstream.

Lire d’abord `README.md`, `deployment-profile`, `.github/workflows/helm.yml` et,
pour les alertes, `monitoring/RUNBOOKS.md`. Les versions du chart et de Helm sont
fixées dans le workflow. Ne pas les changer implicitement.

- `values.yaml` : configuration commune de la démo et du collector.
- `monitoring/values.yaml` : règles Prometheus, Alertmanager et kube-state-metrics.
- `profiles/test.yaml` : paramètres du mode léger.
- `scripts/prepare-profile.py` : assemblage des valeurs complètes pour Helm.
- `monitoring/tests.yaml` et `monitoring/tests-test.yaml` : scénarios promtool.
- `.helm/` : charts téléchargés, rendus et outils locaux, ignorés par Git.

## Intention et profils

Le besoin est de tester les anomalies applicatives et Kubernetes avec une faible
consommation mémoire. Lire `deployment-profile` pour connaître le mode sélectionné.

`test` garde `quote`, une boucle de trafic BusyBox et quatre workloads de
supervision : collector, Prometheus, Alertmanager et kube-state-metrics. Grafana
a zéro réplica avec son PVC conservé ; Jaeger et les autres services sont désactivés.
Les traces deviennent des métriques, sans stockage des traces détaillées.

Le budget vérifié des limites des conteneurs actifs est de 992 Mio sur un nœud.
L’objectif de 1 à 2 Gio pour Minikube reste à mesurer en fonctionnement ; il ne
comprend pas les autres conteneurs Docker de l’hôte, notamment n8n. Ne pas présenter
les limites Kubernetes comme une mesure de consommation réelle.

`full` réactive la démo complète. Le changement de profil se fait dans Git via PR.
Conserver les PVC lors des transitions. Le profil test réduit la rétention
Prometheus à 6 heures / 512 Mo : revenir à `full` ne restaure pas les données purgées.

## Manière de travailler

Communiquer en français. Au début d’une reprise, examiner `git status`, la branche
et l’historique local, puis les PR et le dernier workflow distant si nécessaire.
Des changements locaux peuvent déjà avoir été fusionnés depuis un autre checkout.
Préserver les modifications existantes ; ne pas réinitialiser le dépôt ni les
embarquer toutes dans un commit sans examiner leur provenance.

Le flux convenu est : branche → PR → validation → fusion par l’utilisateur →
déploiement GitHub Actions par le runner local. Préparer et ouvrir les PR utiles ;
laisser la fusion à l’utilisateur sauf instruction explicite différente. Un
`git pull` local ne déploie rien. Une modification documentaire seule ne déclenche
pas le workflow Helm avec les filtres de chemins actuels.

La release, le namespace et le contexte Kubernetes sont `otel-demo`. Le pilote
Minikube est Docker et le runtime des pods est containerd. Vérifier leur état réel
avant de conclure qu’un déploiement est possible. Le runner attendu porte les
labels `self-hosted`, `linux`, `x64`, `otel-demo` et utilise `KUBECONFIG_PATH`.

## Vérifications avant PR

Pour une modification de values, du collector, des règles ou de l’assemblage des
profils, valider `full` et `test` avec les versions du workflow. Après téléchargement
du chart dans `.helm/opentelemetry-demo`, exécuter pour chaque profil :

```bash
python3 scripts/prepare-profile.py --profile test --output .helm/profile-values.yaml
helm lint .helm/opentelemetry-demo --values .helm/profile-values.yaml --strict
helm template otel-demo .helm/opentelemetry-demo --namespace otel-demo \
  --values .helm/profile-values.yaml > .helm/rendered.yaml
python3 scripts/check-monitoring.py .helm/rendered.yaml --profile test
python3 scripts/check-test-profile.py .helm/rendered.yaml
```

Pour `full`, remplacer `test` par `full` et omettre `check-test-profile.py`.
Prérequis : Python 3/PyYAML 6.0.3, Helm et Docker. `PROMTOOL` et `AMTOOL` peuvent
pointer vers des binaires locaux compatibles, comme décrit dans le README.
Utiliser le fichier généré comme **seul fichier de values** pour Helm : ajouter
ensuite les values communes réintroduirait les pipelines et services supprimés.
Une modification documentaire seule ne nécessite pas de relancer ces tests.

## Alertes et points d’attention

Les règles du profil test doivent cibler les services actifs : disponibilité,
absence de télémétrie et chute de trafic surveillent `quote`. Garder les tests de
faible trafic, temporisation, résolution, scale-to-zero et absence de données.
`Watchdog` est volontairement toujours active. Une panne totale de Minikube exige
une surveillance extérieure : un Prometheus arrêté ne peut pas émettre d’alerte.

Le webhook n8n existant reçoit les alertes du namespace `otel-demo`, hors Watchdog,
ainsi que leurs résolutions. Préserver sa route et le montage en lecture seule du
Secret `incident-n8n-webhook`, clé `webhook-token`. Ne jamais enregistrer sa valeur,
un kubeconfig privé ou une clé dans Git ou les logs. Les alertes des autres
namespaces restent consultables dans Prometheus/Alertmanager sans envoi à n8n.

`scripts/smoke-alerting.py --skip-grafana` teste le déclenchement et la résolution
en mode test ; sans cette option, il vérifie aussi Grafana. Il crée une ressource
temporaire et envoie de vraies notifications n8n : ne pas le traiter comme un test
local sans effets externes. Les tests promtool/amtool n’envoient pas de notifications.

Un patch manuel d’Alertmanager a déjà provoqué un conflit de propriété Helm 4
sur `data.alertmanager.yml` et fait échouer le rollback. Avant toute reprise,
inspecter la configuration effective et `managedFields`, sauvegarder les objets
concernés et préserver les modifications utiles dans Git. Suivre le runbook ;
ne pas généraliser `--force-conflicts` à toute la release pour masquer une divergence.

## Entretien de ces repères

Mettre ce fichier à jour quand une décision durable change. Garder ici les
contraintes et les chemins utiles, dans le README les procédures d’utilisation,
et dans les PR l’historique des changements et des validations. Ne pas mémoriser
ici un état temporaire du cluster, un numéro de port dynamique ou une PR en attente
comme s’il s’agissait d’une vérité permanente.
