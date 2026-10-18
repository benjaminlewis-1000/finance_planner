"""
Thin Streamlit client against the FastAPI backend -- no logic lives here,
just forms and tables that call the routers built in step 6. Validates the
end-to-end flow (assumptions -> Tiller pull -> solve -> display) before
investing in the React/Vite build (step 8), per the build plan.
"""

import os
from datetime import date

import requests
import streamlit as st

from auth import logout_url, require_authelia_session

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8010")
DEFAULT_SPREADSHEET_ID = os.environ.get("TILLER_SPREADSHEET_ID", "")

st.set_page_config(page_title="Owl Retirement Dashboard", layout="wide")

# Verifies the session against Authelia directly, independent of whatever
# Cloudflare Access is (or isn't) doing in front of this -- see auth.py.
require_authelia_session()


# --- tiny API client ----------------------------------------------------------

def api(method: str, path: str, allow_statuses: tuple[int, ...] = (), **kwargs):
    """allow_statuses lets a caller treat a specific non-2xx status as a
    normal, silent "not found" rather than an error banner -- e.g. a
    profile that simply hasn't been created yet, not a real failure."""
    resp = requests.request(method, f"{BACKEND_URL}{path}", timeout=30, **kwargs)
    if not resp.ok:
        if resp.status_code in allow_statuses:
            return None
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        st.error(f"{method} {path} -> {resp.status_code}: {detail}")
        return None
    return resp.json()


def get_household_members() -> list[dict]:
    return api("GET", "/assumptions/household-members") or []


def _age_on(dob: date, as_of: date) -> float:
    """Mirrors household.age_on's day-count convention -- kept as plain
    arithmetic here (not a backend call) since it's a one-line formula, not
    FERS-specific logic that could drift from the backend."""
    return (as_of - dob).days / 365.25


def _default_separation_date(dob: date, retirement_age: int = 62) -> date:
    try:
        return dob.replace(year=dob.year + retirement_age)
    except ValueError:
        return dob.replace(year=dob.year + retirement_age, day=28)


# --- sections -------------------------------------------------------------------

def section_household():
    st.header("Household")
    st.caption("Name, date of birth, life expectancy -- needed to build the owlplanner case.")

    for owner, label in [("self", "You"), ("spouse", "Spouse")]:
        with st.form(f"household_{owner}"):
            st.subheader(label)
            existing = next((m for m in get_household_members() if m["owner"] == owner), None)
            display_name = st.text_input("Name", value=existing["display_name"] if existing else "")
            dob = st.date_input(
                "Date of birth",
                value=date.fromisoformat(existing["date_of_birth"]) if existing else date(1985, 1, 1),
            )
            life_expectancy = st.number_input(
                "Life expectancy (age)", value=existing["life_expectancy"] if existing else 92, min_value=1
            )
            if st.form_submit_button(f"Save {label}"):
                if not display_name:
                    st.warning("Name is required.")
                else:
                    api("PUT", f"/assumptions/household-member/{owner}", json={
                        "display_name": display_name, "date_of_birth": dob.isoformat(),
                        "life_expectancy": int(life_expectancy),
                    })
                    st.success(f"Saved {label}.")
                    st.rerun()


