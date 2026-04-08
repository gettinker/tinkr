---
sidebar_position: 2
title: GCP
---

# Deploying on GCP

Tinker uses Workload Identity — no service account JSON files or long-lived credentials needed.

---

## Required IAM roles

Assign these roles to the service account Tinker runs as:

| Role | Purpose |
|---|---|
| `roles/logging.viewer` | Read Cloud Logging entries |
| `roles/monitoring.viewer` | Read Cloud Monitoring metrics |
| `roles/cloudtrace.user` | Read Cloud Trace data |

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudtrace.user"
```

---

## Cloud Run (recommended)

```bash
# Build and push the image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/tinker

# Deploy
gcloud run deploy tinker \
  --image gcr.io/YOUR_PROJECT_ID/tinker \
  --platform managed \
  --region us-central1 \
  --service-account tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars TINKR_BACKEND=gcp,GCP_PROJECT_ID=YOUR_PROJECT_ID \
  --set-secrets ANTHROPIC_API_KEY=tinker-anthropic-key:latest \
  --set-secrets TINKR_API_KEYS=tinker-api-keys:latest \
  --allow-unauthenticated \
  --port 8000 \
  --memory 1Gi \
  --cpu 1
```

### Secrets in GCP Secret Manager

```bash
# Create secrets
echo -n "sk-ant-..." | gcloud secrets create tinker-anthropic-key --data-file=-

echo -n '[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]' \
  | gcloud secrets create tinker-api-keys --data-file=-

# Grant the service account access
gcloud secrets add-iam-policy-binding tinker-anthropic-key \
  --member="serviceAccount:tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## GKE

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
      annotations:
        iam.gke.io/gcp-service-account: tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com
    spec:
      serviceAccountName: tinker-ksa
      containers:
        - name: tinker
          image: gcr.io/YOUR_PROJECT_ID/tinker:latest
          ports:
            - containerPort: 8000
          env:
            - name: TINKR_BACKEND
              value: gcp
            - name: GCP_PROJECT_ID
              value: YOUR_PROJECT_ID
          envFrom:
            - secretRef:
                name: tinker-secrets
---
apiVersion: v1
kind: Service
metadata:
  name: tinker
spec:
  selector:
    app: tinker
  ports:
    - port: 80
      targetPort: 8000
  type: LoadBalancer
```

### GKE Workload Identity binding

```bash
# Create Kubernetes service account
kubectl create serviceaccount tinker-ksa

# Bind it to the GCP service account
gcloud iam service-accounts add-iam-policy-binding \
  tinker@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:YOUR_PROJECT_ID.svc.id.goog[default/tinker-ksa]"
```

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.gcp-prod]
backend    = "gcp"
project_id = "my-project-prod"

[profiles.gcp-staging]
backend    = "gcp"
project_id = "my-project-staging"
```
