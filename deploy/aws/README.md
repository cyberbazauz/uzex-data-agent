# AWS deployment assets (V2.1)

These files are **prepared but not applied**. No AWS resource was created,
modified or deleted during V2.1 because the workstation has no AWS CLI and no
AWS credentials:

```
aws --version  -> command not found
AWS_* env vars -> none
```

The operator must supply the target account, region, VPC/subnet, domain, and
an approved access method before any deployment step. Do not guess these
values and do not launch paid resources without confirmation.

## Security invariants

- The runtime database listens on `5432` only on the private Docker network.
  It is never published to the host or the internet.
- Only ports `80` and `443` may be exposed publicly. The API port `8000` is
  private; nginx proxies `/api/` inside the Docker network.
- SSH port `22` is **not** required. Use AWS Systems Manager Session Manager.
  If SSH is unavoidable, restrict it to an explicit operator CIDR; keep the
  private key only on the operator workstation.
- Never place a PEM key, AWS access key, Groq key, database password or
  `APP_SECRET` in this repository, a Docker image, user-data, a report, or an
  EC2 tag.
- Secrets live in SSM Parameter Store `SecureString` (or Secrets Manager) and
  are rendered at boot into `/etc/uzex/agent.env` with mode `0600`.
- The EC2 instance profile needs only:
  - `ssm:GetParameter` / `ssm:GetParameters` on `/uzex/prod/*`
  - `ssm:GetParametersByPath` on `/uzex/prod/`
  - `kms:Decrypt` for the SSM key if a customer-managed key is used
  - `ssm:UpdateInstanceInformation`, `ssmmessages:*`,
    `ec2messages:*` for Session Manager
- The application database role is `uzex_reader` with `SELECT` only. The
  importer/migration role is used only during import, never by the API.

## Step 1 — read-only inventory (run first)

```bash
export AWS_PROFILE=<approved-profile>
export AWS_REGION=<approved-region>
bash deploy/aws/inventory.sh
```

The script only calls `describe-*`, `list-*` and `get-caller-identity`.
It never creates, changes or deletes anything. Review the output and record:

- existing VPC, public subnet, route table and internet gateway;
- existing EC2 instances and their instance profiles;
- security groups and any rule exposing `22`, `5432` or `8000`;
- existing SSM parameters under `/uzex/prod/`;
- existing S3 buckets and CloudFormation stacks;
- whether an ALB/ACM certificate already exists.

## Step 2 — choose a topology

### Option A (recommended for V2.1): single EC2 host + Docker Compose

- One EC2 instance (Amazon Linux 2023, `t3.large` or larger; the dataset is
  500,000 deals and the image build needs memory).
- Docker Compose runs `db`, `migrate`, `import`, `api`, `web`.
- The database volume stays on the instance EBS volume.
- Security group inbound: `80/443` from the approved CIDR or ALB only.
- Access: SSM Session Manager, no inbound SSH.
- This is the cheapest option and keeps all ports private except the web port.

### Option B: ALB + private EC2 + RDS PostgreSQL

- ALB terminates TLS with an ACM certificate and forwards to the EC2 web port.
- EC2 is in a private subnet; no public IP.
- RDS PostgreSQL is in private subnets with a security group that allows
  `5432` only from the EC2 security group.
- This requires VPC/subnet/ACM/RDS decisions that are **not** known yet.
  Do not create them until the inventory is reviewed.

## Step 3 — publish secrets to SSM (explicit operator action)

`publish-ssm-parameters.sh` prompts for each value with `read -s` and never
echoes or stores it. It uses `--overwrite`; run it only after the inventory
and parameter names are approved. `ssm-parameters.example.json` is a
value-free manifest of the same names, types and purposes; it contains no
secret.

```bash
export AWS_REGION=<approved-region>
bash deploy/aws/publish-ssm-parameters.sh
```

Parameter names:

| Parameter | Type | Purpose |
|---|---|---|
| `/uzex/prod/postgres_password` | SecureString | owner password |
| `/uzex/prod/reader_password` | SecureString | read-only API role |
| `/uzex/prod/app_secret` | SecureString | application secret |
| `/uzex/prod/groq_api_key` | SecureString | Groq primary |
| `/uzex/prod/groq_api_key_secondary` | SecureString | same-scope redundancy |
| `/uzex/prod/groq_api_key_secondary_same_org` | SecureString | same-scope redundancy |
| `/uzex/prod/groq_api_key_tertiary_same_org` | SecureString | same-scope redundancy |
| `/uzex/prod/groq_organization_scope_id` | String | real Groq org scope |
| `/uzex/prod/groq_project_scope_id` | String | real Groq project scope |

The extra Groq keys are **credential redundancy only**. They are not a quota
pool, and deployment must not rotate them after HTTP 429.

## Step 4 — boot the host

`user-data.sh` only installs Docker, the Compose plugin, the SSM agent and
creates `/opt/uzex`. It contains no secret and does not start the application.
It is suitable for a reviewed launch template.

## Step 5 — deploy the application

1. Copy the release artifact to `/opt/uzex` using SSM (for example
   `aws ssm send-command` with an S3 download, or `scp` over a temporary
   operator CIDR rule that is removed immediately).
2. Render secrets: `sudo AWS_REGION=<region> bash deploy/aws/render-env.sh`.
3. Build and start:
   ```bash
   cd /opt/uzex
   sudo docker compose --env-file /etc/uzex/agent.env build
   sudo docker compose --env-file /etc/uzex/agent.env up -d
   ```
4. Verify:
   ```bash
   curl -fsS http://127.0.0.1:8080/api/v1/health
   curl -fsS http://127.0.0.1:8080/api/v1/ready
   ```
5. Run the deterministic evaluation inside the backend container:
   ```bash
   sudo docker compose --env-file /etc/uzex/agent.env exec api \
     python evaluation/run_evaluation.py
   ```

## Step 6 — post-deploy checks

- `ss -lntp` must show `5432` bound only to `127.0.0.1` or the Docker bridge,
  never `0.0.0.0`.
- The security group must not allow `22`, `5432` or `8000` from `0.0.0.0/0`.
- `/api/v1/meta` must report `planner_mode=llm` and the expected primary model
  only after the provider is qualified.
- Logs must contain masked credential IDs only, never keys.
- The full provider qualification report must pass before enabling
  `PLANNER_MODE=llm` in production.
- Back up the Postgres volume and test restore before importing production
  data.

## What is still missing

- AWS CLI and credentials on the build host.
- Target account, region, VPC, subnet, domain and TLS certificate.
- Confirmation that the four Groq keys share one organization/project scope.
- A completed 112-case Groq qualification report.
- Docker daemon on the local workstation, so the Compose file has not been
  built locally.
