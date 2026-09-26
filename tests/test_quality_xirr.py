"""Financial reference examples and data-quality workflow regressions."""
from datetime import date
import io

import pytest

import app
import data_quality
import db
import performance
from test_api import _xlsx_bytes, VALID_HEADER


def confirm(client, through):
    quality = client.get('/api/data-quality').get_json()
    return client.post('/api/cashflow-coverage', json=dict(
        through=through, confirmed=True, revision=quality['cash_flows']['revision']))


def test_xirr_matches_microsoft_published_example():
    # https://support.microsoft.com/en-us/Excel/functions/xirr-function
    result = performance.xirr([
        ('2008-01-01', -10000), ('2008-03-01', 2750), ('2008-10-30', 4250),
        ('2009-02-15', 3250), ('2009-04-01', 2750),
    ])
    assert result['rate'] == pytest.approx(.373362535, abs=1e-8)


@pytest.mark.parametrize('final,expected', [(1100, .1), (900, -.1), (1000, 0)])
def test_xirr_handles_gains_losses_and_zero_return(final, expected):
    assert performance.xirr([('2025-01-01', -1000), ('2026-01-01', final)])['rate'] == pytest.approx(expected, abs=1e-10)


def test_deposit_timing_changes_return():
    # Money arriving at the end earned nothing; the first deposit earned 10%.
    flows = [('2025-01-01', -1000), ('2026-01-01', -1000), ('2026-01-01', 2100)]
    assert performance.xirr(flows)['rate'] == pytest.approx(.1)
    assert performance.xirr([('2025-01-01', -2000), ('2026-01-01', 2100)])['rate'] == pytest.approx(.05)


def test_same_day_flows_and_unsorted_inputs():
    flows = [('2026-01-01', 1100), ('2025-01-01', -600), ('2025-01-01', -400)]
    assert performance.xirr(flows)['rate'] == pytest.approx(.1)


@pytest.mark.parametrize('flows', [[], [('2025-01-01', -100), ('2025-01-01', 110)],
    [('2025-01-01', -100), ('2026-01-01', 0)], [('2025-02-30', -100), ('2026-01-01', 110)],
    [('2025-01-01', float('nan')), ('2026-01-01', 100)],
    [('2025-01-01', float('inf')), ('2026-01-01', 100)]])
def test_xirr_unavailable_instead_of_nan_or_arbitrary_rate(flows):
    result = performance.xirr(flows)
    assert result['rate'] is None
    assert result['reason']


def test_multiple_solution_cashflows_are_not_given_an_arbitrary_xirr():
    # This annual pattern has two roots: 10% and 20%.
    result = performance.xirr([('2025-01-01', -100), ('2026-01-01', 230), ('2027-01-01', -132)])
    assert result['rate'] is None
    assert 'unique' in result['reason']


def test_near_total_loss_and_large_gain_are_finite():
    assert performance.xirr([('2025-01-01', -1e6), ('2026-01-01', 1)])['rate'] == pytest.approx(-.999999)
    assert performance.xirr([('2025-01-01', -1), ('2026-01-01', 10001)])['rate'] == pytest.approx(10000)


def test_missing_balance_is_not_inferred_as_zero(client, make_snapshot):
    sid = make_snapshot('2025-Q4', '2025-12-31', portfolio=1000)
    quality = client.get('/api/data-quality').get_json()
    assert quality['snapshots'][0]['balances'] == dict(cash='missing', ppk='missing', mortgage='missing')
    entries = [dict(type=kind, amount_pln=0, original_amount=0) for kind in data_quality.MANUAL_TYPES]
    assert client.post(f'/api/manual-entries/{sid}', json={'entries':entries}).status_code == 200
    quality = client.get('/api/data-quality').get_json()
    assert set(quality['snapshots'][0]['balances'].values()) == {'confirmed_zero'}
    assert len(client.get(f'/api/manual-entries/{sid}').get_json()) == 3
    assert client.post(f'/api/manual-entries/{sid}', json={'entries':[]}).status_code == 200
    assert client.get('/api/data-quality').get_json()['snapshots'][0]['balances']['cash'] == 'missing'


