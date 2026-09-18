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
  `/equipos-reparto/historico` guarda los equipos por dia con sus legajos actuales
  y asigna rutas cuando fecha, sucursal, camion y legajo del chofer coinciden con
  un unico equipo sin inconsistencias. Recargas ambiguas quedan para revision.
  El boton de importacion conserva los dias existentes; actualizar un dia requiere
  motivo, conserva la version anterior y vuelve a evaluar las rutas (incluidas
  las asignaciones manuales). La asignacion manual permite vincular/desvincular
  rutas validas del mismo dia y sucursal con motivo y auditoria.
  No se importan fechas futuras ni fuentes con errores. Los cambios concurrentes
  se detectan por version. PostgreSQL usa registros `equipos_reparto:YYYY-MM-DD`
  en `settings_dashboard` con bloqueo transaccional, sin una migracion nueva.
  El respaldo local usa `data/equipos_reparto/` con escritura atomica y bloqueo
  entre hilos del proceso.
- KPIs a FichaYA: `/kpis-fichaya` prepara y envia resultados diarios por integrante
  desde el historico. El catalogo inicial reproduce el Excel de Operaciones
  (empresa 1, sector 1): dispersion KM `2`, dispersion tiempo `3`, click `4`,
  TML `6` y TI `7`. DQI `5` (PPM), RMD `RMD` (%), rechazos `1` y NPS `8`/`9`
  quedan pendientes de confirmar formula/unidad/atribucion; no se envian.
  Las dispersiones usan `(plan-real)/plan*100` con totales diarios, click es el
  promedio por ruta y TML/TI usan las fichadas por legajo del chofer y ajustes
  guardados, compartidos con su equipo. No se usan tiempos simulados ni el cruce
  de fichadas por nombre. Cada empleado recibe una sola fila por dia/codigo.
  Preparar guarda una vista previa; enviar requiere pulsar el boton de envio.
  Se rechaza el borrador si cambiaron los equipos, datos o configuracion.
  Cada lote admite hasta 1000 filas (maximo 10000 por borrador). Los lotes ya
  confirmados no se reenvian al continuar. Errores 400/422 requieren un nuevo
  borrador corregido; cortes de conexion y errores temporales permiten reintentar
  exactamente el lote persistido. Una correccion posterior impide reintentar
  borradores anteriores sobre las mismas claves. Se conserva cada intento.
  El historial vive en `settings_dashboard` (prefijo `fichaya_kpis:`) o en
  `data/fichaya_kpis/` local. PostgreSQL serializa envios mediante advisory lock;
  el respaldo JSON utiliza un bloqueo dentro de un solo proceso.
  Requiere credenciales tecnicas `FICHAYA_API_USERNAME/FICHAYA_API_PASSWORD`
  y habilitar `EXTERNAL_API_KPI_WRITE_ENABLED=1` en el backend de FichaYA. El modo
  web de lectura de fichadas puede mantenerse. No se guardan tokens en la base.
  La API valida empleados activos, empresa y KPI del sector al recibir el lote.
  Cambiar de legajo/codigo no borra valores anteriores: corregirlos en FichaYA.
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


### Copias antes de rehacer la base

La opcion de rehacer la base crea un respaldo antes de borrar rutas e intentos
(attempts). Si falla la copia, el borrado se cancela. El archivo de importacion
se procesa antes del borrado; un export invalido o sin rutas no vacia la base.

En PostgreSQL, `dashboard_reset_backups` conserva cada copia con un UUID,
`created_at` y `payload`. El payload version 1 contiene las filas completas de
`rutas_dashboard` y `attempts_dashboard`, incluidas columnas tipadas, datos
originales y marcas de simulacion. La copia y el borrado se confirman en una
misma transaccion, bloqueando escrituras concurrentes en esas dos tablas.
La tabla de respaldos no se borra al rehacer la base.

En modo JSON, las copias verificadas se guardan en `DATA_DIR/backups/` como
`before-reset-<UUID>.json`. El payload conserva los documentos originales
bajo `files.rutas` y `files.attempts`. Estas copias requieren un volumen
persistente si la aplicacion se ejecuta en un contenedor.

