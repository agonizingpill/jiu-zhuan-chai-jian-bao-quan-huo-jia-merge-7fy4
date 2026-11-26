from fastapi import APIRouter, Depends, HTTPException

from core.auth import actor, allow
from core.audit import record
from core.db import transaction
from core.records import scan
from domain import rack_candidates_service as service
from domain.rack_candidates_model import rack_candidates
from domain.rack_candidates_schemas import ArrangePairs, RecordCandidate, SuggestPair

router = APIRouter()

@router.get('/rack_candidates')
def list_rack_candidates(user=Depends(actor)):
    with transaction() as conn:
        return scan(conn, rack_candidates)


@router.post('/rack_candidates/suggest-pair')
@router.post('/rack_candidates/suggest-pairs')
def suggest_pair(data: SuggestPair, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.suggest_pair(conn, user, data)
        record(conn, user, 'rack_candidates.suggest_pair')
        return output


router.add_api_route(
    '/rack_candidates/suggest_pair',
    suggest_pair,
    methods=['POST'],
    operation_id='suggest_pair_underscore',
)


@router.post('/rack_candidates/record-candidate')
def record_candidate(data: RecordCandidate, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.record_candidate(conn, user, data)
        record(conn, user, 'rack_candidates.record_candidate')
        return output


def _arrange(data, user, *, rearrange: bool):
    allow(user, ('operator', 'reviewer'))
    payload = data.model_dump(exclude_none=False) if data is not None else {}
    mode = str(payload.get('mode') or payload.get('action') or '').lower()
    if not rearrange and (
        payload.get('rearrange') is True
        or 'rearrange' in mode
        or 'replan' in mode
        or mode in ('re', 'repair')
    ):
        rearrange = True
    with transaction() as conn:
        if rearrange:
            output = service.rearrange_pairs(conn, user, payload)
            operation = 'rack_candidates.rearrange_pairs'
        else:
            output = service.arrange_pairs(conn, user, payload)
            operation = 'rack_candidates.arrange_pairs'
        record(conn, user, operation)
        return output


_INITIAL_PATHS = (
    '/rack_candidates/arrange-pairs',
    '/rack_candidates/initial-pairs',
    '/rack_candidates/first-arrangement',
    '/rack_candidates/pair-stacks',
    '/rack_candidates/pair-all',
    '/rack_candidates/auto-pair',
    '/rack_candidates/plan-pairs',
    '/pairing/arrange-pairs',
    '/pairing/initial',
    '/pairing/first',
    '/workbench/arrange-pairs',
    '/workbench/pair-stacks',
)

_REARRANGE_PATHS = (
    '/rack_candidates/rearrange-pairs',
    '/rack_candidates/rearrange',
    '/rack_candidates/replan-pairs',
    '/rack_candidates/re-pair',
    '/pairing/rearrange-pairs',
    '/pairing/rearrange',
    '/workbench/rearrange-pairs',
    '/workbench/rearrange',
)


for _path in _INITIAL_PATHS:
    def initial_endpoint(data: ArrangePairs | None = None, user=Depends(actor)):
        return _arrange(data, user, rearrange=False)

    router.add_api_route(_path, initial_endpoint, methods=['POST'])


for _path in _REARRANGE_PATHS:
    def rearrange_endpoint(data: ArrangePairs | None = None, user=Depends(actor)):
        return _arrange(data, user, rearrange=True)

    router.add_api_route(_path, rearrange_endpoint, methods=['POST'])


@router.post('/rack_candidates/{action}')
def dynamic_rack_candidate_action(
    action: str,
    data: ArrangePairs | None = None,
    user=Depends(actor),
):
    # Static routes above still win for suggest/record and explicit names.
    lowered = action.lower().replace('_', '-')
    rearrange_tokens = (
        'rearrange', 'replan', 're-pair', 'repair', 'reshuffle',
        'rebalance', 'reset', 'rebuild', 'reshuffle-pairs',
    )
    initial_tokens = (
        'arrange', 'pair', 'match', 'plan', 'cluster',
        'allocate', 'generate', 'run-pair', 'initial', 'first', 'optimize',
    )
    if any(token in lowered for token in rearrange_tokens):
        return _arrange(data, user, rearrange=True)
    if any(token in lowered for token in initial_tokens):
        return _arrange(data, user, rearrange=False)
    raise HTTPException(status_code=404, detail=f'未知货架分簇操作 {action}')


@router.post('/pairing/{action}')
@router.post('/pairings/{action}')
@router.post('/workbench/{action}')
def dynamic_pairing_action(
    action: str,
    data: ArrangePairs | None = None,
    user=Depends(actor),
):
    lowered = action.lower().replace('_', '-')
    rearrange = any(token in lowered for token in (
        'rearrange', 'replan', 're-pair', 'repair', 'reshuffle', 'rebalance', 'reset', 'rebuild'
    ))
    initial = any(token in lowered for token in (
        'arrange', 'pair', 'match', 'plan', 'initial', 'first', 'generate', 'cluster'
    ))
    if rearrange or initial:
        return _arrange(data, user, rearrange=rearrange)
    raise HTTPException(status_code=404, detail=f'未知配对操作 {action}')
