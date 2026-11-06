# Final AWS deployment report — UZEX Data Agent V2.1

Generated: 2026-11-05 17:55 +05:00
Transfer assets prepared: 2026-11-05 18:00 +05:00

## 1. Status: BLOCKED before any AWS mutation

No AWS resource was created, changed or deleted. No application or database was
transferred. The deployment cannot start because the existing EC2 endpoint is
not reachable and its connection identity is not confirmed.

This is a precise blocker report, not a claim of deployment.

## 2. What is available locally

| Item | State |
|---|---|
| Verified release | Git commit `8d1bc0b` |
| Application package | Complete; `docker compose config` exits 0 |
| Source dataset | `uzex_spot_data.zip` (500,000 deals) present |
| SSH private key | `uzexai.pem` (never committed; deleted after the host was decommissioned) |
| SSH client | OpenSSH for Windows 9.5p2 |
| AWS CLI | not installed |
| AWS credentials / `~/.aws` | absent |
| boto3 | not installed |
| Docker daemon (local) | not running |
| Domain / DNS / Elastic IP | none confirmed |
| Public egress IP of this workstation | `<OPERATOR_IP>` |
| PostgreSQL size | 325 MB (`340,407,999` bytes), database `uzex` at `127.0.0.1:55432` |
| `pg_dump` / `pg_restore` | available, PostgreSQL 18.6 (`C:\Program Files\PostgreSQL\18\bin`) |
| Database dump | `data/runtime/uzex_v21.dump` (33,541,042 bytes, custom format, gzip, `--no-owner --no-privileges`) |
| Free disk on `C:` | ~165 GB at check time |

## 2.1 Transfer assets prepared

- `data/runtime/uzex_v21.dump` — consistent custom-format `pg_dump` of the
  500,000-deal database. `pg_restore --list` reads the archive successfully
  (83 TOC entries, archive version 1.16). `data/runtime/` is gitignored, so the
  dump is never committed.
- A secret-free source bundle is produced from the release commit with
  `git archive`, so it contains only tracked files. `.env`, PEM keys, caches,
  `data/pgdata/` and `data/runtime/` are not included.
- Import is performed on the host from `data/runtime/uzex_v21.dump` or, if a
  clean import is preferred, from the tracked `uzex_spot_data.zip`.

Verified transfer artifacts (SHA-256 computed locally):

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `uzex_v21.dump` | 33,541,042 | `e1700f99f1023119a94265aa3a847973b42a87f54c3202bfc41afaaf2d01a8ea` |
| `uzex_v21_bundle.zip` | 27,052,353 | `a18cd891b919711e9b3734805b3cd2ea713bf157a0fdf52d0ec29b52fab7a69d` |

The bundle is built from Git commit `1315b40` with `git archive` (244 entries),
contains no `.env`, no PEM key, no `data/pgdata/` and no `data/runtime/`, and
includes `docker-compose.yml`, both Dockerfiles, `deploy/` and
`uzex_spot_data.zip`.

## 3. What was tested

The only EC2-related address found locally is the SSH known-host entry:

```text
[<PRIVATE_IP>]:5022
```

`<PRIVATE_IP>` is a private address. From this workstation
(`10.232.58.56`, default gateway `10.232.58.75`):

| Check | Result |
|---|---|
| TCP `<PRIVATE_IP>:22` | timeout |
| TCP `<PRIVATE_IP>:80` | timeout |
| TCP `<PRIVATE_IP>:443` | timeout |
| TCP `<PRIVATE_IP>:5022` | timeout |
| `ssh -i uzexai.pem -p 5022 ubuntu@<PRIVATE_IP>` | connection timed out |
| `ssh -i uzexai.pem -p 5022 ec2-user@<PRIVATE_IP>` | connection timed out |
| `ssh -i uzexai.pem -p 5022 admin@<PRIVATE_IP>` | connection timed out |

The VPN adapter is not connected/present. No public IP, public DNS, instance
ID, region or username was found in the project, SSH configuration, DNS cache
or user-profile connection files. A PEM file alone does not identify the host
or the login user, and it grants no AWS API permission.

## 4. Exact inputs required to proceed

Provide **all** of the following that apply:

1. **EC2 public IP or public DNS name.** The private `<PRIVATE_IP>` is not
   reachable from this network. If the instance has no public address, provide
   the VPN/jump-host connection details instead.
2. **SSH username** (`ubuntu`, `ec2-user`, `admin` or the actual user) and the
   **SSH port** if it is not 22. The `:5022` entry is only a local known-host
   hint and is currently unreachable.
3. **Network authorization:** the EC2 security group (or corporate firewall)
   must allow inbound TCP `22` (or the chosen SSH port) from
   `<OPERATOR_IP>/32`, and inbound `80`/`443` from the internet for the public
   site. If a security-group change is needed, explicit authorization is
   required.
4. **AWS region and instance ID**, or AWS credentials with read-only EC2
   permission, if the public address is unknown and must be discovered.
5. **Domain/DNS control** for a stable HTTPS URL. Without a domain, only the
   EC2 public IP/DNS can be used, and that address may change after
   stop/start; it must not be called permanent.
6. **Sudo/administration confirmation** for installing Docker/Compose and
   managing the service, plus authorization to back up and replace any
   existing application or database on the instance.

Without items 1–3 at minimum, no SSH/SCP transfer can begin. Items 4–6 are
required for a reliable 24/7 HTTPS deployment.

## 5. Prepared deployment sequence (ready to execute)

Once access is confirmed:

1. Connect with the provided key and verify the OS, architecture, RAM, disk and
   existing services; do not resize anything silently.
2. Back up any existing application and database; define rollback.
3. Transfer a secret-free bundle: source, `docker-compose.yml`, Dockerfiles,
   `deploy/`, `data/raw` or `uzex_spot_data.zip`. Exclude `.git`,
   `backend/.env`, the PEM key, caches and test data.
4. Create a production env file on the host with mode `600`; populate only the
   required values (DB passwords, `APP_SECRET`, `PLANNER_MODE=local`).
5. `docker compose build`, run `migrate`, run `import`, verify exactly 500,000
   deals and the reference tables.
6. Start `api` and `web` with `restart: unless-stopped`; enable Docker at boot.
7. Put Caddy/Nginx in front for HTTPS when a domain resolves to the host.
8. Verify from outside: frontend, `/api/v1/health`, `/api/v1/ready`,
   `/api/v1/meta`, 2024 = 106,647, 2023 = 93,985, 2003 = `NOT_FOUND`, RU/UZ
   Latin/UZ Cyrillic, evidence present, no secret in responses.
9. Verify container restart and database persistence, and configure daily
   backups with documented restore.

## 6. What is explicitly not claimed

- AWS deployment is **not complete**.
- No public URL is claimed as permanent or HTTPS-enabled.
- No 24/7 guarantee is made until restart policies, boot configuration and an
  external smoke test have actually passed.
- The Groq provider remains **UNQUALIFIED**; the production default is the
  deterministic local planner.