El resultado de la importacion muestra el identificador de respaldo. Para
consultar las copias de PostgreSQL:

```sql
SELECT backup_id, created_at,
       jsonb_array_length(payload->'rutas_dashboard') AS rutas,
       jsonb_array_length(payload->'attempts_dashboard') AS attempts
FROM dashboard_reset_backups
ORDER BY created_at DESC;
```

Los respaldos no caducan automaticamente. La restauracion requiere una accion
separada: no se mezclan ni sobrescriben registros actuales automaticamente.
Solo se respaldan las tablas que esta accion borra; no es una copia completa
de toda la DB ni protege contra la perdida de la propia instancia PostgreSQL.


### OTIF operativo por cliente y dia

La vista OTIF agrupa por sucursal, cliente y fecha de planilla. Usa la ultima consulta
completa guardada de pedidos, sin unir consultas antiguas que puedan contener registros
obsoletos. Incluye visitas Foxtrot sin pedido como pendientes dentro del rango consultado.
Los comprobantes identifican pedidos sin numero; los registros sin ambas referencias
permanecen pendientes. Las sucursales 1/2/3 corresponden a Central/Dolores/Chascomus.

Cumple cuando todos los pedidos figuran entregados en repartos y las visitas exitosas
estan dentro de las ventanas del maestro actual. No reconcilia cantidades con pedidos
originales. Una entrega parcial o rechazo acreditado determina no cumplimiento una sola
vez. Varias visitas con puntualidad distinta quedan pendientes; no se elige la primera.
Los timestamps con zona se convierten a Buenos Aires; fechas distintas no se cruzan.

La sincronizacion consulta pedidos y rechazos/clientes-diario (empresa configurada para
rechazos). Guarda la nueva consulta solo si ambos recorridos paginados terminan completos.
Los rechazos de ventas son contables: solo se atribuyen como incumplimiento si todos sus
comprobantes exactos y sucursal pertenecen al grupo. Coincidencias parciales o solo por
cliente/dia quedan pendientes. Se consulta el mismo rango; rechazos contabilizados fuera
de ese rango no estan cubiertos. Los snapshots anteriores pueden usar solo estados y
motivos de repartos; la interfaz indica que falta actualizar los rechazos de ventas.

El porcentaje es sobre clientes/dia evaluados (cumplen + no cumplen); los pendientes y
la cobertura se publican al lado y por mes. No acredita completitud del universo de pedidos
comprometidos. El filtro de chofer incluye dias compartidos en que participo y no atribuye
responsabilidad individual. El detalle paginado muestra referencias, visitas y motivos.

Si ningun cliente/dia tiene pedidos y visitas coincidentes, el porcentaje queda sin evaluar,
aunque existan rechazos confirmados; se muestran las fechas disponibles de ambas fuentes.


Se recupera puntualidad historica por cliente desde las listas guardadas en rutas sin
attempts: exige que clientes unicos con ventana = visitas puntuales + fuera de horario,
que la lista de fuera de horario este completa y que todos los contadores reconcilien.
No genera timestamps. Si hay attempts para la ruta se prioriza el detalle y no se rellena
con resumen. Las clasificaciones historicas conservan su origen visible, no se presentan
como recalculadas con el maestro actual. Resumenes incompletos permanecen pendientes.


El importador admite el contrato `comprobantes_ventas_v2`: valida `fecha_movimiento`
contra cada rango paginado, conserva `fecha_entrega` nula, envia empresa_id y verifica
la empresa recibida. Los comprobantes estructurados se muestran con tipo/letra/serie/numero.
No se convierte fecha contable en fecha de entrega: esos grupos quedan pendientes de
confirmar entrega incluso cuando hay un rechazo registrado. El contrato previo de
repartos sigue validando fecha_entrega. Un cambio de contrato durante la paginacion
aborta la consulta y conserva el snapshot anterior.
