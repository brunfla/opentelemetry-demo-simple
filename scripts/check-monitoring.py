#!/usr/bin/env python3
"""Extract and test the monitoring configuration actually rendered by Helm."""

import argparse
import copy
import os
from pathlib import Path
import re
import subprocess

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--profile", choices=["full", "test"], default="full")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / ".helm" / ("monitoring" if args.profile == "full" else "monitoring-test")
    output.mkdir(parents=True, exist_ok=True)
    # Some upstream manifests contain trailing tabs accepted by Kubernetes' YAML
    # parser. Remove trailing whitespace only for parsing with PyYAML.
    rendered = "\n".join(line.rstrip() for line in args.manifest.read_text().splitlines())
    manifests = [obj for obj in yaml.safe_load_all(rendered) if obj]
    configs = {
        obj["metadata"]["name"]: obj["data"]
        for obj in manifests
        if obj["kind"] == "ConfigMap" and obj["metadata"]["name"] in {"prometheus", "alertmanager"}
    }
    rules = yaml.safe_load(configs["prometheus"]["alerting_rules.yml"])
    by_name = {r["alert"]: r for g in rules["groups"] for r in g["rules"]}
    runbooks = (root / "monitoring" / "RUNBOOKS.md").read_text()
    for name, rule in by_name.items():
        assert rule["labels"]["severity"] in {"info", "warning", "critical"}, name
        assert re.search(rf"^## {re.escape(name)}$", runbooks, re.MULTILINE), name
        assert rule["annotations"]["runbook_url"].endswith("#" + name.lower()), name
    tests = yaml.safe_load((root / "monitoring" / "tests.yaml").read_text())
    if args.profile == "test":
        replaced = {"WebstoreUnavailable", "ApplicationTelemetryMissing", "ApplicationTrafficDrop"}
        tests["tests"] = [case for case in tests["tests"]
                          if not any(c["alertname"] in replaced for c in case.get("alert_rule_test", []))]
        tests["tests"].extend(yaml.safe_load((root / "monitoring" / "tests-test.yaml").read_text())["tests"])
    # Expected conditions, labels and timing are explicit in tests.yaml. Reuse
    # annotation text so editorial runbook changes do not rewrite every fixture.
    for case in tests["tests"]:
        for check in case.get("alert_rule_test", []):
            for alert in check.get("exp_alerts", []):
                alert["exp_annotations"] = copy.deepcopy(by_name[check["alertname"]]["annotations"])
    tests["rule_files"] = ["alerting_rules.yml"]
    (output / "alerting_rules.yml").write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False))
    (output / "tests.yaml").write_text(yaml.safe_dump(tests, allow_unicode=True, sort_keys=False))
    (output / "alertmanager.yml").write_text(configs["alertmanager"]["alertmanager.yml"])
    # Keep the CI image in sync with the Prometheus version used by this release.
    prom = next(x for x in manifests if x["kind"] == "Deployment" and x["metadata"]["name"] == "prometheus")
    prom_image = next(c["image"] for c in prom["spec"]["template"]["spec"]["containers"] if c["name"] == "prometheus-server")
    if os.environ.get("PROMTOOL"):
        command = [str(Path(os.environ["PROMTOOL"]).resolve())]
    else:
        command = ["docker", "run", "--rm", "--network=none", "-v", f"{output}:/work:ro", "-w", "/work",
                   "--entrypoint", "/bin/promtool", prom_image]
    for arguments in (["check", "rules", "alerting_rules.yml"], ["test", "rules", "tests.yaml"]):
        subprocess.run(command + arguments, cwd=output, check=True)
    # Validate the routing and inhibition configuration using the chart's exact image.
    am = next(x for x in manifests if x and x["kind"] == "StatefulSet" and x["metadata"]["name"] == "alertmanager")
    am_spec = am["spec"]["template"]["spec"]
    am_container = next(c for c in am_spec["containers"] if c["name"] == "alertmanager")
    am_image = am_container["image"]
    # The restored n8n route must keep its authentication file available in the
    # rendered pod. Never read or copy the actual Secret into CI.
    am_config = yaml.safe_load(configs["alertmanager"]["alertmanager.yml"])
    n8n = next(r for r in am_config["receivers"] if r["name"] == "n8n-incidents")
    token_file = n8n["webhook_configs"][0]["http_config"]["http_headers"]["X-API-Key"]["files"][0]
    mount = next(m for m in am_container["volumeMounts"]
                 if token_file == m["mountPath"].rstrip("/") + "/webhook-token")
    volume = next(v for v in am_spec["volumes"] if v["name"] == mount["name"])
    assert mount.get("readOnly") is True, "n8n credentials must be mounted read-only"
    assert volume["secret"]["secretName"] == "incident-n8n-webhook", "n8n Secret reference changed"
    if os.environ.get("AMTOOL"):
        am_command = [str(Path(os.environ["AMTOOL"]).resolve())]
    else:
        am_command = ["docker", "run", "--rm", "--network=none", "-v", f"{output}:/work:ro", "-w", "/work",
                      "--entrypoint", "/bin/amtool", am_image]
    subprocess.run(am_command + ["check-config", "alertmanager.yml"], cwd=output, check=True)
    # Local routing tests do not contact the webhook or create real alerts.
    routing_cases = [
        ("watchdog", ["alertname=Watchdog", "namespace=otel-demo"]),
        ("n8n-incidents", ["alertname=ApplicationHighErrorRate", "namespace=otel-demo", "severity=critical"]),
        ("n8n-incidents", ["alertname=KubeNodeMemoryHigh", "namespace=otel-demo", "severity=warning"]),
        ("grafana-only", ["alertname=KubePodNotReady", "namespace=kube-system", "severity=warning"]),
    ]
    for receiver, labels in routing_cases:
        subprocess.run(am_command + ["config", "routes", "test", "--config.file=alertmanager.yml",
                                     "--verify.receivers=" + receiver] + labels, cwd=output, check=True)
    print(f"Validated {len(by_name)} alert rules, {len(tests['tests'])} scenarios and Alertmanager configuration.")


if __name__ == "__main__":
    main()
