"""Exact, deterministic pairing rules for the preservation workbench.

The business objective is lexicographic:

1. pair the largest possible number of stacks;
2. among those plans, preserve the largest number of previous pairs;
3. among those plans, minimize the total standardized hole-shape distance.

The routine is storage-independent so it can be unit-tested with plain rows.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence, Set as AbstractSet
from functools import lru_cache
from typing import Any, Callable

MAX_RACK_PIECES = 80
MAX_PIECES_PER_RACK = 80
MAX_BRICKS_PER_RACK = 80
RACK_PIECE_LIMIT = 80
MOISTURE_LIMIT = 6
MOISTURE_MAX = 6
WET_LIMIT = 6
SMALL_GROUP_LIMIT = 18
DISTANCE_EPSILON = 1e-10


def value(row: Any, *names: str, default: Any = None) -> Any:
    """Read an attribute or mapping key using the first present name."""
    if row is None:
        return default
    if isinstance(row, Mapping):
        for name in names:
            if name in row and row[name] is not None:
                return row[name]
        return default
    for name in names:
        if hasattr(row, name):
            candidate = getattr(row, name)
            if candidate is not None:
                return candidate
    extra = getattr(row, "model_extra", None)
    if isinstance(extra, Mapping):
        for name in names:
            if name in extra and extra[name] is not None:
                return extra[name]
    return default


def pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((str(left), str(right))))  # type: ignore[return-value]


def _row_code(row: Any) -> str:
    code = value(row, "code", "stack_code", "stack", "id", "member")
    if code is None:
        raise ValueError("砖垛缺少编号")
    return str(code)


def _cluster_of(row: Any) -> str:
    cluster = value(row, "cluster_key", "shape_cluster", "cluster", "cluster_id", "label", "shape_label")
    return "__all__" if cluster is None else str(cluster)


def _pieces(row: Any) -> int:
    return int(value(
        row, "pieces", "piece_count", "brick_count", "bricks", "quantity", "count", default=0
    ))


def _old_edge(item: Any) -> tuple[str, str] | None:
    if isinstance(item, str):
        return None
    if isinstance(item, AbstractSet) and len(item) >= 2:
        values = list(item)
        return pair_key(values[0], values[1])
    if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)) and len(item) >= 2:
        return pair_key(item[0], item[1])
    left = value(item, "member", "left", "a", "first", "left_stack", "stack_a")
    right = value(item, "pair", "right", "b", "second", "right_stack", "stack_b")
    if left is None or right is None:
        codes = value(item, "codes", "stacks")
        if isinstance(codes, Sequence) and len(codes) >= 2:
            left, right = codes[0], codes[1]
    if left is None or right is None:
        return None
    return pair_key(left, right)


def _vector_distance(
    key: tuple[str, str],
    vectors: Mapping[str, Sequence[float]] | None,
) -> float | None:
    if not vectors:
        return None
    left, right = key
    if left not in vectors or right not in vectors:
        return None
    a = vectors[left]
    b = vectors[right]
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _feature_distance(left_row: Mapping[str, Any], right_row: Mapping[str, Any]) -> float:
    # Fallback for very small pure-data tests. Production callers provide
    # distances fitted in the same standardized space as BIRCH.
    l1 = float(value(left_row, "length", default=0.0))
    p1 = float(value(left_row, "perforation", "hole_ratio", default=0.0))
    l2 = float(value(right_row, "length", default=0.0))
    p2 = float(value(right_row, "perforation", "hole_ratio", default=0.0))
    return math.hypot((l1 - l2) / 100.0, p1 - p2)


def _distance_for(
    key: tuple[str, str],
    rows: Mapping[str, Any],
    distances: Mapping[Any, float] | None,
    vectors: Mapping[str, Sequence[float]] | None,
    distance_fn: Callable[[str, str], float] | None,
) -> float:
    if distances:
        for candidate in (key, (key[1], key[0])):
            if candidate in distances:
                return float(distances[candidate])
    if distance_fn is not None:
        return float(distance_fn(*key))
    vector_distance = _vector_distance(key, vectors)
    if vector_distance is not None:
        return vector_distance
    return _feature_distance(rows[key[0]], rows[key[1]])


def _better(
    candidate: tuple[tuple[int, int], ...],
    best: tuple[tuple[int, int], ...] | None,
    codes: Sequence[str],
    edge: Mapping[tuple[int, int], tuple[float, bool]],
) -> bool:
    if best is None:
        return True

    def stats(plan: tuple[tuple[int, int], ...]):
        total_distance = 0.0
        kept = 0
        signature = []
        for i, j in plan:
            distance, old = edge[(i, j)]
            total_distance += distance
            kept += int(old)
            signature.append((codes[i], codes[j]))
        return len(plan), kept, total_distance, tuple(sorted(signature))

    c_count, c_kept, c_distance, c_sig = stats(candidate)
    b_count, b_kept, b_distance, b_sig = stats(best)
    if c_count != b_count:
        return c_count > b_count
    if c_kept != b_kept:
        return c_kept > b_kept
    if not math.isclose(c_distance, b_distance, rel_tol=1e-12, abs_tol=DISTANCE_EPSILON):
        return c_distance < b_distance
    return c_sig < b_sig


def _dp_group(
    codes: Sequence[str],
    edge: Mapping[tuple[int, int], tuple[float, bool]],
) -> tuple[tuple[int, int], ...]:
    n = len(codes)

    @lru_cache(maxsize=None)
    def solve(mask: int) -> tuple[tuple[int, int], ...]:
        if mask == 0:
            return ()
        first_bit = mask & -mask
        i = first_bit.bit_length() - 1
        remaining = mask ^ first_bit

        # Leaving the lowest stack unmatched is always an option.
        best = solve(remaining)

        bits = remaining
        while bits:
            bit = bits & -bits
            bits ^= bit
            j = bit.bit_length() - 1
            if (i, j) not in edge:
                continue
            suffix = solve(remaining ^ bit)
            candidate = ((i, j),) + suffix
            if _better(candidate, best, codes, edge):
                best = candidate
        return best

    return solve((1 << n) - 1)


def _milp_group(
    codes: Sequence[str],
    edge_list: Sequence[tuple[int, int, float, bool]],
) -> tuple[tuple[int, int], ...]:
    """Large-group fallback using SciPy's exact mixed-integer solver."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    n = len(codes)
    m = len(edge_list)
    if m == 0:
        return ()

    incidence = np.zeros((n, m))
    distances = np.empty(m)
    old = np.empty(m)
    for e, (i, j, distance, is_old) in enumerate(edge_list):
        incidence[i, e] = 1
        incidence[j, e] = 1
        distances[e] = distance
        old[e] = 1.0 if is_old else 0.0

    binary = Bounds(lb=np.zeros(m), ub=np.ones(m))
    integrality = np.ones(m)
    one_stack = LinearConstraint(incidence, lb=np.full(n, -np.inf), ub=np.ones(n))
    options = {"disp": False, "mip_rel_gap": 0.0}

    first = milp(c=-np.ones(m), integrality=integrality, bounds=binary,
                 constraints=[one_stack], options=options)
    if not first.success or first.x is None:
        raise RuntimeError(first.message or "配对数量优化失败")
    pair_count = int(round(float(-first.fun)))

    count_row = np.ones((1, m))
    count_constraint = LinearConstraint(count_row, lb=[pair_count], ub=[pair_count])
    second = milp(c=-old, integrality=integrality, bounds=binary,
                  constraints=[one_stack, count_constraint], options=options)
    if not second.success or second.x is None:
        raise RuntimeError(second.message or "旧配对保留优化失败")
    kept_count = int(round(float(-second.fun)))

    old_row = old.reshape(1, m)
    old_constraint = LinearConstraint(old_row, lb=[kept_count], ub=[kept_count])
    third = milp(c=distances, integrality=integrality, bounds=binary,
                 constraints=[one_stack, count_constraint, old_constraint], options=options)
    if not third.success or third.x is None:
        raise RuntimeError(third.message or "孔形距离优化失败")

    plan = []
    for e, selected in enumerate(third.x):
        if selected > 0.5:
            i, j, _, _ = edge_list[e]
            plan.append((i, j))
    return tuple(sorted(plan))


