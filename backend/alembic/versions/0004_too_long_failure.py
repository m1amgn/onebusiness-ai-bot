"""Allow packet-measured overlong voice recordings.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels: str | None = None
depends_on: str | None = None

OLD_KINDS = (
    "'too_large','unsupported_audio','empty_transcript','transcription_timeout',"
    "'provider_unavailable','transient_exhausted','internal','stuck'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_voice_jobs_failure_kind"), "voice_jobs", type_="check")
    op.create_check_constraint(
        "failure_kind", "voice_jobs", f"failure_kind IN ('too_long',{OLD_KINDS})"
    )


def downgrade() -> None:
    op.execute("UPDATE voice_jobs SET failure_kind='too_large' WHERE failure_kind='too_long'")
    op.drop_constraint(op.f("ck_voice_jobs_failure_kind"), "voice_jobs", type_="check")
    op.create_check_constraint("failure_kind", "voice_jobs", f"failure_kind IN ({OLD_KINDS})")
