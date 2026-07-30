"""Tests for the cash_flows table and its aggregation helpers.

get_net_contributions_by_period() decides which quarter every deposit and
withdrawal belongs to. An off-by-one here would silently shift contributions
between quarters and corrupt every market-return figure downstream, so the
boundary rules are pinned down explicitly.
"""

import pytest

import db


class TestReplaceCashFlows:
    def test_inserts_events(self, temp_db, make_cash_flows):
        make_cash_flows(
            ("2025-01-15", "deposit", 1000.0),
            ("2025-02-20", "withdrawal", 300.0),
        )
        summary = db.get_cash_flow_summary()
        assert summary["count"] == 2
        assert summary["deposited"] == pytest.approx(1000.0)
        assert summary["withdrawn"] == pytest.approx(300.0)
        assert summary["net_invested"] == pytest.approx(700.0)

    def test_replace_is_idempotent(self, temp_db, make_cash_flows):
        """Re-importing the same export must not double-count.

        This is the whole reason the import wipes the table first — the user's
        workflow re-exports the full history every quarter.
        """
        events = (
            ("2025-01-15", "deposit", 1000.0),
            ("2025-02-20", "withdrawal", 300.0),
        )
        make_cash_flows(*events)
        make_cash_flows(*events)
        make_cash_flows(*events)

        summary = db.get_cash_flow_summary()
        assert summary["count"] == 2
        assert summary["net_invested"] == pytest.approx(700.0)

    def test_replace_drops_events_absent_from_the_new_import(self, temp_db, make_cash_flows):
        make_cash_flows(("2025-01-15", "deposit", 1000.0))
        make_cash_flows(("2025-06-01", "deposit", 50.0))
        summary = db.get_cash_flow_summary()
        assert summary["count"] == 1
        assert summary["net_invested"] == pytest.approx(50.0)

    def test_empty_table_summary(self, temp_db):
        summary = db.get_cash_flow_summary()
        assert summary["count"] == 0
        assert summary["net_invested"] == 0
        assert summary["earliest_date"] is None
        assert summary["latest_date"] is None


class TestNetContributionsByPeriod:
    PERIODS = ["2025-03-31", "2025-06-30", "2025-09-30"]

    def test_event_on_a_snapshot_date_belongs_to_that_snapshot(self, temp_db, make_cash_flows):
        """The upper bound is inclusive: an event dated exactly on a snapshot
        counts toward that snapshot, not the following one."""
        make_cash_flows(("2025-06-30", "deposit", 500.0))
        assert db.get_net_contributions_by_period(self.PERIODS) == [0, 500.0, 0]

    def test_pre_snapshot_history_rolls_into_the_first_bucket(self, temp_db, make_cash_flows):
        """Contributions predating the first snapshot are lumped into it, since
        there is no earlier portfolio value to measure a return against."""
        make_cash_flows(
            ("2019-11-01", "deposit", 100.0),
            ("2023-05-05", "deposit", 200.0),
            ("2025-03-30", "deposit", 300.0),
        )
        assert db.get_net_contributions_by_period(self.PERIODS) == [600.0, 0, 0]

    def test_events_after_the_last_snapshot_are_dropped(self, temp_db, make_cash_flows):
        """Money deposited after the newest snapshot has no matching value
        measurement yet, so it must not be attributed to any quarter."""
        make_cash_flows(
            ("2025-09-30", "deposit", 100.0),   # on the last snapshot — kept
            ("2025-10-01", "deposit", 999.0),   # after it — dropped
            ("2026-01-01", "deposit", 999.0),   # far after — dropped
        )
        assert db.get_net_contributions_by_period(self.PERIODS) == [0, 0, 100.0]

    def test_withdrawals_subtract(self, temp_db, make_cash_flows):
        make_cash_flows(
            ("2025-05-01", "deposit", 1000.0),
            ("2025-05-02", "withdrawal", 250.0),
        )
        assert db.get_net_contributions_by_period(self.PERIODS) == [0, 750.0, 0]

    def test_net_can_be_negative(self, temp_db, make_cash_flows):
        make_cash_flows(
            ("2025-05-01", "deposit", 100.0),
            ("2025-05-02", "withdrawal", 400.0),
        )
        assert db.get_net_contributions_by_period(self.PERIODS) == [0, -300.0, 0]

    def test_events_distribute_across_all_buckets(self, temp_db, make_cash_flows):
        make_cash_flows(
            ("2025-01-10", "deposit", 10.0),   # bucket 0 (pre/within first)
            ("2025-04-10", "deposit", 20.0),   # bucket 1
            ("2025-05-10", "deposit", 5.0),    # bucket 1
            ("2025-08-10", "deposit", 30.0),   # bucket 2
        )
        assert db.get_net_contributions_by_period(self.PERIODS) == [10.0, 25.0, 30.0]

    def test_no_events_gives_zeros(self, temp_db):
        assert db.get_net_contributions_by_period(self.PERIODS) == [0, 0, 0]

    def test_empty_period_list(self, temp_db, make_cash_flows):
        make_cash_flows(("2025-05-01", "deposit", 100.0))
        assert db.get_net_contributions_by_period([]) == []

    def test_single_period_absorbs_everything_up_to_its_date(self, temp_db, make_cash_flows):
        make_cash_flows(
            ("2020-01-01", "deposit", 100.0),
            ("2025-03-31", "deposit", 50.0),
            ("2025-04-01", "deposit", 999.0),  # after the only snapshot — dropped
        )
        assert db.get_net_contributions_by_period(["2025-03-31"]) == [150.0]

    def test_sum_of_buckets_matches_summary_when_nothing_is_dropped(
        self, temp_db, make_cash_flows
    ):
        """Guards the two aggregation paths against drifting apart."""
        make_cash_flows(
            ("2024-01-01", "deposit", 1000.0),
            ("2025-04-15", "deposit", 500.0),
            ("2025-07-20", "withdrawal", 200.0),
            ("2025-09-30", "deposit", 75.0),
        )
        buckets = db.get_net_contributions_by_period(self.PERIODS)
        assert sum(buckets) == pytest.approx(db.get_cash_flow_summary()["net_invested"])
