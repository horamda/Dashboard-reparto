from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock,patch
import pytest
from processed_logistics import covered,document_key,period,sync_processed


def test_coverage_requires_no_gaps_and_supports_empty_completed_ranges():
    ranges=[(date(2026,1,1),date(2026,1,31)),(date(2026,2,1),date(2026,2,28))]
    assert covered(ranges,'2026-01-01','2026-02-28')
    assert not covered(ranges,'2026-01-01','2026-03-01')
    assert not covered([(date(2026,1,2),date(2026,1,31))],'2026-01-01','2026-01-31')


def test_identity_does_not_depend_on_order_or_source_line_ids():
    row=dict(empresa='1',sucursal='1',cliente='001',fecha='2026-01-01',doc_key=['FC','A','1','20'],ids=[1])
    assert document_key(row)==document_key(dict(row,ids=[2,3]))
    assert document_key(row)!=document_key(dict(row,cliente='1'))
    assert period({'mes':'2026-02'})['hasta']=='2026-02-28'
    assert period({'mes':'2026-02','dia':'2026-09-17'})['desde']=='2026-09-17'


def test_external_failure_does_not_delete_previous_projection():
    cursor=MagicMock()
    cursor.fetchone.return_value=(True,)
    cn=MagicMock()
    cn.cursor.return_value.__enter__.return_value=cursor
    @contextmanager
    def conn():
        yield cn
    with patch('processed_logistics.ensure_schema'),patch('storage._conn',conn),patch('comprobantes_analysis.load_source_documents',side_effect=RuntimeError('source unavailable')):
        with pytest.raises(RuntimeError):
            sync_processed('2026-01-01','2026-01-31')
    assert not any('DELETE' in str(c) for c in cursor.execute.call_args_list)


def test_reads_do_not_contact_external_database():
    from comprobantes_analysis import read_comprobantes
    with patch('processed_logistics.read_processed',return_value={'local':True}) as read,patch('logistics_db.psycopg2.connect',side_effect=AssertionError('external connection')):
        assert read_comprobantes({'dia':'2026-09-17'})=={'local':True}
        read.assert_called_once_with({'dia':'2026-09-17'},export=False)


def test_sync_post_requires_login_and_csrf():
    import os
    os.environ['DISABLE_CACHE_PREWARM']='1'
    from app import app
    app.config.update(TESTING=True,SECRET_KEY='test')
    client=app.test_client()
    assert client.post('/cumplimiento-comprobantes/sincronizar').status_code==302
    with client.session_transaction() as s:
        s['admin_logged_in']=True
    assert client.post('/cumplimiento-comprobantes/sincronizar').status_code==403
