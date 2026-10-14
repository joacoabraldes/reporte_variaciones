# Contexto del proyecto

Cálculo del índice de variaciones de precios, sobre los datos que captura el repo
**`relevamiento_precios`** (en el Desktop, al lado de éste).

**El proyecto son dos repos.**

```
relevamiento_precios                             reporte_variaciones (ESTE)
  descarga SEPA → Parquet → clasifica   ──▶        Jevons + Laspeyres
  → colapsa a quotes mensuales          bucket     encadenamiento, reporte
  ESCRIBE en gs://outlier-archivos-precios         solo LEE del bucket
```

**La interfaz es el bucket, no el código.** Este repo lee dos tablas Parquet:

- `staged/quotes_mensuales/anio=YYYY/mes=MM/` — un precio por quote por mes
- `staged/catalogo_productos/anio=YYYY/mes=MM/` — descripciones, para poder clasificar

No importa nada del otro repo. No sabe qué es SEPA, ni que los CSV vienen con separador
pipe, ni que hay 77 variantes de unidades de medida. Toda esa suciedad queda del otro lado.

**Por qué separados:** un bug allá cuesta días de datos perdidos para siempre (SEPA
sobrescribe cada 7 días y no tiene histórico). Un bug acá cuesta un número mal, que se
recalcula. Aquello tiene que ser aburrido y estable; esto va a cambiar constantemente.

---

## Qué es un quote

La unidad de medición: **un producto, en una sucursal concreta, de un comercio concreto** —
`(id_comercio, id_sucursal, id_producto)`.

Su precio del mes es el **promedio** de sus precios diarios, porque es la fórmula del INDEC:
*"para el caso de los supermercados, en los que se realizan dos visitas en el mes, primero se
calcula la media aritmética de los precios positivos observados durante el mes"* (fórmula 6,
sección 5.1 de `docs/metodologia_ipc_nacional_2019.pdf`).

Ojo con esto: el repo de captura publica en `quotes_mensuales` la **mediana**, que es con lo
que reconcilia `scripts/reconciliar_mensual.py`. El índice no usa esa tabla, calcula desde las
observaciones diarias con `--agregador`, que por default es `media`. Son dos números distintos
a propósito y cada uno tiene su uso.

El `id_comercio` en la clave no es opcional: `id_sucursal` es un código interno de cada
cadena y la sucursal "7" existe en varias a la vez.

## Metodología

```
Jevons         →  dentro de cada categoría elemental   (media geométrica de relativos)
Laspeyres      →  categorías → clases COICOP           (media aritmética ponderada)
Laspeyres      →  regiones → nacional                  (pesos regionales del INDEC)
Encadenamiento →  indice_t = indice_{t-1} × variación_t
```

**Geométrica abajo, aritmética arriba**, y no es intercambiable. Abajo los productos son
sustitutos y no hay ponderadores: la media aritmética de relativos tiene *drift de Carli*
(un precio que sube 50% y baja 33,3% vuelve al origen, pero la media aritmética de los
relativos da 1,0833 y el índice trepa sin que nada se haya movido). Arriba los rubros no son
sustitutos y sí hay ponderadores.

Jevons se computa **en logaritmos**: multiplicar 5.000 ratios desborda.

**Mediana o promedio dentro del mes** cambia el resultado y es lo primero que hay que mirar
cuando el número parece bajo. Medido sobre agosto → septiembre 2026, sobre los mismos quotes:

| agregador del mes | con MAD 3,5 | solo tope ×2 |
|---|---|---|
| mediana | +1,1633% | +1,5753% |
| **media** (la del INDEC) | +1,2862% | +1,8146% |
| geométrica | +1,2672% | +1,8118% |

Dos lecturas. La media y la geométrica dan lo mismo (0,02 puntos): **lo que importa es mediana
contra promedio, no cuál promedio**. Y la media genera más dispersión, así que el filtro
estadístico manda más a cuarentena (13,94% contra 10,01%) y se come parte de la corrección: las
dos decisiones interactúan y no se pueden mirar por separado.

Cambiar de columna de precio —`precio_lista` a `precio_efectivo`— mueve 0,05 puntos. No es ahí.

**Outliers sobre `log(ratio)`**, no sobre variación porcentual — el log-return es simétrico.
Dos filtros: tope absoluto en ×2 (siempre activo, red de seguridad) y MAD con umbral 3,5.

