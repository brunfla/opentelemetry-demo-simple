#!/usr/bin/env python3
"""Check the rendered test profile's footprint, storage and telemetry contract."""

import argparse
from pathlib import Path
import subprocess

import yaml


def memory_mib(value):
    if value.endswith("Mi"):
        return float(value[:-2])
    if value.endswith("Gi"):
        return float(value[:-2]) * 1024
    raise ValueError(f"Unsupported memory unit: {value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    docs = [d for d in yaml.safe_load_all("\n".join(l.rstrip() for l in args.manifest.read_text().splitlines())) if d]
    workloads = {d["metadata"]["name"]: d for d in docs if d["kind"] in {"Deployment", "DaemonSet", "StatefulSet"}}
    active = {n: d for n, d in workloads.items() if d["spec"].get("replicas", 1) > 0}
    assert set(active) == {"quote", "load-generator", "otel-collector-agent", "prometheus", "alertmanager", "kube-state-metrics", "grafana"}, set(active)
    assert workloads["grafana"]["spec"]["replicas"] == 1
    # One Minikube node; every active container must have an explicit RAM ceiling.
    total = sum(memory_mib(c["resources"]["limits"]["memory"]) * d["spec"].get("replicas", 1)
                for d in active.values() for c in d["spec"]["template"]["spec"]["containers"])
    assert total <= 3072, f"Workloads including Grafana exceed 3 GiB of memory limits: {total} MiB"
    claims = {d["metadata"]["name"]: d for d in docs if d["kind"] == "PersistentVolumeClaim"}
    assert claims["grafana"]["spec"]["resources"]["requests"]["storage"] == "1Gi"
    assert claims["prometheus"]["spec"]["resources"]["requests"]["storage"] == "4Gi"
    am_claims = workloads["alertmanager"]["spec"]["volumeClaimTemplates"]
    assert am_claims[0]["spec"]["resources"]["requests"]["storage"] == "1Gi"
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "otel-collector-agent")
    collector = yaml.safe_load(cm["data"]["relay"])
    assert set(collector["exporters"]) == {"otlp_http/prometheus"}
    assert collector["connectors"]["span_metrics"]["histogram"]["unit"] == "ms"
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["span_metrics"]
    assert "kubeletstats" in collector["service"]["pipelines"]["metrics"]["receivers"]
    assert collector["receivers"]["http_check/quote"]["targets"][0]["method"] == "POST"
    assert set(collector["service"]["pipelines"]) == {"traces", "metrics", "metrics/probe"}
    assert set(collector["extensions"]) == {"health_check"}
    config = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "prometheus")
    rules = yaml.safe_load(config["data"]["alerting_rules.yml"])
    by_name = {r["alert"]: r for g in rules["groups"] for r in g["rules"]}
    assert len(by_name) == 31
    assert "WebstoreUnavailable" not in by_name
    for name in ["TestServiceUnavailable", "ApplicationTelemetryMissing", "ApplicationTrafficDrop"]:
        assert 'service_name="quote"' in by_name[name]["expr"]
        assert "frontend" not in by_name[name]["expr"] and "checkout" not in by_name[name]["expr"]
    # Validate the exact rendered collector config with its own image. Synthetic
    # service-account files satisfy construction; --network=none prevents access
    # to any cluster or notification endpoint.
    output = Path(__file__).resolve().parents[1] / ".helm" / "collector-test"
    output.mkdir(parents=True, exist_ok=True)
    (output / "collector.yaml").write_text(cm["data"]["relay"])
    ca = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "minikube-kubelet-ca")
    (output / "ca.crt").write_text(ca["data"]["ca.crt"])
    (output / "token").write_text("local-validation-only")
    (output / "namespace").write_text("otel-demo")
    collector_image = workloads["otel-collector-agent"]["spec"]["template"]["spec"]["containers"][0]["image"]
    subprocess.run([
        "docker", "run", "--rm", "--network=none",
        "-e", "MY_POD_IP=0.0.0.0", "-e", "K8S_NODE_NAME=otel-demo",
        "-e", "OTEL_K8S_NAMESPACE=otel-demo", "-e", "KUBERNETES_SERVICE_HOST=127.0.0.1",
        "-e", "KUBERNETES_SERVICE_PORT=443",
        "-v", f"{output}/collector.yaml:/etc/collector.yaml:ro",
        "-v", f"{output}/ca.crt:/etc/otel/kubelet-ca/ca.crt:ro",
        "-v", f"{output}:/var/run/secrets/kubernetes.io/serviceaccount:ro",
        collector_image, "validate", "--config=/etc/collector.yaml",
    ], check=True)
    print(f"PASS: 1 app + 1 traffic loop, 5 monitoring workloads, {total:g} MiB aggregate limits; PVCs retained.")


if __name__ == "__main__":
    main()
