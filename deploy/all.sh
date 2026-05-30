#!/usr/bin/env bash
# Full deploy from a project that's already been bootstrapped by setup.sh
# + scripts/create_*.sh. Runs every deploy/*.sh in order.
#
# Re-runnable. The expensive step (01-build-images.sh) caches Docker
# layers in Cloud Build, so subsequent runs are minutes not tens of
# minutes. IAM bindings (06) are no-ops when they already exist.
#
# To redeploy only a subset, run the individual scripts directly.

set -euo pipefail
here="$(dirname "$0")"

# shellcheck source=deploy/env.sh
source "${here}/env.sh"

echo "================================================================"
echo "  Deploying hindsight-guild → ${PROJECT_ID} (${REGION})"
echo "================================================================"

"${here}/01-build-images.sh"
"${here}/02-deploy-services.sh"
"${here}/03-deploy-jobs.sh"
"${here}/04-schedulers.sh"
"${here}/05-deploy-ui.sh"
"${here}/06-bind-iam.sh"

echo ""
echo "================================================================"
echo "  Deploy complete."
echo "================================================================"
echo ""
echo "Manual follow-up:"
echo "  - apps_script/Code.gs: set HANDLER_URL = \$(edit-capture-handler URL)"
echo "  - Populate slack_webhook_url secret with your Slack incoming-webhook URL"
echo "  - Seed demo data:  python -m demo.seed_demo"
echo "  - Open the UI:     https://${FIREBASE_PROJECT:-$PROJECT_ID}.web.app  (Firebase Hosting)"
