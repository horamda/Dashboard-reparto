# Costo de atención por kilómetros

Días Pico → Cluster de clientes → Costo PDV muestra un bloque independiente de
las estimaciones por HL. Tarifa inicial: **ARS 2.500/km, todos los gastos incluidos**.
No vuelve a sumar combustible, personal, vehículo ni almacenamiento.

## M?todo predeterminado: costo prorrateado

Se utilizan los kil?metros reales de Foxtrot (`disp_km_real`, almacenados en metros):

```
km asignados = (metros reales del recorrido / 1000) / PDV identificados del recorrido
costo por atenci?n = km asignados ? tarifa
costo del per?odo = suma de costos de las atenciones del cliente
```

Es un reparto en partes iguales del costo total, **no una medici?n de distancia hasta cada local**.
No requiere calcular manualmente las rutas. Incluye intentos fallidos; se cuenta una sola
atenci?n por cliente y recorrido. Los filtros se aplican despu?s de repartir y no cambian
el denominador. No se suman otros gastos ni se reutilizan los importes de `route_costs`.
Sin kil?metros reales positivos, la atenci?n queda pendiente (nunca se usa el plan ni
la distancia del clic al cliente como reemplazo). El detalle muestra km reales, cantidad
de PDV y cuota asignada. La suma de cuotas conserva el costo del recorrido completo.

## M?todo opcional: tramos calculados

El selector permite conservar el c?lculo anterior para cada cliente y recorrido:

```
km asignados = tramo hasta el cliente + regreso × tramo / suma de tramos de ida
costo = km asignados × tarifa
```

El primer tramo sale del depósito; los siguientes salen del cliente anterior.
No se utiliza la distancia acumulada, que duplicaría tramos compartidos. El regreso
se distribuye proporcionalmente y la suma de kilómetros conserva el total del ruteo.
Los tramos son los del cálculo de ruta vigente en Reparto; un recálculo sustituye al
anterior en este informe. No se suman las versiones históricas.

## Activación

1. Publicar los cambios de ambos proyectos.
2. En Reparto, configurar `PDV_COST_API_KEY` con una clave secreta. Como alternativa
   se reutiliza `LOGISTICS_INTEGRATION_API_KEY` si no hay clave específica.
3. En Días Pico, configurar `REPARTO_COST_API_URL` con la URL raíz de Reparto y
   `REPARTO_COST_API_KEY` con la misma clave. Nunca se entrega esta clave al navegador.
   `REPARTO_COST_EMPRESA_ID` es `1` por defecto.
4. En Reparto, configurar depósitos y coordenadas de clientes y calcular las rutas
   para generar los tramos. El importe de ese cálculo anterior no se usa: este
   informe sólo toma sus distancias y aplica la tarifa elegida.
5. En Días Pico, seleccionar el período en Parámetros, actualizar la segmentación
   y abrir Costo PDV. El botón Calcular / actualizar consulta las distancias vigentes.

La tarifa puede modificarse para simular otros valores; no modifica los costos ni
el histórico de Reparto. Al recargar la pantalla vuelve a ARS 2.500/km. El CSV conserva
la tarifa aplicada, período, cobertura y filtros. Para guardar una simulación,
exportar el CSV. Los valores monetarios se calculan sin redondeo intermedio y se
muestran con dos decimales.

## Contrato y límites

`GET /api/integracion/v1/pdv-distancias?desde=YYYY-MM-DD&hasta=YYYY-MM-DD`
requiere `Authorization: Bearer <clave>`. Responde `version: 1`, período e `items`
por cliente y recorrido. El rango máximo es 366 días de diferencia. Se devuelve
error si excede 100.000 atenciones; nunca se entrega un total truncado.

El cruce usa código de cliente normalizado (sin ceros iniciales) **y sucursal**:
Casa Central / Mar de Ajó = 1, Dolores = 2, Chascomús = 3. No se asigna un cluster
por nombre del comercio. Los clientes sin coincidencia permanecen como Sin clasificar.

Una atención corresponde a un cliente en un recorrido. Las marcas repetidas del
mismo cliente dentro del recorrido se consolidan: no representan un conteo exacto
de reintentos. Una visita fallida conserva su costo. El estado de entrega aparece
en el detalle sin inferir éxito a partir de la existencia de una visita.

En el m?todo opcional por tramos, las distancias son de ruteo, no GPS real. Los fallbacks se identifican como
aproximados. Sin tramos o sin regreso no se calcula un importe. Si sólo algunas
atenciones tienen distancia, se muestra costo parcial y se omiten los cocientes
$/bulto y costo/venta; el promedio divide sólo por atenciones con km.
Los recorridos sin clientes identificados se reflejan en la cobertura de rutas,
pero no se atribuyen arbitrariamente a un PDV. Los totales siempre corresponden
a atenciones registradas, no garantizan cobertura de todas las entregas reales.

El cluster es el vigente en Días Pico. No se reconstruye el cluster histórico.

## Correcci?n de cobertura (25/09/2026)

El reporte original s?lo exportaba distancias de siete c?lculos guardados entre
1.840 recorridos del per?odo 01/01/2026?23/09/2026. Hab?a kil?metros reales positivos
en 1.830 recorridos. Adem?s, 2.602 coordenadas del maestro ten?an latitud redondeada
a dos decimales; tramos OSRM de cero metros no demuestran que atender al local sea gratis.
Se agreg? el prorrateo expl?cito sobre los km reales para poder costear sin inventar
tramos faltantes. Se preserv? el m?todo por tramos como opci?n con advertencia de cobertura.
Los datos del maestro original no se modificaron.

La API mantiene sus campos anteriores y agrega `km_recorrido_real`, `pdv_recorrido`,
`km_prorrateados`, `fuente_km_prorrateados` y `rutas_sin_km_reales` (incluye rutas
sin PDV identificados a los que atribuir km). La UI y el CSV identifican el m?todo.
Las pruebas de conservaci?n, duplicados, faltantes, filtros y selecci?n del m?todo
est?n en `tests/test_pdv_distance_api.py` de Reparto y `tests/test_pdv_km_cost.py` /
`tests/test_pdv_km_ui.cjs` de D?as Pico.
