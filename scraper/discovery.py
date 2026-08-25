"""Descubrimiento de URLs de noticia, fuente a fuente.

Tres vias que se complementan:

``rss``      Lo mas barato y lo mas fresco. Las URLs que salen de un feed se
             dan por buenas sin mas comprobacion que el dominio: un medio no
             mete portadas de seccion en su propio canal de novedades.
``sitemap``  Los sitemaps declarados por la fuente y los que anuncie su
             ``robots.txt``. Es la via del archivo historico.
``crawl``    Recorrido corto por las portadas de seccion, para lo que no
             aparezca en las otras dos.

Cada fuente tiene su propio techo de URLs y su propio plazo, de modo que un
medio con un sitemap gigantesco no puede quedarse con la ejecucion entera.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import config
from . import urls as urlutil
from .fetcher import Fetcher
from .sources import Fuente

log = logging.getLogger(__name__)


def _vencido(plazo: float | None) -> bool:
    return plazo is not None and time.monotonic() >= plazo


def _xml(texto: str) -> BeautifulSoup:
    return BeautifulSoup(texto, "xml")


# ---------------------------------------------------------------------------
# RSS / Atom
# ---------------------------------------------------------------------------

def feeds_de(fuente: Fuente, fetcher: Fetcher) -> list[str]:
    """Los feeds declarados mas los que anuncie la portada.

    Lo segundo es el seguro contra el dia en que un medio mueva su RSS: la
    portada casi siempre sigue declarandolo en un ``<link rel="alternate">``.
    """
    candidatos = list(fuente.feeds)
    resp = fetcher.get(fuente.home)
    if resp is not None:
        sopa = BeautifulSoup(resp.text, "lxml")
        for enlace in sopa.find_all("link", rel=lambda v: v and "alternate" in v):
            tipo = enlace.get("type", "")
            if re.search(r"rss|atom|xml", tipo, re.I) and enlace.get("href"):
                candidatos.append(urljoin(fuente.home, enlace["href"]))
    return list(dict.fromkeys(candidatos))


def desde_feeds(
    fuente: Fuente,
    fetcher: Fetcher,
    feeds: list[str] | None = None,
    plazo: float | None = None,
    tope: int = config.MAX_URLS_POR_FUENTE,
) -> set[str]:
    encontradas: set[str] = set()
    for feed in feeds if feeds is not None else feeds_de(fuente, fetcher):
        if _vencido(plazo) or len(encontradas) >= tope:
            break
        resp = fetcher.get(feed)
        if resp is None:
            continue
        antes = len(encontradas)
        for nodo in _xml(resp.text).find_all(["item", "entry"]):
            # El tope se mira entrada a entrada, no solo entre feeds: un canal
            # con miles de items se saltaria de largo el limite de la fuente.
            if len(encontradas) >= tope:
                break
            bruta = _enlace_de_entrada(nodo)
            if not bruta:
                continue
            candidata = urlutil.normalizar(bruta)
            # Del feed nos fiamos: basta con que el dominio sea de la fuente.
            if fuente.acepta_host(candidata):
                encontradas.add(candidata)
        if len(encontradas) != antes:
            log.debug("%s: feed %s -> %s URLs nuevas", fuente.clave, feed, len(encontradas) - antes)
    log.info("%s: %s URLs desde RSS", fuente.clave, len(encontradas))
    return encontradas


def _enlace_de_entrada(nodo) -> str | None:
    """La URL de un ``<item>`` de RSS o de un ``<entry>`` de Atom."""
    enlace = nodo.find("link")
    if enlace is not None:
        # RSS mete la URL como texto; Atom la mete en href.
        texto = enlace.get_text(strip=True)
        if texto:
            return texto
        if enlace.get("href"):
            return enlace["href"]
    # Atom con varios <link>: el que vale es rel="alternate".
    for otro in nodo.find_all("link"):
        if otro.get("rel") in (None, "alternate", ["alternate"]) and otro.get("href"):
            return otro["href"]
    guid = nodo.find("guid")
    if guid is not None:
        texto = guid.get_text(strip=True)
        if texto.startswith("http"):
            return texto
    return None


# ---------------------------------------------------------------------------
# Sitemaps
# ---------------------------------------------------------------------------

def _recorrer_sitemap(
    fuente: Fuente,
    fetcher: Fetcher,
    url: str,
    visitados: set[str],
    profundidad: int,
    salida: set[str],
    plazo: float | None,
    tope: int,
) -> None:
    if (
        profundidad > config.MAX_SITEMAP_DEPTH
        or url in visitados
        or _vencido(plazo)
        or len(salida) >= tope
    ):
        return
    visitados.add(url)
    resp = fetcher.get(url)
    if resp is None:
        return

    sopa = _xml(resp.text)

    hijos = [loc.get_text(strip=True) for loc in sopa.select("sitemapindex > sitemap > loc")]
    # Los sitemaps de noticias van primero: es donde esta lo reciente, y si el
    # plazo se agota a medias habremos cogido lo que mas importa.
    hijos.sort(key=lambda u: 0 if re.search(r"news|noticia|recent|latest", u, re.I) else 1)
    for hijo in hijos:
        _recorrer_sitemap(fuente, fetcher, hijo, visitados, profundidad + 1, salida, plazo, tope)

    anadidas = 0
    for loc in sopa.select("urlset > url > loc"):
        if len(salida) >= tope:
            break
        candidata = urlutil.normalizar(loc.get_text(strip=True))
        if fuente.es_articulo(candidata):
            salida.add(candidata)
            anadidas += 1
    if anadidas or hijos:
        log.debug("%s: sitemap %s -> %s articulos, %s hijos",
                  fuente.clave, url, anadidas, len(hijos))


def desde_sitemaps(
    fuente: Fuente,
    fetcher: Fetcher,
    plazo: float | None = None,
    tope: int = config.MAX_URLS_POR_FUENTE,
) -> set[str]:
    encontradas: set[str] = set()
    visitados: set[str] = set()

    raices = list(fuente.sitemaps)
    raices += fetcher.sitemaps_de(fuente.home)
    raices += [urljoin(fuente.home, ruta) for ruta in ("/sitemap.xml", "/sitemap_index.xml", "/news-sitemap.xml")]

    for raiz in dict.fromkeys(raices):
        _recorrer_sitemap(fuente, fetcher, raiz, visitados, 0, encontradas, plazo, tope)

    log.info("%s: %s URLs desde sitemaps", fuente.clave, len(encontradas))
    return encontradas


# ---------------------------------------------------------------------------
# Crawl de portadas
# ---------------------------------------------------------------------------

def desde_crawl(
    fuente: Fuente,
    fetcher: Fetcher,
    profundidad: int = 1,
    plazo: float | None = None,
    max_paginas: int = config.MAX_CRAWL_PAGES,
    tope: int = config.MAX_URLS_POR_FUENTE,
) -> set[str]:
    """Recorrido en anchura por las portadas de seccion.

    Una portada enlaza a cientos de otras portadas, asi que el recorrido esta
    acotado por tres sitios a la vez: paginas visitadas, URLs recogidas y
    plazo. Sin los tres, un solo medio se come la ejecucion.
    """
    articulos: set[str] = set()
    visitadas: set[str] = set()
    frontera = [
        urlutil.normalizar(urljoin(fuente.home, ruta))
        for ruta in (fuente.semillas or ("/",))
    ]

    for nivel in range(profundidad + 1):
        siguiente: list[str] = []
        for pagina in frontera:
            if pagina in visitadas:
                continue
            if len(visitadas) >= max_paginas or len(articulos) >= tope or _vencido(plazo):
                log.info("%s: crawl detenido tras %s paginas", fuente.clave, len(visitadas))
                return articulos
            visitadas.add(pagina)
            resp = fetcher.get(pagina)
            if resp is None:
                continue
            sopa = BeautifulSoup(resp.text, "lxml")
            for ancla in sopa.find_all("a", href=True):
                if len(articulos) >= tope:
                    return articulos
                candidata = urlutil.normalizar(urljoin(pagina, ancla["href"]))
                if not fuente.acepta_host(candidata):
                    continue
                if fuente.es_articulo(candidata):
                    articulos.add(candidata)
                elif nivel < profundidad and candidata not in visitadas:
                    siguiente.append(candidata)
        frontera = list(dict.fromkeys(siguiente))
        if not frontera:
            break

    log.info("%s: %s URLs desde crawl", fuente.clave, len(articulos))
    return articulos


# ---------------------------------------------------------------------------

def descubrir(
    fuente: Fuente,
    fetcher: Fetcher,
    vias: list[str],
    profundidad_crawl: int = 1,
    plazo: float | None = None,
    tope: int = config.MAX_URLS_POR_FUENTE,
) -> set[str]:
    """Ejecuta las vias pedidas para una fuente y junta lo que salga."""
    encontradas: set[str] = set()
    # El RSS va primero a proposito: si el plazo se queda corto, lo que se haya
    # recogido sera lo mas reciente en lugar de un trozo del archivo historico.
    if "rss" in vias:
        encontradas |= desde_feeds(fuente, fetcher, plazo=plazo, tope=tope)
    if "sitemap" in vias and len(encontradas) < tope:
        encontradas |= desde_sitemaps(fuente, fetcher, plazo=plazo, tope=tope - len(encontradas))
    if "crawl" in vias and len(encontradas) < tope:
        encontradas |= desde_crawl(
            fuente, fetcher, profundidad=profundidad_crawl, plazo=plazo, tope=tope - len(encontradas)
        )
    log.info("%s: %s URLs descubiertas en total", fuente.clave, len(encontradas))
    return encontradas
