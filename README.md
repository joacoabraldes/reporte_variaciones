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

### Problema abierto: no hay ponderadores por debajo de clase

El INDEC publica pesos hasta **clase** (`01.1.1 Pan y cereales`, 0,0405) y nada más fino.
Verificado contra `docs/ponderadores_ipc.xls`, que llega exactamente hasta ahí.

Pero Jevons se calcula un nivel más abajo, en las **15 categorías elementales**. Para subir
de categoría a clase hace falta un peso que **no existe en la fuente**:

| categoría (clase 01.1.1) | productos | quotes |
|---|---|---|
| `almacen.fideos_secos_500g` | 404 | 124.219 |
| `almacen.arroz_largo_fino_1kg` | 20 | 12.532 |
| `almacen.harina_trigo_000_1kg` | 19 | 13.247 |
| `almacen.harina_trigo_0000_1kg` | 17 | 10.796 |

Cualquier cosa que se ponga ahí es un supuesto, y **el resultado se mueve**: medido sobre
2026-W32 → W33, la variación agregada va de **+0,176%** (pesos iguales) a **+0,264%** (por
cantidad de productos). Casi 0,09 puntos, un 50% del propio número.

Ninguno de los criterios disponibles es un ponderador de verdad:

- **Pesos iguales** — neutro, pero difícilmente la harina 0000 sea un cuarto del gasto en
  pan y cereales.
- **Por cantidad de productos** — la variedad mide en cuántas formas viene el producto (los
  fideos se subdividen en decenas de formas, la harina en dos), no cuánto se compra. Además
  amplifica errores de clasificación: convierte un regex que agarra de más en un sesgo de
  metodología.
- **Por cantidad de quotes** — productos × sucursales: presencia en góndola, no consumo.

**El límite es duro:** un ponderador es participación en el **gasto** (precio × cantidad), y
SEPA publica precios, no ventas. No hay ningún dato de cantidades. Cualquier peso derivado
de nuestros propios datos sólo puede aproximar presencia en góndola.

Mientras tanto, `scripts/correr_semanal.py` **reporta los tres criterios y su banda** en vez
de elegir uno y esconderlo dentro del número.

> **PENDIENTE: buscar una fuente más fina.** Los pesos por clase del INDEC salen de la
> ENGHo, que releva gasto a un nivel bastante más desagregado del que después publica
> agregado. Si la microdata tiene apertura por variedad, el supuesto desaparece y no hay que
> elegir nada. Es lo primero que hay que averiguar antes de publicar un número.

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

**El índice necesita dos meses cerrados para dar la primera variación.** La captura arrancó
el 27/07/2026, así que el primer número real sale a principios de octubre.
