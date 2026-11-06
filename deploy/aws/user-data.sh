#!/usr/bin/env bash
# EC2 user-data for the UZEX Data Agent V2.1 host (Amazon Linux 2023).
#
# This script installs Docker, the Compose plugin and the SSM agent and
# creates /opt/uzex. It is safe to attach to a reviewed launch template:
#
#   * it contains NO secret, key, token or password;
#   * it does NOT clone a repository or download an artifact;
#   * it does NOT start the application.
#
# The application and its secrets are supplied later through SSM, as described
# in deploy/aws/README.md.
set -euxo pipefail

# --- Base packages -----------------------------------------------------------
dnf update -y
dnf install -y docker amazon-ssm-agent
systemctl enable --now docker
systemctl enable --now amazon-ssm-agent

# --- Docker Compose plugin ---------------------------------------------------
# Amazon Linux 2023 packages docker-compose-plugin in some AMIs; fall back to
# the official binary when the package is unavailable.
if ! docker compose version >/dev/null 2>&1; then
  COMPOSE_VERSION="v2.29.7"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64)  COMPOSE_ARCH="x86_64" ;;
    aarch64) COMPOSE_ARCH="aarch64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
  esac
  install -d -m 0755 /usr/local/lib/docker/cli-plugins
  curl -fsSL \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${COMPOSE_ARCH}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose
fi

# --- Runtime directories -----------------------------------------------------
install -d -m 0755 -o root -g root /opt/uzex
install -d -m 0700 -o root -g root /etc/uzex

# Do not create an SSH key pair, do not open ports, do not add users.
# Host access is through SSM Session Manager only.

# --- Operator hints ----------------------------------------------------------
cat >/etc/motd <<'EOF'
UZEX Data Agent V2.1 host.

No application is running yet. To deploy:
  1. Place the reviewed release in /opt/uzex.
  2. sudo AWS_REGION=<region> bash /opt/uzex/deploy/aws/render-env.sh
  3. cd /opt/uzex
     sudo docker compose --env-file /etc/uzex/agent.env build
     sudo docker compose --env-file /etc/uzex/agent.env up -d

Never place keys, PEM files or credentials in /opt/uzex or in this host's
user-data. All secrets come from SSM Parameter Store.
EOF

echo "user-data complete: Docker, Compose and SSM agent installed; no app started."
