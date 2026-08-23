"""The routine's step table, and the job on GitHub that selects from it.

Most of what is here ties the workflow file to the Python it calls. A YAML file
is not checked by anything until it runs, and this one runs on a schedule with
nobody watching — so a slug renamed in `routine.py` would go unnoticed until a
round went unrecorded, which is the exact failure the job exists to prevent.
These tests turn that into a red test suite instead.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "registar-previsoes.yml"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def routine():
    return load_script("routine")


@pytest.fixture(scope="module")
def settle():
    return load_script("record_projection").settle_wrote_anything


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def selected_steps(workflow) -> list[str]:
    """The slugs the job passes to --only."""
    for step in workflow["jobs"]["ledger"]["steps"]:
        run = step.get("run", "")
        if "--only" in run:
            after = run.split("--only", 1)[1].split()
            return [word for word in after if not word.startswith("-")]
    raise AssertionError("the job no longer selects steps with --only")


def test_slugs_are_unique(routine):
    slugs = [step.slug for step in routine.STEPS]
    assert len(slugs) == len(set(slugs))


def test_the_job_selects_steps_that_exist(routine, workflow):
    """A renamed slug fails here rather than in a silent 07:00 job."""
    known = {step.slug for step in routine.STEPS}
    assert set(selected_steps(workflow)) <= known


def test_the_job_records_and_settles(workflow):
    """Recording is the whole reason the job exists; settling is why it helps."""
    assert selected_steps(workflow) == ["registar", "liquidar"]


def test_only_the_private_page_wants_the_league(routine):
    wanting = {step.slug for step in routine.STEPS if step.needs_league}
    assert wanting == {"privada"}


def test_the_job_runs_nothing_that_reads_the_private_league(routine, workflow):
    """The guid grants read access to twenty-nine other people's results.

    It is not in this job's environment and no step it runs would use it, and
    both halves of that need to stay true: a step added to the selection later
    could quietly turn a job that holds no secret into one that needs one.
    """
    by_slug = {step.slug: step for step in routine.STEPS}
    assert not any(by_slug[slug].needs_league for slug in selected_steps(workflow))


def test_the_job_holds_no_secret():
    """Nothing in the file may reach for the secret store."""
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_the_job_commits_only_the_ledger():
    """It stages one path, and refuses outright if anything else is dirty."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git add -- data/projections.json" in text
    staged = [
        line for line in text.splitlines() if line.strip().startswith("git add")
    ]
    assert staged == ["          git add -- data/projections.json"]


def test_the_job_asks_for_no_more_rights_than_it_needs(workflow):
    assert workflow["permissions"] == {"contents": "write"}


def test_the_job_is_scheduled(workflow):
    # PyYAML reads a bare `on:` as the boolean True — YAML 1.1, and the reason
    # this looks for a key that is not a string.
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "schedule" in triggers
    assert len(triggers["schedule"]) >= 2, "one scheduled run is one point of failure"
    assert "workflow_dispatch" in triggers, "it must be runnable by hand"


def test_two_runs_never_write_the_ledger_at_once(workflow):
    assert workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is False


# --- The ledger has two writers, so a no-op must write nothing ----------------
#
# This is the bug that got past everything else. Settling stamped `settled_at`
# on every run, including the overwhelming majority that settle nothing because
# the clubs have not played yet. Alone on a laptop that was invisible. With a
# second writer on GitHub it is corrosive: both sides dirty the same file on
# every run, the laptop's `git pull --ff-only` refuses forever, and two ledgers
# drift apart while every run on both sides reports success.


def test_a_settle_that_settles_nothing_writes_nothing(settle):
    assert not settle(
        settled=0, coach_settled=False, pending=["Zaidu"], was_fully_settled=False
    )


def test_a_settled_player_is_worth_writing(settle):
    assert settle(
        settled=1, coach_settled=False, pending=["Zaidu"], was_fully_settled=False
    )


def test_a_settled_coach_is_worth_writing(settle):
    """The coach settles on his own path, and used to be easy to forget."""
    assert settle(
        settled=0, coach_settled=True, pending=["Zaidu"], was_fully_settled=False
    )


def test_becoming_complete_is_worth_writing(settle):
    """A player who has left the league stops being pending without scoring."""
    assert settle(settled=0, coach_settled=False, pending=[], was_fully_settled=False)


def test_a_round_already_complete_is_left_alone(settle):
    assert not settle(
        settled=0, coach_settled=False, pending=[], was_fully_settled=True
    )


# --- The job must not describe work it did not do -----------------------------
#
# The commit subject was fixed prose: "o ledger, registado antes do apito". The
# job says that whether it recorded a prediction or filled in a result after
# the fact — and it settled six players under exactly that heading. In a file
# whose entire purpose is keeping predictions and results apart, a commit
# message that confuses the two is the one thing it cannot be allowed to say.


def test_the_commit_subject_is_derived_not_fixed(workflow):
    """It has to read what changed, not assert what usually changes."""
    run = next(
        step["run"]
        for step in workflow["jobs"]["ledger"]["steps"]
        if "git commit" in (step.get("run") or "")
    )
    assert 'git commit -m "$subject"' in run, (
        "the commit subject is hardcoded again — derive it from the diff, or "
        "the job will describe settling as predicting"
    )
    # Both halves of the ledger have to be detectable, or one of them gets
    # reported as the other.
    assert '"recorded_at"' in run, "nothing detects a newly recorded round"
    assert '"actual"' in run, "nothing detects a settled player"


def test_the_subject_covers_every_outcome(workflow):
    """Recorded, settled, both, and neither. A missing branch is a wrong message."""
    run = next(
        step["run"]
        for step in workflow["jobs"]["ledger"]["steps"]
        if "git commit" in (step.get("run") or "")
    )
    assert run.count("subject=") >= 4, (
        "fewer than four branches: one of recorded / settled / both / neither "
        "would fall through to another one's wording"
    )
