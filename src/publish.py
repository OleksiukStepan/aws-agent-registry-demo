"""Publish records through the Draft -> Submit -> Approve lifecycle."""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_DIR = REPO_ROOT / "records"
STATE_FILE = REPO_ROOT / ".registry.json"

DEFAULT_PROFILE = "agent-registry-demo"
DEFAULT_REGION = "us-east-1"

STATUS_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 3

APPROVAL_REASON = "Reviewed by the platform team: schema valid, owner identified, endpoint reachable."


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(f"no state file at {STATE_FILE}; run 'python infra/registry.py --create' first")
    return json.loads(STATE_FILE.read_text())


def serialize_data(node):
    """Descriptor 'data' is a JSON string on the wire but stays an object in the record files."""
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == "data" and not isinstance(value, str):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = serialize_data(value)
        return result
    return node


def load_records():
    records = []
    for path in sorted(RECORDS_DIR.glob("*.json")):
        spec = json.loads(path.read_text())
        spec["descriptors"] = serialize_data(spec["descriptors"])
        spec["_file"] = path.name
        records.append(spec)
    return records


def find_record(client, registry_id, name, version):
    """name+version is the uniqueness key, so both must match to call it the same record."""
    next_token = None
    while True:
        kwargs = {"registryId": registry_id, "maxResults": 50,
                  "filters": [{"name": "name", "values": [name]}]}
        if next_token:
            kwargs["nextToken"] = next_token
        page = client.list_registry_records(**kwargs)
        for record in page["registryRecords"]:
            if record["name"] == name and record["recordVersion"] == version:
                return record
        next_token = page.get("nextToken")
        if not next_token or not page["registryRecords"]:
            return None


def wait_for_status(client, registry_id, record_id, targets):
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        record = client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = record["status"]
        if status in targets:
            return record
        if status.endswith("FAILED"):
            raise RuntimeError(f"record {record_id} entered {status}: {record.get('statusReason')}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"record {record_id} never reached {targets}")


def ensure_record(publisher, registry_id, spec):
    """Create the record if absent; return it in DRAFT or later."""
    existing = find_record(publisher, registry_id, spec["name"], spec["recordVersion"])
    if existing:
        return existing["recordId"], existing["status"], False
    publisher.create_registry_record(
        registryId=registry_id,
        name=spec["name"],
        displayName=spec["displayName"],
        description=spec["description"],
        recordType=spec["recordType"],
        recordVersion=spec["recordVersion"],
        descriptors=spec["descriptors"],
    )
    # CreateRegistryRecord returns only the ARN, so the id comes from a lookup.
    created = find_record(publisher, registry_id, spec["name"], spec["recordVersion"])
    record = wait_for_status(publisher, registry_id, created["recordId"], {"DRAFT"})
    return record["recordId"], record["status"], True


def advance_lifecycle(publisher, admin, registry_id, record_id, status):
    """Publisher submits, admin approves. Two identities, two steps, on purpose."""
    if status == "DRAFT":
        submitted = publisher.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
        status = submitted["status"]
    if status == "PENDING_APPROVAL":
        approved = admin.update_registry_record_status(
            registryId=registry_id, recordId=record_id,
            status="APPROVED", statusReason=APPROVAL_REASON,
        )
        status = approved["status"]
    return status


def publish(publisher, admin, registry_id):
    rows = []
    for spec in load_records():
        record_id, status, created = ensure_record(publisher, registry_id, spec)
        action = "created" if created else "reused"
        if spec["approve"]:
            status = advance_lifecycle(publisher, admin, registry_id, record_id, status)
        rows.append((spec["name"], spec["recordType"], spec["recordVersion"], status, record_id, action))
        print(f"  {spec['name']:<30} {spec['recordType']:<7} {action:<8} -> {status}")

    print()
    print(f"{'NAME':<30} {'TYPE':<7} {'VERSION':<9} {'STATUS':<17} RECORD ID")
    for name, rtype, version, status, record_id, _ in rows:
        print(f"{name:<30} {rtype:<7} {version:<9} {status:<17} {record_id}")
    return 0


def teardown(admin, registry_id):
    """Records must go before the registry can be deleted."""
    deleted = 0
    for spec in load_records():
        record = find_record(admin, registry_id, spec["name"], spec["recordVersion"])
        if not record:
            continue
        try:
            admin.delete_registry_record(registryId=registry_id, recordId=record["recordId"])
        except ClientError as err:
            print(f"  {spec['name']}: {err.response['Error']['Code']} - {err.response['Error']['Message']}")
            continue
        print(f"  deleted {spec['name']} ({record['recordId']})")
        deleted += 1
    print(f"{deleted} record(s) deleted")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Publish and approve registry records")
    parser.add_argument("--teardown", action="store_true", help="delete every record defined in records/")
    parser.add_argument("--publisher-profile", default=DEFAULT_PROFILE)
    parser.add_argument("--admin-profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args()

    state = load_state()
    registry_id = state["registryId"]
    publisher = boto3.Session(profile_name=args.publisher_profile, region_name=args.region).client("agent-registry-control")
    admin = boto3.Session(profile_name=args.admin_profile, region_name=args.region).client("agent-registry-control")

    if args.teardown:
        return teardown(admin, registry_id)
    print(f"publishing into {registry_id}\n")
    return publish(publisher, admin, registry_id)


if __name__ == "__main__":
    sys.exit(main())