@pytest.mark.parametrize('amount', [None, 'NaN', 'Infinity', 'bad'])
def test_invalid_manual_amount_never_erases_existing_balances(client, make_snapshot, amount):
    sid = make_snapshot('2025-Q4', '2025-12-31', cash=50)
    response = client.post(f'/api/manual-entries/{sid}', json={'entries':[dict(type='cash', amount_pln=amount)]})
    assert response.status_code == 400
    assert db.get_manual_entries(sid)[0]['amount_pln'] == 50


def test_last_event_does_not_imply_history_coverage(client, make_snapshot, make_cash_flows):
    make_snapshot('2025-Q4', '2025-12-31', portfolio=1000)
    make_cash_flows(('2025-12-31','deposit',1000))
    quality = client.get('/api/data-quality').get_json()
    assert quality['cash_flows']['last_event'] == '2025-12-31'
    assert quality['cash_flows']['confirmed_through'] is None
    assert quality['cash_flows']['covers_snapshot'] is False
    assert confirm(client, '2025-12-31').status_code == 200
    assert client.get('/api/data-quality').get_json()['cash_flows']['covers_snapshot'] is True


def test_coverage_accepts_quiet_periods_and_resets_on_import(client, make_snapshot, make_cash_flows):
    make_snapshot('2025-Q4', '2025-12-31', portfolio=1000)
    make_cash_flows(('2025-01-01', 'deposit',1000))
    assert confirm(client,'2025-12-31').status_code == 200
    assert client.get('/api/data-quality').get_json()['cash_flows']['covers_snapshot'] is True
    file = _xlsx_bytes(VALID_HEADER, ('2025-01-01','Wpłata automatyczna',1000,'PLN',1,1000,'Cash','P'))
    assert client.post('/api/import-cashflows',data={'file':(file,'flows.xlsx')}).status_code == 200
    assert client.get('/api/data-quality').get_json()['cash_flows']['confirmed_through'] is None


def test_failed_import_does_not_clear_coverage(client, make_cash_flows):
    make_cash_flows(('2025-01-01','deposit',1000))
    assert confirm(client,'2025-12-31').status_code == 200
    assert client.post('/api/import-cashflows',data={'file':(io.BytesIO(b'bad'),'bad.xlsx')}).status_code == 400
    assert client.get('/api/data-quality').get_json()['cash_flows']['confirmed_through'] == '2025-12-31'


def test_stale_confirmation_and_future_dates_rejected(client, make_cash_flows):
    make_cash_flows(('2025-01-01','deposit',1000))
    revision = client.get('/api/data-quality').get_json()['cash_flows']['revision']
    make_cash_flows(('2025-02-01','deposit',2000))
    response=client.post('/api/cashflow-coverage',json={'confirmed':True,'through':'2025-12-31','revision':revision})
    assert response.status_code == 400
    assert confirm(client, '2099-12-31').status_code == 400
    assert client.get('/api/data-quality').get_json()['cash_flows']['confirmed_through'] is None


def test_dashboard_xirr_uses_cashflow_dates_and_snapshot_value_only(client, make_snapshot, make_cash_flows):
    sid=make_snapshot('2026-Q1','2026-01-01',portfolio=2100,ppk=10000,mortgage=20000)
    make_cash_flows(('2025-01-01','deposit',1000), ('2026-01-01','deposit',1000), ('2026-03-01','deposit',999999))
    assert client.get('/api/dashboard').get_json()['lifetime']['xirr']['rate'] is None
    assert confirm(client,'2026-01-01').status_code == 200
    assert client.get('/api/dashboard').get_json()['lifetime']['xirr']['rate'] is None  # cash unrecorded
    db.save_manual_entries(sid,[dict(type='cash',amount_pln=0),dict(type='ppk',amount_pln=10000),dict(type='mortgage',amount_pln=20000)])
    data=client.get('/api/dashboard').get_json()
    assert data['lifetime']['xirr']['rate'] == pytest.approx(.1)
    assert data['lifetime']['current_wealth'] == 2100
    assert data['net_worth'] == -7900


def test_withdrawals_count_as_money_received():
    result=performance.portfolio_xirr([
        dict(event_date='2025-01-01',operation='deposit',value_pln=1000),
        dict(event_date='2026-01-01',operation='withdrawal',value_pln=400),
    ], '2026-01-01', 700)
    assert result['rate'] == pytest.approx(.1)


