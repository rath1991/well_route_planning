"""OR-Tools based orienteering / prize-collecting route optimizer.

Strategy
--------
We model this as a **Capacitated Vehicle Routing Problem with Prizes**:

*   Each well node can be *optionally* visited (disjunction with a penalty
    equal to the priority score that would be *lost* by skipping it).
*   The solver minimises total cost, where cost = travel time + penalties for
    skipped nodes.  Because the penalty for skipping a well equals its
    priority_score, minimising "lost priority" is equivalent to **maximising
    total priority_score captured** under the time budget.
*   Service time at each well is added as a transit callback on the time
    dimension so the solver accounts for it.
*   ``must_visit`` wells have their disjunction penalty set to a very large
    number so the solver will never skip them voluntarily.
*   ``max_stops`` is enforced via a count dimension with a hard capacity.

The solver returns the ordered list of well indices to visit
(0-based into the *wells* list, **not** including start/end sentinels).
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2


# Scaling factor: OR-Tools works with integers, so we scale minutes → seconds
_SCALE = 60  # 1 minute → 60 units


@dataclass
class OptimizationResult:
    """Output of the optimizer."""

    visited_indices: list[int]  # 0-based well indices in visit order
    total_drive_minutes: float
    status: str  # "OPTIMAL" | "FEASIBLE" | "NO_SOLUTION"


def solve_route(
    time_matrix: list[list[float]],
    priority_scores: list[float],
    service_times: list[int],
    must_visit_indices: list[int],
    max_stops: int,
    time_budget_minutes: int,
) -> OptimizationResult:
    """Run OR-Tools solver and return visited well indices in order.

    Parameters
    ----------
    time_matrix : (N+2)×(N+2) travel-time matrix in minutes.
        Index 0 = start, 1..N = wells, N+1 = end.
    priority_scores : length-N list of priority scores (0–100) per well.
    service_times : length-N list of service minutes per well.
    must_visit_indices : 0-based well indices that *must* be visited.
    max_stops : hard cap on number of wells visited.
    time_budget_minutes : total time budget including drive + service.
    """
    n_wells = len(priority_scores)
    n_nodes = n_wells + 2  # start(0) + wells(1..N) + end(N+1)
    start_index = 0
    end_index = n_nodes - 1

    manager = pywrapcp.RoutingIndexManager(n_nodes, 1, [start_index], [end_index])
    routing = pywrapcp.RoutingModel(manager)

    # ── Time (transit) callback ──────────────────────────────────────────
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        travel = int(time_matrix[from_node][to_node] * _SCALE)
        # Add service time at the *from* node (if it's a well)
        if 1 <= from_node <= n_wells:
            travel += service_times[from_node - 1] * _SCALE
        return travel

    transit_cb_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_index)

    # ── Time dimension (enforce budget) ──────────────────────────────────
    routing.AddDimension(
        transit_cb_index,
        0,  # no slack
        time_budget_minutes * _SCALE,  # max total time
        True,  # start cumul to zero
        "Time",
    )

    # ── Count dimension (enforce max_stops) ──────────────────────────────
    def count_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        if 1 <= from_node <= n_wells:
            return 1
        return 0

    count_cb_index = routing.RegisterTransitCallback(count_callback)
    routing.AddDimension(
        count_cb_index,
        0,
        max_stops,
        True,
        "Stops",
    )

    # ── Disjunctions (optional nodes with priority-based penalty) ────────
    # Penalty = priority lost by *not* visiting.  Large penalty → must-visit.
    # Scale priority (0–100) by 1000 for integer granularity.
    #
    # Must-visit wells (user's explicit selection) use a flat BIG_PENALTY so
    # the solver treats them all as equally important and visits as many as
    # possible within the time budget — it does NOT filter by priority score
    # within the selection (that filtering was already done by the data query).
    must_visit_set = set(must_visit_indices)
    BIG_PENALTY = 10_000_000

    for well_idx in range(n_wells):
        node = well_idx + 1  # matrix index
        index = manager.NodeToIndex(node)
        if well_idx in must_visit_set:
            penalty = BIG_PENALTY
        else:
            penalty = int(priority_scores[well_idx] * 1_000)
        routing.AddDisjunction([index], penalty)

    # ── Solver parameters (deterministic) ────────────────────────────────
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(5)
    # Deterministic: PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH produce
    # reproducible results for identical inputs.

    # ── Solve ────────────────────────────────────────────────────────────
    solution = routing.SolveWithParameters(search_params)

    if solution is None:
        return OptimizationResult(
            visited_indices=[],
            total_drive_minutes=0,
            status="NO_SOLUTION",
        )

    # Extract route
    visited: list[int] = []
    total_drive = 0.0
    index = routing.Start(0)
    prev_node = manager.IndexToNode(index)

    while not routing.IsEnd(index):
        index = solution.Value(routing.NextVar(index))
        node = manager.IndexToNode(index)
        total_drive += time_matrix[prev_node][node]
        if 1 <= node <= n_wells:
            visited.append(node - 1)  # convert to 0-based well index
        prev_node = node

    status = "OPTIMAL" if routing.status() == 1 else "FEASIBLE"

    return OptimizationResult(
        visited_indices=visited,
        total_drive_minutes=round(total_drive, 2),
        status=status,
    )