El INDEC **no tiene filtro estadístico**: *"a priori, no se excluye ningún precio relevado"*
(sección 3.3 del documento). Lo que tiene es control de calidad con reobservación en el campo,
que nosotros no podemos hacer. Por eso el tope absoluto se queda como mínima red —caza el
dígito de más al tipear— y el filtro por dispersión es el que está en discusión.

> **El caso que rompe la detección ingenua:** en góndola muchísimos precios no cambian, así
> que si más de la mitad de los ratios valen 1, la mediana es 0 y **el MAD también** — y todo
> queda a infinitos MAD. Hay fallback a rango intercuartil y después a solo el tope absoluto.
> Cubierto por `test_el_mad_cero_no_manda_todo_a_cuarentena`.

**Muestra emparejada:** solo entran quotes presentes en los dos meses.

**Renormalización:** los pesos del INDEC suman 1 sobre la canasta completa; el índice cubre
el 10,03%. Hay que reescalar sobre lo cubierto o el número no significa nada.

## La ruta crítica

Las **ocho operaciones** donde se fabrica el número están marcadas en el código con un bloque
de seis campos: qué hace, la fórmula, por qué así, el test que la verifica, y **cómo falla sin
avisar**. El último campo es el que importa: un error en estas ocho no tira una excepción,
devuelve un número plausible.

```bash
python scripts/ruta_critica.py          # el índice, para leer en orden
python scripts/ruta_critica.py --full   # los seis campos de cada una
```

El índice **sale del código**, no de un documento aparte, y el script corta con error si una
operación no está marcada o no declara su test. Un documento escrito a mano se desincroniza y
nadie se entera, que es justamente el problema que esto viene a resolver.

| | operación | dónde |
|---|---|---|
| 1 | precio del mes de cada quote | `periodo.quotes_del_periodo` |
| 2 | muestra emparejada | `elemental.emparejar` |
| 3 | filtro de valores raros | `elemental.detectar_outliers` |
| 4 | Jevons | `elemental.jevons` |
| 5 | categorías → clases COICOP | `agregacion.clases_desde_categorias` |
| 6 | ponderadores a la base de precios | `ponderadores.actualizar_por_precios` |
| 7 | Laspeyres y renormalización | `agregacion.laspeyres` |
| 8 | encadenamiento | `agregacion.encadenar` |

**Antes de tocar cualquiera de las ocho:** leer el bloque, y si el cambio lo contradice,
cambiar el bloque en el mismo commit. Un comentario que miente es peor que no tener comentario
— el de la operación 1 decía "mediana porque es robusta" y la fórmula del INDEC es promedio.

## Comandos

```bash
pytest      # 108 tests sobre datos sintéticos
python scripts/ruta_critica.py
```

### Cierre mensual

El colapso de un mes entero **no entra en el disco libre con el default de DuckDB**. El de
septiembre murió a los 22 minutos: el límite del directorio temporal lo saca del espacio
libre, usó los 10,5 GiB que había y necesitaba más. Terminó bien en 30 minutos derramando
menos a disco, no pidiendo más disco:

```bash
export PRECIOS_MEMORIA_DUCKDB=8GB   # default 4GB, sobre una máquina de 15,9 GB
export PRECIOS_HILOS_DUCKDB=2       # default 4; cada hilo derrama en paralelo
python -m precios.cli quotes --anio 2026 --mes 9
```

No retoma: si falla, el reintento rehace todos los comercios desde cero, porque baja los
parquet a un temporal nuevo y sube recién al final. Un mes son ~30 min de colapso más ~1 h
de las dos reconciliaciones.

No hay entorno virtual propio todavía; se usa el de `relevamiento_precios`:
`c:/Users/Joaco/Desktop/relevamiento_precios/.venv/Scripts/python.exe`

---

## Qué mide y qué no

Esto es un **índice de precios de supermercado**, no un IPC:

1. SEPA solo cubre góndola de comercios alcanzados por la resolución.
2. El INDEC releva 500 supermercados y **más de 16.200 negocios tradicionales** (sección 3.1
   del documento metodológico en `docs/`). Los supermercados son minoría de sus informantes.

Lo demás que releva el INDEC y hay que tener en cuenta para reproducirlo (secciones 3.3 y 5.1):

| | |
|---|---|
| frecuencia | 20 días hábiles del mes como "paneles"; cada panel tiene día e informantes fijos |
| visitas | supermercados **2 por mes**, con ~10 paneles de separación; negocios tradicionales 1 |
| volumen | más de **320.000 precios por mes** |
| qué artículo | el **más vendido** que cumpla la especificación, y después el mismo mes a mes |
| canales | presencial, y también teléfono, mail, **página web** y publicaciones |
| precio del mes | media aritmética dentro del informante, geométrica simple entre informantes |
| super vs tradicional | se combinan con proporción fija de la ENGHo 2004/05 |

