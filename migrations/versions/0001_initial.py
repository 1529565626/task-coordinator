"""Initial Task Coordinator schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("machine_name", sa.String(200), nullable=False),
        sa.Column("client_type", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("owner_agent_id", sa.String(80), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("legacy_owner", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token_hash", sa.String(64), nullable=True),
        sa.Column("claim_reissue_pending", sa.Boolean(), nullable=False),
        sa.Column("branch_name", sa.String(200), nullable=True),
        sa.Column("delivery_commit", sa.String(64), nullable=True),
        sa.Column("delivery_tests", sa.Text(), nullable=True),
        sa.Column("delivery_notes", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("accepted_by", sa.String(80), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_owner_agent_id", "tasks", ["owner_agent_id"])
    op.create_table(
        "task_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("scope", sa.String(300), nullable=False),
        sa.UniqueConstraint("task_id", "scope", name="uq_task_scope"),
    )
    op.create_index("ix_task_scopes_task_id", "task_scopes", ["task_id"])
    op.create_index("ix_task_scopes_scope", "task_scopes", ["scope"])
    op.create_table(
        "task_events",
        sa.Column("event_id", sa.String(40), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor_agent_id", sa.String(80), nullable=True),
        sa.Column("actor_user", sa.String(80), nullable=True),
        sa.Column("old_status", sa.String(40), nullable=True),
        sa.Column("new_status", sa.String(40), nullable=True),
        sa.Column("old_version", sa.Integer(), nullable=True),
        sa.Column("new_version", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(80), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("server_created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_index("ix_task_events_event_type", "task_events", ["event_type"])
    op.create_index("ix_task_events_server_created_at", "task_events", ["server_created_at"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_kind", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_kind", "actor_id", "idempotency_key", name="uq_idempotency"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("committed_result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "service_meta",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "service_meta",
        "import_batches",
        "idempotency_records",
        "task_events",
        "task_scopes",
        "tasks",
        "sessions",
        "users",
        "agents",
        "projects",
    ):
        op.drop_table(name)
