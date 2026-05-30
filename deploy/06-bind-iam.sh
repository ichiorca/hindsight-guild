#!/usr/bin/env bash
# Wire the cross-service IAM bindings that turn an inert pile of deploys
# into a working system. Idempotent — every binding uses add-iam-policy-binding,
# which is a no-op when the binding already exists.
#
# What gets granted:
#   1. sa-agents → roles/run.invoker on every A2A service
#      (so workers + web_api can JSON-RPC call agents)
#   2. sa-agents → roles/run.invoker on substack-publisher
#      (so edit-capture-handler + substack-publish-sweep can call it)
#   3. sa-scheduler → roles/run.invoker on every Cloud Run job
#      (so the scheduler triggers can fire `jobs:run`)
#   4. sa-agents + sa-scheduler → roles/secretmanager.secretAccessor on
#      every secret listed in SECRETS_FOR_AGENTS
#
# This script does NOT create the service accounts. Run
# scripts/create_agent_identity.sh first (sa-agents + sa-scheduler).

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

bind_run_invoker() {
  local service="$1"
  local member="$2"
  gcloud run services add-iam-policy-binding "$service" \
    --region="$REGION" \
    --member="serviceAccount:${member}" \
    --role="roles/run.invoker" \
    --project="$PROJECT_ID" \
    --quiet
}

bind_job_invoker() {
  local job="$1"
  local member="$2"
  # Cloud Run jobs invoker = roles/run.invoker scoped on the job resource.
  # `add-iam-policy-binding` works the same way as for services.
  gcloud run jobs add-iam-policy-binding "$job" \
    --region="$REGION" \
    --member="serviceAccount:${member}" \
    --role="roles/run.invoker" \
    --project="$PROJECT_ID" \
    --quiet
}

bind_secret_accessor() {
  local secret="$1"
  local member="$2"
  # Skip silently if the secret doesn't exist yet (setup.sh creates the
  # full set; older deploys may not have e.g. substack_publisher_url).
  if gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
    gcloud secrets add-iam-policy-binding "$secret" \
      --member="serviceAccount:${member}" \
      --role="roles/secretmanager.secretAccessor" \
      --project="$PROJECT_ID" \
      --quiet >/dev/null
  fi
}

echo "==> 1. sa-agents → run.invoker on every A2A service"
for name in "${!A2A_APPS[@]}"; do
  echo "  -> a2a-${name//_/-}"
  bind_run_invoker "a2a-${name//_/-}" "$SA"
done

echo "==> 2. sa-agents → run.invoker on substack-publisher"
bind_run_invoker substack-publisher "$SA"

echo "==> 3. sa-scheduler → run.invoker on every Cloud Run job"
for job in "${!JOBS[@]}"; do
  echo "  -> ${job}"
  bind_job_invoker "$job" "$SCHED_SA"
done

echo "==> 4. sa-agents + sa-scheduler → secretAccessor on shared secrets"
# Agents read all of them; the scheduler only really needs a2a_url_* and
# the auth secrets used by the cron clients. Grant both for simplicity —
# secret values stay sensitive but the binding is cheap.
for secret in "${SECRETS_FOR_AGENTS[@]}"; do
  bind_secret_accessor "$secret" "$SA"
  bind_secret_accessor "$secret" "$SCHED_SA"
done

# A2A URL secrets are created dynamically in 02-deploy-services.sh — bind
# read access on every one that exists.
echo "==> 5. sa-agents → secretAccessor on a2a_url_* (cross-agent routing)"
for name in "${!A2A_APPS[@]}"; do
  bind_secret_accessor "a2a_url_${name}" "$SA"
done

echo ""
echo "==> IAM bindings applied. The deploy is now self-contained:"
echo "    - Workers can call A2A services."
echo "    - Scheduler can trigger jobs."
echo "    - Every service can read its required secrets."
