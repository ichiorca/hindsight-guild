# Post-hackathon: Cloud API Registry registration

For Phase 2, register MongoDB MCP, BigQuery query, and Slack approval in the
**Cloud API Registry** (the rebranded/integrated Tool Hub in 2026) so all
agents in the org access them through one governed surface.

There is no `gcloud agent-builder tools register` subcommand. Registration is
via the Google Cloud Console UI or the Cloud API Registry REST API. For
hackathon scope, the simpler and verifiably-correct path is to wire MCPToolset
directly in each agent's Python code (see `agents/*.py`).

## Migration steps (Console)

1. Cloud Console → Cloud API Registry.
2. Add an MCP server entry pointing to the MongoDB MCP package.
3. Add HTTP function entries for the BigQuery and Slack helpers.
4. Apply ACLs by agent service account.

REST API reference: https://cloud.google.com/api-registry/docs

In agent code, the `MCPToolset` for a registry-managed MCP is loaded by URI
rather than by inline `StdioServerParameters`. Migration is mechanical when
the registry is set up.

## Intended per-agent tool scopes (the policy the registry should enforce)

| Agent          | MongoDB scope                                    | BigQuery scope | Slack scope |
|----------------|--------------------------------------------------|----------------|-------------|
| Content Agent  | find, find-one, vector-search                    | —              | enabled     |
| Review Agent   | find, find-one, count                            | —              | —           |
| Research Agent | find, insert-many (customer_voice, neg_examples) | —              | —           |
| CMO Planner    | full (find, aggregate, update, insert)           | enabled        | enabled     |

## Hackathon-time enforcement

Soft: agent instructions describe scope expectations.
**Hard**: separate Atlas database users — `agent-readonly` and `agent-writer` —
created by `scripts/create_mongo_users.sh`. Content + Review use
`mongo_uri_readonly`; Research, CMO Planner, and workers use `mongo_uri_writer`.
Atlas rejects writes from the read-only user at the database layer, so a
prompt-injection or hallucinated tool call cannot mutate state.
