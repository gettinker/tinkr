---
sidebar_position: 1
title: AWS
---

# Deploying on AWS

Tinker runs on any AWS compute that supports IAM roles. The server picks up credentials automatically from the instance metadata service — no long-lived access keys needed.

---

## Required IAM permissions

Attach this policy to the IAM role assigned to your compute:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:DescribeLogGroups",
        "logs:FilterLogEvents",
        "logs:GetLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchMetrics",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricStatistics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "XRay",
      "Effect": "Allow",
      "Action": [
        "xray:GetTraceSummaries",
        "xray:BatchGetTraces",
        "xray:GetInsightSummaries"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## EC2

```bash
# 1. Launch EC2 with the IAM role attached
# 2. SSH in and install Tinker
pip install tinker-agent

# 3. Run setup wizard
tinker init server
#   Step 1 → pick LLM provider, enter API key
#   Step 2 → Slack (optional)
#   Step 3 → GitHub token (for fix + PR)
#   Step 4 → server API key
#   Step 5 → backend=cloudwatch, region=us-east-1

# 4. Start as a background service
nohup tinker server > /var/log/tinker.log 2>&1 &
```

### systemd service (recommended)

```ini title="/etc/systemd/system/tinker.service"
[Unit]
Description=Tinker observability agent
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user
ExecStart=/usr/local/bin/tinker server
Restart=always
RestartSec=5
EnvironmentFile=/home/ec2-user/.tinker/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now tinker
sudo systemctl status tinker
journalctl -u tinker -f
```

---

## ECS Fargate

### Task definition (excerpt)

```json
{
  "family": "tinker",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/TinkerTaskRole",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "tinker",
      "image": "<your-ecr-account>.dkr.ecr.us-east-1.amazonaws.com/tinker:latest",
      "portMappings": [{ "containerPort": 8000 }],
      "environment": [
        { "name": "TINKR_BACKEND", "value": "cloudwatch" },
        { "name": "AWS_DEFAULT_REGION", "value": "us-east-1" }
      ],
      "secrets": [
        {
          "name": "ANTHROPIC_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:tinker/anthropic-api-key"
        },
        {
          "name": "TINKR_API_KEYS",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:tinker/api-keys"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/tinker",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

The task role (`TinkerTaskRole`) must have the IAM policy above attached.

### Secrets in AWS Secrets Manager

```bash
# Store the Anthropic key
aws secretsmanager create-secret \
  --name tinker/anthropic-api-key \
  --secret-string "sk-ant-..."

# Store hashed API keys (JSON array)
aws secretsmanager create-secret \
  --name tinker/api-keys \
  --secret-string '[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'
```

---

## Region configuration

```toml title="~/.tinkr/config.toml"
[profiles.aws-prod]
backend = "cloudwatch"
region  = "us-east-1"

[profiles.aws-eu]
backend = "cloudwatch"
region  = "eu-west-1"
```

Switch profiles with:

```bash
tinker profile use aws-eu
```

---

## Multi-account setup

Deploy one Tinker server per AWS account. Each has its own IAM role and profile:

```bash
# Account A server
TINKR_BACKEND=cloudwatch AWS_DEFAULT_REGION=us-east-1 tinker server

# Account B server
TINKR_BACKEND=cloudwatch AWS_DEFAULT_REGION=eu-west-1 tinker server
```

CLI users switch context with `tinker profile use <name>` or by setting `TINKR_SERVER_URL`.
