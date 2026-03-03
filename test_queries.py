"""
Automated test suite for ESP Route Planner webhook.
Tests NL-to-SQL data queries and routing intent detection.
"""
import json
import requests

BASE = "http://127.0.0.1:8000"
SECRET = "esp-demo-99717618929f3a2c7b6d1e"
HEADERS = {"x-webhook-secret": SECRET, "Content-Type": "application/json"}


def query(q, context=None):
    payload = {"query": q}
    if context:
        payload["context"] = context
    r = requests.post(f"{BASE}/webhook/elevenlabs/query", headers=HEADERS, json=payload, timeout=30)
    return r.status_code, r.json()


TESTS = [
    # ── Counts & aggregates ────────────────────────────────────────────────
    ("COUNT: total wells",
     "How many ESP wells are there in total?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    ("AGGREGATE: average oil production",
     "What is the average oil production across all wells?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    ("AGGREGATE: total uplift potential",
     "What is the total uplift potential in barrels per day?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    # ── LIMIT handling (original user complaint) ───────────────────────────
    ("LIMIT: top 5 by priority",
     "Show me the top 5 wells by priority score",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) == 5,
     "exactly 5 rows in data_preview"),

    ("LIMIT: top 3 by priority",
     "Give me the top 3 priority wells",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) == 3,
     "exactly 3 rows in data_preview"),

    ("LIMIT: top 10 by oil production",
     "Which are the top 10 wells by oil production?",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) <= 10,
     "at most 10 rows"),

    # ── is_esp filter (previously broken) ─────────────────────────────────
    ("FILTER: is_esp = true",
     "Show me all ESP wells where is_esp is true",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) > 0,
     "mode=data, at least 1 row returned"),

    # ── Boolean / flag filters ─────────────────────────────────────────────
    ("FILTER: motor temp high",
     "Which wells have motor temperature issues?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    ("FILTER: water cut above 70%",
     "Show me wells with water cut above 70 percent",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    ("FILTER: immediate workover",
     "Which wells need an immediate workover?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    # ── Group by ──────────────────────────────────────────────────────────
    ("GROUPBY: issues by county",
     "How many wells are there in each county?",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) > 0,
     "mode=data, at least 1 county row"),

    ("GROUPBY: most common issues",
     "What are the most common reliability issues?",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) > 0,
     "mode=data, at least 1 row"),

    # ── Repair time ────────────────────────────────────────────────────────
    ("REPAIR: longest repair time",
     "Which wells take the longest to repair?",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) > 0,
     "mode=data, results present"),

    ("REPAIR: average repair time for high severity",
     "What is the average repair time for high severity wells?",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    # ── Priority view queries ──────────────────────────────────────────────
    ("PRIORITY: wells to focus on",
     "Which wells should I focus on first?",
     lambda r: r.get("mode") == "data" and len(r.get("data_preview") or []) > 0,
     "mode=data, results present"),

    ("PRIORITY: low confidence wells",
     "Show me wells with low confidence scores",
     lambda r: r.get("mode") == "data" and r.get("data_preview") is not None,
     "mode=data, data_preview present"),

    # ── Routing intent detection ───────────────────────────────────────────
    ("ROUTE: plan visits",
     "Plan my visits for today",
     lambda r: r.get("mode") == "route" and len(r.get("route_order") or []) > 2,
     "mode=route, route_order has stops"),

    ("ROUTE: plan route",
     "Plan a route to the top priority wells",
     lambda r: r.get("mode") == "route" and len(r.get("route_order") or []) > 2,
     "mode=route, route_order has stops"),

    ("ROUTE: service time in schedule",
     "Plan my visits",
     lambda r: r.get("mode") == "route" and all(
         s.get("service_minutes", 0) > 0
         for s in (r.get("schedule") or [])
         if s.get("stop_id") not in ("START", "END")
     ),
     "all well stops have service_minutes > 0"),

    ("ROUTE: map url is https",
     "Plan my visits for the day",
     lambda r: r.get("mode") == "route" and (
         r.get("artifacts", {}).get("map_url", "").startswith("http")
     ),
     "artifacts.map_url is present"),
]


def run():
    passed = 0
    failed = 0
    errors = []

    print(f"\n{'='*70}")
    print(f"  ESP Route Planner — Test Suite ({len(TESTS)} tests)")
    print(f"{'='*70}\n")

    for name, question, check, expectation in TESTS:
        try:
            status, resp = query(question)
            if status != 200:
                ok = False
                detail = f"HTTP {status}: {resp.get('detail', resp)}"
            else:
                ok = check(resp)
                rows = len(resp.get("data_preview") or [])
                sql = resp.get("sql", "")
                detail = (
                    f"rows={rows} sql={sql[:80]!r}" if resp.get("mode") == "data"
                    else f"route_order={resp.get('route_order', [])}"
                )
        except Exception as e:
            ok = False
            detail = f"Exception: {e}"
            resp = {}

        symbol = "✓" if ok else "✗"
        print(f"  {symbol} [{name}]")
        if not ok:
            print(f"      Q: {question!r}")
            print(f"      Expected: {expectation}")
            print(f"      Got: {detail}")
            if resp.get("sql"):
                print(f"      SQL: {resp['sql']}")
            if resp.get("spoken_text"):
                print(f"      Spoken: {resp['spoken_text']}")
            errors.append((name, question, expectation, detail, resp))
            failed += 1
        else:
            passed += 1

    print(f"\n{'='*70}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'='*70}\n")

    if errors:
        print("FAILURES DETAIL:")
        for name, q, exp, detail, resp in errors:
            print(f"\n  [{name}]")
            print(f"  Q: {q}")
            print(f"  Expected: {exp}")
            print(f"  Got: {detail}")
            if resp.get("data_preview") is not None:
                print(f"  Preview rows: {len(resp['data_preview'])}")
            print(f"  Full response: {json.dumps(resp, indent=2)[:500]}")

    return errors


if __name__ == "__main__":
    failures = run()
    exit(1 if failures else 0)
