#!/usr/bin/env python3
"""Prepare the complete Helm values for the Git-selected deployment profile."""

import argparse
import copy
from pathlib import Path

import yaml


def merge(old, new):
    """Merge maps and replace lists, preserving explicit nulls for Helm defaults."""
    result = copy.deepcopy(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["full", "test"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profile = args.profile or (root / "deployment-profile").read_text().strip()
    if profile not in {"full", "test"}:
        parser.error("deployment-profile must contain full or test")
    base = yaml.safe_load((root / "values.yaml").read_text())
    monitoring = yaml.safe_load((root / "monitoring/values.yaml").read_text())
    values = merge(base, monitoring)
    if profile == "test":
        overlay = yaml.safe_load((root / "profiles/test.yaml").read_text())
        old_config = base["opentelemetry-collector"]["alternateConfig"]
        config = overlay["opentelemetry-collector"]["alternateConfig"]
        telemetry = copy.deepcopy(old_config["service"]["telemetry"])
        telemetry["logs"] = {"level": "warn"}
        telemetry["metrics"]["level"] = "normal"
        for reader in telemetry["metrics"]["readers"]:
            reader["periodic"]["interval"] = 30000
        config["service"]["telemetry"] = telemetry
        values = merge(values, overlay)
        # Replace the collector configuration as a whole. Passing the original
        # values again to Helm would reintroduce removed pipelines and backends.
        values["opentelemetry-collector"]["alternateConfig"] = config
        rules = copy.deepcopy(monitoring["prometheus"]["serverFiles"]["alerting_rules.yml"])
        for group in rules["groups"]:
            for rule in group["rules"]:
                if rule["alert"] == "WebstoreUnavailable":
                    rule["alert"] = "TestServiceUnavailable"
                    rule["expr"] = rule["expr"].replace('service_name="frontend-proxy"', 'service_name="quote"')
                    rule["labels"]["service_name"] = "quote"
                    rule["annotations"] = {
                        "summary": "Le service de test ne répond plus correctement",
                        "description": "La sonde POST /getquote ne reçoit plus de réponse 2xx ou ses métriques ont disparu. Vérifier quote et le collector.",
                        "runbook_url": "https://github.com/brunfla/opentelemetry-demo-simple/blob/main/monitoring/RUNBOOKS.md#testserviceunavailable",
                    }
                elif rule["alert"] == "ApplicationTelemetryMissing":
                    rule["expr"] = 'absent_over_time(traces_span_metrics_calls_total{service_name="quote",span_kind="SPAN_KIND_SERVER"}[5m])'
                elif rule["alert"] == "ApplicationTrafficDrop":
                    rule["expr"] = rule["expr"].replace('service_name=~"frontend|checkout"', 'service_name="quote"')
        values["prometheus"].setdefault("serverFiles", {})["alerting_rules.yml"] = rules
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False))
    print(f"Prepared {profile} profile: {args.output}")


if __name__ == "__main__":
    main()
