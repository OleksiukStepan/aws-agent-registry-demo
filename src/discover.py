"""Consumer-facing discovery CLI: browse, semantic search, fetch details."""

import argparse
import json
import sys
import textwrap
from pathlib import Path

import boto3

STATE_FILE = Path(__file__).resolve().parent.parent / ".registry.json"
DEFAULT_PROFILE = "agent-registry-demo"
DEFAULT_REGION = "us-east-1"


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(f"no state file at {STATE_FILE}; run 'python infra/registry.py --create' first")
    return json.loads(STATE_FILE.read_text())


def print_rows(records):
    if not records:
        print("no records")
        return
    print(f"{'NAME':<30} {'TYPE':<7} {'VERSION':<9} {'STATUS':<10} DESCRIPTION")
    for rec in records:
        summary = textwrap.shorten(rec.get("description", ""), width=58, placeholder="...")
        print(f"{rec['name']:<30} {rec['recordType']:<7} {rec['recordVersion']:<9} {rec['status']:<10} {summary}")


def cmd_list(client, state, args):
    kwargs = {"registryId": state["registryId"], "maxResults": args.limit}
    if args.type:
        kwargs["filters"] = [{"name": "recordType", "values": [args.type]}]
    records = client.list_discoverable_registry_records(**kwargs)["registryRecords"]
    print_rows(records)
    return 0


def cmd_search(client, state, args):
    result = client.search_discoverable_registry_records(
        registryIds=[state["registryArn"]],
        searchQuery=args.query,
        maxResults=args.limit,
    )
    print(f'query: "{args.query}"\n')
    print_rows(result["registryRecords"])
    return 0


def cmd_get(client, state, args):
    result = client.batch_get_discoverable_registry_record(
        entries=[{"registryId": state["registryId"], "recordIds": args.record_ids}]
    )
    for rec in result["registryRecords"]:
        print("=" * 70)
        print(f"{rec['name']} ({rec['recordType']} {rec['recordVersion']}) - {rec['status']}")
        print(f"  displayName {rec.get('displayName')}")
        print(f"  recordArn   {rec['recordArn']}")
        print(f"  description {rec.get('description')}")
        for kind, descriptor in rec["descriptors"].items():
            data = descriptor.get("data", "")
            print(f"  descriptor  {kind} (schema {descriptor.get('dataSchemaVersion', 'n/a')}, {len(data)} chars)")
            if args.full:
                print(textwrap.indent(data, "    "))
    for err in result["errors"]:
        print(f"error: {err['recordId']} -> {err['errorCode']} {err.get('message', '')}")
    return 1 if result["errors"] and not result["registryRecords"] else 0


def main():
    parser = argparse.ArgumentParser(description="Discover approved records in the registry")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="browse everything discoverable")
    listing.add_argument("--type", choices=["MCP", "AGENT", "SKILL", "CUSTOM"])
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(handler=cmd_list)

    search = sub.add_parser("search", help="hybrid semantic and keyword search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(handler=cmd_search)

    get = sub.add_parser("get", help="full details for one or more record ids")
    get.add_argument("record_ids", nargs="+")
    get.add_argument("--full", action="store_true", help="print the raw descriptor payloads")
    get.set_defaults(handler=cmd_get)

    args = parser.parse_args()
    state = load_state()
    # Data plane: the only client a consumer ever needs.
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client("agent-registry")
    return args.handler(client, state, args)


if __name__ == "__main__":
    sys.exit(main())