La media aritmética del INDEC promedia **2 observaciones**; la nuestra promedia **30**. Con dos
puntos, media y mediana son lo mismo: la diferencia aparece recién cuando hay serie diaria.

La comparación honesta es contra la división **"Alimentos y bebidas no alcohólicas"** del
INDEC, no contra el nivel general.

Cobertura del piloto: 6 clases COICOP = **10,03% del IPC nacional**, o el 43% de la división
de alimentos. Los códigos COICOP de la taxonomía coinciden **exactamente** con los del
INDEC, así que los ponderadores se joinean directo.

## Problema abierto: el corte regional

El INDEC agrega por región y **GBA = CABA + los 24 partidos del Gran Buenos Aires**; el resto
de la provincia va a Pampeana. Juntas pesan el **78,9%** del total nacional.

`staged/observaciones` tiene `provincia` pero **no `localidad` ni CP**, así que hoy no se
pueden separar. `config/regiones.yaml` marca Buenos Aires como `REQUIERE_LOCALIDAD`.

Se arregla agregando una dimensión de sucursales **en el repo de captura**, y tiene deadline:
esa info sale de `raw/`, que vence a los 12 meses por TTL.

## Estado y pendientes

Hay **37 días** de datos capturados (2026-07-27 a 2026-09-01), sin ninguno perdido.
**Agosto es el primer mes cerrado**: ya está colapsado a quotes mensuales y reconciliado
contra el repo de captura (1.217.904 quotes, cero diferencias, en las dos series de precio).

Julio tiene 5 días, así que todavía no hay una variación real: el índice necesita dos meses
cerrados y el primer número sale a principios de octubre, cuando cierre septiembre.

Falta:

1. **Decidir el filtro de outliers.** Ya está medido (ver la tabla en Metodología) y el
   criterio de elección está fijado: se compara contra la división de alimentos del INDEC
   cuando publique septiembre y queda la variante que mejor la siga. Las tres se corren con
   `--umbral-mad`. Lo que se sabe del default de 3,5: en 9 de 20 artículos el criterio no
   llega a aplicarse porque el 59,7% de los precios no se movió y la dispersión da cero; donde
   sí muerde descarta entre el 20% y el 50% de las observaciones del artículo. El caso que lo
   muestra: harina 0000 reportaba +0,002% con 5.405 de sus 10.873 ratios en cuarentena.

2. **Extender la taxonomía, que es la cobertura que falta y no necesita scrapear nada.**
   El catálogo de septiembre tiene **150.504 productos** y hay **2.354 clasificados**: el 1,6%.
   Con eso el índice cubre el 41,5% de la división de alimentos del INDEC. Lo que falta pesa
   58,5% de la división y ya está capturado:

   | clase que falta | % de la división |
   |---|---|
   | 01.1.2 Carnes y derivados | 33,1% |
   | 01.1.7 Verduras, tubérculos y legumbres | 9,9% |
   | 01.2.2 Aguas, gaseosas y jugos | 8,9% |
   | 01.1.6 Frutas | 5,0% |
   | 01.1.3 Pescados y mariscos | 1,7% |
3. Revisión a mano de la clasificación: 2.351 de 2.354 productos siguen con `revisado=no`.
4. Los dos juegos de ponderadores (ENGHo 2004/05 y 2017/18) en paralelo. **El INDEC usa
   ENGHo 2004/05** para el IPC base 2016, incluida la proporción supermercados / negocios
   tradicionales (secciones 3.1 y 5.1 del documento); nosotros usamos 2017/18.
5. Persistencia en Postgres y API.

## Cómo trabaja el cliente

Joaquín no es economista ni estadístico; su jefe (Juan) sí, y también programa. Cuando algo
toca metodología conviene explicarlo en términos simples y ofrecerle el texto para reenviar.

Pide explícitamente que se le señalen fallas de diseño **antes** de implementar, y que los
supuestos externos se verifiquen contra la fuente real. Las definiciones de las regiones, por
ejemplo, salieron del PDF del INDEC y no de memoria — y ahí apareció el problema de GBA.

Un bug en el índice es **silencioso**: el output sigue siendo un número plausible. Por eso
todo se valida contra casos con resultado conocido a mano antes de correrlo sobre datos
reales.
