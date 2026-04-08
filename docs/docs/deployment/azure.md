---
sidebar_position: 3
title: Azure
---

# Deploying on Azure

Tinkr uses Managed Identity — no client secrets needed in production.

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

## Container Apps (recommended)

```bash
# Create managed identity
az identity create --name tinker-identity --resource-group my-rg

IDENTITY_ID=$(az identity show --name tinker-identity --resource-group my-rg --query id -o tsv)
CLIENT_ID=$(az identity show --name tinker-identity --resource-group my-rg --query clientId -o tsv)

# Store secrets in Key Vault
az keyvault secret set --vault-name my-vault --name anthropic-api-key --value "sk-ant-..."
az keyvault secret set --vault-name my-vault --name tinker-api-keys \
  --value '[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'

# Deploy
az containerapp create \
  --name tinker \
  --resource-group my-rg \
  --environment my-env \
  --image <your-acr>.azurecr.io/tinker:latest \
  --user-assigned $IDENTITY_ID \
  --target-port 8000 \
  --ingress external \
  --env-vars \
    TINKR_BACKEND=azure \
    AZURE_LOG_ANALYTICS_WORKSPACE_ID=<workspace-id> \
    AZURE_SUBSCRIPTION_ID=<subscription-id> \
    AZURE_RESOURCE_GROUP=my-rg \
    AZURE_CLIENT_ID=$CLIENT_ID \
  --secrets \
    anthropic-api-key=keyvaultref:<vault-uri>/secrets/anthropic-api-key,identityref:$IDENTITY_ID \
    tinker-api-keys=keyvaultref:<vault-uri>/secrets/tinker-api-keys,identityref:$IDENTITY_ID
```

---

## AKS

```yaml title="k8s/tinker.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tinker
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tinker
  template:
    metadata:
      labels:
        app: tinker
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: tinker-sa
      containers:
        - name: tinker
          image: <your-acr>.azurecr.io/tinker:latest
          ports:
            - containerPort: 8000
          env:
            - name: TINKR_BACKEND
              value: azure
            - name: AZURE_LOG_ANALYTICS_WORKSPACE_ID
              value: "<workspace-id>"
            - name: AZURE_SUBSCRIPTION_ID
              value: "<subscription-id>"
            - name: AZURE_RESOURCE_GROUP
              value: "my-rg"
          envFrom:
            - secretRef:
                name: tinker-secrets
```

---

## Required environment variables

| Variable | Description |
|---|---|
| `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | Log Analytics workspace ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | Resource group containing monitored resources |
| `AZURE_CLIENT_ID` | Client ID of Managed Identity (for pod identity / Container Apps) |

For local development with `az login`, `DefaultAzureCredential` picks up your CLI credentials automatically — no environment variables needed.

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.azure-prod]
backend           = "azure"
workspace_id      = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
subscription_id   = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
resource_group    = "prod-rg"
```
