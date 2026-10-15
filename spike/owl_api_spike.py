"""
Throwaway spike to confirm the real owlplanner API before writing backend code.

Builds a two-person household case entirely in Python (no TOML, no Excel file
on disk) using a tiny FERS pension calculation for one spouse, and runs a solve.

Findings this confirmed (see plan file for how they shape case_builder.py):
- Plan(inames, dobs, life_expectancies, name) is the constructor.
- setAccountBalances(taxable=, taxDeferred=, taxFree=, startDate=) takes one
  value per individual, in $k by default -- BUT this is not a plan-wide
  convention: "anticipated wages" (HFP columns, confirmed against Owl's own
  examples/HFP_john+sally.xlsx, e.g. 100000.0 for a $100k salary) and
  setPension's `amounts` ("Units are in $.", per its own docstring) are both
  whole dollars. case_builder.py must track which Owl call expects $k vs $
  per-call -- do not assume a single plan-wide unit convention.
- readHFP(filename=<dict of per-individual DataFrames>) is the programmatic
  path -- no Excel file on disk needed, just pass a dict instead of a path.
  It does the year-padding/zero-fill normalization (via hfp_io) that raw
  setContributions() does NOT do on its own; only "year" is required per row,
  every other recognized column/year defaults to zero if absent.
- setPension(amounts, ages, indexed=, survivor_fraction=) takes a
  pre-computed MONTHLY $ benefit per individual -- Owl does not compute FERS
  tiered multipliers itself, confirming case_builder.py must call our own
  pension_calc.py first and hand the result in.
- setSocialSecurity(pias, ages) similarly takes already-computed monthly PIAs.
- solve('maxSpending' | 'maxBequest', options={...}) runs the MILP.
"""

from datetime import date

import pandas as pd

import owlplanner as owl


def fers_annual_pension(service_start: date, separation_date: date, high_3_salary: float,
                         separation_age: int) -> float:
    """
    Minimal FERS benefit calc for spike purposes only -- the real version
    (with MRA+10 reduction, editable params table) is pension_calc.py, built
    in a later step per the plan.
    """
    years_of_service = (separation_date - service_start).days / 365.25
    if separation_age >= 62 and years_of_service >= 20:
        multiplier = 0.011
    else:
        multiplier = 0.01
    return high_3_salary * multiplier * years_of_service


def main():
    inames = ["Ben", "Jessica"]
    dobs = ["1988-06-01", "1989-09-01"]
    life_expectancy = [92, 92]

    plan = owl.Plan(inames, dobs, life_expectancy, "spike-household", verbose=True)

    # --- account balances, $k, split by Owl's three tax buckets ---
    plan.setAccountBalances(
        taxable=[80, 0],
        taxDeferred=[320, 85],
        taxFree=[135, 120],
        startDate="01-01",
    )

    # --- wages: programmatic path via setContributions, no Excel file ---
    this_year = date.today().year
    # HFP "anticipated wages" is in whole dollars, confirmed against Owl's own
    # examples/HFP_john+sally.xlsx (100000.0 for a $100k salary) -- NOT $k.
    ben_wages = pd.DataFrame({
        "year": [this_year, this_year + 1, this_year + 2],
        "anticipated wages": [140_000, 145_000, 150_000],
    })
    jessica_wages = pd.DataFrame({
        "year": [this_year, this_year + 1, this_year + 2],
        "anticipated wages": [95_000, 98_000, 100_000],
    })
    # readHFP (not setContributions directly) does the year-padding/zero-fill
    # normalization (via hfp_io), and accepts a dict of DataFrames in place of
    # a file path -- confirmed by tracing plan.py/hfp_io.py.
    plan.readHFP({"Ben": ben_wages, "Jessica": jessica_wages})

    # --- FERS pension for Ben: computed by us, fed to Owl as a flat $ amount ---
    separation_date = date(this_year + 3, 1, 1)
    service_start = date(2016, 12, 11)
    annual_pension = fers_annual_pension(
        service_start, separation_date, high_3_salary=145_000, separation_age=57,
    )
    monthly_pension = annual_pension / 12  # setPension wants whole dollars, not $k
    plan.setPension(
        amounts=[monthly_pension, 0],
        ages=[57, 65],
        indexed=[False, False],
        survivor_fraction=[0.5, 0],
    )
    print(f"Computed FERS annual pension for Ben: ${annual_pension:,.0f}")

    # --- social security ---
    plan.setSocialSecurity([2800, 1900], [67, 67])

    plan.setAllocationRatios(
        "individual",
        generic=[[[70, 30, 0, 0], [70, 30, 0, 0]], [[70, 30, 0, 0], [70, 30, 0, 0]]],
    )
    plan.setSpendingProfile("flat")
    plan.setRates("historical average", 1990, 2023)

    result = plan.solve("maxSpending", options={"bequest": 0})
    print("Solve status / result object type:", type(result))

    print("\n--- summary ---")
    plan.summary()


if __name__ == "__main__":
    main()
