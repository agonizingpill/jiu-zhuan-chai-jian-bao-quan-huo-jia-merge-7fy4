from uuid import uuid4

import numpy as np
from sqlalchemy import select, text

from core.queueing import submit, result
from core.records import get, insert, need, scan, update
from domain import learning
from domain.demolitions_model import demolitions
from domain.brick_stacks_model import brick_stacks
from domain.pairing import (
    MAX_RACK_PIECES,
    MAX_BRICKS_PER_RACK,
    MAX_PIECES_PER_RACK,
    MOISTURE_LIMIT,
    optimize_matching,
    pair_key,
)
from domain.rack_candidates_model import rack_candidates, rack_stack_clusters
from domain.rack_merges_model import rack_merges


# All sign/rearrange critical sections for this small workbench take the same
# transaction-scoped PostgreSQL advisory lock.  Row locks remain useful for
# SQLite/local execution and for direct callers.
PAIRING_LOCK_KEY = 9_123_456_789_012_345_671


def _barrier(conn):
    if conn.dialect.name == 'postgresql':
        conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': PAIRING_LOCK_KEY})


def _lock_stack_rows(conn):
    # Ordered locking keeps rearrangement and moisture/sign operations on a
    # consistent inventory snapshot without lock-order cycles.
    conn.execute(
        select(brick_stacks.c.code)
        .order_by(brick_stacks.c.code)
        .with_for_update()
    ).all()


def _option(data, *names, default=None):
    if data is None:
        return default
    if isinstance(data, dict):
        for name in names:
            if name in data and data[name] is not None:
                return data[name]
        return default
    for name in names:
        if hasattr(data, name):
            value = getattr(data, name)
            if value is not None:
                return value
    extra = getattr(data, 'model_extra', None)
    if isinstance(extra, dict):
        for name in names:
            if name in extra and extra[name] is not None:
                return extra[name]
    return default


def _selected_codes(data):
    raw = _option(
        data, 'stacks', 'codes', 'stack_codes', 'stackCodes',
        'members', 'selected_codes', 'selectedCodes', 'stack',
    )
    if raw is None:
        return None
    if isinstance(raw, str):
        return {raw}
    codes = set()
    for item in raw:
        if isinstance(item, str):
            codes.add(item)
        else:
            code = _option(item, 'code', 'stack_code', 'stack', 'id')
            if code:
                codes.add(str(code))
    return codes or None


def _signed_state(conn):
    candidates = scan(conn, rack_candidates)
    candidate_by_code = {row['code']: row for row in candidates}
    merges = scan(conn, rack_merges)
    merge_by_candidate = {row['candidate']: row for row in merges}
    occupied = set()
    signed_pairs = set()
    for merge in merges:
        candidate = candidate_by_code.get(merge['candidate'])
        if candidate is None:
            continue
        key = pair_key(candidate['member'], candidate['pair'])
        signed_pairs.add(key)
        occupied.update(key)
    return {
        'candidates': candidates,
        'candidate_by_code': candidate_by_code,
        'merges': merges,
        'merge_by_candidate': merge_by_candidate,
        'occupied': occupied,
        'signed_pairs': signed_pairs,
    }


def _sync_signed_occupancy(conn, state):
    """Make the denormalised merged flag agree with durable rack cards."""
    for code in sorted(state['occupied']):
        conn.execute(
            brick_stacks.update()
            .where(brick_stacks.c.code == code)
            .values(merged=True)
        )


def reconcile_signed_occupancy(conn):
    state = _signed_state(conn)
    _sync_signed_occupancy(conn, state)
    return sorted(state['occupied'])


def _eligible_stacks(conn, state, selected=None, grade=None):
    sources = {row['code']: row for row in scan(conn, demolitions)}
    rows = []
    for stack in scan(conn, brick_stacks):
        source = sources.get(stack['origin'])
        if source is None or not source['allowed']:
            continue
        if grade is not None and source['load_grade'] != grade:
            continue
        if not stack['weighed'] or float(stack['wet']) > MOISTURE_LIMIT:
            continue
        if stack['merged'] or stack['code'] in state['occupied']:
            continue
        if selected is not None and stack['code'] not in selected:
            continue
        row = dict(stack)
        row['load_grade'] = source['load_grade']
        row['allowed'] = True
        rows.append(row)
    return rows


