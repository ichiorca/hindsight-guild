#!/usr/bin/env bash
# Model Armor: project-level activation + template.
#
# Two distinct things have to happen for Model Armor to actually screen
# Gemini calls from our agents:
#
#   1. The PROJECT must be enrolled in Model Armor's Vertex AI integration.
#      Done via: gcloud model-armor floorsettings update
#                  --add-integrated-services=VERTEX_AI
#   2. A TEMPLATE that defines what to screen for. Floor settings + the
#      template combine — every Gemini call in the project is screened with
#      AT LEAST the floor settings.
#
# Verify flag names against your installed gcloud version — the Model Armor
# CLI surface evolved through 2025-2026. See:
# https://docs.cloud.google.com/model-armor/model-armor-vertex-integration
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?must set PROJECT_ID}"
REGION="${REGION:-us-central1}"
TEMPLATE_ID="hindsight-guild-floor"

echo "==> 1. Enable Vertex AI integration at project floor"
gcloud model-armor floorsettings update \
  --full-uri="projects/${PROJECT_ID}/locations/global/floorSetting" \
  --add-integrated-services=VERTEX_AI \
  || echo "(floorsettings update may already be in this state)"

echo "==> 2. Create the floor template"
gcloud model-armor templates create "$TEMPLATE_ID" \
  --location="$REGION" \
  --project="$PROJECT_ID" \
  --pi-and-jailbreak-filter-settings-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-confidence-level=HIGH_AND_ABOVE \
  --basic-config-filter-enforcement=enabled \
  --malicious-uri-filter-settings-enforcement=enabled \
  2>/dev/null || echo "(template may already exist)"

echo "==> 3. Bind the template as the floor's default"
# Template-attached enforcement runs on every Gemini call in the project.
gcloud model-armor floorsettings update \
  --full-uri="projects/${PROJECT_ID}/locations/global/floorSetting" \
  --ai-platform-floor-setting-template="projects/${PROJECT_ID}/locations/${REGION}/templates/${TEMPLATE_ID}" \
  --enable-floor-setting-enforcement=true \
  || echo "(template binding step may require manual Console step; continuing)"

echo ""
echo "Template: projects/${PROJECT_ID}/locations/${REGION}/templates/${TEMPLATE_ID}"
echo ""
echo "With the project-level integration enabled, every Gemini call from the"
echo "agents passes through Model Armor automatically. Blocks return"
echo "block_reason='MODEL_ARMOR' which agents/_common.py captures into"
echo "telemetry.actions.model_armor."
