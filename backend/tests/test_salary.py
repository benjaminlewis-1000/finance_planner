"""salary.py: current_salary + annual_increase_pct projection, and the
high-3 estimate derived from it."""

from datetime import date

import pytest

from app.assumptions.salary import compute_high_3, get_salary_profile, project_salary, set_salary_profile
from app.db import get_connection, init_db


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_salary_profile_upsert(tmp_path):
    conn = make_conn(tmp_path)
    set_salary_profile(conn, owner="self", current_salary=100_000, annual_increase_pct=0.03)
    profile = get_salary_profile(conn, "self")
    assert profile["current_salary"] == 100_000
    assert profile["annual_increase_pct"] == 0.03

    set_salary_profile(conn, owner="self", current_salary=105_000, annual_increase_pct=0.025)
    profile = get_salary_profile(conn, "self")
    assert profile["current_salary"] == 105_000  # upsert, not a second row


def test_project_salary_compounds_from_this_year(tmp_path):
    conn = make_conn(tmp_path)
    set_salary_profile(conn, owner="self", current_salary=100_000, annual_increase_pct=0.03)
    profile = get_salary_profile(conn, "self")

    this_year = date.today().year
    assert project_salary(profile, this_year) == pytest.approx(100_000)
    assert project_salary(profile, this_year + 1) == pytest.approx(103_000)
    assert project_salary(profile, this_year + 3) == pytest.approx(100_000 * 1.03 ** 3)


def test_compute_high_3_averages_the_three_years_before_separation(tmp_path):
    conn = make_conn(tmp_path)
    set_salary_profile(conn, owner="self", current_salary=100_000, annual_increase_pct=0.03)
    profile = get_salary_profile(conn, "self")

    this_year = date.today().year
    separation_year = this_year + 5
    expected = sum(100_000 * 1.03 ** (y - this_year) for y in (separation_year - 3, separation_year - 2, separation_year - 1)) / 3
    assert compute_high_3(profile, separation_year) == pytest.approx(expected)


def test_zero_increase_is_flat_salary(tmp_path):
    conn = make_conn(tmp_path)
    set_salary_profile(conn, owner="self", current_salary=90_000, annual_increase_pct=0)
    profile = get_salary_profile(conn, "self")

    this_year = date.today().year
    assert project_salary(profile, this_year + 10) == pytest.approx(90_000)
    assert compute_high_3(profile, this_year + 10) == pytest.approx(90_000)
