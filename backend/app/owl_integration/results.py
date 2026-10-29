"""
Per-year result series for the Solve page's charts, built from a solved
owlplanner Plan.

Owl's own export.py already computes per-year balance-sheet arrays (used for
its CSV/worksheet export); this module reuses that rather than re-deriving
balances, and pulls a few more raw per-year arrays (spending, taxes, income
sources) directly off the Plan object the same way plan_to_csv() does. All
values are nominal dollars, matching setPension/setSocialSecurity/HFP's
convention (not setAccountBalances's $k).

Only valid to call after a successful solve (plan.caseStatus == "solved").
"""

import numpy as np
from owlplanner import export as owl_export


def _marginal_federal_rate(plan, idx: int) -> float:
    """The federal bracket rate that applies to the next dollar of ordinary
    income in year idx, derived the same way plan.py's own LP bracket
    constraints are built: theta_tn[t, n] is bracket t's rate, DeltaBar_tn[t, n]
    its (inflation-adjusted) width, sigmaBar_n[n] the standard deduction --
    all populated on the plan object during solve()'s _adjustParameters call.
    Walking cumulative bracket widths against taxable ordinary income (G_n
    minus the deduction) finds which bracket that income falls into."""
    taxable_income = max(0.0, float(plan.G_n[idx]) - float(plan.sigmaBar_n[idx]))
    cumulative = 0.0
    for t in range(plan.N_t):
        cumulative += float(plan.DeltaBar_tn[t, idx])
        if taxable_income <= cumulative or t == plan.N_t - 1:
            return float(plan.theta_tn[t, idx])
    return float(plan.theta_tn[-1, idx])


def _effective_federal_rate(plan, idx: int) -> float:
    """Federal ordinary tax paid divided by federal taxable ordinary income
    (T_n / G_n) -- the blended average rate, as distinct from the marginal
    rate above. Kept on the same income base (G_n, federal-ordinary only,
    no LTCG/NIIT/state) as _marginal_federal_rate so the two are directly
    comparable rather than mixing in taxes that follow different schedules.

    plan.T_n is not pure bracket tax -- plan.py folds the flat 10% early
    (pre-59.5) withdrawal penalty (P_n) into it (`self.T_n += self.P_n`).
    That penalty isn't part of the marginal bracket schedule at all, so it
    has to be backed out here or a year with an early withdrawal reports an
    "effective" rate higher than the marginal rate, which is impossible for
    an actual blended-bracket average."""
    taxable_income = float(plan.G_n[idx])
    if taxable_income <= 0:
        return 0.0
    ordinary_tax = float(plan.T_n[idx]) - float(plan.P_n[idx])
    return ordinary_tax / taxable_income


def yearly_breakdown(plan) -> list[dict]:
    N = plan.N_n
    balances = owl_export.balance_sheet_arrays(plan, N=N)
    total_tax = plan.T_n + plan.U_n + plan.medicare_n + plan.J_n + plan.aca_costs_n + plan.st_T_n
    wages = np.sum(plan.omega_in, axis=0)
    pensions = np.sum(plan.piBar_in, axis=0)
    social_security = np.sum(plan.zetaBar_in, axis=0)
    roth_conversions = np.sum(plan.x_in, axis=0)

    rows = []
    for idx in range(N):
        rows.append({
            "year": int(plan.year_n[idx]),
            "taxable": float(balances["taxable"][idx]),
            "tax_deferred": float(balances["tax_deferred"][idx]),
            "tax_free": float(balances["tax_free"][idx]),
            "hsa": float(balances["hsa"][idx]),
            "total_assets": float(balances["total_assets"][idx]),
            "net_worth": float(balances["net_worth"][idx]),
            "liquid_net_worth": float(balances["liquid_net_worth"][idx]),
            "net_spending": float(plan.g_n[idx]),
            "total_tax": float(total_tax[idx]),
            "wages": float(wages[idx]),
            "pensions": float(pensions[idx]),
            "social_security": float(social_security[idx]),
            "roth_conversions": float(roth_conversions[idx]),
            "marginal_federal_rate": _marginal_federal_rate(plan, idx),
            "effective_federal_rate": _effective_federal_rate(plan, idx),
        })
    return rows
