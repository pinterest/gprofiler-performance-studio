#!/usr/bin/env python3
"""
Spec tests for heartbeat inventory change detection in ``gprofiler_dev.heartbeat_inventory``.

Locks down the two contracts the heartbeat path now relies on:

* ``inventory_digest`` is a stable fingerprint of *what the database would store*:
  order-independent, insensitive to entries the sync filters out, and -- critically --
  ``None`` (inventory not reported) is distinct from ``[]`` (no containers).
  The agent implements the same canonical form; the test vectors below are the shared
  contract, so change them only together with ``DIGEST_VERSION``.

* ``SyncBudget`` is a per-process token bucket that admits at most ``rate`` full
  inventory syncs per second, never banks more than one second of burst, and is
  disabled (admits everything) at ``rate <= 0``.

Loaded directly by path so the ``gprofiler_dev`` package __init__ (boto3/DAL) is not
imported. The module is pure stdlib at import time.

    cd src && python -m pytest tests/spec/backend/test_heartbeat_inventory_spec.py -v
"""

import importlib.util
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]
_MOD_PATH = _SRC_ROOT / "gprofiler-dev" / "gprofiler_dev" / "heartbeat_inventory.py"
_spec = importlib.util.spec_from_file_location("heartbeat_inventory_under_test", _MOD_PATH)
hi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hi)


def _container(cid="c1", name="app", ns="prod", pod="app-7d9f-abc12", procs=((100, "java"), (101, "sh"))):
    return {
        "container_id": cid,
        "container_name": name,
        "runtime": "containerd",
        "namespace": ns,
        "pod_name": pod,
        "workload_name": "app",
        "workload_kind": "Deployment",
        "processes": [{"pid": pid, "process_name": pname} for pid, pname in procs],
    }


# --------------------------------------------------------------------------------------
# Shared test vectors (agent and server must agree on these exact values)
# --------------------------------------------------------------------------------------

# sha256 over the canonical JSON. The agent repo (pinterest/gprofiler,
# tests for gprofiler/metadata/heartbeat_metadata.py) pins the same two values.
EMPTY_DIGEST = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"  # sha256("[]")
SINGLE_CONTAINER_DIGEST = "aae22d2ec4de790a53542e47a558191f3a08229aaa922051e97b8eb8c29e1b57"
SINGLE_CONTAINER_CANONICAL = (
    '[["c1","app","containerd","prod","app-7d9f-abc12","app","Deployment",[[100,"java"],[101,"sh"]]]]'
)


def test_empty_inventory_vector():
    assert hi.inventory_digest([]) == EMPTY_DIGEST


def test_single_container_vector_is_pinned():
    # If this fails, the canonical form changed: bump DIGEST_VERSION and update the
    # agent's implementation + vectors in the same change.
    import json

    assert json.dumps(hi.canonical_inventory([_container()]), separators=(",", ":")) == SINGLE_CONTAINER_CANONICAL
    assert hi.inventory_digest([_container()]) == SINGLE_CONTAINER_DIGEST
    assert hi.DIGEST_VERSION == "1"


def test_none_means_not_reported_and_is_not_empty():
    assert hi.inventory_digest(None) is None
    assert hi.inventory_digest([]) is not None


def test_order_independent():
    a = [_container("c1", procs=((5, "a"), (3, "b"))), _container("c2", procs=((9, "x"),))]
    b = [_container("c2", procs=((9, "x"),)), _container("c1", procs=((3, "b"), (5, "a")))]
    assert hi.inventory_digest(a) == hi.inventory_digest(b)


def test_change_in_any_stored_field_changes_digest():
    base = hi.inventory_digest([_container()])
    assert hi.inventory_digest([_container(procs=((100, "java"),))]) != base  # process gone
    assert hi.inventory_digest([_container(procs=((100, "java"), (101, "sh"), (102, "sh")))]) != base  # new pid
    assert hi.inventory_digest([_container(pod="app-7d9f-zzz99")]) != base  # pod rescheduled
    assert hi.inventory_digest([_container(procs=((100, "python"), (101, "sh")))]) != base  # name changed


