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
    assert body['system'] == gemini.SYSTEM_PROMPT
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
