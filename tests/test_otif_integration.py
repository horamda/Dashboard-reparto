import io
import json
import unittest
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
from otif_integration import fetch_orders, diagnose_links
import pipeline


class OrdersIntegrationTests(unittest.TestCase):
    config = {"base_url": "https://example.test", "api_key": "test-key", "timeout": 1}

    def test_chunks_and_pagination_with_empresa(self):
        calls = []
        def opener(request, **kwargs):
            query = parse_qs(urlparse(request.full_url).query)
            calls.append(query)
            self.assertEqual(query["empresa_id"], ["1"])
            offset = int(query['offset'][0])
            row = {"id_integracion": query['desde'][0]+str(offset), "fecha_entrega": query['desde'][0]}
            return io.StringIO(json.dumps({"api_version": "v1", "datos": [row], "paginacion": {"offset": offset, "devueltos": 1, "total": 2, "hay_mas": offset == 0}}))
        result = fetch_orders(self.config, "2026-01-01", "2026-02-02", opener=opener)
        self.assertEqual(len(result['datos']), 4)
        self.assertEqual(calls[0]['hasta'], ['2026-01-31'])
        self.assertEqual(calls[2]['desde'], ['2026-02-01'])

    def test_missing_key_does_not_call_api(self):
        with self.assertRaisesRegex(ValueError, "API_KEY"):
            fetch_orders({}, "2026-01-01", "2026-01-01", opener=lambda *a,**k:self.fail())

    def sales_response(self, movement):
        return io.StringIO(json.dumps({'api_version': 'v1', 'contrato': 'comprobantes_ventas_v2',
            'datos': [{'id_integracion': 'doc1', 'empresa_id': '1', 'fecha_entrega': None,
                       'fecha_movimiento': movement}],
            'paginacion': {'total': 1, 'offset': 0, 'devueltos': 1, 'hay_mas': False}}))

    def test_sales_contract_validates_movement_without_inventing_delivery_date(self):
        result = fetch_orders(self.config, '2026-09-01', '2026-09-17',
                              opener=lambda *a, **k: self.sales_response('2026-09-17'))
        self.assertEqual(result['contrato'], 'comprobantes_ventas_v2')
        self.assertIsNone(result['datos'][0]['fecha_entrega'])
        self.assertEqual(result['datos'][0]['fecha_movimiento'], '2026-09-17')

    def test_sales_contract_still_rejects_dates_outside_range(self):
        with self.assertRaisesRegex(ValueError, 'fecha_movimiento=2026-09-18 fuera'):
            fetch_orders(self.config, '2026-09-01', '2026-09-17',
                         opener=lambda *a, **k: self.sales_response('2026-09-18'))
        with self.assertRaisesRegex(ValueError, 'falta fecha_movimiento'):
            fetch_orders(self.config, '2026-09-01', '2026-09-17',
                         opener=lambda *a, **k: self.sales_response(None))

    def test_rejection_feed_uses_company_and_customer_day_identity(self):
        def opener(request, **kwargs):
            query = parse_qs(urlparse(request.full_url).query)
            self.assertEqual(query['empresa_id'], ['1'])
            self.assertTrue(urlparse(request.full_url).path.endswith('/rechazos/clientes-diario'))
            return io.StringIO(json.dumps({'api_version': 'v1', 'datos': [
                {'empresa_id': '1', 'fecha': '2026-01-01', 'cliente_id': '001', 'tiene_rechazo': True}
            ], 'paginacion': {'offset': 0, 'devueltos': 1, 'total': 1, 'hay_mas': False}}))
        result = fetch_orders(self.config, '2026-01-01', '2026-01-01', opener=opener, rejection_feed=True)
        self.assertTrue(result['datos'][0]['tiene_rechazo'])

    def test_rejection_fetch_failure_preserves_previous_snapshot(self):
        with patch('otif_integration.fetch_orders', side_effect=[{'datos': []}, ValueError('rechazos incomplete')]), patch.object(pipeline.storage, 'save_setting') as save:
            with self.assertRaises(ValueError):
                pipeline.sincronizar_pedidos_otif('2026-01-01', '2026-01-31')
        save.assert_not_called()

    def test_incomplete_pagination_rejected(self):
        def opener(*args, **kwargs):
            return io.StringIO(json.dumps({"api_version":"v1", "datos":[], "paginacion":{"offset":0,"devueltos":0,"total":3,"hay_mas":False}}))
        with self.assertRaisesRegex(ValueError, "incompleta"):
            fetch_orders(self.config, "2026-01-01", "2026-01-01", opener=opener)

    def test_customer_day_is_not_an_order_link(self):
        order = {"numero_pedido":"P1","cliente_id":"001","sucursal_id":"1","fecha_entrega":"2026-01-01","estado_entrega":"completa"}
        route = {"fecha":"2026-01-01","sucursal_id":"1"}
        attempt = {"route_id":"r1","cliente":"001"}
        result = diagnose_links([order], [attempt], {"r1":route})
        self.assertEqual(result['sin_vinculo'], 1)
        attempt['Order ID'] = 'P1'
        self.assertEqual(diagnose_links([order],[attempt],{'r1':route})['vinculos_candidatos'],1)
        self.assertEqual(diagnose_links([order],[attempt,attempt],{'r1':route})['ambiguos'],1)
        attempt['cliente'] = '1'
        self.assertEqual(diagnose_links([order],[attempt],{'r1':route})['sin_vinculo'],1)

    def test_failed_fetch_does_not_replace_snapshot(self):
        with patch('otif_integration.fetch_orders',side_effect=ValueError('incomplete')), patch.object(pipeline.storage,'save_setting') as save:
            with self.assertRaises(ValueError):
                pipeline.sincronizar_pedidos_otif('2026-01-01','2026-01-31')
        save.assert_not_called()
