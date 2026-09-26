"""Undo must restore exactly the reviewed dataset without erasing later work."""
import io
import json

import pytest

import db
from test_improvements import csv_file, upload
from test_api import _xlsx_bytes, VALID_HEADER


def save_csv(client, amount='200'):
    preview = upload(client, csv_file(amount), preview='1').get_json()
    response = upload(client, csv_file(amount), replace='1', preview_token=preview['preview_token'])
    assert response.status_code == 200
    return response.get_json()['import_id']


def history(client):
    return client.get('/api/import-history').get_json()['items']


def preview_undo(client, iid):
    return client.post(f'/api/import-history/{iid}/undo', json={'preview': True})


def undo(client, iid):
    preview = preview_undo(client, iid)
    assert preview.status_code == 200
    return client.post(f'/api/import-history/{iid}/undo', json={'preview_token':preview.get_json()['preview_token']})


def test_history_records_only_committed_imports(client):
    assert history(client) == []
    upload(client, csv_file(), preview='1')
    upload(client, csv_file('bad'))
    assert history(client) == []
    iid = save_csv(client)
    row = history(client)[0]
    assert row['id'] == iid
    assert row['filename'] == 'positions_2026-06-30.csv'
    assert row['summary']['total_value'] == 200
    assert row['imported_at'].endswith('+00:00')
    assert row['undo_reason'] is None
    assert 'before_data' not in row  # summary endpoint doesn't expose raw records


def test_undo_replacement_preserves_later_manual_balances(client, make_snapshot):
    sid = make_snapshot('2026-Q2','2026-06-30',portfolio=100,ppk=10)
    original = db.get_positions(sid)
    iid = save_csv(client)
    db.save_manual_entries(sid, [dict(type='cash',amount_pln=42,original_amount=42)])
    balances = db.get_manual_entries(sid)
    assert preview_undo(client,iid).get_json()['restored']['total']==100
    assert db.get_positions(sid)[0]['value_pln']==200
    assert undo(client,iid).status_code==200
    assert db.get_positions(sid)==original
    assert db.get_manual_entries(sid)==balances
    assert history(client)[0]['undone_at']
    assert preview_undo(client,iid).status_code==409


def test_undo_new_snapshot_removes_only_that_snapshot(client, make_snapshot):
    older = make_snapshot('2026-Q1','2026-03-31',portfolio=100)
    iid = save_csv(client)
    assert undo(client,iid).status_code==200
    assert [s['id'] for s in db.get_snapshots()]==[older]


def test_new_snapshot_undo_protects_manual_balances_added_after_preview(client):
    iid = save_csv(client)
    token = preview_undo(client,iid).get_json()['preview_token']
    sid = db.get_snapshots()[0]['id']
    db.save_manual_entries(sid,[dict(type='cash',amount_pln=0,original_amount=0)])
    response=client.post(f'/api/import-history/{iid}/undo',json={'preview_token':token})
    assert response.status_code==409
    assert 'saved balances' in response.get_json()['error']
    assert db.get_positions(sid)
    assert not history(client)[0]['undone_at']


def test_multiple_imports_must_be_undone_in_reverse_order(client, make_snapshot):
    sid=make_snapshot('2026-Q2','2026-06-30',portfolio=100)
    a=save_csv(client,'200')
    token=preview_undo(client,a).get_json()['preview_token']
    b=save_csv(client,'300')
    assert client.post(f'/api/import-history/{a}/undo',json={'preview_token':token}).status_code==409
    assert undo(client,b).status_code==200
    assert db.get_positions(sid)[0]['value_pln']==200
    assert undo(client,a).status_code==200
    assert db.get_positions(sid)[0]['value_pln']==100


def save_flows(client, amount):
    file=_xlsx_bytes(VALID_HEADER,('2025-01-01','Wpłata automatyczna',amount,'PLN',1,amount,'Cash','P'))
    response=client.post('/api/import-cashflows',data={'file':(file,'flows.xlsx')})
    assert response.status_code==200
    return response.get_json()['import_id']


def coverage(through):
    _,_,current=db.get_quality_inputs()
    db.confirm_cash_flow_coverage(through,current['revision'])


