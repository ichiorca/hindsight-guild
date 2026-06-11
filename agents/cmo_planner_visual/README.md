# CMO Planner — manual build in Agent Designer

The CMO Planner is the one agent built visually rather than in code. It's the
agent whose prompt and tool-choice evolves most as you learn what a good weekly
memo looks like; Agent Designer's UI makes that iteration trivial.

## Build steps (Console)

1. Open the Agent Builder Console → **Agent Designer** → **New Agent**.
2. **Name**: `cmo_planner`. **Model**: `gemini-3.5-flash`.
3. **Instructions**: paste the contents of `prompts/cmo_planner/weekly_memo_v1.txt`.
4. **Tools** — attach from Tool Hub (or wire directly):
   - `mongodb_mcp` — scope: full. (Uses `mongo_uri_writer` secret.)
   - `bigquery_query` — Custom Function tool. Upload `tools.py`. The function
     name is `bigquery_query` and it returns a list of dicts.
   - `slack_approval` — HTTP tool pointing at the Cloud Run handler in
     `services/slack_approval_handler/`. URL injected at deploy time.
5. **Workflow** — configure the five sequential steps from `playbook.yaml`:
   - `pull_running_experiments` (mongodb.find on experiments where state=running)
   - `pull_recent_decisions` (mongodb.find on experiments where state=decided, sorted)
   - `pull_skill_track_record` (bigquery_query)
   - `pull_rubric_trend` (bigquery_query)
   - `pull_self_critique_proposals` (mongodb.find on skills with proposal status)
   - `draft_memo` (LLM step taking all five upstream outputs)
   - `request_approval` (slack_approval, takes draft_memo as input)
6. **Service account**: `sa-agents@${PROJECT_ID}.iam.gserviceaccount.com`.
7. **Save** and run a test invocation.
8. **Export** the playbook YAML. Overwrite `agents/cmo_planner_visual/playbook.yaml`
   with the real schema from the Console export.

## When to port to ADK

The CMO Planner moves to ADK (`agents/cmo_planner.py` alongside the others)
once the playbook stops changing. Migration is mechanical: copy the prompt
into `_prompts.py`, the tool list into a new `cmo_planner.py` mirroring the
other agents, and use `mongo_uri_writer` as the secret.

That move is in the Phase-2 migration table; deferred until the playbook
has been stable for two weeks.
