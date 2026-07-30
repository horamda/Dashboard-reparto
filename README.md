# Dashboard de Tiempos de reparto — Foxtrot (app Flask + Postgres)

App web que muestra el dashboard y permite **subir el export nuevo desde el navegador**
para actualizarlo online. La base es **incremental**: cada actualización agrega solo los
días que faltan y nunca cambia lo ya cargado (cada ruta se identifica por Route ID y su
TI/TML es determinístico por ID).

## Estructura
```
dashboard-reparto/
├── app.py                   # servidor Flask (dashboard + carga de export)
├── pipeline.py              # procesa el export y arma el HTML
├── storage.py               # persistencia: Postgres (o JSON local si no hay DB)
├── plantilla_dashboard.html # plantilla del dashboard
├── requirements.txt
├── Procfile                 # web: gunicorn app:app --bind 0.0.0.0:$PORT
├── railway.json
└── data/                    # solo se usa en modo local sin DB
```

## Persistencia
- **Producción (Railway):** si existe la variable `DATABASE_URL`, guarda en Postgres,
  en la tabla `rutas_dashboard (rid TEXT PRIMARY KEY, rec JSONB)`. La tabla se crea sola
  al arrancar. La inserción usa `ON CONFLICT (rid) DO NOTHING`: solo entran rutas nuevas.
- **Local sin DB:** si no hay `DATABASE_URL`, cae a un archivo JSON en `DATA_DIR` (./data).

## Rutas
- `/`           → dashboard (o pantalla de carga si aún no hay datos)
- `/admin`      → formulario para subir el export
- `/actualizar` → recibe el `.xls` (+ CSV opcionales) y actualiza la base
- `/salud`      → chequeo simple (JSON)

## Probar local
```
pip install -r requirements.txt
python app.py            # http://localhost:5050  (usa JSON en ./data)
```

## Subir a Railway (con Postgres)
1. Subí esta carpeta a un repo de GitHub.
2. Railway → **New Project → Deploy from GitHub repo**. Nixpacks detecta Python y arranca
   con gunicorn (Procfile).
3. En el mismo proyecto: **New → Database → Add PostgreSQL**. Railway crea la DB y expone
   la variable `DATABASE_URL` al servicio automáticamente (si no, copiala en las Variables
   del servicio web con **Reference → DATABASE_URL**).
4. Variables de entorno del servicio web:
   - `DATABASE_URL`  → la del plugin Postgres (referenciada).
   - `ADMIN_TOKEN = unaclave`  → clave para poder subir datos (recomendado).
5. Deploy. Railway te da la URL pública: dashboard en `/`, carga en `/admin`.

> Con Postgres **no hace falta volumen**: la base vive en la DB y sobrevive a los deploys.

## Actualizar los datos
Entrá a `.../admin`, subí el export nuevo (y CSV de visitas si tenés) y confirmá. Se
agregan solo las rutas nuevas. La opción "Rehacer de cero" hace `TRUNCATE` y recalcula todo
(útil si sumás CSV de meses viejos para recuperar rutas sin cierre).

## Parámetros
En `pipeline.py` (arriba): `TI_CENTRO`, `TML_CENTRO` y `OBJ` (objetivos de TML/TI/ruta,
tolerancia de dispersión ±10% y adherencia ≥85%). Cambiás ahí y redeployás; lo ya guardado
no se recalcula salvo "Rehacer de cero".
