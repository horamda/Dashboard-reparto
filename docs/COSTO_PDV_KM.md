# Costo de atención por kilómetros

Días Pico → Cluster de clientes → Costo PDV muestra un bloque independiente de
las estimaciones por HL. Tarifa inicial: **ARS 2.500/km, todos los gastos incluidos**.
No vuelve a sumar combustible, personal, vehículo ni almacenamiento.

Para cada cliente y recorrido:

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

Las distancias son de ruteo, no GPS real. Los fallbacks se identifican como
aproximados. Sin tramos o sin regreso no se calcula un importe. Si sólo algunas
atenciones tienen distancia, se muestra costo parcial y se omiten los cocientes
$/bulto y costo/venta; el promedio divide sólo por atenciones con km.
Los recorridos sin clientes identificados se reflejan en la cobertura de rutas,
pero no se atribuyen arbitrariamente a un PDV. Los totales siempre corresponden
a atenciones registradas, no garantizan cobertura de todas las entregas reales.

El cluster es el vigente en Días Pico. No se reconstruye el cluster histórico.
