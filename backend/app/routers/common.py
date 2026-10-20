"""Shared request models for the /solve and /scenarios routers -- both need
the same scenario inputs (separation dates, SS claiming ages, a live Tiller
pull target), differing only in whether the result gets a name and gets
persisted."""

from datetime import date
from sqlite3 import Connection
from typing import Literal

from pydantic import BaseModel

from app.assumptions import household
from app.owl_integration.case_builder import ScenarioParams

Owner = Literal["self", "spouse"]


class ScenarioRequest(BaseModel):
    spreadsheet_id: str
    separation_date: dict[Owner, date | None] = {}
    ss_claiming_age: dict[Owner, float | None] = {}
    include_catchup_contributions: dict[Owner, bool] = {}
    objective: Literal["maxSpending", "maxBequest"] = "maxSpending"
    bequest: float = 0
    force_recalculate: bool = False

    def to_scenario_params(self, conn: Connection) -> ScenarioParams:
        """separation_age is no longer client-supplied -- it's derived here
        from each owner's date_of_birth + the chosen separation_date, so the
        UI only ever has to show one number, not two that could disagree."""
        separation_age: dict[str, float | None] = {}
        for owner, sep_date in self.separation_date.items():
            if sep_date is None:
                separation_age[owner] = None
                continue
            member = household.get_household_member(conn, owner)
            separation_age[owner] = household.age_on(member["date_of_birth"], sep_date) if member else None

        return ScenarioParams(
            separation_date=dict(self.separation_date),
            separation_age=separation_age,
            ss_claiming_age=dict(self.ss_claiming_age),
            include_catchup_contributions=dict(self.include_catchup_contributions),
            objective=self.objective,
            bequest=self.bequest,
        )


class SaveScenarioRequest(ScenarioRequest):
    name: str
    description: str | None = None
