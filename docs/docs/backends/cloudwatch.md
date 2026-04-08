---
sidebar_position: 2
title: AWS CloudWatch
---

# AWS CloudWatch Backend

Uses CloudWatch Logs Insights for log queries, CloudWatch Metrics for time series, and AWS X-Ray for distributed tracing.

```bash
TINKR_BACKEND=cloudwatch
```

---

## Authentication

The backend uses the standard AWS credential chain (`boto3` / `botocore`):

1. IAM Task Role (ECS Fargate) — **recommended for production**
2. EC2 instance profile
3. Environment variables (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) — avoid in production
4. `~/.aws/credentials` — local development with `aws configure`

**No credentials go in the Tinker config.** Attach the IAM role to your ECS task or EC2 instance.

---

## Required IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:DescribeLogGroups",
        "logs:FilterLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "xray:GetTraceSummaries",
        "xray:BatchGetTraces"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `AWS_REGION` | Yes | AWS region (e.g. `us-east-1`) |
| `CLOUDWATCH_LOG_GROUP_PREFIX` | No | Filter log groups by prefix (e.g. `/ecs/`) |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.aws-prod]
backend          = "cloudwatch"
region           = "us-east-1"
log_group_prefix = "/ecs/"

[profiles.aws-prod.services.payments-api]
resource_type = "ecs"
repo          = "acme/payments"
```

---

## Log query

Tinker queries CloudWatch Logs Insights:

```sql
fields @timestamp, @message, @logStream
| filter @logStream like /payments-api/
| filter @message like /ERROR/
| sort @timestamp desc
| limit 100
```

Log groups matching `CLOUDWATCH_LOG_GROUP_PREFIX` are queried. If no prefix is set, all log groups in the region are searched (may be slow).

---

## Metrics

Tinker calls `cloudwatch:GetMetricData` with the metric name as a dimension value. Common CloudWatch metric namespaces:

| Service | Namespace | Metric |
|---|---|---|
| ECS | `AWS/ECS` | `CPUUtilization`, `MemoryUtilization` |
| ALB | `AWS/ApplicationELB` | `RequestCount`, `HTTPCode_ELB_5XX_Count`, `TargetResponseTime` |
| Lambda | `AWS/Lambda` | `Errors`, `Duration`, `Throttles` |
| RDS | `AWS/RDS` | `DatabaseConnections`, `WriteLatency`, `ReadLatency` |

---

## Distributed tracing (X-Ray)

X-Ray must be enabled in your application:

- **ECS**: Add the X-Ray daemon as a sidecar container; set `AWS_XRAY_DAEMON_ADDRESS`
- **Lambda**: Enable active tracing in the function configuration
- **SDK**: Add `aws-xray-sdk` to your application

Tinker calls `xray:GetTraceSummaries` for the time window, then `xray:BatchGetTraces` to fetch span details.

---

## Deployment

See [Deploying on AWS](../deployment/aws) for ECS Fargate task definition, IAM role, and Secrets Manager setup.

---

## Local development

```bash
# Configure AWS CLI
aws configure  # or use SSO: aws sso login

export TINKR_BACKEND=cloudwatch
export AWS_REGION=us-east-1
uv run tinkr-server
```