def section_assumptions():
    st.header("Assumptions")
    owners = [m["owner"] for m in get_household_members()]
    if not owners:
        st.info("Add at least yourself under Household before entering assumptions.")
        return

    st.subheader("Current salary & projected growth")
    st.caption(
        "Primary way to project future wages: current salary (this year's rate) "
        "compounded by an annual % increase every Jan 1 after that. Feeds both "
        "the solve's wage schedule and the pension page's high-3 estimate. If "
        "set for an owner, it takes over wage projection entirely -- manual "
        "salary steps below only apply to an owner with no profile set."
    )
    for owner in owners:
        existing_salary = api("GET", f"/assumptions/salary-profile/{owner}", allow_statuses=(404,))
        with st.form(f"salary_profile_{owner}"):
            st.markdown(f"**{owner}**")
            cols = st.columns(2)
            current_salary = cols[0].number_input(
                "Current salary ($/year)",
                value=float(existing_salary["current_salary"]) if existing_salary else 0.0,
                min_value=0.0, step=1000.0, key=f"cur_salary_{owner}",
            )
            increase_pct = cols[1].number_input(
                "Estimated annual increase (%)",
                value=float(existing_salary["annual_increase_pct"]) * 100 if existing_salary else 2.0,
                step=0.1, key=f"salary_pct_{owner}",
            )
            if st.form_submit_button(f"Save salary profile ({owner})"):
                api("PUT", f"/assumptions/salary-profile/{owner}", json={
                    "current_salary": current_salary, "annual_increase_pct": increase_pct / 100,
                })
                st.rerun()

    st.subheader("Salary steps (manual overrides)")
    steps = api("GET", "/assumptions/salary-steps") or []
    if steps:
        st.table(steps)
    with st.form("add_salary_step", clear_on_submit=True):
        cols = st.columns(4)
        owner = cols[0].selectbox("Owner", owners)
        effective_date = cols[1].date_input("Effective date")
        annual_amount = cols[2].number_input("Annual amount ($)", min_value=0.0, step=1000.0)
        note = cols[3].text_input("Note")
        if st.form_submit_button("Add salary step"):
            api("POST", "/assumptions/salary-steps", json={
                "owner": owner, "effective_date": effective_date.isoformat(),
                "annual_amount": annual_amount, "note": note or None,
            })
            st.rerun()
    if steps:
        delete_id = st.selectbox("Delete a salary step", [None] + [s["id"] for s in steps], key="del_salary")
        if delete_id and st.button("Delete salary step"):
            api("DELETE", f"/assumptions/salary-steps/{delete_id}")
            st.rerun()

    st.subheader("Expense changes")
    changes = api("GET", "/assumptions/expense-changes") or []
    if changes:
        st.table(changes)
    with st.form("add_expense_change", clear_on_submit=True):
        cols = st.columns(4)
        effective_date = cols[0].date_input("Effective date", key="exp_date")
        category = cols[1].text_input("Category")
        annual_amount_delta = cols[2].number_input("Annual amount delta ($)", step=500.0)
        note = cols[3].text_input("Note", key="exp_note")
        if st.form_submit_button("Add expense change"):
            api("POST", "/assumptions/expense-changes", json={
                "effective_date": effective_date.isoformat(), "category": category,
                "annual_amount_delta": annual_amount_delta, "note": note or None,
            })
            st.rerun()
    if changes:
        delete_id = st.selectbox("Delete an expense change", [None] + [c["id"] for c in changes], key="del_expense")
        if delete_id and st.button("Delete expense change"):
            api("DELETE", f"/assumptions/expense-changes/{delete_id}")
            st.rerun()
    st.caption(
        "Note: expense changes aren't wired into the solve yet -- Owl's spending "
        "model takes a household-level profile, not itemized deltas, and that "
        "mapping hasn't been confirmed against the real API yet."
    )

    st.subheader("One-off events")
    events = api("GET", "/assumptions/one-off-events") or []
    if events:
        st.table(events)
    with st.form("add_one_off_event", clear_on_submit=True):
        cols = st.columns(5)
        event_date = cols[0].date_input("Date", key="event_date")
        description = cols[1].text_input("Description")
        amount = cols[2].number_input("Amount ($, negative for an expense)", step=1000.0)
        category = cols[3].text_input("Category", key="event_category")
        event_owner = cols[4].selectbox("Owner", owners + (["joint"] if "spouse" in owners else []))
        if st.form_submit_button("Add one-off event"):
            api("POST", "/assumptions/one-off-events", json={
                "event_date": event_date.isoformat(), "description": description,
                "amount": amount, "category": category or None, "owner": event_owner,
            })
            st.rerun()
    if events:
        delete_id = st.selectbox("Delete a one-off event", [None] + [e["id"] for e in events], key="del_event")
        if delete_id and st.button("Delete one-off event"):
            api("DELETE", f"/assumptions/one-off-events/{delete_id}")
            st.rerun()


