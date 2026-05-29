"""Read-only inspection queries for the operator CLI.

Pulled out of mongo/cli.py for clearer separation; the queries here never
mutate state.
"""
from __future__ import annotations

import json

from mongo import queries
from shared import mongo_tools


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_list_collections(args):
    print(mongo_tools.db().list_collection_names())


def cmd_running_experiments(args):
    _print(queries.running_experiments(channel=args.channel,
                                         icp_segment=args.icp))


def cmd_recent_decisions(args):
    _print(queries.recent_decisions(limit=args.limit))


def cmd_self_critique_proposals(args):
    _print(queries.skills_with_self_critique_proposal())


def cmd_promotion_requests(args):
    _print(queries.skills_with_promotion_request())


def cmd_negatives(args):
    _print(queries.recent_negatives(args.channel, args.category, limit=args.limit))


def cmd_drift_status(args):
    _print(queries.drift_investigations())
