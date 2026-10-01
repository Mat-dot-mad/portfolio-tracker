from unittest.mock import Mock

import pytest
import commentary
import gemini


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret-test-key')
    monkeypatch.delenv('ANTHROPIC_MODEL', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setattr(commentary.time, 'sleep', lambda _: None)
    post = Mock()
    monkeypatch.setattr(commentary.requests, 'post', post)
    return post


def response(status=200, stop='end_turn'):
    return Mock(status_code=status, json=lambda: {
        'stop_reason': stop, 'content': [{'type': 'text', 'text': 'Review.'}]})


def test_claude_request_and_retry(setup):
    setup.side_effect = [response(529), response()]
    assert commentary.generate_commentary('{}') == ('Review.', 'claude-sonnet-5-5')
    assert setup.call_count == 2
    body = setup.call_args.kwargs['json']
    assert body['thinking'] == {'type': 'between_tools'}
    assert body['system'] == commentary.CLAUDE_SYSTEM_PROMPT
    assert body['messages'] == [{'role': 'user', 'content': '{}'}]


def test_workspace_header(setup, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_WORKSPACE_ID', 'wrkspc_test')
    setup.return_value = response()
    commentary.generate_commentary('{}')
    assert setup.call_args.kwargs['headers']['anthropic-workspace-id'] == 'wrkspc_test'


def test_fallback_records_actual_model(setup, monkeypatch):
    setup.return_value = response(503)
    monkeypatch.setenv('GEMINI_API_KEY', 'fallback-key')
    monkeypatch.setattr(gemini, 'generate_commentary', lambda _: 'Fallback')
    assert commentary.generate_commentary('{}') == ('Fallback', gemini.get_model())
    assert setup.call_count == 3


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_configuration_errors_not_retried(setup, status):
    setup.return_value = response(status)
    with pytest.raises(ValueError):
        commentary.generate_commentary('{}')
    assert setup.call_count == 1


def test_incomplete_response_rejected(setup):
    setup.return_value = response(stop='max_tokens')
    with pytest.raises(ValueError, match='complete review'):
        commentary.generate_commentary('{}')


def test_failed_generation_preserves_cache(setup, client, make_snapshot, monkeypatch):
    import db
    make_snapshot('2025-Q1', '2025-03-31', portfolio=100)
    sid = make_snapshot('2025-Q2', '2025-06-30', portfolio=110)
    db.save_commentary(sid, 'Previous review', 'old-model', 'old-hash')
    setup.return_value = response(503)
    assert client.post('/api/commentary').status_code == 502
    assert db.get_commentary(sid)['text'] == 'Previous review'


def test_rich_payload_never_reaches_gemini(setup, monkeypatch):
    setup.return_value = response(503)
    monkeypatch.setenv('GEMINI_API_KEY', 'fallback-key')
    fallback = Mock(return_value='Fallback')
    monkeypatch.setattr(gemini, 'generate_commentary', fallback)
    commentary.generate_commentary('{"percentage": 5}', '{"amount_pln": 987654}')
    assert setup.call_args.kwargs['json']['messages'][0]['content'] == '{"amount_pln": 987654}'
    fallback.assert_called_once_with('{"percentage": 5}')


def test_gemini_only_never_receives_rich_payload(setup, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    fallback = Mock(return_value='Review')
    monkeypatch.setattr(gemini, 'generate_commentary', fallback)
    commentary.generate_commentary('safe', 'private')
    fallback.assert_called_once_with('safe')
    setup.assert_not_called()


def test_rich_history_reconciles_and_gates_missing_data(client, make_snapshot, make_cash_flows):
    import app as app_module
    import db
    make_snapshot('2025-Q1', '2025-03-31', portfolio=1000, cash=100, ppk=50, mortgage=200)
    make_snapshot('2025-Q2', '2025-06-30', portfolio=1300, cash=120, ppk=70, mortgage=180)
    make_cash_flows(('2025-05-01', 'deposit', 200))
    data = app_module._build_dashboard_data()
    payload = app_module._build_claude_payload(data)
    assert payload['quarterly_history'][-1]['return_breakdown'] is None
    assert payload['market_return_pct_excluding_contributions'] is None
    coverage = db.get_quality_inputs()[2]
    db.confirm_cash_flow_coverage('2025-06-30', coverage['revision'])
    data = app_module._build_dashboard_data()
    payload = app_module._build_claude_payload(data)
    latest = payload['quarterly_history'][-1]
    breakdown = latest['return_breakdown']
    assert breakdown['opening_investments_plus_cash_excluding_ppk_pln'] == 1100
    assert breakdown['closing_investments_plus_cash_excluding_ppk_pln'] == 1420
    assert breakdown['net_contributions_pln'] == 200
    assert breakdown['residual_change_after_contributions_pln'] == 120
    assert latest['net_worth_pln'] == 1310
    assert payload['quarterly_history'][0]['net_contributions_pln'] is None
    import json
    assert 'Test Account' not in json.dumps(payload)
    data['data_quality']['snapshots'][0]['balances']['cash'] = 'missing'
    latest = app_module._build_claude_payload(data)['quarterly_history'][-1]
    assert latest['cash_pln'] is None
    assert latest['net_worth_pln'] is None
    assert latest['return_breakdown'] is None
