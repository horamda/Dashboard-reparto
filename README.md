# Dashboard de reparto

Aplicacion Flask de Del Palacio S.A. para operacion de reparto, calidad Foxtrot,
pedidos, FichaYA y costos logisticos. Usa PostgreSQL en produccion y archivos JSON
como respaldo para desarrollo local.

## Modulos

- Dashboard operativo: TML, TI, horas, adherencia, dispersion, on time, OTIF,
  rechazos, DQI, DPO y satisfaccion.
- Pedidos: importacion XLSX, filtros operativos, ventas, bultos, HL y pallets.
- Calidad Foxtrot: auditoria y correccion de columnas del export.
- FichaYA: asociacion por legajo y reporte comparativo con Foxtrot.
- Equipos de Casa Central: `/equipos-reparto` muestra chofer y ayudantes de DPO
  por fecha y camion, con filtros y avisos de vinculacion, cantidades y posibles
  recargas. `/asociar-fichaya` incluye personas de Foxtrot y DPO; requiere importar
  el catalogo de empleados FichaYA. Los legajos nuevos conservan ceros iniciales
  cuando el Excel los contiene como texto. Los legajos antiguos que hayan perdido
  ceros requieren reimportar el catalogo y revisar sus asociaciones.
  Esta vista consulta las fuentes DPO actuales; aun no guarda equipos historicos
  ni envia resultados de KPIs a FichaYA.
- Costos de distribucion: depositos CRUD, tarifas por vigencia, perfiles de
  vehiculo, ruteo vial, asignacion por cliente e historial de recalculos.
- Datos cargados: busqueda, paginacion, edicion y borrado controlado.

## Inicio local

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

La aplicacion queda disponible en `http://127.0.0.1:5050`. Completar en `.env`
como minimo `ADMIN_PASSWORD`; para PostgreSQL, definir `DATABASE_URL`.

## Importaciones

Desde `/admin` se pueden cargar:

- Route Analytics `.xls` o `.xlsx`.
- Attempt Analytics `.csv`, `.xls` o `.xlsx`.
- Clientes y ventanas horarias `.csv`.
- Rechazos, articulos, volumen entregado y asignacion de vehiculos.

Las rutas se actualizan por `Route ID`. Las visitas se actualizan por ruta y
cliente. Los lotes usan `execute_values`, indices dedicados y un pool de
conexiones para evitar escrituras fila por fila.

El módulo DQI toma de la hoja publicada únicamente `Depósito = 7`, `TIPO = DQI`
y `TIPOMERC = MERCADERIA`; envases y esqueletos quedan excluidos. Los indicadores de calidad usan `DQI_WQI_BULTOS` y
`DQI_WQI_HL`; `BULTOS_REAL` y `ROTURA_HL_REAL` se conservan como rotura física
separada. La solapa DQI presenta ambas familias en bloques independientes. Antes
de sumar se descartan solo las filas que son duplicados exactos. Team Room
repite las cuatro métricas y las acumula por día, semana y mes.

## Costos logisticos

La configuracion incluye combustible, costo por kilometro, mano de obra,
ayudantes, otros costos, criterio de volumen y pesos de asignacion. Los pesos de
distancia, tiempo y volumen deben sumar `1.00`.

Cada calculo conserva:

- distancia y duracion por tramo;
- geometria y proveedor de ruteo;
- componentes del costo total;
- asignacion por cliente;
- version, usuario, fecha y motivo de recalculo.

El ruteo usa OSRM y guarda cache persistente. Si el proveedor no responde, el
calculo usa distancia Haversine y deja una advertencia trazable.

## Variables principales

Consultar [`.env.example`](.env.example). Las mas relevantes son:

- `DATABASE_URL`, `SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD`.
- `PG_POOL_MAX`, `PG_INSERT_PAGE_SIZE`, `PGSTATEMENT_TIMEOUT_SECONDS`.
- `STORAGE_CACHE_TTL_SECONDS`, `DASHBOARD_CACHE_TTL_SECONDS`.
- `OSRM_BASE_URL`, `ROUTING_TIMEOUT_SECONDS`.
- Credenciales FichaYA y `OPENAI_API_KEY`, ambas opcionales.

No guardar `.env` ni credenciales en Git.

FichaYA usa por defecto `FICHAYA_INTEGRATION_MODE=web`: inicia sesión con
`FICHAYA_WEB_USERNAME` / `FICHAYA_WEB_PASSWORD` y descarga el CSV de marcas, sin
requerir la API externa. Los modos `api` y `auto` quedan disponibles para cuando
esa integración sea habilitada. Un `.env` local no se copia a Railway: las
variables del modo elegido deben cargarse también en el servicio desplegado.

El reporte FichaYA/Foxtrot permite guardar ajustes manuales por `Route ID` para
las cuatro marcas horarias. Se persisten aparte de los datos importados, por lo
que una sincronización posterior no los sobrescribe. Cada ajuste registra
motivo, usuario y fecha; la acción `Restablecer origen` elimina únicamente el
ajuste y vuelve a mostrar los valores recibidos de FichaYA/Foxtrot.

La API logística v1 completa campos faltantes de las rutas con
`GET /api/v1/integracion/logistica/diaria`. El consumidor usa
`LOGISTICS_INTEGRATION_API_BASE_URL` y `LOGISTICS_INTEGRATION_API_KEY`; esta
última debe coincidir con `INTEGRATION_API_KEY` del servicio productor. El
emparejamiento es conservador por fecha, sucursal y chofer, no sobrescribe datos
existentes y omite coincidencias ambiguas. La sincronización se ejecuta desde
`/admin` y guarda la respuesta fuente dentro de `logistics_api` en cada ruta.

## Pruebas

```powershell
python -m pytest -q
```

Las pruebas cubren asignacion de costos, ruteo por lotes y fallback, importacion
de volumen/vehiculos, autenticacion, compresion gzip y respuestas condicionales.

## Produccion

Railway ejecuta Gunicorn con un proceso y cuatro hilos. Esto conserva memoria y
permite atender otras solicitudes mientras una importacion espera por la base o
una integracion externa. El timeout es de 180 segundos para archivos grandes.

`/salud` devuelve el estado y los conteos agregados sin descargar las tablas
completas. Las migraciones son idempotentes; para forzarlas al iniciar se puede
definir `RUN_DB_MIGRATIONS_ON_START=1`.
