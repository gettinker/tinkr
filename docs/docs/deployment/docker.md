---
sidebar_position: 4
title: Docker / Self-hosted
---

# Docker and Self-hosted Deployment

---

## Build and run from source

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
cd tinker
docker build -t tinker:local .
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
  --name tinker \
  -p 8000:8000 \
  --env-file ~/.tinkr/.env \
  -v ~/.tinkr:/root/.tinkr \
  tinker:local
```

---

## Kubernetes (generic)

Build the image and push it to your own container registry first:

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
cd tinker
docker build -t your-registry/tinker:latest .
docker push your-registry/tinker:latest
```

Then deploy:

```yaml title="k8s/tinker.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tinker
  namespace: observability
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tinker
  template:
    metadata:
      labels:
        app: tinker
    spec:
      containers:
        - name: tinker
          image: your-registry/tinker:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: tinker-secrets
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
  name: tinker
  namespace: observability
spec:
  selector:
    app: tinker
  ports:
    - port: 80
      targetPort: 8000
```

```bash
# Create secrets from your .env file
kubectl create secret generic tinker-secrets \
  --from-env-file ~/.tinkr/.env \
  -n observability

kubectl apply -f k8s/tinker.yaml
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
