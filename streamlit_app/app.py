"""
Thin Streamlit client against the FastAPI backend -- no logic lives here,
just forms and tables that call the routers built in step 6. Validates the
end-to-end flow (assumptions -> Tiller pull -> solve -> display) before
investing in the React/Vite build (step 8), per the build plan.
"""

import os
from datetime import date

import altair as alt
import pandas as pd
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

def api(method: str, path: str, allow_statuses: tuple[int, ...] = (), timeout: float = 30, **kwargs):
    """allow_statuses lets a caller treat a specific non-2xx status as a
    normal, silent "not found" rather than an error banner -- e.g. a
    profile that simply hasn't been created yet, not a real failure."""
    resp = requests.request(method, f"{BACKEND_URL}{path}", timeout=timeout, **kwargs)
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


def _show_contribution_projection(owner: str) -> None:
    """Year-by-year salary, IRS caps, and contribution amounts -- rows where
    a cap actually changed from the prior year are highlighted, since real
    caps move in lumpy, infrequent steps rather than every year."""
    st.markdown(f"**{owner} -- year-by-year projection**")
    cols = st.columns(2)
    num_years = cols[0].slider("Years to project", min_value=5, max_value=40, value=15, key=f"proj_years_{owner}")
    include_catchup = cols[1].checkbox(
        "Include catch-up contributions", value=False, key=f"proj_catchup_{owner}",
    )

    data = api("GET", f"/assumptions/contribution-projection/{owner}", allow_statuses=(400,), params={
        "num_years": num_years, "include_catchup": include_catchup,
    })
    if not data:
        st.caption("Set a salary profile above to see this.")
        return
    if not data["has_contribution_profile"]:
        st.caption("No contribution profile set -- showing salary and IRS caps only.")

    df = pd.DataFrame(data["rows"]).set_index("year")
    cap_changed = df["tsp_cap_changed"] | df["ira_cap_changed"]

    st.caption("Salary")
    st.line_chart(df[["salary"]].rename(columns={"salary": "Salary"}))

    st.caption("Contribution limits & contribution amounts")
    chart_cols = {
        "tsp_cap": "TSP cap", "ira_cap": "IRA cap", "tsp_traditional": "TSP traditional",
        "tsp_roth": "TSP Roth", "agency_match": "Agency match (5%)",
        "ira_traditional": "IRA traditional", "ira_roth": "Roth IRA", "taxable": "Taxable",
    }
    st.line_chart(df[list(chart_cols)].rename(columns=chart_cols))

    table_cols = {"salary": "Salary", **chart_cols}
    display_df = df[list(table_cols)].rename(columns=table_cols)

    def _highlight_cap_change(row):
        # Explicit text color too -- a background-only style inherits the
        # theme's default text color, which is unreadable (white-on-light)
        # in dark mode.
        style = "background-color: #ffb300; color: #000000" if cap_changed.loc[row.name] else ""
        return [style] * len(row)

    st.dataframe(
        display_df.style.apply(_highlight_cap_change, axis=1).format("${:,.0f}"),
        use_container_width=True,
    )
    if cap_changed.any():
        changed_years = ", ".join(str(y) for y in df.index[cap_changed])
        st.caption(f"Highlighted rows: a contribution cap increased that year -- {changed_years}.")


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

    st.subheader("Ongoing retirement contributions")
    st.caption(
        "Without this, the solve assumes zero ongoing contributions to TSP/IRA/taxable "
        "during working years -- balances only grow by market return, not by what you "
        "actually keep putting in. TSP is % of salary (traditional + Roth share one IRS "
        "limit); IRA and taxable are flat $/year. Agency TSP match is computed "
        "automatically from the FERS formula (1% automatic + 100% match on the first 3% "
        "+ 50% on the next 2%), always into traditional TSP."
    )
    caps = api("GET", "/assumptions/contribution-caps")
    if caps:
        st.caption(
            f"Current IRS caps (editable below): elective deferral "
            f"${caps['elective_deferral_limit']:,.0f} (+${caps['elective_deferral_catchup_50']:,.0f} "
            f"catch-up 50+, +${caps['elective_deferral_catchup_60_63']:,.0f} super catch-up 60-63); "
            f"IRA ${caps['ira_limit']:,.0f} (+${caps['ira_catchup_50']:,.0f} catch-up 50+). "
            f"Projected to grow {caps['annual_growth_pct'] * 100:.1f}%/year in ${caps['increment']:,.0f} increments."
        )
    with st.expander("Edit IRS contribution caps (adds a new versioned row -- doesn't erase history)"):
        with st.form("contribution_caps"):
            cc_date = st.date_input("Effective date", key="cc_date")
            c1, c2 = st.columns(2)
            elective_limit = c1.number_input(
                "Elective deferral limit (TSP, traditional+Roth)",
                value=float(caps["elective_deferral_limit"]) if caps else 23500.0, step=500.0,
            )
            ira_limit_in = c2.number_input(
                "IRA limit (traditional+Roth)", value=float(caps["ira_limit"]) if caps else 7000.0, step=500.0,
            )
            catchup_50 = c1.number_input(
                "Elective deferral catch-up (50+)",
                value=float(caps["elective_deferral_catchup_50"]) if caps else 7500.0, step=500.0,
            )
            ira_catchup = c2.number_input(
                "IRA catch-up (50+)", value=float(caps["ira_catchup_50"]) if caps else 1000.0, step=500.0,
            )
            catchup_60_63 = st.number_input(
                "Elective deferral super catch-up (60-63)",
                value=float(caps["elective_deferral_catchup_60_63"]) if caps else 11250.0, step=500.0,
            )
            growth_pct = st.number_input(
                "Assumed annual cap growth (%)",
                value=float(caps["annual_growth_pct"]) * 100 if caps else 2.0, step=0.1,
            )
            increment = st.number_input(
                "Cap rounds to the nearest ($)", value=float(caps["increment"]) if caps else 500.0, step=100.0,
            )
            note = st.text_input("Note", key="cc_note")
            if st.form_submit_button("Save new version"):
                api("PUT", "/assumptions/contribution-caps", json={
                    "effective_date": cc_date.isoformat(), "elective_deferral_limit": elective_limit,
                    "elective_deferral_catchup_50": catchup_50, "elective_deferral_catchup_60_63": catchup_60_63,
                    "ira_limit": ira_limit_in, "ira_catchup_50": ira_catchup,
                    "annual_growth_pct": growth_pct / 100, "increment": increment, "note": note or None,
                })
                st.rerun()

    for owner in owners:
        existing_ctrb = api("GET", f"/assumptions/contribution-profile/{owner}", allow_statuses=(404,))
        st.markdown(f"**{owner}**")

        tsp_max = st.checkbox(
            "Max out TSP", value=bool(existing_ctrb["tsp_max"]) if existing_ctrb else False,
            key=f"tsp_max_{owner}",
            help="Contribute the full IRS cap each year as it grows, instead of a fixed %.",
        )
        if tsp_max:
            tsp_traditional_pct = tsp_roth_pct = 0.0  # unused while maxed out
            tsp_roth_share_pct = st.slider(
                "Roth share of the maxed-out TSP contribution (%)", 0, 100,
                int(float(existing_ctrb["tsp_roth_share"]) * 100) if existing_ctrb else 50,
                key=f"tsp_roth_share_{owner}",
            )
            tsp_roth_share = tsp_roth_share_pct / 100
        else:
            c1, c2 = st.columns(2)
            tsp_traditional_pct = c1.number_input(
                "TSP traditional (%)",
                value=float(existing_ctrb["tsp_traditional_pct"]) * 100 if existing_ctrb else 0.0,
                step=0.5, key=f"tsp_trad_{owner}",
            )
            tsp_roth_pct = c2.number_input(
                "TSP Roth (%)", value=float(existing_ctrb["tsp_roth_pct"]) * 100 if existing_ctrb else 0.0,
                step=0.5, key=f"tsp_roth_{owner}",
            )
            tsp_roth_share = float(existing_ctrb["tsp_roth_share"]) if existing_ctrb else 0.5  # unused, kept as-is

        ira_max = st.checkbox(
            "Max out IRA", value=bool(existing_ctrb["ira_max"]) if existing_ctrb else False,
            key=f"ira_max_{owner}",
            help="Contribute the full IRA cap each year as it grows, instead of a fixed $ amount. Works even with no salary (e.g. a spousal IRA).",
        )
        if ira_max:
            ira_traditional = ira_roth = 0.0  # unused while maxed out
            ira_roth_share_pct = st.slider(
                "Roth share of the maxed-out IRA contribution (%)", 0, 100,
                int(float(existing_ctrb["ira_roth_share"]) * 100) if existing_ctrb else 100,
                key=f"ira_roth_share_{owner}",
            )
            ira_roth_share = ira_roth_share_pct / 100
        else:
            c1, c2 = st.columns(2)
            ira_traditional = c1.number_input(
                "IRA traditional ($/year)",
                value=float(existing_ctrb["ira_traditional_annual"]) if existing_ctrb else 0.0,
                step=500.0, key=f"ira_trad_{owner}",
            )
            ira_roth = c2.number_input(
                "Roth IRA ($/year)", value=float(existing_ctrb["ira_roth_annual"]) if existing_ctrb else 0.0,
                step=500.0, key=f"ira_roth_{owner}",
            )
            ira_roth_share = float(existing_ctrb["ira_roth_share"]) if existing_ctrb else 1.0  # unused, kept as-is

        taxable_annual = st.number_input(
            "Taxable investing ($/year, no IRS cap)",
            value=float(existing_ctrb["taxable_annual"]) if existing_ctrb else 0.0,
            step=500.0, key=f"taxable_ctrb_{owner}",
        )
        if st.button(f"Save contribution profile ({owner})", key=f"save_ctrb_{owner}"):
            api("PUT", f"/assumptions/contribution-profile/{owner}", json={
                "tsp_traditional_pct": tsp_traditional_pct / 100, "tsp_roth_pct": tsp_roth_pct / 100,
                "tsp_max": tsp_max, "tsp_roth_share": tsp_roth_share,
                "ira_traditional_annual": ira_traditional, "ira_roth_annual": ira_roth,
                "ira_max": ira_max, "ira_roth_share": ira_roth_share,
                "taxable_annual": taxable_annual,
            })
            st.success("Saved.")

        preview = api("GET", f"/assumptions/contribution-preview/{owner}", allow_statuses=(400,), params={
            "tsp_traditional_pct": tsp_traditional_pct / 100, "tsp_roth_pct": tsp_roth_pct / 100,
            "tsp_max": tsp_max, "tsp_roth_share": tsp_roth_share,
            "ira_traditional_annual": ira_traditional, "ira_roth_annual": ira_roth,
            "ira_max": ira_max, "ira_roth_share": ira_roth_share,
            "taxable_annual": taxable_annual,
        })
        if preview and preview.get("has_contribution_profile"):
            st.caption(
                f"Live, from the fields above (not necessarily saved) -- this year on "
                f"${preview['salary_used']:,.0f} salary: TSP traditional "
                f"${preview['tsp_traditional']:,.0f}, TSP Roth ${preview['tsp_roth']:,.0f}, "
                f"agency match ${preview['agency_match']:,.0f}, IRA traditional "
                f"${preview['ira_traditional']:,.0f}, Roth IRA ${preview['ira_roth']:,.0f}, "
                f"taxable ${preview['taxable']:,.0f}."
            )

        _show_contribution_projection(owner)

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
            # Plain widgets, not st.form -- each edit reruns the page immediately
            # so the preview below updates live as you type, instead of only
            # after a Save click (and even then, only reflecting what got saved).
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
            if st.button(f"Save pension profile ({owner})", key=f"save_pension_{owner}"):
                api("PUT", f"/assumptions/pension-profile/{owner}", json={
                    "service_start_date": service_start_date.isoformat(),
                    "high_3_salary": high_3_salary or None,
                    "mra_age": mra_age, "survivor_benefit_fraction": survivor_fraction,
                })
                st.success("Saved.")

            preview = api("GET", f"/assumptions/pension-preview/{owner}", params={
                "service_start_date": service_start_date.isoformat(),
                "mra_age": mra_age,
                **({"high_3_salary": high_3_salary} if high_3_salary else {}),
            })
            if preview and preview.get("has_pension"):
                st.markdown("**Preliminary estimate** (live, from the fields above -- not necessarily saved; assumes separation at DOB + 62 unless set otherwise under Solve/Scenarios)")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Years of service", f"{preview['years_of_service']:.1f}")
                c2.metric("High-3 salary used", f"${preview['high_3_salary_used']:,.0f}")
                c3.metric("Multiplier used", f"{preview['multiplier_used'] * 100:.2f}%")
                c4.metric("Est. annual pension", f"${preview['annual_pension']:,.0f}")
                if preview.get("projected_final_salary") is not None:
                    st.caption(f"Projected final-year salary: ${preview['projected_final_salary']:,.0f}")
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
    separation_date, ss_claiming_age, include_catchup = {}, {}, {}
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
        include_catchup[owner] = st.checkbox(
            f"Assume {owner} makes age-based catch-up contributions (50+, and the 60-63 super catch-up)",
            value=False, key=f"{key_prefix}_catchup_{owner}",
        )

    objective = st.selectbox("Objective", ["maxSpending", "maxBequest"], key=f"{key_prefix}_objective")
    bequest = st.number_input("Bequest (today's $)", value=0.0, step=10000.0, key=f"{key_prefix}_bequest")
    allow_roth_conversions = st.checkbox(
        "Allow Roth conversions", value=False, key=f"{key_prefix}_allow_roth",
        help="Check to compare against a scenario with Roth conversions enabled.",
    )
    tithing_pct = st.number_input(
        "Tithing (% of wages after retirement contributions)", value=10.0, step=1.0,
        min_value=0.0, max_value=100.0, key=f"{key_prefix}_tithing",
    ) / 100

    return {
        "separation_date": separation_date,
        "ss_claiming_age": ss_claiming_age, "include_catchup_contributions": include_catchup,
        "objective": objective, "bequest": bequest, "allow_roth_conversions": allow_roth_conversions,
        "tithing_pct": tithing_pct,
    }


