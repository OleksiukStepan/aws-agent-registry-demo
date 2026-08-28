import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGISTRY_NAME = "team-tool-catalog"
REGISTRY_DESCRIPTION = "Governed catalog of MCP servers, A2A agents and skills"
DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "agent-registry-demo"

# State lives next to the repo root, not inside infra/, and is gitignored.
STATE_FILE = Path(__file__).resolve().parent.parent / ".registry.json"

READY_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 5


def idempotency_token(scope):
    """Deterministic 36-char UUID: same scope always yields the same token."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, scope))


def control_client(profile, region):
    """Control-plane client: registry lifecycle, records, approvals."""
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client("agent-registry-control")


def find_registry_by_name(client, name):
    """ListRegistries cannot filter by name, so pages are scanned client-side."""
    next_token = None
    while True:
        kwargs = {"maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        page = client.list_registries(**kwargs)
        for registry in page["registries"]:
            if registry["name"] == name:
                return registry
        next_token = page.get("nextToken")
        # The API returns a nextToken even on an empty page, so stop on no results.
        if not next_token or not page["registries"]:
            return None


def wait_until_ready(client, registry_id):
    """CreateRegistry returns while the registry is still CREATING."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        registry = client.get_registry(registryId=registry_id)
        status = registry["status"]
        if status == "READY":
            return registry
        if status.endswith("FAILED"):
            raise RuntimeError(f"registry {registry_id} entered {status}: {registry.get('statusReason')}")
        print(f"  status={status}, waiting {POLL_INTERVAL_SECONDS}s...")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"registry {registry_id} did not reach READY within {READY_TIMEOUT_SECONDS}s")


def save_state(registry, region, profile):
    payload = {
        "name": registry["name"],
        "registryId": registry["registryId"],
        "registryArn": registry["registryArn"],
        "region": region,
        "profile": profile,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(f"no state file at {STATE_FILE}; run --create first")
    return json.loads(STATE_FILE.read_text())


def create(client, region, profile):
    """Idempotent: an existing registry with the same name is reused, not duplicated."""
    existing = find_registry_by_name(client, REGISTRY_NAME)
    if existing:
        print(f"registry '{REGISTRY_NAME}' already exists -> {existing['registryId']} ({existing['status']})")
        registry = existing if existing["status"] == "READY" else wait_until_ready(client, existing["registryId"])
    else:
        print(f"creating registry '{REGISTRY_NAME}'...")
        client.create_registry(
            name=REGISTRY_NAME,
            description=REGISTRY_DESCRIPTION,
            # AWS_IAM means callers are authorized by SigV4 and IAM policy.
            discoveryConfiguration={"authorizerType": "AWS_IAM"},
            # No auto-approval rules means every record needs a human decision.
            approvalConfiguration={"autoApprovalRules": []},
            # Guards against a retried network call creating a second registry.
            clientToken=idempotency_token(f"create-registry-{REGISTRY_NAME}"),
        )
        # CreateRegistry returns only the ARN, so the id is re-read via lookup.
        created = find_registry_by_name(client, REGISTRY_NAME)
        registry = wait_until_ready(client, created["registryId"])

    state = save_state(registry, region, profile)
    print(f"\nregistry is READY")
    print(f"  registryId  {state['registryId']}")
    print(f"  registryArn {state['registryArn']}")
    print(f"  state saved to {STATE_FILE.name}")
    return 0


def describe(client):
    state = load_state()
    registry = client.get_registry(registryId=state["registryId"])
    discovery = registry.get("discoveryConfiguration", {})
    approval = registry.get("approvalConfiguration", {})
    print(f"name         {registry['name']}")
    print(f"registryId   {registry['registryId']}")
    print(f"registryArn  {registry['registryArn']}")
    print(f"status       {registry['status']}")
    print(f"authorizer   {discovery.get('authorizerType')}")
    print(f"autoApproval {'ON' if approval.get('autoApprovalRules') else 'OFF (manual approval)'}")
    print(f"createdAt    {registry['createdAt']}")
    return 0


def teardown(client):
    """Deletes the registry only; records are removed by src/publish.py --teardown."""
    state = load_state()
    try:
        result = client.delete_registry(registryId=state["registryId"])
    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code in ("ConflictException", "ValidationException"):
            print(f"cannot delete registry: {err.response['Error']['Message']}")
            print("hint: remove records first with 'python src/publish.py --teardown'")
            return 1
        raise
    STATE_FILE.unlink(missing_ok=True)
    print(f"registry {state['registryId']} -> {result['status']}, state file removed")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Manage the governed agent registry")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true", help="create the registry, or reuse it if present")
    action.add_argument("--describe", action="store_true", help="print the current registry configuration")
    action.add_argument("--teardown", action="store_true", help="delete the registry")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args()

    client = control_client(args.profile, args.region)
    if args.create:
        return create(client, args.region, args.profile)
    if args.describe:
        return describe(client)
    return teardown(client)


if __name__ == "__main__":
    sys.exit(main())
