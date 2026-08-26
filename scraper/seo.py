"""Sitemaps del sitio, generados a partir del dataset.

Se producen aqui y no en el frontend por dos razones: quedan al dia solos en
cada ejecucion, y el sitio los sirve tal cual sin gastar CPU en construirlos en
cada peticion (Cloudflare Workers corta a los 10 ms en el plan gratuito).

Las rutas tienen que coincidir con las del sitio::

    /                                   el rio universal
    /topics                             directorio de temas
    /<vertical>                         un nicho entero
    /<vertical>/<tema>                  un tema
    /article/<vertical>/<tema>/<id>     una noticia
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, taxonomy

log = logging.getLogger(__name__)

NOMBRE_PUBLICACION = "Gigantum.net"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _escribir(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")


def _urlset(urls: list[str], espacios: str = "") -> str:
    cabecera = f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"{espacios}>'
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', cabecera, *urls, "</urlset>", ""])


def _entrada(
    loc: str,
    lastmod: str | None = None,
    prioridad: str | None = None,
    frecuencia: str | None = None,
    extra: str = "",
) -> str:
    partes = ["  <url>", f"    <loc>{escape(loc)}</loc>"]
    if lastmod:
        partes.append(f"    <lastmod>{lastmod}</lastmod>")
    if frecuencia:
        partes.append(f"    <changefreq>{frecuencia}</changefreq>")
    if prioridad:
        partes.append(f"    <priority>{prioridad}</priority>")
    if extra:
        partes.append(extra)
    partes.append("  </url>")
    return "\n".join(partes)


def construir(
    data_dir: str | Path,
    site_url: str,
    articulos: list[dict],
    categorias: list[dict],
) -> dict:
    """Escribe los sitemaps en ``data/seo/`` y devuelve un manifiesto."""
    base = site_url.rstrip("/")
    destino = Path(data_dir) / "seo"

    ordenados = sorted(
        articulos,
        key=lambda a: (a.get("modified_at") or a.get("published_at") or ""),
        reverse=True,
    )

    # -- noticias, en trozos ------------------------------------------------
    trozos: list[str] = []
    for numero, comienzo in enumerate(range(0, len(ordenados), config.URLS_POR_SITEMAP), start=1):
        lote = ordenados[comienzo : comienzo + config.URLS_POR_SITEMAP]
        urls = [
            _entrada(
                f"{base}/article/{a['category']}/{a['id']}",
                a.get("modified_at") or a.get("published_at"),
                # El primer trozo es lo mas reciente: es donde interesa que el
                # rastreador vuelva a menudo.
                prioridad="0.8" if numero == 1 else "0.5",
                frecuencia="daily" if numero == 1 else "monthly",
            )
            for a in lote
            if a.get("id") and a.get("category")
        ]
        nombre = f"sitemap-noticias-{numero:04d}.xml"
        _escribir(destino / nombre, _urlset(urls))
        trozos.append(nombre)

    # -- secciones ----------------------------------------------------------
    fijas = [
        _entrada(f"{base}/", _ahora(), "1.0", "hourly"),
        _entrada(f"{base}/topics", _ahora(), "0.6", "weekly"),
    ]
    verticales = {c["category"].split("/")[0] for c in categorias}
    for vertical in sorted(verticales):
        fijas.append(_entrada(f"{base}/{vertical}", _ahora(), "0.9", "hourly"))
    for categoria in sorted(c["category"] for c in categorias):
        fijas.append(_entrada(f"{base}/{categoria}", _ahora(), "0.7", "hourly"))
    _escribir(destino / "sitemap-secciones.xml", _urlset(fijas))

    # -- Google News: solo las ultimas 48 h ---------------------------------
    limite = datetime.now(timezone.utc) - timedelta(hours=config.HORAS_GOOGLE_NEWS)
    recientes: list[str] = []
    for a in ordenados:
        publicada = a.get("published_at")
        if not publicada or not a.get("id"):
            continue
        try:
            cuando = datetime.fromisoformat(publicada.replace("Z", "+00:00"))
        except ValueError:
            continue
        if cuando < limite:
            continue
        idioma = (a.get("language") or "es").split("-")[0]
        recientes.append(
            _entrada(
                f"{base}/article/{a['category']}/{a['id']}",
                publicada,
                extra=(
                    "    <news:news>\n"
                    "      <news:publication>\n"
                    f"        <news:name>{NOMBRE_PUBLICACION}</news:name>\n"
                    f"        <news:language>{escape(idioma)}</news:language>\n"
                    "      </news:publication>\n"
                    f"      <news:publication_date>{publicada}</news:publication_date>\n"
                    f"      <news:title>{escape(a.get('title', ''))}</news:title>\n"
                    "    </news:news>"
                ),
            )
        )
        if len(recientes) >= config.MAX_GOOGLE_NEWS:
            break
    _escribir(
        destino / "sitemap-news.xml",
        _urlset(recientes, ' xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"'),
    )

    # -- indice que los agrupa ---------------------------------------------
    hijos = ["sitemap-secciones.xml", "sitemap-news.xml", *trozos]
    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for hijo in hijos:
        lineas += ["  <sitemap>", f"    <loc>{base}/{hijo}</loc>",
                   f"    <lastmod>{_ahora()}</lastmod>", "  </sitemap>"]
    lineas += ["</sitemapindex>", ""]
    _escribir(destino / "sitemap.xml", "\n".join(lineas))

    manifiesto = {
        "generated_at": _ahora(),
        "site_url": base,
        "total_urls": len(ordenados) + len(fijas),
        "sitemaps": hijos,
        "news_urls": len(recientes),
    }
    log.info("SEO: %s URLs en %s sitemaps (%s en el de noticias)",
             manifiesto["total_urls"], len(hijos), len(recientes))
    return manifiesto
