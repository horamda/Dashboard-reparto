"""Authenticated live sales explorer, separate from the saved API snapshot."""
import json
import secrets
from datetime import date
from flask import Blueprint, Response, render_template, request, url_for, session, redirect
from logistics_db import read_source, SOURCES, SourceNotConfigured


def register_logistics_db_view(app, require_login, css):
    bp = Blueprint('logistics_db', __name__)

    @bp.route('/api/otif-comprobantes-dashboard')
    def dashboard_comprobantes():
        blocked = require_login()
        if blocked:
            return blocked
        from processed_logistics import dashboard_rows
        try:
            return Response(json.dumps(dashboard_rows(), ensure_ascii=False, default=str),
                            mimetype='application/json', headers={'Cache-Control': 'private, no-store'})
        except Exception:
            app.logger.error('No se pudo leer OTIF procesado para el dashboard')
            return Response('{"error":"No se pudo consultar OTIF. Reintentá la carga."}',
                            status=503, mimetype='application/json')

    @bp.route('/cumplimiento-comprobantes')
    def comprobantes():
        blocked = require_login()
        if blocked:
            return blocked
        from comprobantes_analysis import read_comprobantes
        args = request.args.to_dict()
        if not request.args:
            args.update(desde=f'{date.today().year}-01-01', hasta=date.today().isoformat())
        try:
            data = read_comprobantes(args, export=args.get('descargar') == 'json')
            if args.get('descargar') == 'json':
                return Response(json.dumps(data,ensure_ascii=False,default=str),mimetype='application/json',
                                headers={'Content-Disposition':'attachment; filename="comprobantes.json"'})
            def link(**changes):
                values=dict(args)
                values.pop('descargar',None)
                values.update(changes)
                return url_for('logistics_db.comprobantes',**values)
            token=session.setdefault('logistics_csrf',secrets.token_urlsafe(32))
            return render_template('comprobantes.html',css=css,data=data,link=link,token=token,
                                   dumps=lambda v:json.dumps(v,ensure_ascii=False,indent=2,default=str))
        except (ValueError, SourceNotConfigured) as exc:
            return render_template('datos_logistica.html',css=css,error=str(exc)),400
        except Exception:
            app.logger.error('No se pudo calcular el resumen de comprobantes')
            return render_template('datos_logistica.html',css=css,error='No se pudo completar la consulta de comprobantes. Acotá el período e intentá nuevamente.'),503

    @bp.route('/cumplimiento-comprobantes/sincronizar',methods=['POST'])
    def sincronizar():
        blocked=require_login()
        if blocked:
            return blocked
        expected=session.get('logistics_csrf','')
        if not expected or not secrets.compare_digest(expected,request.form.get('csrf','')):
            return Response('Sesión vencida. Volvé a abrir la página.',status=403)
        from processed_logistics import sync_processed
        try:
            result=sync_processed(request.form.get('desde',''),request.form.get('hasta',''),request.form.get('modo','ventas'))
            if request.form.get('volver') == 'admin':
                session['logistics_notice'] = (f"Actualización completada: {result['documentos']} comprobantes del "
                                               f"{result['desde']} al {result['hasta']}. Recargá los resultados del dashboard.")
                return redirect(url_for('admin', _anchor='sincronizacion-logistica'))
            return redirect(url_for('logistics_db.comprobantes',desde=result['desde'],hasta=result['hasta']))
        except ValueError as exc:
            return render_template('datos_logistica.html',css=css,error=str(exc)),400
        except Exception:
            app.logger.error('La sincronización procesada falló; se conservaron los datos anteriores')
            return render_template('datos_logistica.html',css=css,error='No se pudo completar la sincronización. Los datos procesados anteriores se conservan.'),503

    @bp.route('/datos-logistica')
    def view():
        blocked = require_login()
        if blocked:
            return blocked
        args = request.args.to_dict()
        if not request.args:
            args.update(desde=f'{date.today().year}-01-01', hasta=date.today().isoformat())
        try:
            data = read_source(args, export=args.get('descargar') == 'json')
            if args.get('descargar') == 'json':
                payload = dict(origen_ventas='PostgreSQL logistica: ventas_detalle',
                               origen_visitas='Base de esta app: Foxtrot', **data)
                return Response(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                mimetype='application/json', headers={
                                    'Content-Disposition': 'attachment; filename="ventas-cruce-foxtrot.json"',
                                    'Cache-Control': 'private, no-store'})

            def link(**changes):
                values = dict(args)
                values.pop('descargar', None)
                values.update(changes)
                return url_for('logistics_db.view', **values)

            return render_template('datos_logistica.html', css=css, data=data, sources=SOURCES,
                                   link=link, dumps=lambda v: json.dumps(v, ensure_ascii=False, indent=2, default=str))
        except SourceNotConfigured as exc:
            return render_template('datos_logistica.html', css=css, error=str(exc)), 503
        except ValueError as exc:
            return render_template('datos_logistica.html', css=css, error=str(exc)), 400
        except Exception:
            # Do not expose connection details or fall back to stale API data.
            app.logger.error('No se pudo consultar la base logistica o las visitas del dashboard')
            return render_template('datos_logistica.html', css=css,
                                   error='No se pudo completar la consulta. Revisá la conexión o acotá los filtros; no se muestran datos de otra fuente.'), 503

    app.register_blueprint(bp)
