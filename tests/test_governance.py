"""Governance invariant: only approved records are discoverable."""

import json
from pathlib import Path

import boto3
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / ".registry.json"
RECORDS_DIR = REPO_ROOT / "records"


@pytest.fixture(scope="session")
def state():
    if not STATE_FILE.exists():
        pytest.skip("registry not provisioned; run infra/registry.py --create")
    return json.loads(STATE_FILE.read_text())


@pytest.fixture(scope="session")
def session(state):
    return boto3.Session(profile_name=state["profile"], region_name=state["region"])


@pytest.fixture(scope="session")
def control(session):
    return session.client("agent-registry-control")


@pytest.fixture(scope="session")
def data_plane(session):
    return session.client("agent-registry")


@pytest.fixture(scope="session")
def specs():
    return [json.loads(path.read_text()) for path in sorted(RECORDS_DIR.glob("*.json"))]


@pytest.fixture(scope="session")
def control_records(control, state):
    records = control.list_registry_records(registryId=state["registryId"], maxResults=50)["registryRecords"]
    return {rec["name"]: rec for rec in records}


@pytest.fixture(scope="session")
def discoverable(data_plane, state):
    records = data_plane.list_discoverable_registry_records(registryId=state["registryId"], maxResults=50)
    return {rec["name"]: rec for rec in records["registryRecords"]}


def test_registry_requires_manual_approval(control, state):
    registry = control.get_registry(registryId=state["registryId"])
    assert registry["status"] == "READY"
    assert registry["discoveryConfiguration"]["authorizerType"] == "AWS_IAM"
    assert not registry.get("approvalConfiguration", {}).get("autoApprovalRules")


def test_approved_records_are_discoverable(specs, control_records, discoverable):
    expected = {spec["name"] for spec in specs if spec["approve"]}
    assert expected, "fixture problem: no record is marked for approval"
    for name in expected:
        assert control_records[name]["status"] == "APPROVED"
        assert name in discoverable


def test_unapproved_record_is_not_discoverable(specs, control_records, discoverable):
    withheld = {spec["name"] for spec in specs if not spec["approve"]}
    assert withheld, "fixture problem: every record is approved, nothing proves the boundary"
    for name in withheld:
        assert control_records[name]["status"] == "DRAFT"
        assert name not in discoverable


def test_unapproved_record_is_not_reachable_by_id(specs, control_records, data_plane, state):
    withheld = [spec["name"] for spec in specs if not spec["approve"]]
    record_ids = [control_records[name]["recordId"] for name in withheld]
    result = data_plane.batch_get_discoverable_registry_record(
        entries=[{"registryId": state["registryId"], "recordIds": record_ids}]
    )
    assert result["registryRecords"] == []
    assert {err["errorCode"] for err in result["errors"]} == {"RESOURCE_NOT_FOUND"}


def test_semantic_search_finds_shipping_without_keyword_overlap(data_plane, state):
    result = data_plane.search_discoverable_registry_records(
        registryIds=[state["registryArn"]],
        searchQuery="tool that can ship a parcel",
        maxResults=10,
    )
    names = [rec["name"] for rec in result["registryRecords"]]
    assert "shipping-mcp-server" in names
    assert names.index("shipping-mcp-server") < names.index("weather-mcp-server")


def test_multiple_record_types_are_published(specs, discoverable):
    types = {rec["recordType"] for rec in discoverable.values()}
    assert len(types) >= 2, f"catalog is not unified, only {types} present"
