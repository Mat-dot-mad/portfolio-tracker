"""Cross-view accounting, reviewed imports and isolated retirement scenarios."""
import io

import pytest

import app
import db
from test_api import _xlsx_bytes, VALID_HEADER


def csv_file(amount="200", name="Fund (F)"):
    return ("Walor;ISIN;Konto;Grupa;Waluta waloru;Tagi;Wartość waloru [PLN]\n"
            f"{name};;Broker;ETF;PLN;ETF;{amount}\n").encode("cp1250")


def upload(client, content, **fields):
    return client.post('/api/import-csv', data={
        'file': (io.BytesIO(content), 'positions_2026-06-30.csv'), **fields})


def test_returns_agree_and_ignore_ppk_payroll_and_mortgage(client, make_snapshot, make_cash_flows):
    make_snapshot('2026-Q1', '2026-03-31', portfolio=100000, ppk=5000, mortgage=90000)
    make_snapshot('2026-Q2', '2026-06-30', portfolio=115000, ppk=15000, mortgage=80000)
    make_cash_flows(('2026-01-01', 'deposit', 100000), ('2026-05-01', 'deposit', 10000))
    data = app._build_dashboard_data()
    latest = data['timeline'][-1]
    assert latest['market_gain'] == 5000
    assert latest['market_return'] == pytest.approx(.05)
    assert latest['tracked_wealth'] == data['lifetime']['current_wealth'] == 115000
    assert data['net_worth'] == 50000  # PPK and liability still counted here
    commentary = app._build_commentary_payload(data)
    assert commentary['market_return_pct_excluding_contributions'] == 5
    assert app._real_return_pool(data, 0, size=1)[0] == pytest.approx(1.05**4-1)


def test_preview_is_read_only_and_replacement_preserves_balances(client, make_snapshot):
    sid = make_snapshot('2026-Q2', '2026-06-30', portfolio=100, cash=20, ppk=30, mortgage=40)
    before = db.get_manual_entries(sid)
    preview = upload(client, csv_file(), preview='1').get_json()
    assert preview['replacing']
    assert preview['previous_total'] == 100
    assert db.get_positions(sid)[0]['value_pln'] == 100
    saved = upload(client, csv_file(), replace='1', preview_token=preview['preview_token'])
    assert saved.status_code == 200
    assert saved.get_json()['snapshot_id'] == sid
    assert db.get_manual_entries(sid) == before
    assert db.get_positions(sid)[0]['value_pln'] == 200
    assert len(db.get_snapshots()) == 1


@pytest.mark.parametrize('amount', ['oops', 'NaN', 'Infinity', ''])
def test_bad_position_values_never_replace_existing_data(client, make_snapshot, amount):
    sid = make_snapshot('2026-Q2', '2026-06-30', portfolio=100)
    response = upload(client, csv_file(amount), preview='1')
    assert response.status_code == 400
    assert 'Row 2' in response.get_json()['errors'][0]
    assert db.get_positions(sid)[0]['value_pln'] == 100


def test_stale_or_changed_file_requires_new_preview(client, make_snapshot):
    sid = make_snapshot('2026-Q2', '2026-06-30', portfolio=100)
    token = upload(client, csv_file(), preview='1').get_json()['preview_token']
    assert upload(client, csv_file('300'), replace='1', preview_token=token).status_code == 409
    with db.get_db() as conn:
        conn.execute('UPDATE positions SET value_pln=110 WHERE snapshot_id=?', (sid,))
    assert upload(client, csv_file(), replace='1', preview_token=token).status_code == 409
    assert db.get_positions(sid)[0]['value_pln'] == 110


