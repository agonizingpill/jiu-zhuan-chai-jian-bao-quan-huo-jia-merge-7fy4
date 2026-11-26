"""Service-name compatibility module."""

from domain.pairing import MAX_RACK_PIECES, MOISTURE_LIMIT, optimize_matching
from domain.rack_candidates_service import (
    arrange_pairs,
    auto_pair,
    first_arrangement,
    pair_all,
    pair_stacks,
    rearrange,
    rearrange_pairs,
    replan_pairs,
)

best_pairing = optimize_matching
plan_pairs = arrange_pairs

__all__ = [
    'MAX_RACK_PIECES',
    'MOISTURE_LIMIT',
    'optimize_matching',
    'arrange_pairs',
    'auto_pair',
    'best_pairing',
    'first_arrangement',
    'pair_all',
    'pair_stacks',
    'plan_pairs',
    'rearrange',
    'rearrange_pairs',
    'replan_pairs',
]
