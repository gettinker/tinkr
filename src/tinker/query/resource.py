"""Resource type definitions and per-backend resolution maps.

Pass `resource_type` explicitly to CLI commands and client calls.
Each backend translates it to its native concept (log group, resource.type,
KQL table, Loki label, ES index pattern).

Supported resource types:
    lambda / cloudfn        AWS Lambda / GCP Cloud Functions
    ecs / fargate           AWS ECS / Fargate
    eks / gke / aks / k8s   Kubernetes (any cloud)
    ec2 / gce / vm / host   Virtual machines
    apigw                   API Gateway
    rds / aurora / db       Relational databases
    cloudrun                GCP Cloud Run
    appengine               GCP App Engine
    appservice              Azure App Service
    container               Generic container (cross-cloud alias)

Cross-cloud aliases map to the closest equivalent on the active backend.
Unknown resource types are treated as best-effort so users are never blocked.
"""

from __future__ import annotations

# ── CloudWatch ────────────────────────────────────────────────────────────────
# Maps resource type → log group pattern (f-string with {service})

CW_LOG_GROUP: dict[str, str] = {
    "lambda":    "/aws/lambda/{service}",
    "ecs":       "/ecs/{service}",
    "fargate":   "/ecs/{service}",
    "eks":       "/aws/containerinsights/{service}/application",
    "ec2":       "/aws/ec2/{service}",
    "apigw":     "API-Gateway-Execution-Logs_{service}/prod",
    "rds":       "/aws/rds/instance/{service}/postgresql",
    "aurora":    "/aws/rds/cluster/{service}/postgresql",
    "codebuild": "/aws/codebuild/{service}",
    "stepfn":    "/aws/states/{service}",
    # Cross-cloud aliases → best guess for AWS
    "container": "/ecs/{service}",
    "k8s":       "/aws/containerinsights/{service}/application",
    "host":      "/aws/ec2/{service}",
    "db":        "/aws/rds/instance/{service}/postgresql",
}

# ── GCP ───────────────────────────────────────────────────────────────────────
# Maps resource type → (resource.type value, label key for service name)

GCP_RESOURCE: dict[str, tuple[str, str]] = {
    "cloudrun":   ("cloud_run_revision",  "service_name"),
    "gke":        ("k8s_container",       "container_name"),
    "appengine":  ("gae_app",             "module_id"),
    "gce":        ("gce_instance",        "instance_id"),
    "cloudfn":    ("cloud_function",      "function_name"),
    "dataflow":   ("dataflow_step",       "job_name"),
    # Cross-cloud aliases
    "lambda":     ("cloud_function",      "function_name"),
    "ecs":        ("cloud_run_revision",  "service_name"),
    "eks":        ("k8s_container",       "container_name"),
    "container":  ("cloud_run_revision",  "service_name"),
    "k8s":        ("k8s_container",       "container_name"),
    "host":       ("gce_instance",        "instance_id"),
    "db":         ("cloudsql_database",   "database_id"),
}

# ── Azure ─────────────────────────────────────────────────────────────────────
# Maps resource type → KQL table name

AZURE_TABLE: dict[str, str] = {
    "appservice":  "AppServiceConsoleLogs",
    "aks":         "ContainerLog",
    "vm":          "Syslog",
    "function":    "FunctionAppLogs",
    "apigw":       "ApiManagementGatewayLogs",
    "sql":         "AzureDiagnostics",
    "db":          "AzureDiagnostics",
    # Default app instrumentation
    "app":         "AppTraces",
    # Cross-cloud aliases
    "lambda":      "FunctionAppLogs",
    "ecs":         "ContainerLog",
    "eks":         "ContainerLog",
    "ec2":         "Syslog",
    "rds":         "AzureDiagnostics",
    "container":   "ContainerLog",
    "k8s":         "ContainerLog",
    "host":        "Syslog",
}

# ── Loki ──────────────────────────────────────────────────────────────────────
# Maps resource type → extra stream selector labels to add

LOKI_LABELS: dict[str, dict[str, str]] = {
    "lambda":   {"resource": "lambda"},
    "ecs":      {"resource": "container"},
    "fargate":  {"resource": "container"},
    "eks":      {"resource": "container"},
    "k8s":      {"resource": "container"},
    "ec2":      {"resource": "host"},
    "host":     {"resource": "host"},
    "apigw":    {"resource": "apigw"},
    "rds":      {"resource": "db"},
    "db":       {"resource": "db"},
    "container":{"resource": "container"},
    "cloudrun": {"resource": "container"},
    "gke":      {"resource": "container"},
    "aks":      {"resource": "container"},
    "appservice": {"resource": "container"},
    "vm":       {"resource": "host"},
    "gce":      {"resource": "host"},
}

# ── Elastic ───────────────────────────────────────────────────────────────────
# Maps resource type → Elasticsearch index pattern

ELASTIC_INDEX: dict[str, str] = {
    "lambda":    "lambda-*",
    "ecs":       "ecs-*",
    "fargate":   "ecs-*",
    "eks":       "kubernetes-*",
    "k8s":       "kubernetes-*",
    "ec2":       "syslog-*",
    "host":      "syslog-*",
    "apigw":     "apigw-*",
    "rds":       "rds-*",
    "db":        "rds-*",
    "container": "ecs-*",
    "cloudrun":  "ecs-*",
    "gke":       "kubernetes-*",
    "aks":       "kubernetes-*",
    "appservice":"appservice-*",
    "vm":        "syslog-*",
    "gce":       "syslog-*",
}

DEFAULT_ELASTIC_INDEX = "logs-*"
