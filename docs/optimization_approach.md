# Optimization Approach: ESP Route Planning

## Problem Statement

A field engineer has **one day** (e.g. 420 minutes) and a list of ESP wells to potentially visit. Each well has:
- A **priority score** (0–100) reflecting how important it is to visit
- A **service time** (minutes spent on-site)
- A **location** (lat/lon)

The engineer starts at a base, visits some subset of wells, and returns. The goal:

> **Maximize total priority score captured**, subject to:
> - Total time (driving + service) ≤ time budget
> - Number of stops ≤ max_stops
> - Certain wells may be mandatory (must-visit)

This is the **Orienteering Problem** (also called the Prize-Collecting Travelling Salesman Problem).

---

## Why Not Just Visit the Highest-Priority Wells?

A greedy "pick top-N by score" approach fails because it ignores geography. Consider:

```
Well A: priority 80, but 90 min drive from everything else
Well B: priority 60, 10 min from base
Well C: priority 55, 12 min from Well B
Well D: priority 50, 8 min from Well C
```

Greedy picks A first (score 80), but then wastes so much drive time that you can only fit 1 more well. Total: 80 + 60 = 140.

A smarter route skips A and visits B → C → D: total 60 + 55 + 50 = 165, with far less driving.

**The optimizer balances score vs. travel cost.**

---

## Our Approach: OR-Tools with Disjunction Penalties

We use Google OR-Tools' routing solver, modelling this as a **Vehicle Routing Problem with optional nodes**.

### Core Idea

OR-Tools minimizes total cost. We want to maximize priority. The trick:

> For each optional well, set a **disjunction penalty** equal to the priority score
> that would be **lost** by skipping it.

The solver then minimizes:

```
total_cost = travel_time + Σ (penalty for each skipped well)
```

Since `penalty = priority_score`, minimizing skipped penalties = maximizing collected priority.

### Step-by-Step

#### 1. Build the Time Matrix

All locations (start, wells, end) form an (N+2) × (N+2) matrix of travel times in minutes, computed via haversine distance ÷ average speed.

```
Example with 3 wells (indices 0=start, 1-3=wells, 4=end):

        Start   W1    W2    W3    End
Start   0.0    15.0  25.0  40.0  0.0
W1      15.0   0.0   12.0  30.0  15.0
W2      25.0   12.0  0.0   18.0  25.0
W3      40.0   30.0  18.0  0.0   40.0
End     0.0    15.0  25.0  40.0  0.0
```

#### 2. Score Each Well

Using the priority formula:

```
priority_score = 0.30 × prod_score
               + 0.25 × uplift_score
               + 0.25 × urgency_score
               + 0.10 × confidence_score
               + 0.10 × recency_score
```

Example:

| Well | Prod | Uplift | Urgency | Confidence | Recency | **Priority** |
|------|------|--------|---------|------------|---------|--------------|
| W1   | 84.0 | 93.3   | 50.0    | 85.0       | 70.0    | **76.5**     |
| W2   | 62.0 | 100.0  | 25.0    | 72.0       | 100.0   | **67.0**     |
| W3   | 58.0 | 73.3   | 0.0     | 55.0       | 23.3    | **43.6**     |

#### 3. Set Up Disjunctions

Each well node is made **optional** with a penalty:

```python
# For each well:
penalty = int(priority_score × 1000)   # scale for integer solver

# Must-visit wells get a huge penalty so they're never skipped:
penalty = 10,000,000
```

| Well | Priority | Disjunction Penalty | Effect |
|------|----------|---------------------|--------|
| W1   | 76.5     | 76,500              | Solver strongly prefers visiting |
| W2   | 67.0     | 67,000              | High incentive to visit |
| W3   | 43.6     | 43,600              | Lower incentive — may skip if detour is costly |

#### 4. Add Time Dimension (Budget Constraint)

The transit callback returns `travel_time + service_time` for each arc:

```python
def time_callback(from_node, to_node):
    travel = time_matrix[from_node][to_node]    # minutes
    if from_node is a well:
        travel += service_time[from_node]       # add on-site work
    return travel
```

The time dimension enforces: `total accumulated time ≤ time_budget_minutes`.

#### 5. Add Count Dimension (Max Stops)

A simple counter increments by 1 at each well node:

```python
def count_callback(from_node, to_node):
    return 1 if from_node is a well else 0
```

Dimension capacity = `max_stops`.

#### 6. Solve