def test_position_replacement_rolls_back_on_database_error(client, make_snapshot):
    sid = make_snapshot('2026-Q2', '2026-06-30', portfolio=100, cash=20)
    token = upload(client, csv_file(), preview='1').get_json()['preview_token']
    with db.get_db() as conn:
        conn.execute("CREATE TRIGGER reject_positions BEFORE INSERT ON positions BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(Exception, match='test failure'):
        upload(client, csv_file(), replace='1', preview_token=token)
    assert db.get_positions(sid)[0]['value_pln'] == 100
    assert db.get_manual_entries(sid)[0]['amount_pln'] == 20


def test_cashflow_preview_and_commit_recheck_history(client, make_cash_flows):
    make_cash_flows(('2025-01-01', 'deposit', 999))
    def send(**fields):
        file = _xlsx_bytes(VALID_HEADER, ('2026-01-01', 'Wpłata automatyczna', 10, 'PLN', 1, 10, 'Cash', 'P'))
        return client.post('/api/import-cashflows', data={'file': (file, 'flows.xlsx'), **fields})
    preview = send(preview='1').get_json()
    assert preview['previous']['net_invested'] == 999
    assert preview['added_rows'] == preview['removed_rows'] == 1
    assert db.get_cash_flow_summary()['net_invested'] == 999
    make_cash_flows(('2025-01-01', 'deposit', 1000))
    assert send(preview_token=preview['preview_token']).status_code == 409
    token = send(preview='1').get_json()['preview_token']
    assert send(preview_token=token).status_code == 200
    assert db.get_cash_flow_summary()['net_invested'] == 10


@pytest.mark.parametrize('when,amount', [('2026-02-30', 10), ('2026-01-01', 'NaN'), ('2026-01-01', None)])
def test_invalid_cashflows_preserve_history(client, make_cash_flows, when, amount):
    make_cash_flows(('2025-01-01', 'deposit', 999))
    file = _xlsx_bytes(VALID_HEADER, (when, 'Wpłata automatyczna', 10, 'PLN', 1, amount, 'Cash', 'P'))
    response = client.post('/api/import-cashflows', data={'file': (file, 'flows.xlsx'), 'preview': '1'})
    assert response.status_code == 400
    assert db.get_cash_flow_summary()['net_invested'] == 999


def test_scenario_preview_never_changes_baseline_and_uses_live_balances(client, make_snapshot):
    make_snapshot('2026-Q1', '2026-03-31', portfolio=100000)
    baseline = {'current_age': 59, 'retirement_age': 60, 'horizon_age': 65,
                'use_historical_returns': 0, 'annual_spending': 10000}
    assert client.post('/api/retirement', json=baseline).status_code == 200
    before = db.get_retirement_settings()
    response = client.post('/api/retirement/preview', json={**baseline, 'annual_spending': 20000})
    assert response.status_code == 200
    assert response.get_json()['settings']['annual_spending'] == 20000
    assert db.get_retirement_settings() == before
    sid = client.post('/api/retirement/scenarios', json={'name': 'Earlier', 'settings': {**baseline, 'retirement_age': 59}}).get_json()['id']
    assert db.get_retirement_settings() == before
    scenario = client.get('/api/retirement/scenarios').get_json()[0]
    make_snapshot('2026-Q2', '2026-06-30', portfolio=200000)
    calculated = client.post('/api/retirement/preview', json=scenario['settings']).get_json()
    assert calculated['snapshot_date'] == '2026-06-30'
    assert calculated['balances']['taxable'] == 200000
    assert calculated == client.post('/api/retirement/preview', json=scenario['settings']).get_json()
    assert client.delete('/api/retirement').status_code == 200
    assert client.get('/api/retirement/scenarios').get_json()[0]['id'] == sid
    assert client.delete(f'/api/retirement/scenarios/{sid}').status_code == 200
    assert client.get('/api/retirement/scenarios').get_json() == []


@pytest.mark.parametrize('settings', [{'annual_spending': 'NaN'}, {'inflation_rate': -1}, {'current_age': 70, 'retirement_age': 60}, {'ppk_installment_years': 0}])
def test_invalid_scenarios_and_baselines_are_rejected(client, settings):
    for endpoint in ['/api/retirement', '/api/retirement/preview']:
        assert client.post(endpoint, json=settings).status_code == 400
    assert db.get_retirement_settings() == {}
    assert client.post('/api/retirement/scenarios', json={'name': 'Bad', 'settings': settings}).status_code == 400
    assert db.get_retirement_scenarios() == []


def test_chart_failure_share_matches_success_calculation_with_same_paths():
    import retirement
    from test_retirement import make_params
    params = make_params(current_age=60, retirement_age=60, horizon_age=85,
                         start_taxable=500000, start_taxable_basis=400000,
                         annual_spending=30000)
    returns = [-.2, .1, .02, .15]
    success = retirement.success_rate(params, returns, paths=80, seed=42)
    chart = retirement.median_path(params, returns, paths=80, seed=42)
    assert chart[-1]['failed_share'] == pytest.approx(1 - success)
