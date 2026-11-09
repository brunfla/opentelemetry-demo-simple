#!/usr/bin/env python3
"""Exercise an actual Kubernetes alert and its recovery without stopping the app."""

import argparse
import json
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="otel-demo")
    parser.add_argument("--namespace", default="otel-demo", choices=["otel-demo"])
    parser.add_argument("--timeout", type=int, default=480, help="Timeout for each phase in seconds")
    parser.add_argument("--skip-grafana", action="store_true", help="Test profile: Grafana has zero replicas")
    args = parser.parse_args()
    kubectl = ["kubectl", "--context", args.context, "--request-timeout=15s", "-n", args.namespace]
    name = "alerting-smoke-" + uuid.uuid4().hex[:8]
    prefix = f"/api/v1/namespaces/{args.namespace}/services/"

    def run(*command, **kwargs):
        return subprocess.run(kubectl + list(command), text=True, capture_output=True, check=True, timeout=30, **kwargs).stdout

    def read(path):
        return json.loads(run("get", "--raw=" + prefix + path))

    def matches(alert):
        return alert.get("labels", {}).get("alertname") == "KubeDeploymentUnavailable" and alert["labels"].get("deployment") == name

    def check():
        prom = read("prometheus:9090/proxy/api/v1/alerts")["data"]["alerts"]
        manager = read("alertmanager:9093/proxy/api/v2/alerts")
        status = {
            "Prometheus": any(matches(a) and a["state"] == "firing" for a in prom),
            "Alertmanager": any(matches(a) for a in manager),
        }
        if not args.skip_grafana:
            grafana = read("grafana:80/proxy/grafana/api/datasources/proxy/uid/webstore-metrics/api/v1/alerts")["data"]["alerts"]
            status["Grafana"] = any(matches(a) and a["state"] == "firing" for a in grafana)
        return status

    def wait_for(firing):
        deadline = time.monotonic() + args.timeout
        last_status = None
        last_progress = 0
        while time.monotonic() < deadline:
            try:
                status = check()
            except (subprocess.SubprocessError, ValueError, KeyError) as exc:
                status = {"retry": str(exc)}
            now = time.monotonic()
            if status != last_status or now - last_progress >= 30:
                print(json.dumps(status, ensure_ascii=False), flush=True)
                last_status, last_progress = status, now
            if "retry" not in status and all(value == firing for value in status.values()):
                return
            time.sleep(10)
        raise TimeoutError(f"Timeout waiting for firing={firing}: {last_status}")

    # Preflight before creating anything; endpoint failure must not look like recovery.
    check()
    manifest = {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                 "template": {"metadata": {"labels": {"app": name}},
                              "spec": {"nodeSelector": {"otel-demo/alerting-smoke": name},
                                       "containers": [{"name": "smoke", "image": "busybox:1.38.0",
                                                       "command": ["sleep", "600"],
                                                       "resources": {"requests": {"cpu": "1m", "memory": "8Mi"},
                                                                     "limits": {"memory": "16Mi"}}}]}}}}
    created = False
    try:
        run("create", "-f", "-", input=json.dumps(manifest))
        created = True
        print(f"Created {name}; waiting for the three-minute deployment alert.", flush=True)
        wait_for(True)
    finally:
        if created:
            run("delete", "deployment", name, "--wait=false", "--ignore-not-found=true")
            print(f"Deleted {name}.", flush=True)
    wait_for(False)
    destinations = "Prometheus and Alertmanager" if args.skip_grafana else "Prometheus, Alertmanager and Grafana"
    print(f"PASS: alert fired and resolved in {destinations}.", flush=True)


if __name__ == "__main__":
    main()
