"""Operator CLI for MongoDB ops. Single entry point.

  python -m mongo.cli load-all                          # idempotent seed
  python -m mongo.cli list-collections
  python -m mongo.cli running-experiments
  python -m mongo.cli recent-decisions [--limit 10]
  python -m mongo.cli self-critique-proposals
  python -m mongo.cli promotion-requests
  python -m mongo.cli approve-promotion <skill_id>
  python -m mongo.cli reject-promotion <skill_id>
  python -m mongo.cli negatives <channel> <category> [--limit 5]
  python -m mongo.cli ingest-quotes <file.jsonl>        # bulk customer_voice
  python -m mongo.cli drift-status

Concerns are split across cli_seed / cli_query / cli_ops; this module
just wires their cmd_* functions into argparse.
"""
from __future__ import annotations

import argparse

# This CLI is operator-only; mongo_tools defaults to mongo_uri_writer
# (see shared/mongo_tools.py:_DEFAULT_SECRET). Override per-call with
# secret_name="mongo_uri_readonly" if you ever add a read-only inspect
# subcommand — no module-level use_secret() needed.
from mongo.cli_ops import cmd_approve_promotion, cmd_reject_promotion
from mongo.cli_query import (
    cmd_drift_status,
    cmd_list_collections,
    cmd_negatives,
    cmd_promotion_requests,
    cmd_recent_decisions,
    cmd_running_experiments,
    cmd_self_critique_proposals,
)
from mongo.cli_seed import cmd_ingest_quotes, cmd_load_all


def main():
    p = argparse.ArgumentParser(prog="mongo.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("load-all").set_defaults(fn=cmd_load_all)
    sub.add_parser("list-collections").set_defaults(fn=cmd_list_collections)

    r = sub.add_parser("running-experiments")
    r.add_argument("--channel")
    r.add_argument("--icp")
    r.set_defaults(fn=cmd_running_experiments)

    rd = sub.add_parser("recent-decisions")
    rd.add_argument("--limit", type=int, default=10)
    rd.set_defaults(fn=cmd_recent_decisions)

    sub.add_parser("self-critique-proposals").set_defaults(fn=cmd_self_critique_proposals)
    sub.add_parser("promotion-requests").set_defaults(fn=cmd_promotion_requests)

    n = sub.add_parser("negatives")
    n.add_argument("channel")
    n.add_argument("category")
    n.add_argument("--limit", type=int, default=5)
    n.set_defaults(fn=cmd_negatives)

    sub.add_parser("drift-status").set_defaults(fn=cmd_drift_status)

    ap = sub.add_parser("approve-promotion")
    ap.add_argument("skill_id")
    ap.set_defaults(fn=cmd_approve_promotion)

    rp = sub.add_parser("reject-promotion")
    rp.add_argument("skill_id")
    rp.set_defaults(fn=cmd_reject_promotion)

    iq = sub.add_parser("ingest-quotes")
    iq.add_argument("file")
    iq.set_defaults(fn=cmd_ingest_quotes)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
