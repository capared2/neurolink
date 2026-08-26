# neurolink — el scraper del agregador

Recoge noticias de **21 medios repartidos en cuatro nichos**, las clasifica en
una taxonomía común, agrupa las que cuentan la misma historia y publica el
resultado como JSON en este mismo repositorio. Se ejecuta solo con GitHub
Actions y no necesita servidor.

| Pieza | Dónde |
| --- | --- |
| Scraper | `scraper/` — este documento |
| Dataset | `data/` — un JSON por tema, partido en trozos |
| Estado | `state/` — una carpeta por fuente |
| Automatización | `.github/workflows/` |
| Sitio | Repositorio aparte: [capared2/VORTEX](https://github.com/capared2/VORTEX) |

## Qué lo hace distinto de un scraper de un solo medio

Recoger de veintiún sitios a la vez no es "lo mismo pero veintiún veces". Los
tres problemas nuevos y cómo se resuelven:

**1. Cada medio nombra sus secciones a su manera.** `/futbol/`, `/soccer/`,
`/futebol/`, `/sport/football/`. Sin traducirlas, el agregador serían veintiún
sitios apilados. Todo pasa por una taxonomía única (`taxonomy.py`) que decide
una clave `vertical/tema` mirando, de más fiable a menos: los segmentos de la
URL, la sección que declara el artículo, sus etiquetas y, en último lugar, las
palabras del titular.

**2. Cada medio se maqueta distinto, y se rediseña.** El parser
(`parser.py`) baja una escalera de respaldos hasta que algo funciona: JSON-LD →
selectores de la fuente → selectores genéricos → **densidad de texto**. El
último peldaño elige el bloque de la página con más texto propio y menos
enlaces, y es el que salva a una fuente el día que cambia de plantilla.

**3. El reparto.** Cualquier cosa que se haga "en orden" le dedica la corrida
entera a la primera fuente: basta un sitemap grande o un medio lento. El
presupuesto se reparte dos veces — una rebanada del plazo por fuente al
descubrir, y lotes que se turnan entre fuentes al descargar — y cada fuente
tiene además su propio techo de URLs.

Y una cuarta, que es la que convierte esto en un agregador de verdad: cuando
cinco medios cuentan lo mismo, la portada enseña **una historia con cinco
coberturas**, no cinco noticias repetidas (`dedupe.py`).

## Las fuentes

```bash
python -m scraper fuentes     # lista lo que hay declarado
```

| Nicho | Medios activos |
| --- | --- |
| Noticias | BBC · Globo · CNN · Fox News · Times of India · Al Jazeera · NBC News · Yahoo News · The New York Times |
| Deportes | Marca · Sky Sports · Bleacher Report |
| Gamer | IGN · FACEIT · Twitch · Steam |
| Tecnología | The Verge · TechCrunch |

**The New York Times rinde a medias y es esperable**: su antibots (DataDome)
contesta 403 a una parte de las peticiones y deja pasar el resto, así que su
fila del resumen tendrá bastantes fallos. Lo que entra, entra bien.

Hay tres más declaradas y **apagadas** (`activa=False`). No es una sospecha:
`python -m scraper doctor --detalle` mide qué contesta cada una.

| Medio | Lo que contesta | Por qué está apagada |
| --- | --- | --- |
| **ESPN** | Portada `202` de CloudFront, los seis feeds caídos | Desafío antibot antes de servir nada |
| **The Hill** | `403` de Varnish en artículos **y** portada | Bloqueo duro y uniforme |
| **FIFA** | `200`, pero 4,5 KB **sin `__NEXT_DATA__` ni `ld+json`** | El contenido no viaja en el HTML: lo monta el navegador |

FIFA es la única cerrada de verdad: no es que leamos mal la página, es que lo
que llega no contiene la noticia. Para ESPN y The Hill la causa tampoco es
`robots.txt` --no prohíben nada--, sino su protección antibots; recuperarlas
exigiría hacerse pasar por un navegador, que es una decisión distinta.

Añadir un medio es añadir una entrada en `sources.py`: nada más del scraper
sabe que existe BBC o IGN. Cada fuente declara dónde buscar (feeds, sitemaps,
portadas), cómo distinguir un artículo de una portada de sección, y dónde vive
el cuerpo si su maquetación se resiste a la heurística general.

### Los feeds son candidatos, no promesas

Los medios mueven sus RSS sin avisar. El descubrimiento se salta en silencio el
feed que no responda y además lee los `<link rel="alternate">` de la portada,
así que una URL caducada degrada una fuente pero no rompe la corrida.

Para saber cuáles siguen vivas:

```bash
python -m scraper doctor              # las 21
python -m scraper doctor --fuentes ign,steam
```

El doctor toca cada fuente por encima —un feed, un sitemap, una portada y un
par de artículos de muestra— y dice qué funciona y qué no, sin guardar nada.
Sale con código distinto de cero si algo está caído, y el workflow
[Revisar fuentes](.github/workflows/doctor.yml) lo pasa cada lunes.

El doctor juzga **por el resultado**, no por que todo lo declarado responda: una
fuente está en pie si descubre noticias y al menos una de las de muestra llega
con cuerpo. Un feed caído sale como aviso (`~`), no como fallo. La distinción no
es cosmética: sin ella, cuatro fuentes que funcionaban perfectamente salían en
rojo, y una revisión que grita cada lunes se acaba ignorando.

Dos fuentes activas rinden menos de lo que su nombre promete, y conviene saberlo:

- **FACEIT** es una aplicación de una sola página y sus datos de competición van
  por API con clave; aquí solo se recoge su blog, que sí funciona.
- **Twitch** solo expone el directo por la API Helix con credenciales; de Twitch
  se recoge su blog oficial, descubierto por sitemap.

## Cómo queda el dataset

```
data/
├── index.json                        catálogo: verticales, temas, ficheros, totales
├── latest.json                       últimas noticias, sin cuerpo (listados)
├── portada.json                      historias agrupadas: el río universal
├── fuentes.json                      salud de cada fuente en la última corrida
├── deportes/futbol/
│   ├── part-0001.json                100 noticias por fichero
│   └── lookup.json                   id de noticia → fichero que la contiene
├── noticias/mundo/part-0001.json
├── gamer/esports/part-0001.json
└── seo/sitemap*.xml
```

El tope de 100 noticias por fichero no es estético: el sitio parsea el fichero
entero para pintar una noticia y el plan gratuito de Cloudflare Workers corta a
los **10 ms de CPU por petición**.

### El sitio no puede ver de dónde sale una noticia

`latest.json` y `portada.json` —los ficheros que alimentan los listados— se
derivan **sin la identidad del medio**, y hay un test que lo comprueba. Además:

- el **identificador** de cada noticia es un hash opaco, no lleva el nombre de
  la fuente delante, aunque acabe formando parte de la URL pública;
- la **imagen** de portada viaja como una ruta propia (`/img/<testigo>`), no
  como una URL al servidor del medio.

El archivo completo de cada noticia sí conserva su origen, porque el scraper lo
necesita para su propio estado y para no contar dos veces la misma historia.

### Formato de cada noticia

```jsonc
{
  "id": "3f9a2b7c1d4e88",
  "url": "https://…",                  // de dónde se descargó
  "category": "deportes/futbol",
  "vertical": "deportes",
  "topic": "futbol",
  "topic_name": "Fútbol",
  "title": "…",
  "standfirst": "…",                   // entradilla
  "summary": "…",
  "body": "texto completo con saltos de párrafo",
  "paragraphs": ["…", "…"],
  "word_count": 512,
  "authors": ["…"],
  "tags": ["…"],
  "published_at": "2026-08-25T20:47:00Z",   // siempre UTC ISO-8601
  "modified_at": "2026-08-25T23:10:00Z",
  "language": "es",
  "country": "ES",
  "images": [{"url": "…", "caption": "…"}],
  "videos": ["…"],
  "is_premium": false,
  "source": "marca",
  "scraped_at": "2026-08-25T23:12:41Z"
}
```

## Ejecución

```bash
pip install -r requirements.txt

python -m scraper                                  # lo publicado en las últimas horas
python -m scraper --max-articulos 20 --verbose     # prueba corta
python -m scraper --fuentes bbc,espn,ign           # solo unas cuantas
python -m scraper --modo full --presupuesto 0      # también los sitemaps, sin límite
python -m scraper --modo full --desde 2026-01-01   # solo desde una fecha
```

La lista completa está en `python -m scraper scrape --help`.

### Bajar el archivo histórico

No entra en una sola corrida, así que el scraper es **reanudable**: lo que no
da tiempo a procesar queda en la cola de su fuente y la corrida siguiente sigue
por donde se quedó.

1. Lanza el workflow a mano con `modo = full`. Esa corrida recorre los sitemaps
   y llena las colas.
2. Vuelve a lanzarlo con `saltar_descubrimiento = true` tantas veces como haga
   falta, o deja que las corridas programadas las vacíen.

El progreso se ve en `state/run.json` (`pending` = cuánto falta).

## Estado entre corridas

```
state/
├── run.json                  resumen de la última corrida
└── fuentes/<clave>/
    ├── seen.txt              URLs ya guardadas: nunca se vuelven a descargar
    ├── pending.txt           cola descubierta y todavía sin procesar
    ├── failed.json           URLs con fallos y su contador
    └── empty.json            URLs que llegaron sin cuerpo y sus reintentos
```

Una carpeta por fuente para que una que se rompa —o una que se añada a mitad de
camino— no toque el progreso de las demás.

Las páginas que llegan sin cuerpo **no se dan por vistas**: un directo se llena
de texto cuando acaba el acontecimiento y esa misma URL suele traer luego la
crónica. Se reintentan un número acotado de veces (`--reintentos-vacio`) para no
gastar peticiones eternamente en galerías que nunca van a tener texto.

## Tests

```bash
python -m pytest tests -q
```

Corren **sin red**: usan tres medios inventados (`tests/fake_site.py`) que
imitan los tres casos reales —uno con JSON-LD completo, uno que obliga a tirar
de selectores y uno que solo se salva por densidad de texto— y publican la
misma historia para poder comprobar la agrupación. Cubren el pipeline entero:
descubrimiento, parseo, clasificación, agrupación, particionado, reanudación y
sitemaps.

## Buen comportamiento

El scraper respeta `robots.txt` (incluido `Crawl-delay`), se identifica con un
User-Agent propio y espacia las peticiones **por dominio**, no en global: con
veintiún hosts, un solo reloj convertiría la corrida en una fila india. Si un
medio responde `429`, la espera de ese host se dobla para el resto de la
corrida.

Baja `--workers` y sube `--delay` para ser aún más suave.

## Una advertencia que conviene leer

Los contenidos que recoge este scraper son de sus respectivos medios y están
sujetos a sus condiciones de uso. Guardar el texto completo para uso personal o
de investigación es una cosa; **republicarlo** —sobre todo sin citar al medio y
sin enlazarlo— es otra bastante distinta, y es el escenario que más
probabilidades tiene de acabar en una reclamación de copyright o en una
exclusión de Google News. El sitio que consume este dataset trae un interruptor
(`MOSTRAR_CUERPO_COMPLETO`) para pasar de texto completo a resumen sin tocar
código.
