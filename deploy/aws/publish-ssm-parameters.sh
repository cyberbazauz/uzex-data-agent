#!/usr/bin/env bash
# Publish UZEX Data Agent V2.1 secrets to AWS SSM Parameter Store.
#
# This is an explicit operator action. It prompts for every secret with
# `read -s`, never echoes a value, never writes one to disk and never puts one
# in shell history. It uses --overwrite, so rerunning it replaces values.
#
# Required:
#   AWS_REGION and an approved profile with ssm:PutParameter on /uzex/prod/*
#
# Usage:
#   export AWS_PROFILE=<approved-profile>
#   export AWS_REGION=<approved-region>
#   bash deploy/aws/publish-ssm-parameters.sh
set -euo pipefail

REGION="${AWS_REGION:-}"
PREFIX="/uzex/prod"

if [[ -z "$REGION" ]]; then
  echo "ERROR: set AWS_REGION" >&2
  exit 1
fi
if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not installed" >&2
  exit 1
fi
if [[ ! -t 0 ]]; then
  echo "ERROR: run this interactively; it hides secret input with read -s" >&2
  exit 1
fi

cat <<'EOF'
This will write the following parameters to SSM Parameter Store:

  SecureString: postgres_password, reader_password, app_secret,
                groq_api_key, groq_api_key_secondary,
                groq_api_key_secondary_same_org,
                groq_api_key_tertiary_same_org
  String:       groq_organization_scope_id, groq_project_scope_id,
                postgres_db, planner_mode, web_port, cors_origins,
                dataset_cutoff_date, llm_fallback_chain,
                llm_plan_critic_mode, llm_rag_enabled

The four Groq keys are same-scope credential redundancy, not a quota pool.
Never use them to work around a 429.
EOF
echo
read -r -p "Type 'publish' to continue: " CONFIRM
if [[ "$CONFIRM" != "publish" ]]; then
  echo "Aborted; nothing was written."
  exit 1
fi
echo

put_secure() {
  local name="$1"
  local label="$2"
  local value
  read -r -s -p "${label}: " value
  echo
  if [[ -z "$value" ]]; then
    echo "ERROR: empty value refused for ${PREFIX}/${name}" >&2
    exit 1
  fi
  aws ssm put-parameter \
    --region "$REGION" \
    --name "${PREFIX}/${name}" \
    --type SecureString \
    --value "$value" \
    --overwrite \
    >/dev/null
  unset value
  echo "  stored ${PREFIX}/${name}"
}

put_plain() {
  local name="$1"
  local label="$2"
  local default="${3:-}"
  local value
  read -r -p "${label}${default:+ [${default}]}: " value
  if [[ -z "$value" ]]; then
    value="$default"
  fi
  if [[ -z "$value" ]]; then
    echo "ERROR: empty value refused for ${PREFIX}/${name}" >&2
    exit 1
  fi
  aws ssm put-parameter \
    --region "$REGION" \
    --name "${PREFIX}/${name}" \
    --type String \
    --value "$value" \
    --overwrite \
    >/dev/null
  echo "  stored ${PREFIX}/${name}"
}

echo "== Database credentials =="
put_secure postgres_password "PostgreSQL owner password"
put_secure reader_password "PostgreSQL read-only (uzex_reader) password"
put_secure app_secret "Application secret"
put_plain postgres_db "Database name" "uzex"

echo
echo "== Groq credentials (same organization/project scope) =="
put_secure groq_api_key "Groq primary API key"
put_secure groq_api_key_secondary "Groq secondary API key (same scope)"
put_secure groq_api_key_secondary_same_org "Groq same-org key"
put_secure groq_api_key_tertiary_same_org "Groq tertiary same-org key"
put_plain groq_organization_scope_id "Real Groq organization scope ID (not a placeholder)"
put_plain groq_project_scope_id "Real Groq project scope ID (not a placeholder)"

echo
echo "== Non-secret runtime settings =="
put_plain planner_mode "Planner mode (local|llm)" "llm"
put_plain web_port "Public web port" "8080"
put_plain cors_origins "Allowed browser origins (comma separated)" ""
put_plain dataset_cutoff_date "Dataset cutoff date" "2026-10-30"
put_plain llm_fallback_chain "Fallback chain (empty = disabled)" ""
put_plain llm_plan_critic_mode "Plan critic mode (off|risk_based)" "off"
put_plain llm_rag_enabled "Hybrid RAG enabled (true|false)" "false"

echo
echo "Done. No value was printed or stored locally."
echo "Next: run deploy/aws/render-env.sh ON the host to render /etc/uzex/agent.env."
