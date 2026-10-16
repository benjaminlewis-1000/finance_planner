"""Shared request models for the /solve and /scenarios routers -- both need
the same scenario inputs (separation dates/ages, SS claiming ages, a live
Tiller pull target), differing only in whether the result gets a name and
gets persisted."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.owl_integration.case_builder import ScenarioParams

Owner = Literal["self", "spouse"]


class ScenarioRequest(BaseModel):
    spreadsheet_id: str
    separation_date: dict[Owner, date | None] = {}
    separation_age: dict[Owner, float | None] = {}
    ss_claiming_age: dict[Owner, float | None] = {}
    objective: Literal["maxSpending", "maxBequest"] = "maxSpending"
    bequest: float = 0
    force_recalculate: bool = False

    def to_scenario_params(self) -> ScenarioParams:
        return ScenarioParams(
            separation_date=dict(self.separation_date),
            separation_age=dict(self.separation_age),
            ss_claiming_age=dict(self.ss_claiming_age),
            objective=self.objective,
            bequest=self.bequest,
        )


class SaveScenarioRequest(ScenarioRequest):
    name: str
    description: str | None = None