def _show_pension_estimates(owners: list[str], scenario: dict) -> None:
    """Same preview math as the Pension & Social Security page, but against
    the separation date actually chosen for this scenario rather than the
    DOB + 62 default."""
    any_shown = False
    for owner in owners:
        profile = api("GET", f"/assumptions/pension-profile/{owner}", allow_statuses=(404,))
        if profile is None:
            continue
        any_shown = True
        preview = api("GET", f"/assumptions/pension-preview/{owner}", params={
            "service_start_date": profile["service_start_date"],
            "mra_age": profile["mra_age"],
            "separation_date": scenario["separation_date"].get(owner),
            **({"high_3_salary": profile["high_3_salary"]} if profile["high_3_salary"] else {}),
        })
        if not preview or not preview.get("has_pension"):
            continue
        st.markdown(f"**{owner}** -- estimated pension at the chosen separation date")
        c1, c2, c3 = st.columns(3)
        c1.metric("Years of service", f"{preview['years_of_service']:.1f}")
        c2.metric("Est. annual pension", f"${preview['annual_pension']:,.0f}")
        c3.metric("Est. monthly pension", f"${preview['monthly_pension']:,.0f}")
        st.caption(
            f"Separation age {preview['separation_age']:.1f} -- {preview['eligibility_note']}"
        )
    if not any_shown:
        st.caption("No pension profile set for anyone in this household.")


