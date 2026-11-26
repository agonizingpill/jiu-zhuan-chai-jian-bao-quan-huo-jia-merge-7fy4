from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, JSON, String, Table, text

from core.db import metadata


rack_candidates = Table('rack_candidates', metadata,
    Column('code', String(64), primary_key=True),
    Column('member', String(160), ForeignKey('brick_stacks.code'), nullable=False),
    Column('pair', String(160), ForeignKey('brick_stacks.code'), nullable=False),
    Column('cluster_size', Integer, nullable=False),
    Column('token', String(64), nullable=False, unique=True),
    # Global pairing workbench fields.  Existing rows default to active.
    Column('cluster_key', String(160), nullable=True),
    Column('distance', Float, nullable=True),
    Column('batch_code', String(64), nullable=True),
    Column('status', String(32), nullable=False, server_default='active'),
    Column('active', Boolean, nullable=False, server_default=text('true')),
    Column('revision', Integer, nullable=False, server_default=text('1')),
)


# First-run BIRCH assignments are retained so a rearrangement does not fit a
# new set of cluster labels.
rack_stack_clusters = Table('rack_stack_clusters', metadata,
    Column('code', String(64), primary_key=True),
    Column('load_grade', String(160), nullable=False),
    Column('cluster_key', String(160), nullable=False, index=True),
    Column('batch_code', String(64), nullable=True),
    Column('coordinates', JSON, nullable=True),
)