def test_filters_match_what_the_sync_stores():
    # Containers without a container_id are never stored; duplicates keep the first;
    # non-integer / duplicate pids are skipped; empty process_name == None.
    noisy = [
        _container("c1", procs=((100, "java"), ("abc", "bad"), (100, "dup"), (7, ""))),
        {"container_name": "no-id", "processes": [{"pid": 1, "process_name": "x"}]},
        _container("c1", name="shadowed"),
        "not-a-dict",
    ]
    clean = [_container("c1", procs=((7, None), (100, "java")))]
    assert hi.canonical_inventory(noisy) == hi.canonical_inventory(clean)
    assert hi.inventory_digest(noisy) == hi.inventory_digest(clean)


def test_string_pids_are_accepted_like_ints():
    a = [_container(procs=(("100", "java"),))]
    b = [_container(procs=((100, "java"),))]
    assert hi.inventory_digest(a) == hi.inventory_digest(b)


# --------------------------------------------------------------------------------------
# SyncBudget
# --------------------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_budget_admits_rate_per_second_and_defers_the_rest():
    clock = _Clock()
    budget = hi.SyncBudget(rate=2, burst_seconds=10, now=clock)
    assert budget.acquire_up_to(5) == 2  # starts with one second's worth, not a full burst
    assert budget.acquire_up_to(5) == 0  # nothing left this instant
    clock.t += 0.5
    assert budget.acquire_up_to(5) == 1  # half a second refilled one token
    clock.t += 3
    assert budget.acquire_up_to(100) == 6  # a quiet stretch is banked ...
    clock.t += 1000
    assert budget.acquire_up_to(100) == 20  # ... but never more than burst_seconds x rate


def test_budget_try_acquire_single():
    clock = _Clock()
    budget = hi.SyncBudget(rate=1, burst_seconds=1, now=clock)
    assert budget.try_acquire() is True
    assert budget.try_acquire() is False
    clock.t += 1
    assert budget.try_acquire() is True


def test_fractional_rate_banks_whole_syncs_over_the_burst_window():
    clock = _Clock()
    budget = hi.SyncBudget(rate=0.5, burst_seconds=10, now=clock)  # capacity 5
    assert budget.acquire_up_to(3) == 1  # one second's worth = min 1 token to start
    clock.t += 1
    assert budget.acquire_up_to(3) == 0  # 0.5 token: not yet a whole sync
    clock.t += 1
    assert budget.acquire_up_to(3) == 1  # 1.0 -> one sync, fraction preserved
    clock.t += 7
    assert budget.acquire_up_to(10) == 3  # 3.5 banked -> 3 whole syncs
    clock.t += 60
    assert budget.acquire_up_to(10) == 5  # capped at 5 = 0.5/s x 10 s


def test_uneven_flushes_no_longer_lose_the_fractional_budget():
    """The failure mode measured on dev (F-25): flushes once a second, most carrying no
    differing host and a few carrying several. With a 1-s burst the bucket sits at its
    cap during the empty flushes and the accrual is lost; with a 10-s burst the quiet
    seconds pay for the busy flush. Same average rate either way."""

    def run(burst_seconds):
        clock = _Clock()
        budget = hi.SyncBudget(rate=0.5, burst_seconds=burst_seconds, now=clock)
        granted = 0
        for second in range(1, 601):
            clock.t += 1
            if second % 10 == 0:  # one busy flush every 10 s, 5 differing hosts
                granted += budget.acquire_up_to(5)
            else:
                granted += budget.acquire_up_to(0)
        return granted

    assert run(burst_seconds=1) == 60  # 1 per busy flush: a fifth of the 0.5/s budget
    assert 290 <= run(burst_seconds=10) <= 300  # ~0.5/s delivered


def test_zero_or_negative_rate_disables_budget():
    assert hi.SyncBudget(rate=0).acquire_up_to(10_000) == 10_000
    assert hi.SyncBudget(rate=-1).unlimited is True
    assert hi.SyncBudget(rate=0).acquire_up_to(0) == 0


def test_drain_time_matches_fleet_math():
    # 150k hosts, 2 syncs/s/worker x 56 workers = 112/s -> ~22 min.
    clock = _Clock()
    workers = [hi.SyncBudget(rate=2, now=clock) for _ in range(56)]
    remaining = 150_000
    seconds = 0
    while remaining > 0:
        for w in workers:
            remaining -= w.acquire_up_to(remaining)
        clock.t += 1
        seconds += 1
    assert 1300 <= seconds <= 1400