def _separation_years(separation_date: dict) -> dict:
    return {owner: date.fromisoformat(iso).year for owner, iso in separation_date.items() if iso}


def _fixed_axis_chart(df: pd.DataFrame, columns: list[str], mark: str = "line"):
    """Altair chart with the x-axis locked to the plan's actual year range
    and the y-axis floored at 0 -- Streamlit's native line/area/bar charts
    allow free scroll-zoom/pan, which made it easy to zoom into a misleading
    slice of the data. No .interactive() call here is deliberate: it's what
    keeps the axes fixed instead of pannable/zoomable."""
    long_df = df[columns].reset_index().melt("year", var_name="series", value_name="value")
    x_domain = [int(df.index.min()), int(df.index.max())]

    if mark == "bar":
        x_enc = alt.X("year:O", axis=alt.Axis(title="Year"))
    else:
        x_enc = alt.X("year:Q", scale=alt.Scale(domain=x_domain, nice=False), axis=alt.Axis(format="d", title="Year"))

    y_enc = alt.Y(
        "value:Q",
        scale=alt.Scale(domainMin=0, nice=True),
        stack="zero" if mark == "area" else None,
        title=None,
    )
    color_enc = alt.Color("series:N", title=None)

    base = alt.Chart(long_df)
    chart = {"line": base.mark_line(), "area": base.mark_area(), "bar": base.mark_bar()}[mark]
    st.altair_chart(chart.encode(x=x_enc, y=y_enc, color=color_enc), use_container_width=True)


