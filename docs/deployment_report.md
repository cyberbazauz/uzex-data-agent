# Deployment report — UZEX Data Agent V2.1 (AWS)

Generated: 2026-11-05

## 1. Status: prepared, not deployed

No AWS resource was created, modified or deleted. The deployment is
**blocked on operator inputs and a missing AWS CLI on the build host**. The
repository now contains a reviewable, secret-free deployment package:

```
deploy/aws/
├── README.md                        runbook, topology, invariants
├── inventory.sh                     read-only AWS inventory
├── publish-ssm-parameters.sh        interactive SecureString publication
├── render-env.sh                    SSM -> /etc/uzex/agent.env (0600)
├── user-data.sh                     EC2 bootstrap, no secrets, starts nothing
└── ssm-parameters.example.json      value-free parameter manifest
```

## 2. Environment evidence

Checked on the build workstation:

| Check | Result |
|---|---|
| `aws --version` | command not found |
| `AWS_*` environment variables | none |
| Docker CLI | present (`29.8.2`) |
| Docker Compose plugin | present (`v5.6.0`) |
| Docker daemon | **not running** (`docker info` cannot reach the pipe) |
| Git repository | absent; all V2.1 changes are filesystem edits |
| `docker compose config` | **exit 0** with dummy validation-only values |

The Compose file was therefore validated syntactically and semantically, but
**images were not built and containers were not started** on this host.

Sanitized port check from `docker compose config`:

- the only published port is `web` → `8080:80`;
- `db` publishes nothing (only the internal Docker network on `5432`);
- `api` publishes nothing (nginx proxies `/api/` internally on `8000`).

This matches the invariants in section 5.

## 3. Blockers that prevent deployment

1. **No AWS CLI and no AWS credentials** on the build host. Read-only
   inventory and any deploy command cannot run.
2. **No target environment facts**: account, region, VPC, subnets, domain,
   TLS certificate, approved operator CIDR, and whether an ALB already exists
   are all unknown. Guessing them is forbidden.
3. **No confirmed Groq scope IDs**. `GROQ_ORGANIZATION_SCOPE_ID` and
   `GROQ_PROJECT_SCOPE_ID` are still placeholders
   (`groq-default-org`, `groq-default-project`). `render-env.sh` refuses to
   render an env file with empty scope IDs.
4. **Provider qualification is not yet proven by the full 112-case run.**
   `PLANNER_MODE=llm` must not be enabled in production until that report
   passes. If the run fails, deploy with `planner_mode=local` and keep the
   provider unqualified.
5. **Docker daemon is unavailable locally**, so the image build, healthchecks
   and container start order have not been exercised here.

## 4. Topology

### Option A — single EC2 + Docker Compose (recommended for V2.1)

- Amazon Linux 2023, `t3.large` or larger with an EBS gp3 volume.
- One host runs `db`, `migrate`, `import`, `api`, `web` from
  `docker-compose.yml`.
- Only the web port is reachable; the database never leaves the Docker bridge.
- Access via SSM Session Manager; no inbound `22`.
- Cheapest and simplest, and it matches the already-tested Compose topology.

### Option B — ALB + private EC2 + RDS PostgreSQL

- ALB terminates TLS with an ACM certificate and forwards to `web:80`.
- EC2 in a private subnet, RDS in private subnets.
- RDS security group allows `5432` only from the EC2 security group.
- Better availability and backup story, but requires VPC/subnet/ACM/RDS
  decisions that are not known yet.

**Recommendation:** start with Option A after the inventory confirms there is
no existing approved VPC/ALB to reuse. Do not create paid resources before the
operator confirms account, region, instance class and budget.

## 5. Security invariants enforced by the package

- No secret, PEM key or AWS credential is stored in `deploy/`, the Docker
  images, user-data, reports or environment examples.
- Secrets live in SSM Parameter Store `SecureString` under `/uzex/prod/`.
- `render-env.sh` runs as root, reads with `--with-decryption`, writes an
  atomic temp file, then `chmod 0600` / `chown root:root` and renames it to
  `/etc/uzex/agent.env`. It never prints a value.
- `publish-ssm-parameters.sh` uses `read -s`, refuses empty values, requires an
  interactive terminal and an explicit `publish` confirmation.
- `user-data.sh` installs Docker, Compose and the SSM agent and creates
  `/opt/uzex`; it contains no secret, clones nothing and starts no
  application.
- The API uses the `uzex_reader` role, which is `SELECT`-only with
  `default_transaction_read_only = on`. The owner role is used only by
  `db`/`migrate`/`import`.
- The instance profile needs only SSM read on `/uzex/prod/*` (plus
  `kms:Decrypt` if a customer-managed key is used) and the SSM Session Manager
  messages permissions.
- Public exposure is limited to `80`/`443`. `5432`, `8000` and `22` must never
  be open to `0.0.0.0/0`.

## 6. Prepared deployment sequence (not executed)

1. `bash deploy/aws/inventory.sh <region>` — read-only; record existing VPC,
   subnets, security groups, ALBs, ACM, RDS, SSM parameters, instance
   profiles.
2. Review inventory and pick Option A or B. Confirm budget and region.
3. `bash deploy/aws/publish-ssm-parameters.sh` — publish secrets and non-secret
   settings to `/uzex/prod/*`.
4. Launch the EC2 host with `deploy/aws/user-data.sh` as user-data and the
   least-privilege instance profile. No SSH key pair is required.
5. Copy the reviewed release to `/opt/uzex` over SSM (or a temporary operator
   CIDR that is removed immediately).
6. On the host:
   ```bash
   sudo AWS_REGION=<region> bash /opt/uzex/deploy/aws/render-env.sh
   cd /opt/uzex
   sudo docker compose --env-file /etc/uzex/agent.env build
   sudo docker compose --env-file /etc/uzex/agent.env up -d
   ```
7. Verify:
   ```bash
   curl -fsS http://127.0.0.1:8080/api/v1/health
   curl -fsS http://127.0.0.1:8080/api/v1/ready
   sudo docker compose --env-file /etc/uzex/agent.env exec api \
     python evaluation/run_evaluation.py
   ```
8. Enable `planner_mode=llm` only after the 112-case qualification report
   passes; otherwise keep `local` and report the provider as unqualified.

## 7. Post-deploy acceptance checks

- `ss -lntp` shows `5432` bound only to the Docker bridge or loopback, never
  `0.0.0.0`.
- Security groups expose only `80`/`443`, and only to the approved CIDR/ALB.
- `/api/v1/meta` reports the expected planner mode and model, and never a key.
- Logs contain masked credential fingerprints only.
- `/api/v1/ready` is green and the deterministic evaluation is 100/100.
- A backup/restore of the Postgres volume is tested before production import.
- The full Groq qualification report is stored under `docs/` and its status is
  reported honestly.

## 8. What is verified vs unverified

**Verified here**

- `docker compose config` exits 0 and publishes only `8080:80`.
- All four shell scripts pass `bash -n` syntax checking and are LF-terminated.
- No AWS resource exists as a result of this work (nothing was run against
  AWS).
- The repository contains no live AWS/Groq credential or PEM in `deploy/`,
  `docs/`, Docker files or environment examples.

**Unverified / not executed**

- Any AWS read-only inventory output.
- Any EC2/RDS/ALB/ACM resource creation.
- Image build, container startup, healthchecks and the in-container evaluation.
- Real Groq organization/project scope for the four keys.
- Production provider qualification (see `docs/final_v2_validation_report.md`
  for the final status).

No claim is made that the service is already publicly reachable. The public-URL
acceptance criterion remains open until an operator performs the steps above.
