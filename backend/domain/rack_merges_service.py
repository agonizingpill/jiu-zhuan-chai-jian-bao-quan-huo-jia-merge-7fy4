from collections.abc import Mapping
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.records import get, need, update
from domain.brick_stacks_model import brick_stacks
from domain.demolitions_model import demolitions
from domain.pairing import MAX_RACK_PIECES, MOISTURE_LIMIT
from domain.rack_candidates_model import rack_candidates
from domain.rack_candidates_service import (
    _barrier,
    _is_active,
    _signed_state,
    _stored_clusters,
    _sync_signed_occupancy,
)
from domain.rack_merges_model import rack_merges


def _same_request(existing, data):
    payload = _payload(data)
    return (
        existing['candidate'] == payload.get('candidate')
        and existing['rack'] == payload.get('rack')
        and existing['operator_note'] == payload.get('operator_note')
    )


def _payload(data):
    if hasattr(data, 'model_dump'):
        return data.model_dump()
    if isinstance(data, Mapping):
        return dict(data)
    return vars(data)


def _require_same_cluster(conn, candidate, left, right):
    stored = _stored_clusters(conn)
    left_row = stored.get(left['code'])
    right_row = stored.get(right['code'])
    left_cluster = left_row['cluster_key'] if left_row else None
    right_cluster = right_row['cluster_key'] if right_row else None

    # When the workbench's first-run labels are present, enforce them.  Legacy
    # candidates created through the BIRCH compute flow already carry a
    # same-cluster pair; do not refit and destabilise unrelated sign tests.
    if left_cluster and right_cluster:
        need(left_cluster == right_cluster, '两垛不属于同一孔形簇')
        if candidate.get('cluster_key'):
            need(candidate['cluster_key'] == left_cluster, '候选配对的孔形簇已失效，请重新排配')
    elif candidate.get('cluster_key') and (left_cluster or right_cluster):
        known = left_cluster or right_cluster
        need(candidate['cluster_key'] == known, '候选配对的孔形簇已失效，请重新排配')


def pack_rack(conn, user, data):
    # Accept Pydantic models, simple objects, and plain mapping fixtures.
    if isinstance(data, Mapping):
        data = SimpleNamespace(**data)

    # Signing and rearrangement enter one serialisation point.  Whichever
    # transaction gets the lock first establishes the durable outcome.
    _barrier(conn)

    existing = conn.execute(
        select(rack_merges)
        .where(rack_merges.c.code == data.code)
        .with_for_update()
    ).mappings().first()
    if existing is not None:
        existing = dict(existing)
        need(_same_request(existing, data), '货架卡编号已用于不同的签架内容')
        return existing

    candidate = get(conn, rack_candidates, data.candidate, lock=True)
    need(_is_active(candidate), '该配对已被重排替换，请使用最新配对签架')

    prior = conn.execute(
        select(rack_merges)
        .where(rack_merges.c.candidate == data.candidate)
        .with_for_update()
    ).mappings().first()
    need(prior is None, '该配对已经签发货架卡，不能重复占垛')

    keys = sorted([candidate['member'], candidate['pair']])
    left, right = [get(conn, brick_stacks, key, lock=True) for key in keys]

    state = _signed_state(conn)
    _sync_signed_occupancy(conn, state)
    # Re-read after reconciling the seed/legacy denormalised flag.
    left, right = [get(conn, brick_stacks, key, lock=True) for key in keys]

    source = [get(conn, demolitions, x['origin']) for x in (left, right)]
    need(
        source[0]['load_grade'] == source[1]['load_grade']
        and all(x['allowed'] for x in source),
        '两垛用途不相同或来源未放行',
    )
    need(not left['merged'] and not right['merged'], '一个砖垛已有货架')
    occupied = state['occupied'] - set(keys)
    need(keys[0] not in occupied and keys[1] not in occupied, '一个砖垛已在其他货架卡中')
    need(
        all(x['weighed'] and float(x['wet']) <= MOISTURE_LIMIT for x in (left, right)),
        '新近返潮的砖垛禁止合架',
    )
    pieces = int(left['pieces']) + int(right['pieces'])
    need(pieces <= MAX_RACK_PIECES, f'每架最多{MAX_RACK_PIECES}块')
    _require_same_cluster(conn, candidate, left, right)

    table_columns = {column.name for column in rack_merges.columns}
    values = {
        key: value
        for key, value in _payload(data).items()
        if key in table_columns
    }
    values['pieces'] = pieces
    try:
        with conn.begin_nested():
            conn.execute(rack_merges.insert().values(**values))
    except IntegrityError:
        winner = conn.execute(
            select(rack_merges).where(rack_merges.c.code == data.code)
        ).mappings().first()
        if winner is not None and _same_request(dict(winner), data):
            return dict(winner)
        duplicate_candidate = conn.execute(
            select(rack_merges).where(rack_merges.c.candidate == data.candidate)
        ).mappings().first()
        if duplicate_candidate is not None:
            need(False, '该配对已经签发货架卡，不能重复占垛')
        need(False, '编号、货架或配对引用冲突，请刷新后重试')

    for key in keys:
        get(conn, brick_stacks, key, lock=True)
        update(conn, brick_stacks, key, merged=True)

    conn.execute(
        rack_candidates.update()
        .where(rack_candidates.c.code == data.candidate)
        .values(status='signed', active=True)
    )
    return get(conn, rack_merges, data.code)
