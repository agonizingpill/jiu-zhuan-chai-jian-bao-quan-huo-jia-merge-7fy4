from contextlib import contextmanager

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from core.config import DATABASE_URL

metadata = MetaData()
if DATABASE_URL.startswith('sqlite'):
    in_memory = ':memory:' in DATABASE_URL or DATABASE_URL in ('sqlite://', 'sqlite:///')
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={'check_same_thread': False, 'timeout': 30},
        **({'poolclass': StaticPool} if in_memory else {}),
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)


@contextmanager
def transaction():
    with engine.begin() as conn:
        yield conn


def ensure_runtime_schema():
    """Add columns introduced after the initial snapshot on old databases."""
    inspector = inspect(engine)
    if 'rack_candidates' not in inspector.get_table_names():
        return

    existing = {column['name'] for column in inspector.get_columns('rack_candidates')}
    additions = {
        'cluster_key': 'VARCHAR(160)',
        'distance': 'FLOAT',
        'batch_code': 'VARCHAR(64)',
        'status': "VARCHAR(32) NOT NULL DEFAULT 'active'",
        'active': 'BOOLEAN NOT NULL DEFAULT true',
        'revision': 'INTEGER NOT NULL DEFAULT 1',
    }
    with engine.begin() as conn:
        for name, declaration in additions.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE rack_candidates ADD COLUMN {name} {declaration}'))
