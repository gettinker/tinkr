---
sidebar_position: 1
title: Docker / Self-hosted
---

# Docker and Self-hosted Deployment

---

## Build and run from source

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
docker build -t tinkr:local .
```

Create `~/.tinkr/.env` with your config:

```bash title="~/.tinkr/.env"
# LLM — required
ANTHROPIC_API_KEY=sk-ant-...

# Backend — pick one that matches your observability stack
TINKR_BACKEND=cloudwatch   # or gcp, azure, grafana, datadog, elastic, otel

# Auth — hashed API key for CLI users
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
# Hash:     python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw>
TINKR_API_KEYS='[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'

# Optional
GITHUB_TOKEN=ghp_...
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
```

Run:

```bash
docker run -d \
  --name tinkr \
  -p 8000:8000 \
  --env-file ~/.tinkr/.env \
  -v ~/.tinkr:/root/.tinkr \
  tinkr:local
```

---

## Kubernetes (generic)

Build the image and push it to your own container registry first:

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
docker build -t your-registry/tinkr:latest .
docker push your-registry/tinkr:latest
```

Then deploy:

```yaml title="k8s/tinkr.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tinkr
  namespace: observability
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tinkr
  template:
    metadata:
      labels:
        app: tinkr
    spec:
      containers:
        - name: tinkr
          image: your-registry/tinkr:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: tinkr-secrets
          env:
            - name: TINKR_BACKEND
              value: cloudwatch   # set to your backend
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: tinkr
  namespace: observability
spec:
  selector:
    app: tinkr
  ports:
    - port: 80
      targetPort: 8000
```

```bash
# Create secrets from your .env file
kubectl create secret generic tinkr-secrets \
  --from-env-file ~/.tinkr/.env \
  -n observability

kubectl apply -f k8s/tinkr.yaml
```

Set `TINKR_BACKEND` to whichever backend your cluster has access to — see [Backends](../backends/index.md) for the full list and required environment variables per backend.

---

## Health check

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

## API docs

```
http://localhost:8000/docs
```
