# Revisión de pantallas — 2026-09-19

Alcance: inventario de rutas y plantillas, navegación interna, formularios y reportes repetidos. Las acciones de escritura, borrado y envío externo se validan con pruebas aisladas; no se ejecutan sobre datos reales durante esta revisión.

| Pantalla | Función conservada / ajuste |
| --- | --- |
| Inicio | Directorio de módulos; acceso diferenciado a evidencia procesada y ventas originales. |
| Dashboard | Indicadores por filtros compartidos. Un solo cálculo OTIF por comprobante; se retiró la presentación anterior por cliente/día y su código de renderizado. |
| Mensual / Semanal / Diario | Distinta granularidad de tiempos; se conservan. |
| On Time / In Full / OTIF | Puntualidad de visitas, rechazos por comprobante y cumplimiento combinado, respectivamente; denominadores explícitos. |
| Rechazos / DQI / DPO / Team Room / Satisfacción / Calidad de datos | Distintos indicadores y fuentes; no se eliminan por compartir fechas o gráficos. |
| Administración | Único formulario de sincronización de ventas procesadas y recálculo Foxtrot. Consulta API anterior agrupada como histórica, sin presentarla como actualización de OTIF. |
| Datos locales | Edición y revisión de tablas de la app; enlaces claros a las consultas externas y procesadas. |
| Evidencia por comprobante | Inspección y exportación; un único formulario de filtros. Sin segundo formulario de sincronización. |
| Ventas en origen | Lectura de todos los campos externos; se retiran la copia JSON de cada fila y las columnas repetidas del cruce. Exportación completa conservada. |
| Archivo API | Consulta y exportación de la integración anterior, explícitamente separada del OTIF actual. |
| Calidad Foxtrot y sus alias | Revisión/corrección de columnas; los alias sirven el mismo controlador, no implementaciones separadas. |
| Reporte FichaYA / edición / asociación | Informe, ajuste y correspondencia de personas; funciones complementarias. |
| Equipos actuales / histórico | Fuente DPO frente a versiones guardadas; se conserva trazabilidad. |
| KPIs FichaYA / detalle de envío | Preparación frente a revisión y seguimiento; no se duplican envíos. |
| Pedidos | Análisis comercial por corte/franja/canal; distinto del cumplimiento de entrega. |
| Costos / dashboard de costos | Configuración y cálculo frente a histórico y comparación; ambos necesarios. |
| Login / errores / exportaciones | Flujos auxiliares; acceso autenticado conservado. |

Validación: suite Python, pruebas JavaScript de filtros y porcentajes, IDs únicos en dashboard y resolución de enlaces internos estáticos. Los enlaces directos a solapas conservan la selección en la URL. No se modifican ventas, rutas, rechazos ni resultados almacenados como parte de esta reorganización.
