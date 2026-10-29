"""
Runs the owlplanner solve, cached by a hash of the inputs that actually
affect the result.

The spike measured ~2-3s wall clock for a 2-person, 55-year horizon solve --
not slow enough that caching is load-bearing for usability, but still worth
avoiding on every keystroke while someone's mid-edit in the UI, and it's what
backs a saved scenario's "show me exactly what I saw before, instantly"
behavior described in the build plan.

solve() itself returns None -- confirmed directly against the installed
owlplanner (2026.10.10), not guessed. Status lives on plan.caseStatus after
the call. Structured results come from plan.summaryDic() (human-labeled
key/value pairs) and plan.metricsDict() (stable snake_case keys, plain
floats) -- both confirmed JSON-serializable, so the cache stores those
instead of scraping summary()'s printed text.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from app.owl_integration.case_builder import ScenarioParams, build_plan
from app.owl_integration.results import yearly_breakdown


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_cache_key(pulled_accounts: list[dict], scenario: ScenarioParams, assumptions_fingerprint: dict) -> str:
    """Hashes everything that can change the solve result: current account
    balances, the scenario's separation/claiming ages, and a fingerprint of
    the stored assumptions (salary steps, events, profiles, pension params)
    the caller is responsible for assembling -- case_builder.py's inputs."""
    payload = {
        "pulled_accounts": sorted(
            (row.get("Account Id", ""), row.get("Last Balance", "")) for row in pulled_accounts
        ),
        "separation_date": {k: (v.isoformat() if v else None) for k, v in scenario.separation_date.items()},
        "separation_age": scenario.separation_age,
        "ss_claiming_age": scenario.ss_claiming_age,
        "include_catchup_contributions": scenario.include_catchup_contributions,
        "objective": scenario.objective,
        "bequest": scenario.bequest,
        "allow_roth_conversions": scenario.allow_roth_conversions,
        "tithing_pct": scenario.tithing_pct,
        "assumptions_fingerprint": assumptions_fingerprint,
    }
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def solve_scenario(conn: sqlite3.Connection, pulled_accounts: list[dict], scenario: ScenarioParams,
                    assumptions_fingerprint: dict, force_recalculate: bool = False) -> dict:
    cache_key = compute_cache_key(pulled_accounts, scenario, assumptions_fingerprint)

    if not force_recalculate:
        cached = conn.execute(
            "SELECT result FROM solve_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if cached is not None:
            return {**json.loads(cached["result"]), "cache_key": cache_key, "from_cache": True}

    plan = build_plan(conn, pulled_accounts, scenario)
    # units=1 forces every dollar-valued solve option (bequest, netSpending,
    # maxRothConversion, ...) to be read as whole dollars. Confirmed directly
    # against a real solve: without it, Owl reads these in $k, so a bequest of
    # 500_000 (meant as $500k) was silently solved as a $500M target -- which
    # is why it came back "infeasible" instead of the sane answer it should
    # have given. g_n/T_n/etc. (the per-year result arrays) were never
    # affected -- those were always whole dollars, matching the HFP/pension/SS
    # convention case_builder.py already uses everywhere else.
    solve_options = {"bequest": scenario.bequest, "units": 1}
    if not scenario.allow_roth_conversions:
        # maxRothConversion caps each individual's annual conversion ($);
        # 0 disables conversions for everyone in one shot (noRothConversions
        # only excludes a single named individual, not the whole household).
        solve_options["maxRothConversion"] = 0
    plan.solve(scenario.objective, options=solve_options)

    result = {
        "case_status": plan.caseStatus,
        "metrics": plan.metricsDict(),
        "summary": plan.summaryDic(),
        "yearly": yearly_breakdown(plan) if plan.caseStatus == "solved" else [],
    }

    conn.execute(
        "INSERT INTO solve_cache (cache_key, result, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET result = excluded.result, created_at = excluded.created_at",
        (cache_key, json.dumps(result), _now()),
    )
    conn.commit()

    return {**result, "cache_key": cache_key, "from_cache": False}