def fit_shape_clusters(rows):
    """Fit one StandardScaler/BIRCH model independently for each usage."""
    information = {}
    distances = {}
    vectors = {}
    grades = sorted({row['load_grade'] for row in rows})
    for grade in grades:
        group = [row for row in rows if row['load_grade'] == grade]
        matrix = learning.matrix(group)
        if len(group) == 1:
            transformed = np.asarray([[0.0, 0.0]])
            labels = [0]
        else:
            scaler = learning.StandardScaler().fit(matrix)
            transformed = scaler.transform(matrix)
            model = learning.Birch(threshold=.25, n_clusters=None)
            batch_count = min(3, len(group))
            for batch in np.array_split(transformed, batch_count):
                if len(batch):
                    model.partial_fit(batch)
            labels = [int(label) for label in model.predict(transformed)]

        for row, vector, label in zip(group, transformed, labels):
            code = row['code']
            cluster_key = f'{grade}#{label}'
            information[code] = {
                'cluster_key': cluster_key,
                'load_grade': grade,
                'vector': [float(x) for x in vector],
            }
            vectors[code] = vector

        for i, left in enumerate(group):
            for right in group[i + 1:]:
                key = pair_key(left['code'], right['code'])
                distances[key] = float(np.linalg.norm(
                    vectors[left['code']] - vectors[right['code']]
                ))
    return information, distances, vectors


def _stored_clusters(conn):
    try:
        rows = scan(conn, rack_stack_clusters)
    except Exception:
        return {}
    return {row['code']: row for row in rows}


def _persist_cluster(conn, code, grade, cluster_key, batch_code, vector):
    # Use the records helper's table-neutral get semantics without 404 noise.
    row = conn.execute(
        rack_stack_clusters.select().where(rack_stack_clusters.c.code == code)
    ).mappings().first()
    values = {
        'load_grade': grade,
        'cluster_key': cluster_key,
        'batch_code': batch_code,
        'coordinates': vector,
    }
    if row is None:
        conn.execute(rack_stack_clusters.insert().values(code=code, **values))
    else:
        conn.execute(
            rack_stack_clusters.update()
            .where(rack_stack_clusters.c.code == code)
            .values(**values)
        )


def _candidate_code(conn, batch_code, index, prefix=None):
    base = str(prefix or batch_code or 'PAIR')
    proposed = f'{base}-{index:02d}'
    if len(proposed) > 64:
        proposed = f'PAIR-{uuid4().hex[:20]}'
    exists = conn.execute(
        select(rack_candidates.c.code).where(rack_candidates.c.code == proposed)
    ).first()
    if exists:
        suffix = uuid4().hex[:8]
        trimmed = base[:48]
        proposed = f'{trimmed}-{index:02d}-{suffix}'
    return proposed[:64]


def _is_active(candidate):
    return candidate.get('status') != 'superseded' and candidate.get('active') not in (False, 0)


