"""add owner_client_id scope to projects and reference_files

Revision ID: 013_add_owner_client_id_scope
Revises: 012
Create Date: 2026-02-12

"""
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '013_add_owner_client_id_scope'
down_revision = '012'
branch_labels = None
depends_on = None


def _random_owner() -> str:
    return f"legacy_{uuid.uuid4().hex}"


def upgrade():
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_client_id', sa.String(length=128), nullable=True))

    with op.batch_alter_table('reference_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_client_id', sa.String(length=128), nullable=True))

    conn = op.get_bind()

    # Backfill projects first.
    project_rows = conn.execute(
        sa.text("SELECT id FROM projects WHERE owner_client_id IS NULL")
    ).fetchall()
    for row in project_rows:
        conn.execute(
            sa.text("UPDATE projects SET owner_client_id = :owner WHERE id = :project_id"),
            {"owner": _random_owner(), "project_id": row[0]},
        )

    # Backfill reference files:
    # - Use owning project's owner_client_id when associated
    # - Otherwise generate isolated legacy owner id
    ref_rows = conn.execute(
        sa.text(
            """
            SELECT rf.id, p.owner_client_id
            FROM reference_files rf
            LEFT JOIN projects p ON rf.project_id = p.id
            WHERE rf.owner_client_id IS NULL
            """
        )
    ).fetchall()
    for row in ref_rows:
        file_id, project_owner_client_id = row
        owner = project_owner_client_id or _random_owner()
        conn.execute(
            sa.text("UPDATE reference_files SET owner_client_id = :owner WHERE id = :file_id"),
            {"owner": owner, "file_id": file_id},
        )

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.alter_column('owner_client_id', existing_type=sa.String(length=128), nullable=False)
        batch_op.create_index('ix_projects_owner_client_id', ['owner_client_id'], unique=False)

    with op.batch_alter_table('reference_files', schema=None) as batch_op:
        batch_op.alter_column('owner_client_id', existing_type=sa.String(length=128), nullable=False)
        batch_op.create_index('ix_reference_files_owner_client_id', ['owner_client_id'], unique=False)


def downgrade():
    with op.batch_alter_table('reference_files', schema=None) as batch_op:
        batch_op.drop_index('ix_reference_files_owner_client_id')
        batch_op.drop_column('owner_client_id')

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index('ix_projects_owner_client_id')
        batch_op.drop_column('owner_client_id')