def test_cashflow_undo_restores_rows_and_previous_coverage(client, make_cash_flows):
    make_cash_flows(('2025-01-01','deposit',1000))
    coverage('2025-12-31')
    before=db.get_quality_inputs()
    iid=save_flows(client,2000)
    coverage('2026-03-31')
    preview=preview_undo(client,iid).get_json()
    assert preview['current']['confirmed_through']=='2026-03-31'
    assert preview['restored']['confirmed_through']=='2025-12-31'
    assert undo(client,iid).status_code==200
    assert db.get_quality_inputs()==before


def test_coverage_change_after_undo_preview_requires_review_again(client, make_cash_flows):
    make_cash_flows(('2025-01-01','deposit',1000))
    iid=save_flows(client,2000)
    token=preview_undo(client,iid).get_json()['preview_token']
    coverage('2025-12-31')
    response=client.post(f'/api/import-history/{iid}/undo',json={'preview_token':token})
    assert response.status_code==409
    assert db.get_cash_flow_summary()['net_invested']==2000


def test_unjournaled_changes_and_deleted_snapshots_block_undo(client, make_snapshot):
    sid=make_snapshot('2026-Q2','2026-06-30',portfolio=100)
    iid=save_csv(client)
    with db.get_db() as conn:
        conn.execute('UPDATE positions SET value_pln=201 WHERE snapshot_id=?',(sid,))
    assert preview_undo(client,iid).status_code==409
    db.delete_snapshot(sid)
    assert preview_undo(client,iid).status_code==409
    assert len(history(client))==1


def test_undo_tokens_are_required_and_bound_to_import(client):
    a=save_csv(client)
    token=preview_undo(client,a).get_json()['preview_token']
    b=save_flows(client,100)
    assert client.post(f'/api/import-history/{b}/undo',json={}).status_code==409
    assert client.post(f'/api/import-history/{b}/undo',json={'preview_token':token}).status_code==409
    assert client.post(f'/api/import-history/{b}/undo',json={'preview_token':123}).status_code==409
    assert client.post('/api/import-history/999/undo',json={'preview':True}).status_code==404
    assert client.post(f'/api/import-history/{b}/undo',json=[]).status_code==400


def test_undo_and_import_journal_roll_back_together(client, make_snapshot):
    sid=make_snapshot('2026-Q2','2026-06-30',portfolio=100)
    with db.get_db() as conn:
        conn.execute("CREATE TRIGGER reject_journal BEFORE INSERT ON import_history BEGIN SELECT RAISE(ABORT,'journal failure'); END")
    with pytest.raises(Exception,match='journal failure'):
        save_csv(client)
    assert db.get_positions(sid)[0]['value_pln']==100
    assert history(client)==[]
    with db.get_db() as conn:
        conn.execute('DROP TRIGGER reject_journal')
    iid=save_csv(client)
    with db.get_db() as conn:
        conn.execute("CREATE TRIGGER reject_undo BEFORE UPDATE ON import_history BEGIN SELECT RAISE(ABORT,'undo failure'); END")
    with pytest.raises(Exception,match='undo failure'):
        undo(client,iid)
    assert db.get_positions(sid)[0]['value_pln']==200
    assert history(client)[0]['undone_at'] is None


def test_history_paginates_and_newer_other_scope_does_not_block(client):
    iid=save_csv(client)
    for i in range(26):
        save_flows(client,i+1)
    first=client.get('/api/import-history').get_json()
    second=client.get('/api/import-history',query_string={'before':first['next_before']}).get_json()
    assert len(first['items'])==25
    assert len(second['items'])==2 and second['next_before'] is None
    assert first['items'][-1]['id']>second['items'][0]['id']
    assert undo(client,iid).status_code==200
    assert client.get('/api/import-history?before=bad').status_code==400


def test_history_and_undo_require_login(client, monkeypatch):
    monkeypatch.setenv('DASHBOARD_PASSWORD','test')
    assert client.get('/api/import-history').status_code==302
    assert client.post('/api/import-history/1/undo',json={'preview':True}).status_code==302
