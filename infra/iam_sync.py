import argparse
import json
import sys
from pathlib import Path

import boto3

POLICY_DIR = Path(__file__).resolve().parent / "policies"
POLICY_NAME_PREFIX = "AgentRegistryDemo"

# Writing IAM policies needs an admin identity, not the scoped demo user.
DEFAULT_PROFILE = "default"

# IAM keeps at most five versions per managed policy.
MAX_POLICY_VERSIONS = 5


def policy_files():
    """admin.json -> AgentRegistryDemoAdmin"""
    for path in sorted(POLICY_DIR.glob("*.json")):
        yield path, POLICY_NAME_PREFIX + path.stem.capitalize()


def find_policy_arn(iam, name):
    paginator = iam.get_paginator("list_policies")
    for page in paginator.paginate(Scope="Local"):
        for policy in page["Policies"]:
            if policy["PolicyName"] == name:
                return policy["Arn"]
    return None


def prune_versions(iam, arn):
    """Delete the oldest non-default version when the five-version limit is reached."""
    versions = iam.list_policy_versions(PolicyArn=arn)["Versions"]
    if len(versions) < MAX_POLICY_VERSIONS:
        return
    oldest = min((v for v in versions if not v["IsDefaultVersion"]), key=lambda v: v["CreateDate"])
    iam.delete_policy_version(PolicyArn=arn, VersionId=oldest["VersionId"])
    print(f"    pruned version {oldest['VersionId']}")


def sync(iam, dry_run):
    exit_code = 0
    for path, name in policy_files():
        document = json.dumps(json.loads(path.read_text()), separators=(",", ":"))
        arn = find_policy_arn(iam, name)
        if not arn:
            print(f"{name}: not found in IAM, skipped ({path.name})")
            exit_code = 1
            continue
        if dry_run:
            print(f"{name}: would update from {path.name}")
            continue
        prune_versions(iam, arn)
        version = iam.create_policy_version(PolicyArn=arn, PolicyDocument=document, SetAsDefault=True)
        print(f"{name}: updated from {path.name} -> version {version['PolicyVersion']['VersionId']}")
    return exit_code


def main():
    parser = argparse.ArgumentParser(description="Sync infra/policies/*.json into IAM managed policies")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="AWS profile with IAM write access")
    parser.add_argument("--dry-run", action="store_true", help="print what would change without writing")
    args = parser.parse_args()

    iam = boto3.Session(profile_name=args.profile).client("iam")
    return sync(iam, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
