#!/usr/bin/env bash
# Lecture seule : alertes reçues par Alertmanager ou règles Prometheus.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/list-alerts.sh [--rules] [--json] [--context NOM] [--namespace NOM]

Par défaut : alertes présentes dans Alertmanager, avec leur fingerprint.
  --rules       Toutes les règles Prometheus, même inactives
  --json        Réponse JSON complète de l'API, pour inspection ou traitement
  --context     Contexte Kubernetes (défaut : otel-demo)
  --namespace   Namespace de la supervision (défaut : otel-demo)
  -h, --help    Afficher cette aide

Les alertes encore pending dans Prometheus ne sont pas envoyées à Alertmanager.
L'état Alertmanager (active/suppressed/unprocessed) concerne la notification.
Ce script ne liste pas un historique des alertes résolues.
EOF
}

context=otel-demo
namespace=otel-demo
mode=alerts
json=false
while (($#)); do
  case "$1" in
    --rules) mode=rules; shift ;;
    --json) json=true; shift ;;
    --context|--namespace)
      if (($# < 2)) || [[ -z "$2" || "$2" == -* ]]; then
        printf 'Valeur manquante pour %s\n' "$1" >&2
        exit 2
      fi
      if [[ "$1" == --context ]]; then context=$2; else namespace=$2; fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue : %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

for tool in kubectl jq; do
  command -v "$tool" >/dev/null || { printf 'Commande requise : %s\n' "$tool" >&2; exit 1; }
done

endpoint='alertmanager:9093/proxy/api/v2/alerts'
if [[ "$mode" == rules ]]; then
  endpoint='prometheus:9090/proxy/api/v1/rules?type=alert'
fi
response=$(kubectl --context "$context" --request-timeout=15s -n "$namespace" get \
  --raw="/api/v1/namespaces/$namespace/services/$endpoint")

if [[ "$mode" == rules ]]; then
  jq -e '.status == "success"' <<< "$response" >/dev/null || {
    printf 'Erreur de réponse Prometheus\n%s\n' "$response" >&2
    exit 1
  }
fi

if [[ "$json" == true ]]; then
  jq . <<< "$response"
elif [[ "$mode" == rules ]]; then
  jq -r '
    ["GROUPE", "REGLE", "ETAT", "GRAVITE"],
    (.data.groups[] | .name as $group | .rules[] |
      [$group, .name, .state, (.labels.severity // "-")]) | @tsv
  ' <<< "$response"
else
  jq -r '
    if length == 0 then "Aucune alerte dans Alertmanager."
    else
      ["FINGERPRINT", "ALERTE", "ETAT_AM", "GRAVITE", "CIBLE", "DEBUT"],
      (sort_by(.labels.alertname, .fingerprint)[] |
        [.fingerprint, .labels.alertname, .status.state, (.labels.severity // "-"),
         (.labels.pod // .labels.deployment // .labels.service_name // .labels.instance // "-"),
         .startsAt]) | @tsv
    end
  ' <<< "$response"
fi
