#!/usr/bin/env bash
# C12 — Atlas-side governance: create separate read-only and writer DB users.
# This is the HARD governance line at hackathon — Atlas rejects writes from
# the read-only user at the database layer.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?must set PROJECT_ID}"
ATLAS_PROJECT_ID="${ATLAS_PROJECT_ID:?must set ATLAS_PROJECT_ID (atlas projects list to find)}"

RO_PW="$(openssl rand -base64 24 | tr -d /=+)"
RW_PW="$(openssl rand -base64 24 | tr -d /=+)"

# NOTE: no built-in role positional — the read-only user must be read-only.
# Passing a builtin like `readWriteAnyDatabase` here would OVER-GRANT it
# write access everywhere, defeating the governance split. The scoped
# `--role read@hindsight_guild` is the only grant.
atlas dbusers create \
  --username "agent-readonly" --password "$RO_PW" \
  --projectId "$ATLAS_PROJECT_ID" --role "read@hindsight_guild" \
  2>/dev/null || echo "agent-readonly may already exist; continuing"

atlas dbusers create \
  --username "agent-writer" --password "$RW_PW" \
  --projectId "$ATLAS_PROJECT_ID" --role "readWrite@hindsight_guild" \
  2>/dev/null || echo "agent-writer may already exist; continuing"

CLUSTER_SRV="$(atlas clusters describe hindsight-guild --projectId "$ATLAS_PROJECT_ID" \
  --output json | jq -r .connectionStrings.standardSrv)"
HOST="${CLUSTER_SRV#mongodb+srv://}"

# Write to Secret Manager (overwriting existing versions if any).
for pair in "mongo_uri_readonly:agent-readonly:$RO_PW" \
            "mongo_uri_writer:agent-writer:$RW_PW"; do
  IFS=":" read -r secret user pw <<<"$pair"
  uri="mongodb+srv://${user}:${pw}@${HOST}/hindsight_guild?retryWrites=true&w=majority"
  printf '%s' "$uri" | gcloud secrets create "$secret" --data-file=- --project "$PROJECT_ID" 2>/dev/null \
    || printf '%s' "$uri" | gcloud secrets versions add "$secret" --data-file=- --project "$PROJECT_ID"
done

# Grant sa-agents access to both secrets
for s in mongo_uri_readonly mongo_uri_writer; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:sa-agents@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" --project "$PROJECT_ID" 1>/dev/null
done

echo ""
echo "Mongo users created and bound to Secret Manager:"
echo "  mongo_uri_readonly — for Content + Review agents"
echo "  mongo_uri_writer   — for Research, CMO Planner, workers, seed_demo"
