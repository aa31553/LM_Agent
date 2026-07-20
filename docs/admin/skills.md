# Skills Administration

LM Agent stores Agent Skills as filesystem packages compatible with the common Claude-style
`SKILL.md` structure. Each package requires YAML frontmatter with `name` and `description`,
followed by Markdown instructions. Optional `scripts/`, `references/`, `assets/`, and
`agents/openai.yaml` files are preserved as bundled resources.

## Defaults and Storage

At first access, templates from `app/default_skills/` are copied into `SKILLS_ROOT`
(`data/skills` by default):

- `skill-creator`
- `document-analysis`
- `secure-rag` (always on)

Runtime edits affect only `SKILLS_ROOT`; packaged defaults remain the recovery baseline.
System Skills cannot be deleted. A private `.lm-agent-skill.json` sidecar stores enabled,
system, always-on, permission, and timestamp metadata and is never exposed through the file API.

## API

| Method | Path | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/v1/skills` | List authorized Skills and SKILL.md links | Authenticated + matching rule |
| POST | `/api/v1/skills` | Create a Skill | Admin |
| GET | `/api/v1/skills/{name}` | Read metadata, instructions, and file links | Authenticated + matching rule |
| PATCH | `/api/v1/skills/{name}` | Update description, instructions, or enabled state | Admin |
| DELETE | `/api/v1/skills/{name}` | Delete a custom Skill package | Admin |
| POST | `/api/v1/skills/{name}/files` | Upload or replace a bundled file | Admin |
| GET | `/api/v1/skills/{name}/files/{path}` | Stream the original file inline | Authenticated + matching rule |
| DELETE | `/api/v1/skills/{name}/files/{path}` | Delete a bundled file | Admin |
| GET | `/api/v1/permissions/skills/{name}` | List Skill access rules | Admin |
| POST | `/api/v1/permissions/skills/{name}` | Create or update a Skill access rule | Admin |
| DELETE | `/api/v1/permissions/skills/{name}/{permission_id}` | Delete a Skill access rule | Admin |

Raw file URLs are relative API links and require the normal Bearer token. The frontend uses
an authenticated fetch before showing text, images, or a local binary-file link.

### Create Example

```json
{
  "name": "materials-review",
  "description": "Review materials research. Use for evidence-based synthesis.",
  "instructions": "# Workflow\n\nCompare claims and cite supplied sources.",
  "enabled": true
}
```

### Upload a Resource

Send `multipart/form-data` to `/api/v1/skills/materials-review/files` with:

- `file`: resource bytes
- `relative_path`: for example `references/policy.md`
- `overwrite`: `true` or `false`

Paths are resolved inside the selected Skill directory. Absolute paths, `..`, required
metadata files, and uploads larger than `SKILL_MAX_FILE_BYTES` are rejected.

## Permissions

Skill permissions use the same `user`, `department`, and `role` subjects as document and
knowledge-base permissions. A Skill with no rules is open to every authenticated active user.
As soon as one or more rules exist, access becomes allow-list based: only a matching `read` or
`admin` rule can list, inspect, preview, or activate the Skill. Application administrators
always retain access. The `write` permission level is intentionally rejected for Skills.

```json
{
  "subject_type": "role",
  "subject_value": "researcher",
  "permission": "read"
}
```

Filtering is enforced by the backend. Direct requests to a protected Skill or raw file return
`403`, and unauthorized Skills are removed before progressive prompt resolution, including
explicit `$skill-name` requests.

## Runtime Loading

Every enabled and authorized Skill contributes only its name and description to the model's
configured Skill catalog. Full SKILL.md instructions are loaded when the query explicitly contains
`$skill-name`, matches the Skill metadata, or the Skill is marked always-on. Audit events store
the names of Skills activated for each successful query.