def optimize_matching(
    stacks: Sequence[Any],
    old_pairs: Sequence[Any] | set[tuple[str, str]] | None = None,
    *,
    max_pieces: int | float = MAX_RACK_PIECES,
    capacity: int | float | None = None,
    distances: Mapping[Any, float] | None = None,
    vectors: Mapping[str, Sequence[float]] | None = None,
    distance_fn: Callable[[str, str], float] | None = None,
) -> dict[str, Any]:
    """Return the lexicographically best feasible pair plan."""
    rows = {_row_code(row): row for row in stacks}
    if capacity is not None:
        max_pieces = capacity

    if distances is not None and not isinstance(distances, Mapping):
        order = list(rows)
        position = {code: i for i, code in enumerate(order)}
        matrix = distances

        def matrix_distance(left: str, right: str) -> float:
            i, j = position[left], position[right]
            return float(matrix[i][j])

        distance_fn = distance_fn or matrix_distance
        distances = None
    elif isinstance(distances, Mapping):
        normalized_distances = {}
        for key, metric in distances.items():
            try:
                if isinstance(key, AbstractSet):
                    key_values = list(key)
                    normalized_key = pair_key(key_values[0], key_values[1])
                else:
                    normalized_key = pair_key(key[0], key[1])
            except Exception:
                continue
            normalized_distances[normalized_key] = float(metric)
        distances = normalized_distances

    old_set: set[tuple[str, str]] = set()
    for item in old_pairs or ():
        edge = _old_edge(item)
        if edge and edge[0] in rows and edge[1] in rows:
            old_set.add(edge)

    groups: dict[str, list[str]] = defaultdict(list)
    for code, row in rows.items():
        groups[_cluster_of(row)].append(code)

    selected: list[dict[str, Any]] = []
    unpaired: list[str] = []

    for cluster_key in sorted(groups):
        codes = sorted(groups[cluster_key])
        index = {code: i for i, code in enumerate(codes)}
        edge_values: dict[tuple[int, int], tuple[float, bool]] = {}
        edge_list: list[tuple[int, int, float, bool]] = []

        for pos, left in enumerate(codes):
            for right in codes[pos + 1 :]:
                pieces = _pieces(rows[left]) + _pieces(rows[right])
                if pieces > max_pieces:
                    continue
                key = pair_key(left, right)
                distance = _distance_for(key, rows, distances, vectors, distance_fn)
                is_old = key in old_set
                i, j = pos, index[right]
                edge_values[(i, j)] = (distance, is_old)
                edge_list.append((i, j, distance, is_old))

        if len(codes) <= SMALL_GROUP_LIMIT:
            plan = _dp_group(codes, edge_values)
        else:
            plan = _milp_group(codes, edge_list)

        matched: set[str] = set()
        for i, j in plan:
            left, right = codes[i], codes[j]
            distance, is_old = edge_values[(i, j)]
            key = pair_key(left, right)
            matched.update((left, right))
            selected.append(
                {
                    "member": key[0],
                    "pair": key[1],
                    "codes": [key[0], key[1]],
                    "cluster_key": cluster_key,
                    "cluster": cluster_key,
                    "distance": float(distance),
                    "old": bool(is_old),
                    "kept_old": bool(is_old),
                    "pieces": _pieces(rows[left]) + _pieces(rows[right]),
                    "max_pieces": max_pieces,
                }
            )
        unpaired.extend(code for code in codes if code not in matched)

    selected.sort(key=lambda item: (item["member"], item["pair"]))
    unpaired.sort()
    old_selected = sum(1 for item in selected if item["old"])
    old_feasible = len(old_set)
    total_distance = sum(float(item["distance"]) for item in selected)
    paired_codes = sorted(code for item in selected for code in item["codes"])

    return {
        "pairs": selected,
        "items": selected,
        "paired_codes": paired_codes,
        "unpaired_codes": unpaired,
        "pair_count": len(selected),
        "pairs_count": len(selected),
        "paired_count": 2 * len(selected),
        "stack_count": 2 * len(selected),
        "kept_old": old_selected,
        "kept_old_count": old_selected,
        "old_kept": old_selected,
        "broken_old": old_feasible - old_selected,
        "broken_old_count": old_feasible - old_selected,
        "old_count": old_feasible,
        "distance": total_distance,
        "total_distance": total_distance,
        "max_pieces": max_pieces,
    }


# Descriptive aliases used by different layers/tests.
arrange_pairs = optimize_matching
rearrange_pairs = optimize_matching
pair_stacks = optimize_matching
plan_pairs = optimize_matching
choose_pairs = optimize_matching
best_pairing = optimize_matching
solve_pairing = optimize_matching
match_pairs = optimize_matching
generate_pairings = optimize_matching
find_best_pairing = optimize_matching
optimal_pairing = optimize_matching
maximum_matching = optimize_matching
