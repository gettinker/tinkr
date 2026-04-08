---
sidebar_position: 4
title: Azure Monitor
---

# Azure Monitor Backend

Uses Log Analytics (KQL) for logs, Azure Monitor Metrics API for metrics, and Application Insights for distributed tracing.

```bash
TINKR_BACKEND=azure
```

---

## Authentication

The backend uses `DefaultAzureCredential` from `azure-identity`:

1. Managed Identity (Container Apps / AKS) — **recommended for production**
2. Azure CLI (`az login`) — local development
3. Environment variables (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`) — avoid in production

**Never use `AZURE_CLIENT_SECRET` in production.** Use Managed Identity instead.

---

## Required RBAC roles

Assign at the subscription or resource group scope:

| Role | Purpose |
|---|---|
| `Monitoring Reader` | Read Azure Monitor metrics |
| `Log Analytics Reader` | Run KQL queries against Log Analytics |

```bash
PRINCIPAL_ID=$(az identity show \
  --name tinker-identity \
  --resource-group my-rg \
  --query principalId -o tsv)

az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role "Monitoring Reader" \
  --scope /subscriptions/SUBSCRIPTION_ID

az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role "Log Analytics Reader" \
  --scope /subscriptions/SUBSCRIPTION_ID
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | Yes | Log Analytics workspace ID |
| `AZURE_SUBSCRIPTION_ID` | Yes | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | Yes | Resource group containing monitored resources |
| `AZURE_CLIENT_ID` | For pod/Container Apps identity | Client ID of Managed Identity |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.azure-prod]
backend         = "azure"
workspace_id    = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
subscription_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
resource_group  = "prod-rg"
```

---

## Log query (KQL)

Tinkr queries the Log Analytics workspace using KQL:

```kql
AppTraces
| where TimeGenerated >= ago(1h)
| where AppRoleName == "payments-api"
| where SeverityLevel >= 3
| project TimeGenerated, Message, SeverityLevel, Properties
| order by TimeGenerated desc
| take 100
```

Severity mapping:

| KQL `SeverityLevel` | Log level |
|---|---|
| 1 | Verbose |
| 2 | Information |
| 3 | Warning |
| 4 | Error |
| 5 | Critical |

---

## Metrics

Tinkr calls the Azure Monitor Metrics REST API to fetch time series data. Common metrics:

| Resource type | Metric |
|---|---|
| App Service | `requests`, `responseTime`, `http5xx` |
| Container Apps | `Requests`, `ResponseTime` |
| Azure Functions | `FunctionExecutionCount`, `FunctionExecutionUnits` |
| Azure SQL | `dtu_consumption_percent`, `connection_failed` |

---

## Distributed tracing (Application Insights)

Application Insights must be connected to your Log Analytics workspace. Tinkr queries the `AppRequests` table:

```kql
AppRequests
| where TimeGenerated >= ago(1h)
| where AppRoleName == "payments-api"
| where Success == false or DurationMs > 1000
| project OperationId, Name, DurationMs, Success, ResultCode, TimeGenerated
| order by DurationMs desc
| take 20
```

Each `OperationId` groups all spans for a single distributed trace.

To send traces from your application, enable Application Insights auto-instrumentation or use the OpenTelemetry SDK with the Azure Monitor exporter.

---

## Deployment

See [Deploying on Azure](../deployment/azure) for Container Apps, AKS, Managed Identity, and Key Vault setup.

---

## Local development

```bash
az login

export TINKR_BACKEND=azure
export AZURE_LOG_ANALYTICS_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export AZURE_RESOURCE_GROUP=dev-rg
uv run tinkr-server
```