The solver explores routes, balancing:
- **Visiting high-penalty wells** (to avoid paying the penalty)
- **Minimizing travel time** (to fit more wells in the budget)
- **Respecting constraints** (time budget, max stops, must-visits)

---

## Worked Example

### Input

```
Base: Midland Field Office (31.95, -102.1)
Time budget: 420 minutes
Max stops: 4

Wells:
  CMC RANGER     priority=76.5  service=35min  drive_from_base=17min
  VLT VULCAN     priority=67.0  service=40min  drive_from_base=24min
  CMC SNAPDRAGON priority=66.5  service=30min  drive_from_base=16min
  NMC FALCON     priority=58.5  service=45min  drive_from_base=43min
  SMC MUSTANG    priority=43.6  service=30min  drive_from_base=29min
```

### Solver's Decision Process

The solver evaluates candidate routes. Here are some it considers:

**Route A: All 5 wells (greedy by priority)**
```
Start → RANGER → VULCAN → SNAPDRAGON → FALCON → MUSTANG → End
Drive: ~180 min  Service: 180 min  Total: ~360 min
Priority captured: 76.5 + 67.0 + 66.5 + 58.5 + 43.6 = 312.1
But: violates max_stops=4, must skip one.
```

**Route B: Skip MUSTANG (lowest priority)**
```
Start → FALCON → VULCAN → RANGER → SNAPDRAGON → End
Drive: 119 min  Service: 150 min  Total: 269 min
Priority captured: 76.5 + 67.0 + 66.5 + 58.5 = 268.5
Penalty paid: 43,600 (for skipping MUSTANG)
✓ Within budget, within max_stops
```

**Route C: Skip FALCON (saves most drive time)**
```
Start → RANGER → VULCAN → SNAPDRAGON → MUSTANG → End
Drive: ~95 min  Service: 135 min  Total: ~230 min
Priority captured: 76.5 + 67.0 + 66.5 + 43.6 = 253.6
Penalty paid: 58,500 (for skipping FALCON)
✓ Within budget, but lower priority captured
```

**Solver picks Route B** — it pays the smallest penalty (skips the lowest-priority well) and captures the most total priority.

### Output

```
Route: START → NMC FALCON → VLT VULCAN → CMC RANGER → CMC SNAPDRAGON → END

Schedule:
  Stop 1: NMC FALCON      ETA 08:43  Priority 58.5  "Significant uplift; overdue 42d"
  Stop 2: VLT VULCAN      ETA 09:44  Priority 67.0  "Significant uplift; overdue 35d"
  Stop 3: CMC RANGER      ETA 10:40  Priority 76.5  "High production; urgent issues"
  Stop 4: CMC SNAPDRAGON  ETA 11:42  Priority 66.5  "High production; water cut trend"

Total: 269 of 420 min | Priority captured: 268.5 | Skipped: SMC MUSTANG (43.6)
```

---

## Why Disjunction Penalties Work

The mathematical equivalence:

```
minimize(travel_cost + Σ penalties_for_skipped)
= minimize(travel_cost + Σ all_priorities - Σ visited_priorities)
= minimize(travel_cost - Σ visited_priorities) + constant
≈ maximize(Σ visited_priorities - travel_cost)
```

This is exactly the orienteering objective: maximize collected value while minimizing travel cost under a budget constraint.

---

## Constraint Summary

| Constraint | How Enforced | Effect |
|---|---|---|
| Time budget | Time dimension capacity | Route total (drive+service) cannot exceed budget |
| Max stops | Count dimension capacity | At most N wells visited |
| Must-visit | Disjunction penalty = 10M | Solver never skips these (cost of skipping > any route) |
| Min priority threshold | Pre-filter before solver | Wells below threshold removed from candidate set |

---

## Determinism

The solver produces identical results for identical inputs because:
1. **First solution strategy**: `PATH_CHEAPEST_ARC` — deterministic greedy initial route
2. **Metaheuristic**: `GUIDED_LOCAL_SEARCH` — deterministic neighborhood exploration
3. **Time limit**: Fixed at 5 seconds — same search depth each run
4. No random restarts or stochastic components

---

## Scaling Considerations

| Wells | Solver Behavior |
|---|---|
| 1–20 | Optimal solution found in <1 second |
| 20–50 | Near-optimal in 2–5 seconds |
| 50–100 | Good feasible solution in 5 seconds; may not be globally optimal |
| 100+ | Consider pre-clustering by geography before solving |

For the MVP (single engineer, single day), 20–50 wells is the typical input size.
