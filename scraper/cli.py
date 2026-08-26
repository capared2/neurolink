"""Linea de comandos: ``python -m scraper``.

Tres subcomandos::

    python -m scraper scrape     recoge noticias (es lo que hace por defecto)
    python -m scraper doctor     comprueba que las fuentes siguen vivas
    python -m scraper fuentes    lista lo que hay declarado
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config, probe, sources
from .runner import Opciones, ejecutar

VIAS = ("rss", "sitemap", "crawl")


def _comunes(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fuentes", default="",
                        help="claves separadas por coma (vacio = todas). Ver `fuentes`.")
    parser.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    parser.add_argument("--delay", type=float, default=config.DEFAULT_DELAY,
                        help="segundos entre peticiones AL MISMO dominio")
    parser.add_argument("--timeout", type=int, default=config.DEFAULT_TIMEOUT)
    parser.add_argument("--reintentos", type=int, default=config.DEFAULT_RETRIES)
    parser.add_argument("--user-agent", default=config.DEFAULT_USER_AGENT)
    parser.add_argument("--ignorar-robots", action="store_true",
                        help="no recomendado: se salta robots.txt")
    parser.add_argument("-v", "--verbose", action="store_true")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper",
        description="Scraper multinicho: recoge noticias de varias fuentes y las "
                    "guarda clasificadas en JSON.",
    )
    subs = parser.add_subparsers(dest="comando")

    # -- scrape ------------------------------------------------------------
    scrape = subs.add_parser("scrape", help="recoge noticias (por defecto)")
    _comunes(scrape)
    scrape.add_argument("--modo", choices=("incremental", "full"), default="incremental",
                        help="incremental: solo lo nuevo. full: tambien los sitemaps.")
    scrape.add_argument("--vias", default="",
                        help=f"de donde salen las URLs: {','.join(VIAS)} (vacio = segun modo)")
    scrape.add_argument("--profundidad-crawl", type=int, default=1)
    scrape.add_argument("--max-articulos", type=int, default=0, help="0 = sin limite")
    scrape.add_argument("--tam-parte", type=int, default=config.DEFAULT_SHARD_SIZE,
                        help="articulos por fichero JSON")
    scrape.add_argument("--presupuesto", type=int, default=config.DEFAULT_TIME_BUDGET,
                        help="segundos maximos de ejecucion (0 = sin limite)")
    scrape.add_argument("--desde", default=None, help="descarta noticias anteriores a AAAA-MM-DD")
    scrape.add_argument("--min-palabras", type=int, default=config.DEFAULT_MIN_WORDS,
                        help="cuerpo minimo para guardar una noticia (0 = guardar todo)")
    scrape.add_argument("--max-fallos", type=int, default=config.DEFAULT_MAX_FAILURES)
    scrape.add_argument("--reintentos-vacio", type=int, default=config.DEFAULT_EMPTY_RETRIES)
    scrape.add_argument("--tope-por-fuente", type=int, default=config.MAX_URLS_POR_FUENTE,
                        help="URLs maximas que puede aportar una fuente por ejecucion")
    scrape.add_argument("--site-url", default=config.SITE_URL,
                        help="dominio publico del sitio, para los sitemaps")
    scrape.add_argument("--data-dir", default="data")
    scrape.add_argument("--state-dir", default="state")
    scrape.add_argument("--saltar-descubrimiento", action="store_true",
                        help="solo vacia la cola pendiente, sin buscar URLs nuevas")
    scrape.add_argument("--resumen", default=None, help="escribe el resumen del run en este JSON")

    # -- doctor ------------------------------------------------------------
    doctor = subs.add_parser("doctor", help="comprueba que las fuentes siguen vivas")
    _comunes(doctor)
    doctor.add_argument("--sin-articulo", action="store_true",
                        help="no descarga articulos de muestra, solo mira feeds y URLs")
    doctor.add_argument("--json", dest="como_json", action="store_true",
                        help="saca el informe en JSON en vez de en texto")
    doctor.add_argument("--salida", default=None, help="escribe el informe en este fichero")

    # -- fuentes -----------------------------------------------------------
    subs.add_parser("fuentes", help="lista las fuentes declaradas")

    return parser


def _resolver_vias(args) -> list[str]:
    if args.vias.strip():
        elegidas = [v.strip() for v in args.vias.split(",") if v.strip()]
        raras = [v for v in elegidas if v not in VIAS]
        if raras:
            raise SystemExit(f"vias desconocidas: {', '.join(raras)}")
        return elegidas
    # En incremental el sitemap sobra: lo recien publicado esta en el RSS, y
    # recorrer sitemaps enteros cada dos horas es tirar el presupuesto.
    return ["rss", "sitemap", "crawl"] if args.modo == "full" else ["rss", "crawl"]


def _validar_fuentes(crudas: str) -> list[str]:
    if not crudas.strip():
        return []
    elegidas = [c.strip() for c in crudas.split(",") if c.strip()]
    raras = sources.desconocidas(elegidas)
    if raras:
        raise SystemExit(
            f"fuentes desconocidas: {', '.join(raras)}\n"
            f"disponibles: {', '.join(sorted(sources.POR_CLAVE))}"
        )
    return elegidas


def _mandar_scrape(args) -> int:
    opciones = Opciones(
        modo=args.modo,
        fuentes=_validar_fuentes(args.fuentes),
        vias=_resolver_vias(args),
        profundidad_crawl=args.profundidad_crawl,
        max_articulos=args.max_articulos,
        workers=args.workers,
        delay=args.delay,
        timeout=args.timeout,
        reintentos=args.reintentos,
        tam_parte=args.tam_parte,
        presupuesto=args.presupuesto,
        desde=args.desde,
        min_palabras=args.min_palabras,
        max_fallos=args.max_fallos,
        reintentos_vacio=args.reintentos_vacio,
        tope_por_fuente=args.tope_por_fuente,
        site_url=args.site_url,
        data_dir=args.data_dir,
        state_dir=args.state_dir,
        user_agent=args.user_agent,
        respetar_robots=not args.ignorar_robots,
        saltar_descubrimiento=args.saltar_descubrimiento,
    )

    resumen = ejecutar(opciones)
    presentado = json.dumps(resumen, ensure_ascii=False, indent=2)
    print("\n=== RESUMEN ===")
    print(presentado)

    if args.resumen:
        with open(args.resumen, "w", encoding="utf-8") as fichero:
            fichero.write(presentado + "\n")
    return 0


def _mandar_doctor(args) -> int:
    informes = probe.revisar_todas(
        claves=_validar_fuentes(args.fuentes),
        workers=args.workers,
        con_articulo=not args.sin_articulo,
        user_agent=args.user_agent,
        delay=args.delay,
        timeout=args.timeout,
        retries=args.reintentos,
        respetar_robots=not args.ignorar_robots,
    )

    salida = (
        json.dumps(informes, ensure_ascii=False, indent=2)
        if args.como_json
        else probe.formatear(informes)
    )
    print(salida)
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as fichero:
            fichero.write(salida + "\n")

    # Codigo de salida distinto de cero si alguna fuente esta caida, para que
    # el workflow lo marque en rojo sin tener que leer el informe.
    return 0 if all(i["ok"] for i in informes) else 1


def _mandar_fuentes() -> int:
    print(f"{len(sources.FUENTES)} fuentes declaradas\n")
    for vertical in ("news", "sports", "gaming", "tech"):
        delvertical = [f for f in sources.FUENTES if f.vertical == vertical]
        if not delvertical:
            continue
        print(f"  {vertical}")
        for fuente in delvertical:
            vias = []
            if fuente.feeds:
                vias.append(f"{len(fuente.feeds)} feeds")
            if fuente.sitemaps:
                vias.append("sitemaps")
            if fuente.semillas:
                vias.append("crawl")
            marca = " " if fuente.activa else "x"
            print(f"   {marca} {fuente.clave:<16} {fuente.nombre:<22} "
                  f"{fuente.idioma}  {', '.join(vias)}")
            if fuente.nota:
                print(f"       i {fuente.nota}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    # Sin subcomando se scrapea: es lo que hace el workflow programado.
    if not argumentos or argumentos[0].startswith("-"):
        argumentos = ["scrape", *argumentos]

    args = construir_parser().parse_args(argumentos)

    if args.comando == "fuentes":
        return _mandar_fuentes()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    if args.comando == "doctor":
        return _mandar_doctor(args)
    return _mandar_scrape(args)
