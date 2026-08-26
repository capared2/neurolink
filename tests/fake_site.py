"""Tres medios inventados para probar el pipeline entero sin tocar la red.

Cada uno se parece a un tipo real de fuente:

``diario``  Un periodico generalista con JSON-LD completo, como BBC o CNN.
``cancha``  Un medio deportivo sin JSON-LD, que obliga a tirar de selectores.
``pixel``   Un sitio gamer sin JSON-LD ni selectores conocidos: solo se salva
            por la heuristica de densidad de texto.

Entre los tres cubren los tres peldaños de la escalera de respaldos del parser
y, ademas, publican la misma historia para poder comprobar la agrupacion.
"""
from __future__ import annotations

import json

from scraper.fetcher import Respuesta
from scraper.sources import Fuente

PARRAFOS = [
    "La reunion se prolongo durante mas de cuatro horas y termino sin un acuerdo firmado, "
    "segun tres personas presentes en la sala que pidieron no ser identificadas.",
    "Los equipos tecnicos seguiran trabajando durante el fin de semana para acercar "
    "posiciones en los dos puntos que siguen bloqueados desde el martes pasado.",
    "El calendario aprieta: el plazo vence el proximo dia treinta y ninguna de las dos "
    "partes ha querido decir en publico que pasaria si se agota sin novedades.",
    "Fuentes cercanas a la negociacion apuntan a que la proxima cita sera decisiva y "
    "que se celebrara en un lugar todavia por confirmar.",
]

# ---------------------------------------------------------------------------
# Las fuentes de mentira
# ---------------------------------------------------------------------------

DIARIO = Fuente(
    clave="diario", nombre="Diario Uno", home="https://diario.test",
    vertical="news", idioma="es", pais="ES",
    hosts=frozenset({"diario.test"}),
    feeds=("https://diario.test/rss.xml",),
    semillas=("/",),
    articulo=(r"^/\d{4}/\d{2}/\d{2}/",),
    cuerpo=("div.cuerpo",),
    delay=0.0,
)

CANCHA = Fuente(
    clave="cancha", nombre="Cancha", home="https://cancha.test",
    vertical="sports", idioma="es", pais="AR",
    hosts=frozenset({"cancha.test"}),
    feeds=("https://cancha.test/feed.xml",),
    semillas=("/futbol",),
    articulo=(r"^/[a-z]+/\d+-",),
    cuerpo=("article.nota",),
    delay=0.0,
)

PIXEL = Fuente(
    clave="pixel", nombre="Pixel", home="https://pixel.test",
    vertical="gaming", idioma="en", pais="US",
    hosts=frozenset({"pixel.test"}),
    sitemaps=("https://pixel.test/sitemap.xml",),
    semillas=("/news",),
    # Sin `cuerpo`: a proposito, para que tenga que salvarlo la densidad.
    delay=0.0,
    tema_por_defecto="gaming/games",
)

FUENTES = (DIARIO, CANCHA, PIXEL)


# ---------------------------------------------------------------------------
# Paginas
# ---------------------------------------------------------------------------

def _articulo_con_jsonld(titulo: str, ruta: str, seccion: str, fecha: str, etiquetas: list[str]) -> str:
    datos = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": titulo,
        "description": f"Entradilla de {titulo}.",
        "datePublished": fecha,
        "dateModified": fecha,
        "articleSection": seccion,
        "keywords": ", ".join(etiquetas),
        "author": {"@type": "Person", "name": "Redaccion"},
        "image": ["https://diario.test/img/portada.jpg"],
    }
    cuerpo = "".join(f"<p>{p}</p>" for p in PARRAFOS)
    return f"""<!doctype html><html lang="es"><head>
<title>{titulo} | Diario Uno</title>
<link rel="canonical" href="https://diario.test{ruta}">
<meta property="og:description" content="Entradilla de {titulo}.">
<script type="application/ld+json">{json.dumps(datos, ensure_ascii=False)}</script>
</head><body>
<nav><a href="/">Portada</a><a href="/mundo">Mundo</a></nav>
<h1>{titulo}</h1>
<div class="cuerpo">{cuerpo}</div>
<div class="related"><a href="/2026/08/01/otra">Otra noticia que no debe colarse en el cuerpo</a></div>
</body></html>"""


