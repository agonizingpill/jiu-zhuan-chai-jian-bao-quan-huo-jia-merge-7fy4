"""Compatibility alias for the pure pairing algorithm."""

from domain.pairing import *  # noqa: F401,F403
from domain.pairing import optimize_matching as pair_stacks  # noqa: F401

arrange_pairs = optimize_matching
rearrange_pairs = optimize_matching
best_pairing = optimize_matching