def section_pension_ss():
    st.header("Pension & Social Security")
    owners = [m["owner"] for m in get_household_members()]
    if not owners:
        st.info("Add at least yourself under Household first.")
        return

    st.subheader("FERS pension formula parameters (current)")
    params = api("GET", "/assumptions/pension-params")
    if params:
        st.json({k: v for k, v in params.items() if k not in ("id",)})
    with st.expander("Edit (adds a new versioned row -- doesn't erase history)"):
        with st.form("pension_params"):
            effective_date = st.date_input("Effective date", key="pp_date")
            multiplier_standard = st.number_input("Standard multiplier", value=0.01, format="%.4f")
            multiplier_age62_20yos = st.number_input("Enhanced multiplier (62+, 20+ YOS)", value=0.011, format="%.4f")
            reduction = st.number_input("MRA+10 reduction per year under 62", value=0.05, format="%.4f")
            note = st.text_input("Note", key="pp_note")
            if st.form_submit_button("Save new version"):
                api("PUT", "/assumptions/pension-params", json={
                    "effective_date": effective_date.isoformat(),
                    "multiplier_standard": multiplier_standard,
                    "multiplier_age62_20yos": multiplier_age62_20yos,
                    "mra_plus_10_reduction_per_year_under_62": reduction,
                    "note": note or None,
                })
                st.rerun()

    for owner in owners:
        st.subheader(f"Pension profile -- {owner}")
        existing = api("GET", f"/assumptions/pension-profile/{owner}", allow_statuses=(404,))
        has_pension = st.checkbox(
            f"{owner} has a FERS pension", value=existing is not None, key=f"has_pension_{owner}",
        )
        if not has_pension:
            if existing is not None:
                api("DELETE", f"/assumptions/pension-profile/{owner}")
                st.rerun()
            st.caption("No pension for this person.")
        else:
            with st.form(f"pension_profile_{owner}"):
                service_start_date = st.date_input(
                    "Service start date",
                    value=date.fromisoformat(existing["service_start_date"]) if existing else date(2016, 1, 1),
                    key=f"psd_{owner}",
                )
                high_3_salary = st.number_input(
                    "High-3 salary override ($, leave 0 to use the projected salary growth above)",
                    value=float(existing["high_3_salary"]) if existing and existing["high_3_salary"] else 0.0,
                    key=f"h3_{owner}",
                )
                mra_age = st.number_input("MRA age", value=float(existing["mra_age"]) if existing else 57.0, key=f"mra_{owner}")
                survivor_fraction = st.number_input(
                    "Survivor benefit fraction", value=float(existing["survivor_benefit_fraction"]) if existing else 0.0,
                    min_value=0.0, max_value=1.0, key=f"surv_{owner}",
                )
                if st.form_submit_button(f"Save pension profile ({owner})"):
                    api("PUT", f"/assumptions/pension-profile/{owner}", json={
                        "service_start_date": service_start_date.isoformat(),
                        "high_3_salary": high_3_salary or None,
                        "mra_age": mra_age, "survivor_benefit_fraction": survivor_fraction,
                    })
                    st.rerun()

            preview = api("GET", f"/assumptions/pension-preview/{owner}")
            if preview and preview.get("has_pension"):
                st.markdown("**Preliminary estimate** (assuming separation at DOB + 62, unless set otherwise under Solve/Scenarios)")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Years of service", f"{preview['years_of_service']:.1f}")
                c2.metric("High-3 salary used", f"${preview['high_3_salary_used']:,.0f}")
                c3.metric("Multiplier used", f"{preview['multiplier_used'] * 100:.2f}%")
                c4.metric("Est. annual pension", f"${preview['annual_pension']:,.0f}")
                st.caption(
                    f"Separation age {preview['separation_age']:.1f} on {preview['separation_date']} "
                    f"-- {preview['eligibility_note']}"
                )

        st.subheader(f"Social Security profile -- {owner}")
        existing_ss = api("GET", f"/assumptions/social-security-profile/{owner}", allow_statuses=(404,))
        with st.form(f"ss_profile_{owner}"):
            pia = st.number_input(
                "Estimated PIA at full retirement age ($/month)",
                value=float(existing_ss["estimated_pia_monthly"]) if existing_ss else 0.0, key=f"pia_{owner}",
            )
            fra = st.number_input(
                "Full retirement age", value=float(existing_ss["full_retirement_age"]) if existing_ss else 67.0,
                key=f"fra_{owner}",
            )
            if st.form_submit_button(f"Save Social Security profile ({owner})"):
                api("PUT", f"/assumptions/social-security-profile/{owner}", json={
                    "estimated_pia_monthly": pia, "full_retirement_age": fra,
                })
                st.rerun()


