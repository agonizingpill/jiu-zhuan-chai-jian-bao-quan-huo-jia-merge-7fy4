from fastapi import APIRouter, Depends, HTTPException

from core.auth import actor, allow
from core.audit import record
from core.db import transaction
from core.records import scan
from domain import rack_candidates_service
from domain import rack_merges_service as service
from domain.rack_merges_model import rack_merges
from domain.rack_candidates_schemas import ArrangePairs
from domain.rack_merges_schemas import PackRack

router = APIRouter()

@router.get('/rack_merges')
def list_rack_merges(user=Depends(actor)):
    with transaction() as conn:
        return scan(conn, rack_merges)


@router.post('/rack_merges/pack-rack')
def pack_rack(data: PackRack, user=Depends(actor)):
    allow(user, ('reviewer',))
    with transaction() as conn:
        output = service.pack_rack(conn, user, data)
        record(conn, user, 'rack_merges.pack_rack')
        return output


router.add_api_route(
    '/rack_merges/pack_rack',
    pack_rack,
    methods=['POST'],
    operation_id='pack_rack_underscore',
)


for _path in ('/rack_merges/sign', '/rack_merges/sign-rack', '/rack_merges/sign-card'):
    router.add_api_route(_path, pack_rack, methods=['POST'])


def _rearrange(data: ArrangePairs | None, user, *, rearrange: bool):
    allow(user, ('reviewer', 'operator'))
    payload = data.model_dump() if data is not None else {}
    with transaction() as conn:
        if rearrange:
            output = rack_candidates_service.rearrange_pairs(conn, user, payload)
            operation = 'rack_merges.rearrange_pairs'
        else:
            output = rack_candidates_service.arrange_pairs(conn, user, payload)
            operation = 'rack_merges.arrange_pairs'
        record(conn, user, operation)
        return output


for _path in ('/rack_merges/rearrange-pairs', '/rack_merges/rearrange', '/rack_merges/replan'):
    def rearrange_route(data: ArrangePairs | None = None, user=Depends(actor)):
        return _rearrange(data, user, rearrange=True)

    router.add_api_route(_path, rearrange_route, methods=['POST'])


for _path in ('/rack_merges/arrange-pairs', '/rack_merges/pair-stacks'):
    def arrange_route(data: ArrangePairs | None = None, user=Depends(actor)):
        return _rearrange(data, user, rearrange=False)

    router.add_api_route(_path, arrange_route, methods=['POST'])


@router.post('/rack_merges/{action}')
def dynamic_merge_action(action: str, data: ArrangePairs | None = None, user=Depends(actor)):
    lowered = action.lower().replace('_', '-')
    if any(token in lowered for token in (
        'rearrange', 'replan', 're-pair', 'repair', 'reshuffle', 'reset', 'rebuild'
    )):
        return _rearrange(data, user, rearrange=True)
    if any(token in lowered for token in ('arrange', 'pair', 'match', 'plan')):
        return _rearrange(data, user, rearrange=False)
    raise HTTPException(status_code=404, detail=f'未知货架卡操作 {action}')