def _articulo_sin_jsonld(titulo: str, ruta: str, fecha: str) -> str:
    cuerpo = "".join(f"<p>{p}</p>" for p in PARRAFOS)
    return f"""<!doctype html><html lang="es"><head>
<title>{titulo}</title>
<link rel="canonical" href="https://cancha.test{ruta}">
<meta property="og:title" content="{titulo}">
<meta property="og:description" content="Cronica del partido.">
<meta property="article:published_time" content="{fecha}">
<meta property="article:section" content="Futbol">
<meta name="news_keywords" content="futbol, liga">
</head><body>
<h1>{titulo}</h1>
<article class="nota">{cuerpo}</article>
</body></html>"""


def _articulo_desnudo(titulo: str, ruta: str, fecha: str) -> str:
    """Ni JSON-LD ni selectores conocidos: la densidad es la unica salida."""
    cuerpo = "".join(f"<p>{p}</p>" for p in PARRAFOS)
    return f"""<!doctype html><html lang="en"><head>
<title>{titulo}</title>
<link rel="canonical" href="https://pixel.test{ruta}">
<meta property="og:description" content="A story about a game.">
<meta name="date" content="{fecha}">
</head><body>
<header><a href="/">Home</a><a href="/news">News</a><a href="/reviews">Reviews</a></header>
<div class="most-read"><a href="/a">Trending story one with a fairly long anchor text</a>
<a href="/b">Trending story two with a fairly long anchor text as well</a></div>
<div class="wrap-xyz"><h1>{titulo}</h1>{cuerpo}</div>
<footer><p>Copyright 2026. All rights reserved for this footer paragraph here.</p></footer>
</body></html>"""