def section_tiller():
    st.header("Tiller accounts")

    status = api("GET", "/tiller/auth/status")
    if status:
        if status["authenticated"]:
            st.success("Google OAuth token is valid.")
        else:
            st.error(f"Not authenticated: {status['message']}")

    spreadsheet_id = st.text_input("Tiller spreadsheet ID", value=st.session_state.get("spreadsheet_id", DEFAULT_SPREADSHEET_ID))
    st.session_state["spreadsheet_id"] = spreadsheet_id

    if st.button("Pull & sync accounts") and spreadsheet_id:
        result = api("POST", "/tiller/accounts/sync", params={"spreadsheet_id": spreadsheet_id})
        if result:
            st.success(
                f"{len(result['new_accounts'])} new, {len(result['changed_accounts'])} changed, "
                f"{result['unchanged_count']} unchanged."
            )

    accounts = api("GET", "/tiller/accounts") or []
    if not accounts:
        st.info("No accounts yet -- pull & sync first.")
        return

    owners = [m["owner"] for m in get_household_members()] + ["joint"]

    needs_review = [a for a in accounts if a["needs_review"]]
    reviewed = [a for a in accounts if not a["needs_review"]]
    by_bucket = {
        bucket: [a for a in reviewed if a["owl_bucket"] == bucket]
        for bucket in ("taxable", "tax_deferred", "roth", "mixed", "excluded")
    }

    st.subheader(f"Needs review ({len(needs_review)})")
    if needs_review:
        for acct in needs_review:
            _account_row(acct, owners)
    else:
        st.caption("Nothing to review.")

    for bucket, label in [
        ("taxable", "Taxable"), ("tax_deferred", "Tax-deferred"), ("roth", "Roth"), ("mixed", "Mixed"),
    ]:
        group = by_bucket[bucket]
        with st.expander(f"{label} ({len(group)})", expanded=bool(group)):
            for acct in group:
                _account_row(acct, owners)

    with st.expander(f"Excluded ({len(by_bucket['excluded'])})", expanded=False):
        for acct in by_bucket["excluded"]:
            _account_row(acct, owners)


BUCKET_OPTIONS = ["excluded", "taxable", "tax_deferred", "roth", "mixed"]


def _account_row(acct: dict, owners: list[str]) -> None:
    with st.container(border=True):
        cols = st.columns([3, 2, 2, 2, 2])
        balance = acct.get("last_seen_balance")
        balance_str = f" -- ${balance:,.0f}" if balance is not None else ""
        cols[0].write(f"**{acct['display_name']}**{balance_str} ({acct['last_seen_institution']} / {acct['last_seen_type']})")
        bucket = cols[1].selectbox(
            "Bucket", BUCKET_OPTIONS, index=BUCKET_OPTIONS.index(acct["owl_bucket"]),
            key=f"bucket_{acct['account_id']}",
        )
        owner = cols[2].selectbox(
            "Owner", owners,
            index=owners.index(acct["owner"]) if acct["owner"] in owners else 0,
            key=f"owner_{acct['account_id']}",
        )
        if acct["needs_review"]:
            cols[3].warning("Needs review")
        else:
            cols[3].write("Reviewed")

        roth_fraction = None
        if bucket == "mixed":
            default_pct = (acct.get("mixed_roth_fraction") or 0.0) * 100
            roth_pct = st.slider(
                "% Roth (rest is tax-deferred)", 0, 100, int(default_pct),
                key=f"mixedpct_{acct['account_id']}",
            )
            roth_fraction = roth_pct / 100

        if cols[4].button("Save", key=f"save_{acct['account_id']}"):
            api("PATCH", f"/tiller/accounts/{acct['account_id']}", json={
                "owl_bucket": bucket, "owner": owner, "mixed_roth_fraction": roth_fraction,
            })
            st.rerun()


