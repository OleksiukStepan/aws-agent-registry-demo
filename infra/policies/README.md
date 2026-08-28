# IAM policies for the three registry personas

JSON has no comment syntax, and IAM rejects any key outside its schema.
So the annotations live here instead of inline.

## Anatomy of an IAM policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "...", "Effect": "Allow", "Action": [...], "Resource": "..." }
  ]
}
```

| Key | Meaning |
|---|---|
| `Version` | Version of the **policy language**, not of this file. `2012-10-17` is current; `2008-10-17` is legacy and lacks policy variables. |
| `Statement` | List of independent rules. Evaluated together, not top-to-bottom. |
| `Sid` | Statement ID. IAM ignores it for identity policies — it exists purely to name a rule. The closest thing to a comment a policy can hold. |
| `Effect` | `Allow` or `Deny`. Those are the only two values. |
| `Action` | `<service-prefix>:<OperationName>`. Accepts a string or a list. Wildcards allowed. |
| `Resource` | ARN(s) the actions apply to. |
| `Condition` | Optional extra constraints on the request context. |

## Evaluation order

1. **Explicit `Deny`** anywhere -> denied. Nothing overrides it.
2. Otherwise an **explicit `Allow`** -> allowed.
3. Otherwise -> **implicit deny**. Permissions are never granted by omission.

## ARN format

```
arn:aws:agent-registry:us-east-1:183631317727:registry/W5DVvLCeEQ4OqGAi
 |   |         |            |          |          |
 |   |         |            |          |          +-- resource type / id
 |   |         |            |          +------------- account id (always 12 digits)
 |   |         |            +------------------------ region
 |   |         +------------------------------------- service
 |   +----------------------------------------------- partition (aws, aws-cn, aws-us-gov)
 +--------------------------------------------------- literal
```

Three resource levels exist in this service, and the persona boundary uses all three:

| Level | ARN pattern | Used for |
|---|---|---|
| Account | `arn:aws:agent-registry:*:<account>:*` | `CreateRegistry`, `ListRegistries` — no registry exists yet to name |
| Registry | `...:registry/*` | `GetRegistry`, `ListRegistryRecords`, discovery APIs |
| Record | `...:registry/*/record/*` | Everything that acts on one record |

## Why these three files

Both API planes sign as `agent-registry`, so the persona boundary is drawn by the
action list and the resource level, not by the endpoint.

**`admin.json`** - the platform owner and curator.
Owns the registry object, approves records via `UpdateRegistryRecordStatus`, and
holds the two out-of-namespace grants the service needs to provision itself
(see below). Cannot create or submit records: publishing is not an admin job.

**`publisher.json`** - the team shipping an MCP server or agent.
Creates, edits and submits records. `SubmitRegistryRecordForApproval` moves a
record to `PENDING_APPROVAL`; it cannot move it further. Deliberately absent:
`UpdateRegistryRecordStatus` and `DeleteRegistryRecord`. A publisher cannot
approve its own work, and cannot delete a record to hide a rejection. That is the
governance model, expressed as two missing lines.

**`consumer.json`** - the team looking for something to reuse.
The data-plane discovery operations plus enough control-plane read access to
locate a registry by id. No record-level control-plane action at all, so drafts
and rejected records are unreachable even by direct id.

## The two grants outside the agent-registry namespace

`CreateRegistry` fails for a caller that holds only `agent-registry:*` actions.
The registry provisions two things on the caller's behalf, and both live elsewhere.

**Service-linked role.**

```
AccessDeniedException: Unable to create the service-linked role required for this
registry. Ensure the caller has iam:CreateServiceLinkedRole permission for
agent-registry.amazonaws.com.
```

A service-linked role is created by the service itself, its permissions are
defined by AWS and cannot be edited, and it is created once per account. The
grant is narrowed twice rather than given as a blanket `iam:CreateServiceLinkedRole`:
`Resource` pins the exact role name, and `Condition` pins the service principal.

**Workload identity.**

```
CREATE_FAILED: Unable to create workload identity because access was denied.
```

The registry provisions a managed workload identity through AgentCore Identity,
which stayed under the `bedrock-agentcore` namespace after the service was renamed.
Hence `bedrock-agentcore:*WorkloadIdentity`, scoped to the default
workload-identity-directory.

This failure mode is invisible to an account root caller, whose requests are never
evaluated against a policy. Running the demo as a scoped IAM user is what surfaced
both grants.

## Known deviations from the AWS-documented examples

- The docs use `StringLike` on the service-linked role condition. The value holds no
  wildcard, so `StringEquals` is used here: same effect, stricter operator.
- The API operation is `BatchGetDiscoverableRegistryRecord`, but the documented IAM
  action is `GetDiscoverableRegistryRecord` and the console policy linter rejects the
  batch form as unknown. Only the documented action is granted; if a live batch-get
  call is denied, that is the evidence needed to revisit this.
- The workload identity statement grants `bedrock-agentcore:*WorkloadIdentity*` on both
  `workload-identity-directory/default` and `.../default/*`. The AWS example covers only
  the child path, which assumes the directory already exists. On an account that has
  never used AgentCore Identity, registry creation fails at `CREATE_FAILED` because the
  directory itself must be created first.
- The publisher policy omits the three sync-related statements from the AWS example.
  Those authorize `Synchronize from endpoint`, which this demo does not use.
