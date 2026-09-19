"""Read-only explorer of the exact latest saved logistics API snapshot."""
import json
from functools import lru_cache
from math import ceil

import storage
from flask import Blueprint, Response, render_template, request, url_for


@lru_cache(maxsize=1)
def _snapshot(key, revision):
    return (storage.load_setting(key) or {}).get('valor', {})


def latest_import():
    meta = (storage.load_setting('otif_ultima_consulta') or {}).get('valor', {})
    if not meta:
        return {}
    key = 'otif_pedidos:' + ':'.join(str(meta.get(k, '')) for k in ('desde', 'hasta', 'sucursal'))
    return _snapshot(key, json.dumps(meta, sort_keys=True))


def select_records(snapshot, args):
    source = args.get('fuente', 'datos')
    if source not in ('datos', 'rechazos_clientes'):
        source = 'datos'
    records = snapshot.get(source) or []
    # Union across the entire import: sparse fields must remain discoverable.
    columns = list(dict.fromkeys(k for row in records for k in row))
    query = args.get('q', '').strip().casefold()
    date_field = args.get('campo_fecha', '')
    if date_field not in ('fecha_entrega', 'fecha_movimiento', 'fecha'):
        date_field = 'fecha' if source == 'rechazos_clientes' else (
            'fecha_movimiento' if snapshot.get('contrato') == 'comprobantes_ventas_v2' else 'fecha_entrega')
    selected = []
    for row in records:
        day = str(row.get(date_field) or '')[:10]
        if args.get('desde') and (not day or day < args['desde']):
            continue
        if args.get('hasta') and (not day or day > args['hasta']):
            continue
        if query and query not in json.dumps(row, ensure_ascii=False).casefold():
            continue
        selected.append(row)
    return source, records, columns, selected, date_field


def register_api_import_view(app, require_login, css):
    bp = Blueprint('api_import', __name__)

    @bp.route('/datos-api')
    def view():
        blocked = require_login()
        if blocked:
            return blocked
        try:
            snapshot = latest_import()
            source, records, columns, selected, date_field = select_records(snapshot, request.args)
            download = request.args.get('descargar')
            if download in ('filtrado', 'completo'):
                payload = snapshot if download == 'completo' else {
                    'metadatos': {k: v for k, v in snapshot.items() if k not in ('datos', 'rechazos_clientes')},
                    'fuente': source, 'filtros': {k: request.args.get(k, '') for k in ('q', 'campo_fecha', 'desde', 'hasta')},
                    'campo_fecha_aplicado': date_field, 'registros': selected,
                }
                return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                                mimetype='application/json', headers={
                                    'Content-Disposition': f'attachment; filename="importacion-api-{download}.json"',
                                    'Cache-Control': 'private, no-store'})
            pages = max(1, ceil(len(selected) / 50))
            try:
                page = min(pages, max(1, int(request.args.get('pagina', 1))))
            except ValueError:
                page = 1

            def link(**changes):
                args = request.args.to_dict()
                args.pop('descargar', None)
                args.update(changes)
                return url_for('api_import.view', **args)

            metadata = {k: v for k, v in snapshot.items() if k not in ('datos', 'rechazos_clientes')}
            return render_template('datos_api.html', css=css, snapshot=snapshot,
                                   metadata=metadata, source=source, columns=columns,
                                   rows=selected[(page-1)*50:page*50], count=len(selected),
                                   total=len(records), page=page, pages=pages, link=link,
                                   date_field=date_field, dumps=lambda v: json.dumps(v, ensure_ascii=False, indent=2))
        except Exception:
            app.logger.exception('No se pudo leer la importacion API guardada')
            return render_template('datos_api.html', css=css, error=True), 503

    app.register_blueprint(bp)