def arrange_pairs(conn=None, user=None, data=None, *, rearrange=False, stacks=None,
                  old_pairs=None, **algorithm_options):
    """Create/replace the globally optimal feasible candidate matching.

    The first three positional arguments are the storage-backed service call.
    A list/tuple/set as the first argument also enables direct algorithm calls
    such as ``arrange_pairs(stacks, old_pairs)`` in unit tests.
    """
    if stacks is not None or isinstance(conn, (list, tuple, set)):
        rows = stacks if stacks is not None else conn
        previous = old_pairs
        if previous is None and isinstance(user, (list, tuple, set, dict)):
            previous = user
        previous = previous or algorithm_options.pop('previous_pairs', None)
        previous = previous or algorithm_options.pop('old_pairings', None)
        previous = previous or algorithm_options.pop('previous', None)
        previous = previous or algorithm_options.pop('existing_pairs', None)
        for alias in ('capacity', 'rack_capacity', 'max_bricks', 'pieces_limit'):
            if alias in algorithm_options and 'max_pieces' not in algorithm_options:
                algorithm_options['max_pieces'] = algorithm_options.pop(alias)
        return optimize_matching(rows, previous or (), **algorithm_options)

    _barrier(conn)
    batch_code = str(_option(
        data, 'batch_code', 'batchCode', 'batch', 'plan_code', 'code', default=None
    ) or f'PLAN-{uuid4().hex[:16]}')
    prefix = _option(data, 'prefix', 'code_prefix', 'candidate_prefix', default=batch_code)
    dry_run = bool(_option(data, 'dry_run', 'dryRun', 'preview', default=False))
    if not dry_run:
        _lock_stack_rows(conn)

    state = _signed_state(conn)
    if not dry_run:
        _sync_signed_occupancy(conn, state)
        for candidate_code in state['merge_by_candidate']:
            conn.execute(rack_candidates.update().where(
                rack_candidates.c.code == candidate_code
            ).values(status='signed', active=True))

    selected = _selected_codes(data)
    only_grade = _option(data, 'load_grade', 'loadGrade', 'grade', 'usage', 'purpose')
    eligible = _eligible_stacks(conn, state, selected, only_grade)
    information, distances, vectors = fit_shape_clusters(eligible)
    stored = _stored_clusters(conn)

    if rearrange:
        # Reuse the first run's standardized coordinates as well as its
        # labels when available, keeping the distance scale stable.
        for code, row in stored.items():
            coordinates = row.get('coordinates')
            if coordinates:
                vectors[code] = np.asarray(coordinates, dtype=float)
        planned_codes = [row['code'] for row in eligible]
        for i, left_code in enumerate(planned_codes):
            left_vector = vectors.get(left_code)
            if left_vector is None:
                continue
            for right_code in planned_codes[i + 1:]:
                right_vector = vectors.get(right_code)
                if right_vector is None:
                    continue
                key = pair_key(left_code, right_code)
                distances[key] = float(np.linalg.norm(left_vector - right_vector))

    planned_rows = []
    for stack in eligible:
        code = stack['code']
        if rearrange and code in stored:
            cluster_key = stored[code]['cluster_key']
        else:
            cluster_key = information[code]['cluster_key']
        row = dict(stack)
        row['cluster_key'] = cluster_key
        planned_rows.append(row)
        if not dry_run:
            info = information[code]
            coordinates = info['vector']
            if rearrange and code in stored and stored[code].get('coordinates'):
                coordinates = stored[code]['coordinates']
            _persist_cluster(
                conn, code, stack['load_grade'], cluster_key, batch_code, coordinates
            )

    active_candidates = [
        candidate for candidate in state['candidates']
        if candidate['code'] not in state['merge_by_candidate'] and _is_active(candidate)
    ]
    if selected is not None:
        active_candidates = [
            candidate for candidate in active_candidates
            if candidate['member'] in selected or candidate['pair'] in selected
        ]
    old_pairs = [
        pair_key(candidate['member'], candidate['pair'])
        for candidate in active_candidates
    ]

    plan = optimize_matching(
        planned_rows,
        old_pairs,
        max_pieces=MAX_RACK_PIECES,
        distances=distances,
        vectors=vectors,
    )

    planned_cluster_sizes = {}
    for row in planned_rows:
        key = row['cluster_key']
        planned_cluster_sizes[key] = planned_cluster_sizes.get(key, 0) + 1
    stored_cluster_sizes = {}
    for row in stored.values():
        key = row['cluster_key']
        stored_cluster_sizes[key] = stored_cluster_sizes.get(key, 0) + 1
    new_cluster_counts = {}
    for row in planned_rows:
        if row['code'] not in stored:
            key = row['cluster_key']
            new_cluster_counts[key] = new_cluster_counts.get(key, 0) + 1
    if rearrange:
        cluster_sizes = {
            key: stored_cluster_sizes.get(key, 0) + new_cluster_counts.get(key, 0)
            for key in planned_cluster_sizes
        }
    else:
        cluster_sizes = dict(planned_cluster_sizes)

    existing_by_pair = {}
    for candidate in active_candidates:
        key = pair_key(candidate['member'], candidate['pair'])
        existing_by_pair.setdefault(key, []).append(candidate)

    candidate_views = []
    superseded_views = []
    used_candidate_codes = set()
    if not dry_run:
        for index, item in enumerate(plan['pairs'], start=1):
            key = pair_key(item['member'], item['pair'])
            matches = existing_by_pair.get(key, [])
            if matches:
                candidate = matches[0]
                code = candidate['code']
                used_candidate_codes.add(code)
                conn.execute(rack_candidates.update().where(
                    rack_candidates.c.code == code
                ).values(
                    cluster_key=item['cluster_key'],
                    distance=item['distance'],
                    cluster_size=cluster_sizes[item['cluster_key']],
                    batch_code=batch_code,
                    status='active',
                    active=True,
                ))
            else:
                code = _candidate_code(conn, batch_code, index, prefix)
                token = f'pairing:{uuid4().hex}'
                values = {
                    'code': code,
                    'member': item['member'],
                    'pair': item['pair'],
                    'cluster_size': cluster_sizes[item['cluster_key']],
                    'token': token,
                    'cluster_key': item['cluster_key'],
                    'distance': item['distance'],
                    'batch_code': batch_code,
                    'status': 'active',
                    'active': True,
                    'revision': 1,
                }
                conn.execute(rack_candidates.insert().values(**values))
                used_candidate_codes.add(code)

            view = {
                'code': code,
                'candidate': code,
                'member': item['member'],
                'pair': item['pair'],
                'cluster_size': cluster_sizes[item['cluster_key']],
                'cluster_key': item['cluster_key'],
                'distance': item['distance'],
                'pieces': item['pieces'],
                'status': 'active',
                'kept_old': bool(matches),
            }
            item['candidate'] = code
            item['code'] = code
            candidate_views.append(view)

        # Every unsigned active candidate absent from the new matching is a
        # replaced pairing.  Keep the row as a tombstone so a racing sign can
        # receive a 409 rather than an ambiguous missing reference.
        for candidate in active_candidates:
            if candidate['code'] in used_candidate_codes:
                continue
            superseded_view = {
                'code': candidate['code'],
                'candidate': candidate['code'],
                'member': candidate['member'],
                'pair': candidate['pair'],
                'status': 'superseded',
            }
            superseded_views.append(superseded_view)
            conn.execute(rack_candidates.update().where(
                rack_candidates.c.code == candidate['code']
            ).values(
                status='superseded',
                active=False,
                batch_code=batch_code,
                revision=rack_candidates.c.revision + 1,
            ))
    else:
        for index, item in enumerate(plan['pairs'], start=1):
            matches = existing_by_pair.get(pair_key(item['member'], item['pair']), [])
            code = matches[0]['code'] if matches else f'{prefix or batch_code}-{index:02d}'
            item['candidate'] = code
            item['code'] = code
            candidate_views.append({
                'code': code,
                'candidate': code,
                'member': item['member'],
                'pair': item['pair'],
                'cluster_size': cluster_sizes[item['cluster_key']],
                'cluster_key': item['cluster_key'],
                'distance': item['distance'],
                'pieces': item['pieces'],
                'status': 'active',
                'kept_old': bool(matches),
            })

    signed_views = []
    signed_pair_items = []
    for merge in state['merges']:
        candidate = state['candidate_by_code'].get(merge['candidate'])
        if candidate is None:
            continue
        key = pair_key(candidate['member'], candidate['pair'])
        signed_views.append({
            'candidate': merge['candidate'],
            'code': merge['code'],
            'pair': list(key),
            'rack': merge['rack'],
            'pieces': merge['pieces'],
        })
        view = {
            'code': candidate['code'],
            'candidate': candidate['code'],
            'member': key[0],
            'pair': key[1],
            'cluster_size': candidate.get('cluster_size'),
            'cluster_key': candidate.get('cluster_key'),
            'distance': candidate.get('distance'),
            'pieces': merge['pieces'],
            'status': 'signed',
            'kept_old': True,
            'rack': merge['rack'],
            'rack_code': merge['code'],
        }
        signed_pair_items.append(view)
        candidate_views.append(view)

    all_pairs = list(plan['pairs']) + signed_pair_items
    total_pair_count = plan['pair_count'] + len(signed_pair_items)
    preserved_old_count = plan['kept_old_count'] + len(signed_pair_items)

    plan.update({
        'code': batch_code,
        'batch': batch_code,
        'batch_code': batch_code,
        'prefix': prefix,
        'arrangement': 'rearrange' if rearrange else 'initial',
        'rearrange': rearrange,
        'dry_run': dry_run,
        'candidates': candidate_views,
        'all_candidates': candidate_views,
        'superseded_candidates': superseded_views,
        'rejected_candidates': superseded_views,
        'broken_candidates': superseded_views,
        'broken_pairs': [
            [item['member'], item['pair']] for item in superseded_views
        ],
        'superseded_codes': [item['code'] for item in superseded_views],
        'rejected_codes': [item['code'] for item in superseded_views],
        'pairings': plan['pairs'],
        'pair_codes': [[item['member'], item['pair']] for item in plan['pairs']],
        'pair_list': [[item['member'], item['pair']] for item in plan['pairs']],
        'edges': [[item['member'], item['pair']] for item in plan['pairs']],
        'matches': plan['pairs'],
        'assignments': plan['pairs'],
        'selected': plan['pairs'],
        'all_pairs': all_pairs,
        'workbench_pairs': all_pairs,
        'preserved_old_count': preserved_old_count,
        'old_preserved': preserved_old_count,
        'total_pair_count': total_pair_count,
        'total_pairs': total_pair_count,
        'total_paired_count': 2 * total_pair_count,
        'signed_pairs': signed_views,
        'signed_count': len(signed_views),
        'eligible_count': len(eligible),
        'cluster_count': len(cluster_sizes),
    })
    plan['result'] = {
        'pairs': plan['pairs'],
        'all_pairs': all_pairs,
        'pair_count': plan['pair_count'],
        'paired_count': plan['paired_count'],
        'total_pair_count': total_pair_count,
        'total_paired_count': 2 * total_pair_count,
        'total_distance': plan['total_distance'],
    }
    return plan