def _scenario_inputs(owners: list[str], key_prefix: str) -> dict:
    """Separation age is computed from date_of_birth + the chosen separation
    date, not typed -- the backend derives the same value from the same
    inputs when building the plan, so the two can't disagree."""
    members = {m["owner"]: m for m in get_household_members()}
    separation_date, ss_claiming_age = {}, {}
    for owner in owners:
        st.markdown(f"**{owner}**")
        dob = date.fromisoformat(members[owner]["date_of_birth"])
        cols = st.columns(3)
        sep_date = cols[0].date_input(
            "Separation date", value=_default_separation_date(dob), key=f"{key_prefix}_sepdate_{owner}",
        )
        cols[1].metric("Separation age", f"{_age_on(dob, sep_date):.1f}")
        ss_age = cols[2].number_input("SS claiming age", value=67.0, key=f"{key_prefix}_ssage_{owner}")
        separation_date[owner] = sep_date.isoformat()
        ss_claiming_age[owner] = ss_age

    objective = st.selectbox("Objective", ["maxSpending", "maxBequest"], key=f"{key_prefix}_objective")
    bequest = st.number_input("Bequest (today's $)", value=0.0, step=10000.0, key=f"{key_prefix}_bequest")

    return {
        "separation_date": separation_date,
        "ss_claiming_age": ss_claiming_age, "objective": objective, "bequest": bequest,
    }


def _show_solve_result(result: dict):
    st.write(f"Status: **{result['case_status']}**")
    st.caption("From cache" if result["from_cache"] else "Freshly solved")
    st.subheader("Key metrics")
    st.json(result["metrics"])


def section_solve():
    st.header("Solve")
    owners = [m["owner"] for m in get_household_members()]
    if not owners:
        st.info("Add at least yourself under Household first.")
        return

    spreadsheet_id = st.text_input(
        "Tiller spreadsheet ID", value=st.session_state.get("spreadsheet_id", DEFAULT_SPREADSHEET_ID), key="solve_sheet_id"
    )
    st.session_state["spreadsheet_id"] = spreadsheet_id

    scenario = _scenario_inputs(owners, "solve")
    force_recalculate = st.checkbox("Force recalculate (bypass cache)")

    if st.button("Run solve") and spreadsheet_id:
        result = api("POST", "/solve", json={
            **scenario, "spreadsheet_id": spreadsheet_id, "force_recalculate": force_recalculate,
        })
        if result:
            _show_solve_result(result)


def section_scenarios():
    st.header("Scenarios")
    owners = [m["owner"] for m in get_household_members()]

    st.subheader("Save a new scenario")
    if owners:
        spreadsheet_id = st.text_input(
            "Tiller spreadsheet ID", value=st.session_state.get("spreadsheet_id", DEFAULT_SPREADSHEET_ID), key="scenario_sheet_id"
        )
        name = st.text_input("Scenario name")
        description = st.text_input("Description")
        scenario = _scenario_inputs(owners, "scenario")
        if st.button("Save scenario") and name and spreadsheet_id:
            result = api("POST", "/scenarios", json={
                **scenario, "spreadsheet_id": spreadsheet_id, "name": name, "description": description or None,
            })
            if result:
                st.success(f"Saved '{name}'.")
                _show_solve_result(result["result"])

    st.subheader("Saved scenarios")
    scenarios = api("GET", "/scenarios") or []
    if not scenarios:
        st.info("No saved scenarios yet.")
        return

    st.table(scenarios)
    selected = st.selectbox("View a scenario", [s["name"] for s in scenarios])
    if selected:
        detail = api("GET", f"/scenarios/{selected}")
        if detail:
            _show_solve_result(detail["solve_result"])
            if st.button(f"Delete '{selected}'"):
                api("DELETE", f"/scenarios/{selected}")
                st.rerun()


# --- navigation -----------------------------------------------------------------

SECTIONS = {
    "Household": section_household,
    "Assumptions": section_assumptions,
    "Pension & Social Security": section_pension_ss,
    "Tiller accounts": section_tiller,
    "Solve": section_solve,
    "Scenarios": section_scenarios,
}

st.sidebar.title("Owl Retirement Dashboard")
choice = st.sidebar.radio("Section", list(SECTIONS.keys()))
st.sidebar.divider()
st.sidebar.link_button("Log out", logout_url())
SECTIONS[choice]()