def _show_solve_result(result: dict, separation_years: dict | None = None):
    st.write(f"Status: **{result['case_status']}**")
    st.caption("From cache" if result["from_cache"] else "Freshly solved")

    yearly = result.get("yearly") or []
    if not yearly:
        st.info("No year-by-year data available for this solve.")
        with st.expander("Raw metrics / summary"):
            st.json(result["metrics"])
            st.json(result["summary"])
        return

    df = pd.DataFrame(yearly).set_index("year")

    retirement_year = min(separation_years.values()) if separation_years else None
    if retirement_year is not None and retirement_year in df.index:
        row = df.loc[retirement_year]
        st.subheader(f"At retirement ({retirement_year})")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total account balance", f"${row['total_assets']:,.0f}")
        c2.metric("Net worth", f"${row['net_worth']:,.0f}")
        c3.metric("Yearly spending", f"${row['net_spending']:,.0f}")
        c4.metric("Monthly spending", f"${row['net_spending'] / 12:,.0f}")
    elif retirement_year is not None:
        st.caption(f"Separation year {retirement_year} falls outside the solved horizon.")

    st.subheader("Account balances by year")
    _fixed_axis_chart(df, ["taxable", "tax_deferred", "tax_free", "hsa"], mark="area")

    st.subheader("Net worth by year")
    _fixed_axis_chart(df, ["net_worth", "liquid_net_worth"], mark="line")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Yearly spending")
        _fixed_axis_chart(df, ["net_spending"], mark="bar")
    with col2:
        st.subheader("Yearly taxes")
        _fixed_axis_chart(df, ["total_tax"], mark="bar")

    has_conversions = (df["roth_conversions"] > 0).any()

    st.subheader("Marginal vs. effective federal tax rate")
    rate_df = df[["marginal_federal_rate", "effective_federal_rate"]].rename(columns={
        "marginal_federal_rate": "marginal rate (next dollar)",
        "effective_federal_rate": "effective rate (avg. on ordinary income)",
    }) * 100
    _fixed_axis_chart(rate_df, list(rate_df.columns), mark="line")
    st.caption(
        "Marginal is the rate on your *next* dollar of ordinary income -- what a Roth conversion "
        "decision actually hinges on. Effective is total federal tax paid over total taxable "
        "ordinary income -- the blended average people usually mean by \"my tax bracket.\" "
        "Effective is always lower than marginal; the gap between them is why a year can look "
        "fine \"on average\" while the marginal cost of one more dollar withdrawn is high."
    )

    if has_conversions:
        st.subheader("Roth conversions by year")
        _fixed_axis_chart(df, ["roth_conversions"], mark="bar")

        conversion_years = df[df["roth_conversions"] > 0]
        avg_conversion_rate = conversion_years["marginal_federal_rate"].mean()
        later_years = df[df.index > conversion_years.index.max()]
        peak_future_marginal = (later_years if not later_years.empty else df)["marginal_federal_rate"].max()
        peak_future_effective = (later_years if not later_years.empty else df)["effective_federal_rate"].max()
        if peak_future_marginal > avg_conversion_rate:
            st.caption(
                f"Why Owl converts: your modeled marginal federal rate during these conversion "
                f"years averages ~{avg_conversion_rate * 100:.0f}%, versus a projected marginal "
                f"peak of ~{peak_future_marginal * 100:.0f}% later once RMDs, pension, and Social "
                f"Security stack up -- even though the *effective* rate in that later peak year "
                f"(~{peak_future_effective * 100:.0f}%) may look unremarkable on its own. Paying "
                f"tax on the conversion now, at the lower marginal rate, beats paying it later at "
                f"the higher one. (Owl also weighs IRMAA/Medicare, NIIT, and ACA-subsidy effects "
                f"beyond the plain bracket shown here.)"
            )
        else:
            st.caption(
                f"Roth conversions occurred, but the modeled marginal rate during those years "
                f"(~{avg_conversion_rate * 100:.0f}%) isn't clearly lower than the later marginal "
                f"peak (~{peak_future_marginal * 100:.0f}%) -- the benefit is likely coming from "
                f"avoiding future RMDs, IRMAA, or NIIT effects rather than pure bracket arbitrage."
            )
        st.caption("Uncheck \"Allow Roth conversions\" above to compare against a no-conversion scenario.")
        st.caption("Uncheck \"Allow Roth conversions\" above to compare against a no-conversion scenario.")

    with st.expander("Year-by-year table"):
        st.dataframe(df)
    with st.expander("Raw metrics / summary"):
        st.json(result["metrics"])
        st.json(result["summary"])


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

    st.subheader("Estimated pension")
    _show_pension_estimates(owners, scenario)

    force_recalculate = st.checkbox("Force recalculate (bypass cache)")

    if st.button("Run solve") and spreadsheet_id:
        result = api("POST", "/solve", json={
            **scenario, "spreadsheet_id": spreadsheet_id, "force_recalculate": force_recalculate,
        })
        if result:
            _show_solve_result(result, _separation_years(scenario["separation_date"]))


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
                _show_solve_result(result["result"], _separation_years(scenario["separation_date"]))

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
            saved_separation_date = (detail.get("case_params_snapshot") or {}).get("separation_date", {})
            _show_solve_result(detail["solve_result"], _separation_years(saved_separation_date))
            if st.button(f"Delete '{selected}'"):
                api("DELETE", f"/scenarios/{selected}")
                st.rerun()