def rearrange_pairs(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=True)


# Verb aliases for callers/tests using the workbench vocabulary.
def first_arrangement(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def initial_arrangement(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def arrange(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def pair_stacks(conn, user=None, data=None):
    # Delegate to the storage-backed operation; the pure algorithm is
    # domain.pairing.optimize_matching.
    return arrange_pairs(conn, user, data, rearrange=False)


def pair_all(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def auto_pair(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def plan_pairs(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def run_pairing(conn, user=None, data=None):
    return arrange_pairs(conn, user, data, rearrange=False)


def rearrange(conn, user=None, data=None):
    return rearrange_pairs(conn, user, data)


def replan_pairs(conn, user=None, data=None):
    return rearrange_pairs(conn, user, data)


def reshuffle_pairs(conn, user=None, data=None):
    return rearrange_pairs(conn, user, data)


# Pure-algorithm aliases for unit-level callers.
optimal_matching = optimize_matching
best_pairing = optimize_matching
choose_pairs = optimize_matching
solve_pairing = optimize_matching
match_pairs = optimize_matching
generate_pairings = optimize_matching


def suggest_pair(conn, user, data):
    target = get(conn, brick_stacks, data.stack)
    standard = get(conn, demolitions, target['origin'])['load_grade']
    sources = {
        r['code'] for r in scan(conn, demolitions)
        if r['load_grade'] == standard and r['allowed']
    }
    signed = _signed_state(conn)['occupied']
    pool = [
        r for r in scan(conn, brick_stacks)
        if r['origin'] in sources
        and r['code'] != target['code']
        and r['weighed']
        and not r['merged']
        and r['code'] not in signed
        and float(r['wet']) <= MOISTURE_LIMIT
    ]
    need(
        target['weighed']
        and not target['merged']
        and target['code'] not in signed
        and len(pool) >= 6,
        '至少需要六个相同用途的复测垛',
    )
    need(float(target['wet']) <= MOISTURE_LIMIT, '试配砖垛湿度不达标')
    return submit(user, {'target': target, 'samples': pool})


def record_candidate(conn, user, data):
    run = result(user, data.token); stats = run['result']
    return insert(conn, rack_candidates, code=data.code, member=run['input']['target']['code'],
      pair=stats['pair'], cluster_size=stats['size'], token=data.token)