def _rss(titulo_canal: str, base: str, entradas: list[tuple[str, str]]) -> str:
    elementos = "".join(
        f"<item><title>{t}</title><link>{base}{r}</link>"
        f"<guid>{base}{r}</guid></item>"
        for t, r in entradas
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{titulo_canal}</title>{elementos}</channel></rss>"""


def _sitemap(base: str, rutas: list[str]) -> str:
    urls = "".join(f"<url><loc>{base}{r}</loc></url>" for r in rutas)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>"""


class SitioFalso:
    """Un servidor de mentira: un diccionario de rutas a respuestas."""

    def __init__(self) -> None:
        self.paginas: dict[str, tuple[str, str]] = {}
        self.peticiones: list[str] = []
        self._construir()

    def anadir(self, url: str, texto: str, tipo: str = "text/html; charset=utf-8") -> None:
        self.paginas[url.rstrip("/")] = (texto, tipo)

    def get(self, url: str) -> Respuesta | None:
        """Sustituye a ``Fetcher.get`` durante los tests."""
        self.peticiones.append(url)
        encontrada = self.paginas.get(url.rstrip("/"))
        if encontrada is None:
            return None
        texto, tipo = encontrada
        return Respuesta(url=url, status=200, text=texto, content_type=tipo)

    # -- contenido ---------------------------------------------------------
    def _construir(self) -> None:
        xml = "application/xml"

        for base in ("https://diario.test", "https://cancha.test", "https://pixel.test"):
            self.anadir(f"{base}/robots.txt", "User-agent: *\nAllow: /\n", "text/plain")

        # -- diario.test: JSON-LD completo ---------------------------------
        articulos_diario = [
            ("/2026/08/25/cumbre-sin-acuerdo", "La cumbre termina sin acuerdo tras cuatro horas",
             "Mundo", "2026-08-25T18:00:00Z", ["cumbre", "diplomacia"]),
            ("/2026/08/25/mercados-a-la-baja", "Los mercados cierran a la baja por tercera sesion",
             "Economia", "2026-08-25T17:30:00Z", ["bolsa", "mercados"]),
            ("/2026/08/24/eleccion-en-el-congreso", "El Congreso vota manana la reforma pendiente",
             "Politica", "2026-08-24T09:00:00Z", ["congreso", "elecciones"]),
            # La misma historia que publica cancha.test: es lo que tiene que
            # agrupar la portada en una sola entrada con dos coberturas.
            ("/2026/08/25/el-clasico-acaba-con-victoria",
             "El clasico acaba con victoria y un gol en el descuento",
             "Deportes", "2026-08-25T20:10:00Z", ["futbol", "clasico"]),
        ]
        for ruta, titulo, seccion, fecha, etiquetas in articulos_diario:
            self.anadir(f"https://diario.test{ruta}",
                        _articulo_con_jsonld(titulo, ruta, seccion, fecha, etiquetas))

        self.anadir("https://diario.test/rss.xml",
                    _rss("Diario Uno", "https://diario.test",
                         [(t, r) for r, t, *_ in articulos_diario]), xml)
        # La portada anuncia un feed extra que no esta declarado en la fuente:
        # asi se comprueba el descubrimiento por <link rel="alternate">.
        self.anadir("https://diario.test/", """<html><head>
<link rel="alternate" type="application/rss+xml" href="/rss-mundo.xml"></head>
<body><a href="/2026/08/25/cumbre-sin-acuerdo">Cumbre</a></body></html>""")
        self.anadir("https://diario.test/rss-mundo.xml",
                    _rss("Mundo", "https://diario.test",
                         [("La cumbre termina sin acuerdo tras cuatro horas",
                           "/2026/08/25/cumbre-sin-acuerdo")]), xml)

        # -- cancha.test: sin JSON-LD, con selector -------------------------
        articulos_cancha = [
            ("/futbol/1001-la-cumbre-del-futbol-sin-acuerdo",
             "La cumbre del futbol acaba sin acuerdo tras cuatro horas", "2026-08-25T18:20:00Z"),
            ("/futbol/1002-victoria-en-el-clasico",
             "Victoria en el clasico con un gol en el descuento", "2026-08-25T20:00:00Z"),
        ]
        for ruta, titulo, fecha in articulos_cancha:
            self.anadir(f"https://cancha.test{ruta}", _articulo_sin_jsonld(titulo, ruta, fecha))
        self.anadir("https://cancha.test/feed.xml",
                    _rss("Cancha", "https://cancha.test",
                         [(t, r) for r, t, _ in articulos_cancha]), xml)
        self.anadir("https://cancha.test/", "<html><body><h1>Cancha</h1></body></html>")
        self.anadir("https://cancha.test/futbol",
                    '<html><body><a href="/futbol/1002-victoria-en-el-clasico">Clasico</a>'
                    '<a href="/futbol">Seccion</a></body></html>')

        # -- pixel.test: solo densidad, descubierto por sitemap -------------
        articulos_pixel = [
            ("/news/a-long-awaited-game-finally-arrives",
             "A long awaited game finally arrives on every console", "2026-08-25T12:00:00Z"),
            ("/news/the-studio-announces-a-sequel",
             "The studio announces a sequel for next year", "2026-08-24T12:00:00Z"),
        ]
        for ruta, titulo, fecha in articulos_pixel:
            self.anadir(f"https://pixel.test{ruta}", _articulo_desnudo(titulo, ruta, fecha))
        self.anadir("https://pixel.test/sitemap.xml",
                    _sitemap("https://pixel.test", [r for r, _, _ in articulos_pixel]), xml)
        self.anadir("https://pixel.test/", "<html><body><h1>Pixel</h1></body></html>")
        self.anadir("https://pixel.test/news",
                    '<html><body><a href="/news/the-studio-announces-a-sequel">Sequel</a></body></html>')
