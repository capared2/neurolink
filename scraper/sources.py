"""Registro de fuentes.

Anadir un medio al agregador es anadir una entrada aqui: nada mas del scraper
sabe que existe BBC o IGN. Cada fuente declara donde buscar sus noticias
(feeds, sitemaps, portadas), como distinguir un articulo de una pagina de
seccion, y donde vive el cuerpo si su maquetacion se resiste a la heuristica
general.

Los feeds son candidatos, no promesas: los medios los mueven de sitio sin
avisar. El descubrimiento se salta en silencio el que no responda y ademas
lee los ``<link rel="alternate">`` de la portada, asi que una URL caduca
degrada la fuente pero no rompe la ejecucion. ``python -m scraper doctor``
comprueba las 21 de una pasada y dice cuales siguen vivas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

from . import config

# Rutas que en cualquier medio son indice, tramite o ruido, nunca una noticia.
DENEGAR_GLOBAL = (
    r"^/(tag|tags|topic|topics|author|authors|autor|autores|search|buscar|busca)/",
    r"^/(newsletter|newsletters|subscribe|suscri|registro|login|signin|account|cuenta)",
    r"^/(about|contact|contacto|privacy|privacidad|terms|terminos|legal|cookies|aviso)",
    r"^/(rss|feed|feeds|sitemap|amp)(/|$|\.)",
    r"/(comentarios|comments)/?$",
    r"\.(jpg|jpeg|png|gif|webp|svg|pdf|mp4|mp3|zip|xml|json|css|js)$",
)


@dataclass(frozen=True)
class Fuente:
    clave: str                  # identificador corto: "bbc"
    nombre: str                 # como se muestra: "BBC"
    home: str                   # portada, base de las semillas relativas
    vertical: str               # vertical "de casa" (noticias/deportes/gamer/tecnologia)
    idioma: str
    pais: str
    hosts: frozenset[str]       # de que dominios aceptamos articulos
    feeds: tuple[str, ...] = ()
    sitemaps: tuple[str, ...] = ()
    semillas: tuple[str, ...] = ()      # portadas de seccion para el crawl
    articulo: tuple[str, ...] = ()      # regex de path que marcan una noticia
    denegar: tuple[str, ...] = ()       # regex de path propias de la fuente
    cuerpo: tuple[str, ...] = ()        # selectores CSS del cuerpo del articulo
    tema_por_defecto: str | None = None
    delay: float = config.DEFAULT_DELAY
    activa: bool = True
    nota: str = ""                      # avisos que salen en el doctor

    # -- ayudas -----------------------------------------------------------
    def acepta_host(self, url: str) -> bool:
        return urlsplit(url).netloc.lower().removeprefix("www.") in self.hosts_normalizados

    @property
    def hosts_normalizados(self) -> frozenset[str]:
        return frozenset(h.lower().removeprefix("www.") for h in self.hosts)

    def es_articulo(self, url: str) -> bool:
        """True si la URL parece una noticia suelta de esta fuente."""
        if not self.acepta_host(url):
            return False
        ruta = urlsplit(url).path or "/"
        if any(patron.search(ruta) for patron in _compilar(DENEGAR_GLOBAL + self.denegar)):
            return False
        if self.articulo:
            return any(patron.search(ruta) for patron in _compilar(self.articulo))
        return _parece_articulo(ruta)


@lru_cache(maxsize=256)
def _compilar(patrones: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in patrones)


_FECHA_EN_RUTA = re.compile(r"/(?:19|20)\d{2}/\d{1,2}(?:/\d{1,2})?/")
_ID_FINAL = re.compile(r"[-/](?:rcna)?\d{5,}(?:\.html?)?$")


def _parece_articulo(ruta: str) -> bool:
    """Heuristica para fuentes sin patron propio.

    Una noticia casi siempre tiene una fecha en la ruta, un identificador
    numerico largo al final, o un titular convertido en slug. Una portada de
    seccion no tiene ninguna de las tres: "/news" o "/about" no llevan guiones.
    """
    segmentos = [s for s in ruta.split("/") if s]
    if not segmentos:
        return False
    hoja = segmentos[-1].removesuffix(".html").removesuffix(".htm")
    parece_titular = hoja.count("-") >= 3

    # Un blog publica en la raiz: ahi el titular en la URL es la unica pista,
    # y por eso se exige entera.
    if len(segmentos) < 2:
        return parece_titular

    return bool(_FECHA_EN_RUTA.search(ruta) or _ID_FINAL.search(ruta)) or parece_titular


# ---------------------------------------------------------------------------
# Las fuentes
# ---------------------------------------------------------------------------

FUENTES: tuple[Fuente, ...] = (
    # ---------------------------------------------------------------- Noticias
    Fuente(
        clave="bbc", nombre="BBC", home="https://www.bbc.com",
        vertical="noticias", idioma="en", pais="GB",
        hosts=frozenset({"bbc.com", "bbc.co.uk"}),
        feeds=(
            "https://feeds.bbci.co.uk/news/rss.xml",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            "https://feeds.bbci.co.uk/news/health/rss.xml",
            "https://feeds.bbci.co.uk/news/politics/rss.xml",
            "https://feeds.bbci.co.uk/sport/rss.xml",
            "https://feeds.bbci.co.uk/sport/football/rss.xml",
        ),
        semillas=("/news", "/sport", "/news/technology", "/news/world"),
        articulo=(r"^/news/articles/[a-z0-9]+$", r"^/news/[a-z0-9-]+-\d{6,}$",
                  r"^/sport/[a-z0-9/-]*articles/[a-z0-9]+$", r"^/sport/[a-z0-9-]+/\d{6,}$"),
        denegar=(r"^/(iplayer|sounds|weather|programmes|bitesize|ideas)",),
        cuerpo=("article", "main[role=main]", "[data-component=text-block]"),
    ),
    Fuente(
        clave="globo", nombre="Globo", home="https://g1.globo.com",
        vertical="noticias", idioma="pt", pais="BR",
        hosts=frozenset({"g1.globo.com", "ge.globo.com", "oglobo.globo.com", "globo.com"}),
        feeds=(
            "https://g1.globo.com/rss/g1/",
            "https://g1.globo.com/rss/g1/mundo/",
            "https://g1.globo.com/rss/g1/economia/",
            "https://g1.globo.com/rss/g1/tecnologia/",
            "https://g1.globo.com/rss/g1/politica/",
            "https://g1.globo.com/rss/g1/ciencia-e-saude/",
            "https://ge.globo.com/rss/",
        ),
        semillas=("/", "/mundo", "/economia", "/tecnologia"),
        articulo=(r"/\d{4}/\d{2}/\d{2}/.+\.ghtml$",),
        cuerpo=(".mc-article-body", ".content-text", "article"),
    ),
    Fuente(
        clave="nytimes", nombre="The New York Times", home="https://www.nytimes.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"nytimes.com"}),
        feeds=(
            "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        ),
        articulo=(r"^/\d{4}/\d{2}/\d{2}/.+\.html$",),
        denegar=(
            r"^/(interactive|live|video|crosswords|games|cooking|wirecutter|athletic)/",
            # En NYT la seccion va detras de la fecha: /2026/08/25/crosswords/...
            r"^/\d{4}/\d{2}/\d{2}/(interactive|crosswords|games|cooking|wirecutter|athletic)/",
        ),
        cuerpo=("section[name=articleBody]", "article#story", "article"),
        nota="Es un medio de pago: muchas noticias llegan con el cuerpo recortado "
             "y las descarta el filtro de --min-words.",
    ),
    Fuente(
        clave="cnn", nombre="CNN", home="https://edition.cnn.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"cnn.com", "edition.cnn.com"}),
        feeds=(
            "http://rss.cnn.com/rss/edition.rss",
            "http://rss.cnn.com/rss/edition_world.rss",
            "http://rss.cnn.com/rss/edition_technology.rss",
            "http://rss.cnn.com/rss/edition_sport.rss",
            "http://rss.cnn.com/rss/edition_business.rss",
            "http://rss.cnn.com/rss/cnn_topstories.rss",
        ),
        semillas=("/world", "/business", "/sport"),
        articulo=(r"^/\d{4}/\d{2}/\d{2}/",),
        denegar=(r"^/(videos|live-news|interactive|audio|profiles)/",),
        cuerpo=(".article__content", ".article__content-container", "article"),
    ),
    Fuente(
        clave="foxnews", nombre="Fox News", home="https://www.foxnews.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"foxnews.com"}),
        feeds=(
            "https://moxie.foxnews.com/google-publisher/latest.xml",
            "https://moxie.foxnews.com/google-publisher/world.xml",
            "https://moxie.foxnews.com/google-publisher/politics.xml",
            "https://moxie.foxnews.com/google-publisher/tech.xml",
            "https://moxie.foxnews.com/google-publisher/science.xml",
            "https://moxie.foxnews.com/google-publisher/sports.xml",
            "https://moxie.foxnews.com/google-publisher/us.xml",
        ),
        semillas=("/world", "/politics", "/tech", "/sports"),
        articulo=(r"^/(politics|us|world|opinion|tech|science|health|entertainment|sports|media|lifestyle|travel|food-drink)/[a-z0-9-]{10,}",),
        denegar=(r"^/video/", r"^/shows/"),
        cuerpo=(".article-body", "article"),
    ),
    Fuente(
        clave="toi", nombre="Times of India", home="https://timesofindia.indiatimes.com",
        vertical="noticias", idioma="en", pais="IN",
        hosts=frozenset({"timesofindia.indiatimes.com"}),
        feeds=(
            "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
            "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
            "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",
            "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms",
            "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",
            "https://timesofindia.indiatimes.com/rssfeeds/5880659.cms",
        ),
        semillas=("/world", "/business", "/sports"),
        articulo=(r"/articleshow/\d+\.cms$",),
        denegar=(r"^/(photo|videos|web-stories|city/[a-z]+/photo)",),
        cuerpo=("div._s30J", ".ga-headlines", "article"),
    ),
    Fuente(
        clave="thehill", nombre="The Hill", home="https://thehill.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"thehill.com"}),
        feeds=("https://thehill.com/feed/",),
        semillas=("/policy", "/homenews", "/business"),
        articulo=(r"^/[a-z0-9-]+/(?:[a-z0-9-]+/)?\d{6,}-[a-z0-9-]+/?$",),
        cuerpo=(".article__text", ".content-wrp", "article"),
        tema_por_defecto="noticias/politica",
    ),
    Fuente(
        clave="aljazeera", nombre="Al Jazeera", home="https://www.aljazeera.com",
        vertical="noticias", idioma="en", pais="QA",
        hosts=frozenset({"aljazeera.com"}),
        feeds=("https://www.aljazeera.com/xml/rss/all.xml",),
        semillas=("/news", "/economy", "/sports"),
        articulo=(r"^/(news|sports|economy|features|opinion|climate-crisis)/\d{4}/\d{1,2}/\d{1,2}/",),
        denegar=(r"^/(program|videos|live)/",),
        cuerpo=(".wysiwyg--all-content", "main .wysiwyg", "article"),
    ),
    Fuente(
        clave="nbcnews", nombre="NBC News", home="https://www.nbcnews.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"nbcnews.com"}),
        feeds=(
            "https://feeds.nbcnews.com/nbcnews/public/news",
            "https://feeds.nbcnews.com/nbcnews/public/world",
            "https://feeds.nbcnews.com/nbcnews/public/politics",
            "https://feeds.nbcnews.com/nbcnews/public/tech",
            "https://feeds.nbcnews.com/nbcnews/public/health",
        ),
        semillas=("/world", "/politics", "/tech-media"),
        articulo=(r"-rcna\d+/?$",),
        denegar=(r"^/(video|select|shopping|live-blog)/",),
        cuerpo=(".article-body__content", "div.article-body", "article"),
    ),
    Fuente(
        clave="yahoo", nombre="Yahoo News", home="https://www.yahoo.com",
        vertical="noticias", idioma="en", pais="US",
        hosts=frozenset({"yahoo.com", "news.yahoo.com", "sports.yahoo.com", "finance.yahoo.com"}),
        feeds=(
            "https://www.yahoo.com/news/rss",
            "https://news.yahoo.com/rss/world",
            "https://sports.yahoo.com/rss/",
            "https://finance.yahoo.com/news/rssindex",
        ),
        articulo=(r"-\d{6,}\.html$",),
        denegar=(r"^/(video|lifestyle/shopping)/",),
        cuerpo=(".caas-body", "article"),
        nota="Yahoo sindica noticias de terceros. Cuando el articulo declara un "
             "canonical fuera de yahoo.com se respeta el original como fuente.",
    ),

    # ---------------------------------------------------------------- Deportes
    Fuente(
        clave="espn", nombre="ESPN", home="https://www.espn.com",
        vertical="deportes", idioma="en", pais="US",
        hosts=frozenset({"espn.com"}),
        feeds=(
            "https://www.espn.com/espn/rss/news",
            "https://www.espn.com/espn/rss/soccer/news",
            "https://www.espn.com/espn/rss/nba/news",
            "https://www.espn.com/espn/rss/nfl/news",
            "https://www.espn.com/espn/rss/mlb/news",
            "https://www.espn.com/espn/rss/nhl/news",
        ),
        semillas=("/soccer/", "/nba/", "/nfl/"),
        articulo=(r"/story/_/id/\d+/",),
        denegar=(r"^/(watch|video|fantasy)/",),
        cuerpo=(".article-body", ".Story__Body", "article"),
    ),
    Fuente(
        clave="fifa", nombre="FIFA", home="https://www.fifa.com",
        vertical="deportes", idioma="en", pais="CH",
        hosts=frozenset({"fifa.com", "inside.fifa.com"}),
        sitemaps=("https://www.fifa.com/sitemap.xml", "https://inside.fifa.com/sitemap.xml"),
        semillas=("/en/news", "/es/news", "/articles"),
        articulo=(r"/articles/", r"/news/[a-z0-9-]{10,}"),
        cuerpo=("article", ".ff-mt-0", "main"),
        tema_por_defecto="deportes/futbol",
        nota="No publica RSS conocido y su web depende mucho de JavaScript: se "
             "descubre por sitemap y portadas, y puede rendir poco.",
    ),
    Fuente(
        clave="marca", nombre="Marca", home="https://www.marca.com",
        vertical="deportes", idioma="es", pais="ES",
        hosts=frozenset({"marca.com"}),
        feeds=(
            "https://e00-marca.uecdn.es/rss/portada.xml",
            "https://e00-marca.uecdn.es/rss/futbol/primera-division.xml",
            "https://e00-marca.uecdn.es/rss/futbol/real-madrid.xml",
            "https://e00-marca.uecdn.es/rss/futbol/barcelona.xml",
            "https://e00-marca.uecdn.es/rss/futbol/champions-league.xml",
            "https://e00-marca.uecdn.es/rss/futbol/premier-league.xml",
            "https://e00-marca.uecdn.es/rss/baloncesto.xml",
            "https://e00-marca.uecdn.es/rss/baloncesto/nba.xml",
            "https://e00-marca.uecdn.es/rss/motor/formula1.xml",
            "https://e00-marca.uecdn.es/rss/tenis.xml",
            "https://e00-marca.uecdn.es/rss/nfl.xml",
            "https://e00-marca.uecdn.es/rss/esports.xml",
        ),
        semillas=("/futbol.html", "/baloncesto.html", "/motor.html"),
        articulo=(r"/(?:19|20)\d{2}/\d{2}/\d{2}/[^/]+\.html$", r"/[0-9a-f]{16,}\.html$"),
        denegar=(r"^/(servicios|registro|suscripcion|estaticos|promociones|participacion)/",),
        # Selectores comprobados en el scraper anterior (capared2/markap).
        cuerpo=("div.ue-l-article__body div.ue-l-article__main-column",
                "div.ue-c-article__body", "div.ue-l-article__body", "article"),
    ),
    Fuente(
        clave="skysports", nombre="Sky Sports", home="https://www.skysports.com",
        vertical="deportes", idioma="en", pais="GB",
        hosts=frozenset({"skysports.com"}),
        feeds=(
            "https://www.skysports.com/rss/12040",
            "https://www.skysports.com/rss/11095",
            "https://www.skysports.com/rss/11661",
            "https://www.skysports.com/rss/12023",
        ),
        semillas=("/football/news", "/f1/news", "/nfl/news"),
        articulo=(r"/news/\d+/\d+/", r"/[a-z0-9-]+/news/\d+/"),
        denegar=(r"^/(watch|video|live)/",),
        cuerpo=(".sdc-article-body", "article"),
    ),
    Fuente(
        clave="bleacherreport", nombre="Bleacher Report", home="https://bleacherreport.com",
        vertical="deportes", idioma="en", pais="US",
        hosts=frozenset({"bleacherreport.com"}),
        feeds=("https://bleacherreport.com/articles/feed",),
        semillas=("/articles", "/nba", "/nfl"),
        articulo=(r"^/articles/\d+",),
        cuerpo=(".articleContent", ".organism-article", "article"),
    ),

    # ------------------------------------------------------------------ Gamer
    Fuente(
        clave="ign", nombre="IGN", home="https://www.ign.com",
        vertical="gamer", idioma="en", pais="US",
        hosts=frozenset({"ign.com"}),
        feeds=(
            "https://feeds.feedburner.com/ign/all",
            "https://www.ign.com/rss/articles/feed",
            "https://www.ign.com/rss/news/feed",
        ),
        semillas=("/news", "/articles", "/reviews"),
        articulo=(r"^/(articles|news|review|reviews|previews)/[a-z0-9-]{5,}",),
        denegar=(r"^/(videos|wikis|games/[a-z0-9-]+$|maps)/",),
        cuerpo=(".article-page", "section.article-content", ".article-content", "article"),
    ),
    Fuente(
        clave="faceit", nombre="FACEIT", home="https://www.faceit.com",
        vertical="gamer", idioma="en", pais="GB",
        hosts=frozenset({"faceit.com", "blog.faceit.com"}),
        feeds=("https://blog.faceit.com/feed/", "https://blog.faceit.com/rss/"),
        semillas=("/en/news", "/en"),
        cuerpo=("article", ".post-content", "main"),
        tema_por_defecto="gamer/esports",
        nota="faceit.com es una aplicacion de una sola pagina y sus datos de "
             "competicion van por API con clave. Aqui solo se recoge su blog; "
             "si el doctor lo da por muerto, desactiva la fuente.",
    ),
    Fuente(
        clave="twitch", nombre="Twitch", home="https://blog.twitch.tv",
        vertical="gamer", idioma="en", pais="US",
        hosts=frozenset({"blog.twitch.tv", "twitch.tv"}),
        feeds=("https://blog.twitch.tv/en/rss.xml", "https://blog.twitch.tv/feed/"),
        semillas=("/en/", "/en/news/"),
        articulo=(r"^/[a-z]{2}/\d{4}/\d{2}/\d{2}/",),
        cuerpo=("article", ".post-body", "main"),
        tema_por_defecto="gamer/streaming",
        nota="Lo que pasa en directo (canales, espectadores, categorias) solo se "
             "lee por la API Helix con credenciales de aplicacion, que este "
             "scraper no usa: de Twitch se recoge su blog oficial.",
    ),
    Fuente(
        clave="steam", nombre="Steam", home="https://store.steampowered.com",
        vertical="gamer", idioma="en", pais="US",
        hosts=frozenset({"store.steampowered.com", "steamcommunity.com", "steampowered.com"}),
        feeds=(
            "https://store.steampowered.com/feeds/news.xml",
            "https://store.steampowered.com/feeds/newshub.xml",
        ),
        semillas=("/news/", "/news/collection/steam"),
        articulo=(r"/news/(app|group)/\d+/view/\d+", r"/news/[a-z0-9-]{8,}"),
        cuerpo=(".newsPostBlock", ".body", "article", "main"),
        tema_por_defecto="gamer/juegos",
    ),

    # ------------------------------------------------------------- Tecnologia
    Fuente(
        clave="theverge", nombre="The Verge", home="https://www.theverge.com",
        vertical="tecnologia", idioma="en", pais="US",
        hosts=frozenset({"theverge.com"}),
        feeds=("https://www.theverge.com/rss/index.xml",),
        semillas=("/tech", "/news"),
        articulo=(r"^/\d{4}/\d{1,2}/\d{1,2}/", r"^/[a-z0-9-]+/\d{5,}/[a-z0-9-]+"),
        denegar=(r"^/(videos|podcasts)/",),
        cuerpo=(".duet--article--article-body-component", "article", "main"),
    ),
    Fuente(
        clave="techcrunch", nombre="TechCrunch", home="https://techcrunch.com",
        vertical="tecnologia", idioma="en", pais="US",
        hosts=frozenset({"techcrunch.com"}),
        feeds=("https://techcrunch.com/feed/",),
        semillas=("/", "/category/artificial-intelligence/", "/category/startups/"),
        articulo=(r"^/\d{4}/\d{2}/\d{2}/",),
        denegar=(r"^/(events|video|podcast)/",),
        cuerpo=(".article-content", ".entry-content", "article"),
        tema_por_defecto="tecnologia/empresas",
    ),
)

POR_CLAVE: dict[str, Fuente] = {f.clave: f for f in FUENTES}


def activas(claves: list[str] | None = None) -> list[Fuente]:
    """Las fuentes que se van a scrapear en esta ejecucion."""
    if not claves:
        return [f for f in FUENTES if f.activa]
    pedidas = {c.strip().lower() for c in claves if c.strip()}
    return [f for f in FUENTES if f.clave in pedidas]


def desconocidas(claves: list[str]) -> list[str]:
    return sorted({c.strip().lower() for c in claves if c.strip()} - set(POR_CLAVE))


def fuente_de(url: str) -> Fuente | None:
    """A que fuente pertenece una URL, si es que pertenece a alguna."""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    for fuente in FUENTES:
        if host in fuente.hosts_normalizados:
            return fuente
    return None
