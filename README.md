# reporte-variaciones

Índice de variaciones de precios construido sobre los datos que captura
[`relevamiento_precios`](https://github.com/joacoabraldes/relevamiento_precios).

## Por qué es un repo aparte

Son dos actividades con costos de error muy distintos:

| | `relevamiento_precios` | este repo |
|---|---|---|
| Un bug cuesta | días de datos **perdidos para siempre** | un número mal, que se recalcula |
| Ritmo de cambio | casi nunca | constante |
| Cómo corre | automático, desatendido, todos los días | a mano, explorando |
| Permisos sobre el bucket | escritura | **solo lectura** |

Si estuvieran juntos, cada experimento con la metodología tocaría el código que
tiene que andar sí o sí mañana a las 13:20.

**La interfaz entre los dos es el bucket**, no el código:

```
relevamiento_precios  →  escribe  →  staged/quotes_mensuales/    →  lee  →  este repo
                                     staged/catalogo_productos/
```

Este repo no sabe nada de SEPA, ni de ZIPs anidados, ni de separadores pipe, ni de las 77
variantes de unidades de medida. Levanta dos tablas Parquet limpias y calcula.

---

## Metodología

Replica la estructura del IPC del INDEC
([documento metodológico](docs/metodologia_ipc_nacional_2019.pdf)):

```
Jevons        →  dentro de cada categoría elemental     (media geométrica de relativos)
Laspeyres     →  categorías → clases COICOP             (media aritmética ponderada)
Laspeyres     →  regiones → nacional                    (pesos regionales)
Encadenamiento →  indice_t = indice_{t-1} × variación_t
```

### Por qué geométrica abajo y aritmética arriba

**Abajo (Jevons):** los productos de una categoría elemental son sustitutos —una marca de
leche reemplaza a otra— y no hay ponderadores para distinguirlos. La media aritmética de
relativos tiene **drift de Carli**: si un precio sube 50% y después baja 33,3% vuelve al
valor original, pero la media aritmética de los dos relativos da 1,0833. El índice treparía
sin que los precios se hayan movido. La geométrica da exactamente 1.

Se computa en logaritmos: multiplicar 5.000 ratios y sacar la raíz n-ésima desborda; sumar
logaritmos no.

**Arriba (Laspeyres):** los rubros **no** son sustitutos entre sí —que suba el pan no hace
que se compre menos yerba de la forma en que una marca reemplaza a otra— y sí hay
ponderadores. Ahí corresponde la media aritmética ponderada.

### Detección de outliers

Sobre `log(ratio)`, no sobre la variación porcentual: el log-return es **simétrico**, +100%
y −50% son el mismo movimiento con signo opuesto. En porcentaje uno mide 100 y el otro 50.

Dos filtros, y el absoluto corre **siempre**:

1. **Tope absoluto**: ratio fuera de `[0,5 , 2]`. Duplicar el precio en un mes es
   sospechoso; un dígito de más al tipear multiplica por 10, así que ×2 lo caza de sobra y
   deja pasar aumentos grandes pero legítimos. Es la red de seguridad: si una categoría está
   muy dispersa, el umbral estadístico podría dejar pasar un ×10 y éste no.

2. **MAD sobre `log(ratio)`**, umbral 3,5 desvíos. Se usa MAD y no desvío estándar porque el
   desvío se infla con el propio outlier y termina enmascarándolo.

> **El caso que rompe la detección ingenua.** En góndola muchísimos precios no cambian de un
> mes a otro. Si más de la mitad de los ratios de una categoría valen exactamente 1, la
> mediana es 0 y **el MAD también da 0** — y ahí cualquier variación distinta de cero queda
> a infinitos MAD y la categoría entera se va a cuarentena. Por eso hay fallback a rango
> intercuartil, y si eso también da cero queda solo el tope absoluto.
> Está cubierto por `test_el_mad_cero_no_manda_todo_a_cuarentena`.

Con menos de 15 quotes no se aplica el criterio estadístico: la dispersión de una muestra
chica no es informativa.

### Muestra emparejada

Solo entran los quotes presentes en los **dos** meses. Un producto nuevo no tiene contra qué
compararse y uno que desapareció tampoco.

### Renormalización de los ponderadores

Los pesos del INDEC suman 1 sobre la canasta **completa**. El índice cubre una parte, así
que hay que reescalarlos sobre lo efectivamente cubierto — si no, una categoría con peso
0,04 y variación +10% aportaría 0,004 en vez de 1,10.

Las clases que tienen peso pero no tienen dato **se excluyen del numerador y del
denominador**: no se asume que se movieron como el promedio.

### Ponderadores por debajo de clase: resuelto con la ENGHo

El INDEC publica ponderaciones hasta **clase** (`01.1.1 Pan y cereales`) y nada más fino.
Pero Jevons se calcula un nivel más abajo, en las **categorías elementales**, así que para
subir de categoría a clase hacía falta un peso que ese archivo no trae. Durante un tiempo se
usaron **pesos iguales**, un supuesto que movía el resultado agregado alrededor del 50%.

**El peso existe.** La metodología del IPC (sección 4.2) explica cómo construyó el suyo:

> *"se estimaron partiendo de los gastos de los hogares urbanos de la ENGHo 2004/05 por
> región y de las variedades que se relevaban en diciembre de 2015. Primero, se procedió a
> estimar el gasto promedio de los hogares por variedad relevada"*

`src/reporte/ponderadores.py` hace **ese mismo procedimiento** con la encuesta más nueva
publicada, la **ENGHo 2017/18**, cuyos microdatos el INDEC distribuye completos: 901.804
registros de gasto de 21.543 hogares, con región y factor de expansión.

La pieza que lo hace posible es que **el código de artículo de la ENGHo es el código COICOP
de producto**, escrito sin puntos:

```
ENGHo   A0111101     ->  COICOP  01.1.1.1.01     Facturas y churros
```

El join es directo y no hace falta tabla de equivalencias. Las seis regiones de la ENGHo son
además exactamente las seis del IPC.

#### Lo que cambia

| categoría | artículo ENGHo | peso real en su clase | supuesto anterior |
|---|---|---|---|
| aceite de girasol | `A0115102` | 48,26% | 33% |
| yerba mate | `A0121301` | 45,16% | 50% |
| manteca | `A0115301` | 26,78% | 33% |
| leche entera | `A0114101` | 16,18% | 20% |
| azúcar | `A0118101` | 12,51% | 100% |
| fideos secos | `A0111304` | 9,33% | 25% |
| arroz blanco | `A0111201` | 5,08% | 25% |
| sal fina | `A0119101` | 3,70% | 100% |
| harina de trigo | `A0111210` | 2,78% | 50% |

Ninguno de los criterios que se habían probado acertaba: para fideos, pesos iguales daba
25%, por cantidad de productos 87,8%, y el real es **9,33%**.

El mapeo vive en [`config/mapeo_categorias_engho.yaml`](config/mapeo_categorias_engho.yaml),
versionado y revisable.

#### La unidad de cálculo es el artículo, no la categoría

Tres artículos cubren dos categorías nuestras cada uno (harina 000/0000, yerba 500 g/1 kg,
yogur firme/bebible) porque el INDEC no los separa. **No se reparte el peso entre ellas.**

El nivel elemental de un índice es, por definición, el agrupamiento más chico que tiene
ponderación asignada — es literalmente cómo el INDEC define "variedad". Si el peso llega
hasta "harina de trigo", ahí está el nivel elemental: los quotes de las dos categorías entran
a **una sola media geométrica** y sale un solo índice para el artículo.

Repartir habría sido inventar un dato que la fuente no tiene. Pooleando, el peso relativo
entre las dos sale solo —por cuántos ratios aporta cada una dentro del Jevons— y queda en el
espacio geométrico, en vez de combinarse por fuera con una media aritmética.

Las categorías se siguen reportando por separado en el diagnóstico; lo que cambia es qué se
usa para calcular.

> **PENDIENTE: actualizar por precios.** Laspeyres pide que el período de referencia de los
> ponderadores coincida con el de la base de precios, y el propio documento del INDEC lo
> recomienda explícitamente. Los pesos que salen de acá son participaciones de gasto a
> precios de 2017/18, sin actualizar.

### Cobertura del gasto dentro de cada clase

Al mapear contra la ENGHo quedó a la vista algo que antes no se podía medir: **qué fracción
del gasto de cada clase mide realmente el índice**. Muestrear es normal en un IPC, pero la
muestra del INDEC está elegida para ser representativa y la nuestra era lo que se había
llegado a clasificar.

Con esa tabla como prioridad se agregaron **8 categorías** al repo de captura, elegidas por
peso y no por facilidad:

| clase | antes | ahora |
|---|---|---|
| 01.1.5 Aceites, grasas y manteca | 81,0% | 81,0% |
| 01.1.4 Leche, lácteos, huevos | 30,7% | **54,4%** |
| 01.2.1 Café, té, yerba y cacao | 45,2% | 45,2% |
| 01.1.9 Otros alimentos | 3,7% | **32,7%** |
| 01.1.1 Pan y cereales | 17,2% | **28,6%** |
| 01.1.8 Azúcar, dulces, golosinas | 12,5% | **19,2%** |

Las nuevas: galletitas dulces envasadas, snacks, mayonesa, huevos, mermelada, queso crema
untable, queso rallado y dulce de leche. La clasificación pasó de 987 a **2.337 productos**.

#### Por qué el número cambió tanto

La variación agregada de 2026-W32 → W33 pasó de **+0,24% a +0,56%** al ampliar la cobertura.
No es un error: `galletitas_dulces_envasadas` dio **+2,13%** esa semana, empujada por un
grupo coherente de marcas (Rumba, Chocolinas, Mana, Amor, Sonrisas, Coquitas, Macucas) que
subieron entre 5,7% y 17%. El 75,4% de los quotes de la categoría no se movió y la mediana
del ratio es exactamente 1, así que el aumento viene de un subconjunto real y no de ruido.

El índice era **ciego a ese aumento** porque no medía galletitas. Es la razón de ampliar
cobertura, y también la advertencia: mientras queden clases con cobertura baja, el número
puede estar perdiéndose movimientos igual de grandes.

#### Techo real de "Otros alimentos"

01.1.9 no puede llegar al 100%: el **32% de esa clase** son artículos "Gastos no
discriminados en alimentos y bebidas", que por definición no tienen un precio de góndola que
relevar. El techo alcanzable es ~68%.

#### Lo que sigue faltando

En "Pan y cereales", el artículo más pesado es **pan tipo francés fresco (23,6% de la
clase)** y no se mide: se vende suelto por peso y no aparece en SEPA con presentación
normalizada. Las facturas y churros (5,8%) tienen el mismo problema.

---

## Estado de la cobertura

Las 6 clases COICOP del piloto pesan **10,03% del IPC nacional** (GBA). La división de
alimentos completa pesa 23,44%, así que el piloto cubre el **43% de "Alimentos y bebidas no
alcohólicas"**.

| Código | Clase | Peso GBA |
|---|---|---|
| 01.1.1 | Pan y cereales | 0,0405 |
| 01.1.4 | Leche, productos lácteos, huevos | 0,0345 |
| 01.1.5 | Aceites, grasas y manteca | 0,0055 |
| 01.1.8 | Azúcar, dulces, chocolate, golosinas | 0,0101 |
| 01.1.9 | Otros alimentos | 0,0029 |
| 01.2.1 | Café, té, yerba y cacao | 0,0068 |
| | **Total cubierto** | **0,1003** |

Los códigos COICOP de la taxonomía coinciden **exactamente** con los del INDEC, así que los
ponderadores se joinean directo sin tabla de mapeo.

## Qué mide y qué no

Esto es un **índice de precios de supermercado**, no un IPC. Dos razones:

1. SEPA solo cubre góndola de comercios alcanzados por la resolución.
2. El INDEC releva **500 supermercados y más de 16.200 negocios tradicionales** (sección 3.1
   del documento metodológico). Los supermercados son una minoría de sus informantes.

La comparación honesta es contra la división **"Alimentos y bebidas no alcohólicas"** del
INDEC, no contra el nivel general.

---

## Problema abierto: el corte regional

El INDEC agrega por región, y **GBA = CABA + los 24 partidos del Gran Buenos Aires**. El
resto de la provincia de Buenos Aires va a Pampeana. Juntas pesan el **78,9%** del total
nacional.

`staged/observaciones` tiene `provincia` pero **no tiene `localidad` ni código postal**, así
que hoy no se puede separar GBA de Pampeana.

**Se arregla** agregando una dimensión de sucursales en el repo de captura
(`id_comercio, id_sucursal → localidad, CP, tipo, lat/long`). Son 1.767 filas. Hay que
hacerlo **antes de que el TTL de 12 meses empiece a borrar el crudo**, que es de donde sale
esa información.

Mientras tanto, `config/regiones.yaml` marca Buenos Aires como `REQUIERE_LOCALIDAD`.

---

## Estructura

```
docs/
  metodologia_ipc_nacional_2019.pdf     documento metodológico del INDEC
  ponderadores_ipc.xls                  ponderaciones por región (fuente)
  ponderaciones_regionales.jpeg         pesos de cada región sobre el total
config/
  ponderadores.yaml                     extraído del xls, versionado y legible
  regiones.yaml                         provincia → región + partidos del GBA
src/reporte/
  elemental.py                          Jevons y detección de outliers
  agregacion.py                         Laspeyres, renormalización, encadenamiento
tests/
  test_indice.py                        22 tests sobre datos sintéticos
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Los dos obligatorios:

- `test_precios_que_suben_y_vuelven_dan_cero_acumulado` — sube 50%, baja 33,3%, el
  encadenado tiene que volver exactamente a la base. Es el caso que separa Jevons de la
  media aritmética.
- `test_una_canasta_de_un_solo_producto_reproduce_su_variacion_exacta`.

Un bug en el índice es silencioso: el output sigue siendo un número plausible. Por eso todo
se valida contra casos con resultado conocido a mano **antes** de correrlo sobre datos
reales.

---

## Lo que falta

- **Ponderadores por debajo de clase**: averiguar si la microdata de la ENGHo tiene apertura
  por variedad. Hoy hay un supuesto que mueve el resultado ~50% — ver arriba. **Bloquea
  publicar un número.**
- Imputación de faltantes (un quote que desaparece 1-2 meses se imputa con la variación de
  su categoría; si falta más de 2, sale de la muestra).
- Revisión a mano de la clasificación: los 987 productos están con `revisado=no`, y el 41%
  cayó en una sola categoría (`fideos_secos_500g`), lo que sugiere que la regla agarra de
  más.
- Los dos juegos de ponderadores (ENGHo 2004/05 y 2017/18) en paralelo.
- Persistencia en Postgres y la API.

### Ya hecho

- Lector del bucket con caché local (`src/reporte/lectura.py`), incluida la detección de
  huecos: días ausentes **y** días presentes a los que les falta un comercio.
- Ventana temporal parametrizable (`src/reporte/periodo.py`): semanal y mensual con el mismo
  método, sólo cambian dos números de `config/parametros.yaml`.
- Las dos series en paralelo: `precio_lista` y `precio_efectivo` (`--precio`).
- Corrida de diagnóstico semanal (`scripts/correr_semanal.py`).
- **Reconciliación contra el repo de captura** (`scripts/reconciliar_mensual.py`). Es la
  única validación que no usa datos sintéticos: calcula los quotes de un mes acá y los cruza
  contra `staged/quotes_mensuales/`, que produce `relevamiento_precios` con otro código.

  | corrida | quotes | diferencias |
  |---|---|---|
  | 2026-07, `precio_lista` | 464.810 | **0** |
  | 2026-07, `precio_efectivo` | 464.810 | **0** |
  | 2026-08, `precio_lista` | 466.380 | **0** |

  Coinciden la clave, la mediana y el conteo de días, en todos los quotes. De paso quedó
  resuelto que `id_bandera` no parte quotes: agrupar por 3 campos o por 4 da el mismo total
  (15.140.643), así que la clave `(id_comercio, id_sucursal, id_producto)` es correcta.

  Valida el **colapso a quotes**, no el índice: Jevons, Laspeyres y el encadenamiento siguen
  respaldados solo por los tests con resultado calculado a mano, que es lo que corresponde
  porque no hay contra qué compararlos.

**El índice necesita dos meses cerrados para dar la primera variación.** La captura arrancó
el 27/07/2026, así que el primer número real sale a principios de octubre.
