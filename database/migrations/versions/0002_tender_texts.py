"""keep long document text in its own table

The bid-document text (up to 20,000 characters) and its consignee sections used to sit inside tenders.details, so
every query that listed tenders pulled them over the network. On 25 Nov 2026 that used up the database's 5 GB
monthly transfer allowance. They now live in tender_texts and are read only when a document is actually scored.
The move is done with SQL inside the database, so the migration itself transfers nothing.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tender_texts',
    sa.Column('tender_id', sa.Integer(), nullable=False),
    sa.Column('doc_text', sa.Text(), nullable=True),
    sa.Column('doc_consignee', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('tender_id')
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # plain json (not jsonb) operators only: stored PDF text may contain \u0000, which jsonb and text reject
        def val(k):
            return f"(('[' || replace((details -> '{k}')::text, '\\u0000', '') || ']')::json ->> 0)"
        has = "(details -> 'doc_text') IS NOT NULL OR (details -> 'doc_consignee') IS NOT NULL"
        moves = [f"""INSERT INTO tender_texts (tender_id, doc_text, doc_consignee, updated_at)
                     SELECT id, {val('doc_text')}, {val('doc_consignee')}, now() FROM tenders WHERE {has}""",
                 f"""UPDATE tenders SET details = COALESCE((SELECT json_object_agg(e.key, e.value) FROM json_each(details) e
                                                         WHERE e.key NOT IN ('doc_text', 'doc_consignee')), '{{}}'::json)
                     WHERE {has}"""]
    else:   # SQLite (local runs)
        has = "json_extract(details, '$.doc_text') IS NOT NULL OR json_extract(details, '$.doc_consignee') IS NOT NULL"
        moves = [f"""INSERT INTO tender_texts (tender_id, doc_text, doc_consignee, updated_at)
                     SELECT id, json_extract(details, '$.doc_text'), json_extract(details, '$.doc_consignee'),
                            CURRENT_TIMESTAMP FROM tenders WHERE {has}""",
                 f"UPDATE tenders SET details = json_remove(details, '$.doc_text', '$.doc_consignee') WHERE {has}"]
    # the move is an optimisation: the code still reads text left inside details, so a failure here must never
    # stop the app from starting
    try:
        with bind.begin_nested():
            for sql in moves:
                bind.execute(sa.text(sql))
    except Exception as e:                                  # pragma: no cover
        print(f"0002: document text left inside details ({type(e).__name__}: {e})")


def downgrade() -> None:
    op.drop_table('tender_texts')