def section_system():
    st.header("System")
    st.subheader("owlplanner version")

    status = api("GET", "/system/owlplanner/status")
    if not status:
        return

    c1, c2 = st.columns(2)
    c1.metric("Installed", status["installed_version"])
    c2.metric("Latest on PyPI", status["latest_version"])

    if status["up_to_date"]:
        st.success("Already on the latest owlplanner release.")
        return

    st.info(
        f"A newer owlplanner ({status['latest_version']}) is available. Testing it won't change "
        f"anything running right now -- it installs into a throwaway virtualenv, runs the real "
        f"backend test suite against it, and discards the result. This can take a few minutes."
    )
    if st.button("Test upgrade"):
        with st.spinner(f"Installing owlplanner {status['latest_version']} into a scratch venv and running the test suite..."):
            result = api("POST", "/system/owlplanner/test-upgrade", timeout=900)
        if result:
            st.session_state["owlplanner_test_result"] = result

    result = st.session_state.get("owlplanner_test_result")
    if result and result.get("ran"):
        if result["test_passed"]:
            st.success(
                f"owlplanner {result['latest_version']} passed the full test suite in "
                f"{result['test_duration_seconds']:.0f}s. Safe to apply manually: bump the pin in "
                f"backend/requirements.txt to {result['latest_version']}, then "
                f"`docker compose up -d --build backend streamlit`."
            )
        else:
            st.error(
                f"owlplanner {result['latest_version']} failed the test suite "
                f"(exit code {result['test_returncode']}) after {result['test_duration_seconds']:.0f}s. "
                f"Nothing running was touched -- the running app is still on "
                f"{result['installed_version']}. Review the output below before upgrading manually."
            )
        with st.expander("Test output"):
            st.code(result["test_output"])


# --- navigation -----------------------------------------------------------------

SECTIONS = {
    "Household": section_household,
    "Assumptions": section_assumptions,
    "Pension & Social Security": section_pension_ss,
    "Tiller accounts": section_tiller,
    "Solve": section_solve,
    "Scenarios": section_scenarios,
    "System": section_system,
}

st.sidebar.title("Owl Retirement Dashboard")
choice = st.sidebar.radio("Section", list(SECTIONS.keys()))
st.sidebar.divider()
st.sidebar.link_button("Log out", logout_url())
SECTIONS[choice]()
