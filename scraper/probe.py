"""Doctor de fuentes: comprueba que lo declarado en ``sources.py`` sigue vivo.

Los medios mueven sus feeds sin avisar y rediseñan sus paginas cada pocos
meses. Este comando toca cada fuente por encima --un feed, un sitemap, una
portada y un articulo de muestra-- y dice que sigue funcionando y que no, sin
llegar a guardar nada.

Es el comando que hay que ejecutar cuando una fuente deja de publicar: en un
minuto se ve si el problema es el feed, el detector de articulos o el parseo.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from . import discovery
from .fetcher import Fetcher
from .parser import parsear
from .sources import FUENTES, Fuente, activas

log = logging.getLogger(__name__)

MUESTRA = 2          # articulos que se descargan por fuente


def revisar(fuente: Fuente, fetcher: Fetcher, con_articulo: bool = True,
            detalle: bool = False) -> dict:
    """Revision rapida de una sola fuente."""
    informe: dict = {
        "clave": fuente.clave,
        "nombre": fuente.nombre,
        "vertical": fuente.vertical,
        "feeds_ok": [],
        "feeds_ko": [],
        "urls_rss": 0,
        "urls_sitemap": 0,
        "urls_crawl": 0,
        "muestra": [],
        "nota": fuente.nota,
        # Lo que impide que la fuente aporte nada.
        "problemas": [],
        # Lo que conviene arreglar pero no la deja fuera de juego.
        "avisos": [],
    }

    if detalle:
        informe["portada_detalle"] = fetcher.inspeccionar(fuente.home)

    # -- portada -----------------------------------------------------------
    # Que no responda es solo un aviso: hay fuentes que publican desde un
    # subdominio --un blog, un CDN de feeds-- y su portada principal no pinta
    # nada aqui. Lo que decide es si al final salen noticias.
    portada = fetcher.get(fuente.home)
    informe["home_ok"] = portada is not None
    if portada is None:
        informe["avisos"].append("no responde la portada")

    # -- feeds, uno a uno --------------------------------------------------
    for feed in fuente.feeds:
        resp = fetcher.get(feed)
        if resp is None:
            informe["feeds_ko"].append(feed)
            continue
        entradas = len(discovery._xml(resp.text).find_all(["item", "entry"]))
        if entradas:
            informe["feeds_ok"].append({"url": feed, "entradas": entradas})
        else:
            informe["feeds_ko"].append(feed)

    if fuente.feeds and not informe["feeds_ok"]:
        # Tampoco es mortal: el descubrimiento lee ademas los feeds que anuncia
        # la portada y puede tirar de sitemap o de crawl. Se decide al final.
        informe["avisos"].append("ningun feed declarado responde con entradas")

    # -- que rinde cada via -------------------------------------------------
    urls: set[str] = set()
    if fuente.feeds:
        desde_rss = discovery.desde_feeds(fuente, fetcher, tope=60)
        informe["urls_rss"] = len(desde_rss)
        urls |= desde_rss
    if fuente.sitemaps or not urls:
        desde_sitemap = discovery.desde_sitemaps(fuente, fetcher, tope=60)
        informe["urls_sitemap"] = len(desde_sitemap)
        urls |= desde_sitemap
    if not urls:
        desde_crawl = discovery.desde_crawl(fuente, fetcher, profundidad=1, max_paginas=8, tope=60)
        informe["urls_crawl"] = len(desde_crawl)
        urls |= desde_crawl

    informe["urls_totales"] = len(urls)
    if not urls:
        informe["problemas"].append("no se descubre ninguna URL de noticia")

    # -- se parsea de verdad lo que se descubre? ----------------------------
    if con_articulo:
        for url in sorted(urls)[:MUESTRA]:
            resp = fetcher.get(url)
            if resp is None:
                fallo = {"url": url, "estado": "no descarga"}
                if detalle:
                    fallo["detalle"] = fetcher.inspeccionar(url)
                informe["muestra"].append(fallo)
                continue
            try:
                articulo = parsear(resp.text, resp.url, fuente)
            except Exception as exc:
                informe["muestra"].append({"url": url, "estado": f"error de parseo: {exc}"})
                continue
            if articulo is None:
                fallo = {"url": url, "estado": "sin titular"}
                if detalle:
                    fallo["detalle"] = fetcher.inspeccionar(url)
                informe["muestra"].append(fallo)
                continue
            informe["muestra"].append({
                "url": url,
                "estado": "ok",
                "titulo": articulo["title"][:70],
                "categoria": articulo["category"],
                "palabras": articulo["word_count"],
                "imagenes": len(articulo["images"]),
            })

        con_cuerpo = [m for m in informe["muestra"] if m.get("palabras", 0) >= 60]
        informe["con_cuerpo"] = len(con_cuerpo)
        if informe["muestra"] and not con_cuerpo:
            sin_descarga = [m for m in informe["muestra"] if m["estado"] == "no descarga"]
            if sin_descarga:
                informe["problemas"].append(
                    "se descubren noticias pero el medio no deja descargarlas"
                )
            else:
                informe["problemas"].append(
                    "se descubren noticias pero ninguna trae cuerpo: revisa los "
                    "selectores de `cuerpo` en sources.py"
                )

    # Una fuente esta en pie si de verdad rinde: descubre noticias y al menos
    # una de las de muestra llega con cuerpo. Que un feed declarado este muerto
    # es algo que arreglar, no un motivo para darla por perdida, y confundir las
    # dos cosas convierte la revision semanal en ruido que se acaba ignorando.
    informe["ok"] = not informe["problemas"]
    return informe


def revisar_todas(
    claves: list[str] | None = None,
    workers: int = 6,
    con_articulo: bool = True,
    detalle: bool = False,
    **opciones_fetcher,
) -> list[dict]:
    # Con --detalle se revisa tambien lo que este apagado: es justo cuando hace
    # falta saber si sigue sin rendir o ya se puede volver a encender.
    fuentes = activas(claves) if not detalle or claves else list(FUENTES)
    fetcher = Fetcher(**opciones_fetcher)
    try:
        # Cada fuente es un dominio distinto, asi que el paralelismo aqui no
        # aprieta a nadie: el reloj por host sigue mandando.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            informes = list(pool.map(lambda f: revisar(f, fetcher, con_articulo, detalle), fuentes))
    finally:
        fetcher.close()
    return informes


def formatear(informes: list[dict]) -> str:
    """Informe legible para la terminal y para el resumen del workflow."""
    lineas: list[str] = []
    vivas = [i for i in informes if i["ok"]]
    con_avisos = [i for i in vivas if i.get("avisos") or i["feeds_ko"]]
    lineas.append(f"{len(vivas)} de {len(informes)} fuentes en pie"
                  + (f", {len(con_avisos)} con algo que revisar" if con_avisos else "")
                  + "\n")

    for informe in sorted(informes, key=lambda i: (i["ok"], i["vertical"], i["clave"])):
        marca = "OK  " if informe["ok"] else "MAL "
        lineas.append(
            f"{marca} {informe['clave']:<16} {informe['vertical']:<11} "
            f"feeds {len(informe['feeds_ok'])}/{len(informe['feeds_ok']) + len(informe['feeds_ko'])}  "
            f"URLs {informe['urls_totales']:<5} "
            f"(rss {informe['urls_rss']}, sitemap {informe['urls_sitemap']}, crawl {informe['urls_crawl']})"
        )
        for muestra in informe["muestra"]:
            if muestra["estado"] == "ok":
                lineas.append(
                    f"       · {muestra['categoria']:<22} {muestra['palabras']:>5} palabras  "
                    f"{muestra['titulo']}"
                )
            else:
                lineas.append(f"       · {muestra['estado']}: {muestra['url']}")
                d = muestra.get("detalle")
                if d:
                    lineas.append(
                        f"           HTTP {d.get('status')} {d.get('error') or ''} "
                        f"{d.get('bytes')}B {d.get('content_type','')[:30]} {d.get('servidor','')}"
                    )
                    if d.get("json_incrustado"):
                        lineas.append(f"           JSON incrustado: {', '.join(d['json_incrustado'])}"
                                      f" | <h1>: {d.get('tiene_h1')}")
        for problema in informe["problemas"]:
            lineas.append(f"       ! {problema}")
        for aviso in informe.get("avisos", []):
            lineas.append(f"       ~ {aviso}")
        if informe["nota"] and not informe["ok"]:
            lineas.append(f"       i {informe['nota']}")
        portada = informe.get("portada_detalle")
        if portada and not informe["home_ok"]:
            lineas.append(f"       > portada: HTTP {portada.get('status')} "
                          f"{portada.get('error') or ''} {portada.get('servidor','')}")
        for feed in informe["feeds_ko"]:
            lineas.append(f"       x feed caido: {feed}")

    return "\n".join(lineas)