def test_gaps_and_latest_completed_quarter_are_explicit():
    snapshots=[dict(id=2,quarter='2025-Q3',snapshot_date='2025-09-30'),dict(id=1,quarter='2025-Q1',snapshot_date='2025-03-31')]
    quality=data_quality.summarize(snapshots,[],[],dict(confirmed_through=None,revision='x'),today=date(2026,1,10))
    assert quality['missing_quarters']==['2025-Q2']
    assert quality['last_completed_quarter_end']=='2025-12-31'
    assert quality['holdings_behind'] is True


def test_confirmed_zero_ppk_overrides_saved_retirement_fallback(client, make_snapshot):
    sid=make_snapshot('2025-Q4','2025-12-31',portfolio=1000)
    db.save_manual_entries(sid,[dict(type='ppk',amount_pln=0)])
    params,_,_=app._retirement_params({'start_ppk':50000},app._build_dashboard_data())
    assert params['start_ppk']==0


def test_schema_upgrade_preserves_existing_records_and_is_repeatable(client, make_snapshot, make_cash_flows):
    sid=make_snapshot('2025-Q4','2025-12-31',portfolio=1000,cash=10)
    make_cash_flows(('2025-01-01','deposit',1000))
    with db.get_db() as conn:
        conn.execute('DROP TABLE cash_flow_coverage')
    db.init_db()
    db.init_db()
    assert db.get_positions(sid)[0]['value_pln']==1000
    assert db.get_manual_entries(sid)[0]['amount_pln']==10
    assert db.get_cash_flow_summary()['count']==1


def test_reinvestment_after_withdrawal_with_unique_xirr():
    result = performance.xirr([('2025-01-01', -1000), ('2026-01-01', 100),
                               ('2027-01-01', -100), ('2028-01-01', 1320)])
    assert result['rate'] == pytest.approx(.1)


def test_repeated_root_without_sign_crossing():
    result = performance.xirr([('2025-01-01', -100), ('2026-01-01', 220), ('2027-01-01', -121)])
    assert result['rate'] == pytest.approx(.1)


@pytest.mark.parametrize('count,expected', [(650, -.12), (1100, .08)])
def test_long_mixed_history_has_known_return(count, expected):
    # Exceeds both the former 200-level cap and Python's recursion limit.
    # Grow each flow to the valuation date at a known rate to create an
    # independently specified ending balance, with hundreds of withdrawals.
    from datetime import timedelta
    start = date(2020, 1, 1)
    flows = [(start.isoformat(), -10000)]
    flows.extend(((start + timedelta(days=i * 3)).isoformat(), 10 if i % 2 else -100)
                 for i in range(1, count))
    end = start + timedelta(days=count * 3)
    ending = -sum(value * (1 + expected) ** ((end - date.fromisoformat(day)).days / 365)
                  for day, value in flows)
    flows.append((end.isoformat(), ending))
    assert performance.xirr(flows)['rate'] == pytest.approx(expected, abs=1e-9)


def test_small_coefficients_survive_long_derivative_chain():
    # Multiplying the two-root annual polynomial by a positive polynomial
    # preserves its roots while producing hundreds of alternating cash flows.
    from datetime import timedelta
    coefficients = [0.0] * 653
    for i in range(651):
        weight = 1e-200 if i % 2 else 1
        for j, value in enumerate((-100, 230, -132)):
            coefficients[i + j] += weight * value
    start = date(1000, 1, 1)
    flows = [((start + timedelta(days=365 * i)).isoformat(), value)
             for i, value in enumerate(coefficients)]
    result = performance.xirr(flows)
    assert result['rate'] is None
    assert 'multiple' in result['reason']


def test_xirr_cache_tracks_actual_inputs_and_cannot_be_mutated():
    performance._solve_xirr.cache_clear()
    flows = [('2025-01-01', -1000), ('2026-01-01', 1100)]
    result = performance.xirr(flows)
    result['rate'] = 99
    assert performance.xirr(flows)['rate'] == pytest.approx(.1)
    assert performance._solve_xirr.cache_info().hits == 1
    assert performance.xirr([flows[0], ('2026-01-01', 1200)])['rate'] == pytest.approx(.2)
    assert performance.xirr([('2024-01-02', -1000), flows[1]])['rate'] != pytest.approx(.1)


def test_overflow_in_same_day_netting_is_rejected():
    result = performance.xirr([('2025-01-01', -1e308), ('2025-01-01', -1e308),
                               ('2026-01-01', 100)])
    assert result['rate'] is None
    assert 'finite' in result['reason']
