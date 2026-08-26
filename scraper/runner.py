"""Orquestacion: descubrir, descargar, clasificar y guardar.

El problema que no tiene un scraper de un solo medio es el reparto. Con
veintiuna fuentes, cualquier cosa que se haga "en orden" acaba dedicandole la
ejecucion entera a la primera: un sitemap grande, un medio lento o una cola
heredada de la corrida anterior bastan. Aqui se reparte dos veces:

* **Al descubrir**, cada fuente tiene su propia rebanada del plazo. La que se
  quede sin tiempo deja lo que le falte para la proxima.
* **Al descargar**, los lotes se arman alternando fuentes, asi que todas
  avanzan a la vez y ninguna se queda sin publicar nada.

Todo lo que no de tiempo a procesar queda en la cola de su fuente, y la
ejecucion siguiente sigue por donde se quedo.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import config, discovery, seo, sources
from . import urls as urlutil
from .fetcher import Fetcher
from .parser import parsear, parsear_wordpress
from .sources import Fuente
from .storage import Almacen, Estado

log = logging.getLogger(__name__)

VOLCAR_CADA = 150
TAM_LOTE = 60


@dataclass
class Opciones:
    modo: str = "incremental"
    fuentes: list[str] = field(default_factory=list)       # vacio = todas
    vias: list[str] = field(default_factory=lambda: ["rss", "crawl"])
    profundidad_crawl: int = 1
    max_articulos: int = 0                                  # 0 = sin limite
    workers: int = config.DEFAULT_WORKERS
    delay: float = config.DEFAULT_DELAY
    timeout: int = config.DEFAULT_TIMEOUT
    reintentos: int = config.DEFAULT_RETRIES
    tam_parte: int = config.DEFAULT_SHARD_SIZE
    presupuesto: int = config.DEFAULT_TIME_BUDGET
    desde: str | None = None                                # ISO, descarta lo anterior
    min_palabras: int = config.DEFAULT_MIN_WORDS
    max_fallos: int = config.DEFAULT_MAX_FAILURES
    reintentos_vacio: int = config.DEFAULT_EMPTY_RETRIES
    tope_por_fuente: int = config.MAX_URLS_POR_FUENTE
    site_url: str = config.SITE_URL
    reparto_descubrimiento: float = config.DISCOVERY_SHARE
    data_dir: str = "data"
    state_dir: str = "state"
    user_agent: str = config.DEFAULT_USER_AGENT
    respetar_robots: bool = True
    saltar_descubrimiento: bool = False


def _demasiado_vieja(url: str, desde: str | None) -> bool:
    if not desde:
        return False
    codificada = urlutil.fecha_en_ruta(url)
    return bool(codificada and codificada < desde)


def _descargar(fetcher: Fetcher, fuente: Fuente, url: str) -> tuple[str, Fuente, dict | None]:
    resp = fetcher.get(url)
    if resp is None or not fetcher.es_texto(resp):
        return url, fuente, None
    try:
        return url, fuente, parsear(resp.text, resp.url, fuente)
    except Exception:      # una pagina malformada no puede tumbar la ejecucion
        log.exception("no se pudo parsear %s", url)
        return url, fuente, None


def _leer_por_rest(
    fuente: Fuente,
    fetcher: Fetcher,
    estado: Estado,
    almacen: Almacen,
    opciones: "Opciones",
    resumen: dict,
    salud: dict,
    plazo: float | None,
) -> None:
    """Fuentes que se leen por su propio REST en vez de por sus paginas.

    No pasan por la cola: la API devuelve el articulo entero de una vez, asi
    que se descubre y se guarda en el mismo paso. Es lo que permite leer un
    medio cuyo cortafuegos rechaza las paginas pero deja pasar su REST.
    """
    estado_fuente = estado.de(fuente.clave)
    try:
        entradas = discovery.desde_wordpress(
            fuente, fetcher, plazo=plazo, tope=opciones.tope_por_fuente
        )
    except Exception:
        log.exception("fallo el REST de %s", fuente.clave)
        return

    salud[fuente.clave]["discovered"] = len(entradas)
    resumen["discovered"] += len(entradas)

    for entrada in entradas:
        enlace = entrada.get("link")
        if isinstance(enlace, str) and urlutil.normalizar(enlace) in estado_fuente.vistas:
            continue

        resumen["fetched"] += 1
        try:
            articulo = parsear_wordpress(entrada, fuente)
        except Exception:
            log.exception("no se pudo convertir una entrada de %s", fuente.clave)
            articulo = None

        if articulo is None:
            resumen["failed"] += 1
            salud[fuente.clave]["failed"] += 1
            continue
        if opciones.desde and (articulo.get("published_at") or "")[:10] < opciones.desde:
            resumen["skipped_old"] += 1
            estado_fuente.marcar_vista(articulo["url"])
            continue
        if articulo["word_count"] < opciones.min_palabras:
            resumen["skipped_empty"] += 1
            salud[fuente.clave]["empty"] += 1
            estado_fuente.marcar_vista(articulo["url"])
            continue

        almacen.anadir(articulo)
        estado_fuente.marcar_vista(articulo["url"])
        resumen["saved"] += 1
        salud[fuente.clave]["saved"] += 1


def _lote_alternado(estado: Estado, claves: list[str], tam: int) -> list[tuple[str, str]]:
    """Arma un lote turnandose entre las colas de cada fuente.

    Sin esto, la fuente con la cola mas larga monopolizaria los lotes y el
    resto no publicaria nada en toda la ejecucion.
    """
    lote: list[tuple[str, str]] = []
    vivas = [c for c in claves if estado.de(c).pendientes]
    while vivas and len(lote) < tam:
        for clave in list(vivas):
            if len(lote) >= tam:
                break
            tomadas = estado.de(clave).tomar(1)
            if tomadas:
                lote.append((clave, tomadas[0]))
            else:
                vivas.remove(clave)
    return lote


def ejecutar(opciones: Opciones) -> dict:
    empezado = time.monotonic()
    elegidas = sources.activas(opciones.fuentes)
    if not elegidas:
        raise SystemExit("no hay ninguna fuente activa que scrapear")

    fetcher = Fetcher(
        user_agent=opciones.user_agent,
        delay=opciones.delay,
        timeout=opciones.timeout,
        retries=opciones.reintentos,
        respetar_robots=opciones.respetar_robots,
    )
    # Cada fuente puede pedir mas calma que la general. Estaba declarado en el
    # registro y no lo leia nadie: un medio con antibots agresivo necesita ir
    # mucho mas despacio que el resto para que le dejen pasar algo.
    for fuente in elegidas:
        if fuente.delay and fuente.delay != opciones.delay:
            for host in fuente.hosts_normalizados:
                fetcher.reloj.fijar(host, fuente.delay)

    almacen = Almacen(opciones.data_dir, opciones.tam_parte)
    estado = Estado(opciones.state_dir, opciones.max_fallos, opciones.reintentos_vacio)
    por_clave = {f.clave: f for f in elegidas}

    salud: dict[str, dict] = {
        f.clave: {
            "name": f.nombre, "vertical": f.vertical, "language": f.idioma,
            "discovered": 0, "saved": 0, "failed": 0, "empty": 0, "note": f.nota,
        }
        for f in elegidas
    }
    resumen = {
        "mode": opciones.modo,
        "sources": len(elegidas),
        "discovered": 0, "queued": 0, "fetched": 0, "saved": 0,
        "skipped_old": 0, "skipped_empty": 0, "failed": 0,
        "categories": {},
    }

    try:
        fin = empezado + opciones.presupuesto if opciones.presupuesto else None
        fin_descubrimiento = (
            empezado + opciones.presupuesto * opciones.reparto_descubrimiento
            if opciones.presupuesto else None
        )

        # Las que se leen por su propio REST no pasan por la cola: se resuelven
        # enteras en el paso de descubrimiento.
        por_rest = [f for f in elegidas if f.wordpress]
        por_paginas = [f for f in elegidas if not f.wordpress]

        # -- 1. descubrimiento, con una rebanada del plazo por fuente --------
        if not opciones.saltar_descubrimiento:
            rebanada = (
                (fin_descubrimiento - time.monotonic()) / len(elegidas)
                if fin_descubrimiento else None
            )

            for fuente in por_rest:
                plazo = time.monotonic() + rebanada if rebanada else None
                _leer_por_rest(fuente, fetcher, estado, almacen, opciones,
                               resumen, salud, plazo)

            for fuente in por_paginas:
                plazo = time.monotonic() + rebanada if rebanada else None
                try:
                    encontradas = discovery.descubrir(
                        fuente, fetcher, opciones.vias,
                        profundidad_crawl=opciones.profundidad_crawl,
                        plazo=plazo, tope=opciones.tope_por_fuente,
                    )
                except Exception:
                    # Una fuente rota no puede llevarse por delante a las otras
                    # veinte: se anota y se sigue.
                    log.exception("fallo el descubrimiento de %s", fuente.clave)
                    continue

                frescas = []
                for url in sorted(encontradas, key=lambda u: (urlutil.fecha_en_ruta(u) or ""), reverse=True):
                    if _demasiado_vieja(url, opciones.desde):
                        resumen["skipped_old"] += 1
                        continue
                    frescas.append(url)

                salud[fuente.clave]["discovered"] = len(encontradas)
                resumen["discovered"] += len(encontradas)
                resumen["queued"] += estado.de(fuente.clave).encolar(frescas)

            log.info("descubrimiento: %s URLs, %s nuevas en cola",
                     resumen["discovered"], resumen["queued"])

        # -- 2. descarga, alternando fuentes --------------------------------
        for fuente in elegidas:
            estado.de(fuente.clave)          # abre la cola heredada de otras corridas

        limite = opciones.max_articulos if opciones.max_articulos > 0 else float("inf")
        desde_volcado = 0
        claves = [f.clave for f in por_paginas]

        while estado.pendientes and resumen["fetched"] < limite:
            if fin is not None and time.monotonic() > fin:
                log.info("agotado el presupuesto de %ss; quedan %s URLs en cola",
                         opciones.presupuesto, estado.pendientes)
                break

            restantes = limite - resumen["fetched"]
            lote = _lote_alternado(estado, claves, int(min(TAM_LOTE, restantes)))
            if not lote:
                break

            with ThreadPoolExecutor(max_workers=opciones.workers) as pool:
                tareas = [
                    pool.submit(_descargar, fetcher, por_clave[clave], url)
                    for clave, url in lote
                ]
                for tarea in as_completed(tareas):
                    url, fuente, articulo = tarea.result()
                    estado_fuente = estado.de(fuente.clave)
                    resumen["fetched"] += 1

                    if articulo is None:
                        resumen["failed"] += 1
                        salud[fuente.clave]["failed"] += 1
                        estado_fuente.marcar_fallida(url)
                        continue

                    if opciones.desde and (articulo.get("published_at") or "")[:10] < opciones.desde:
                        resumen["skipped_old"] += 1
                        estado_fuente.marcar_vista(url)
                        continue

                    # Sin cuerpo casi siempre es un directo, una galeria o un
                    # video. No se da por vista: esa misma URL suele traer la
                    # cronica cuando acaba, y se reintenta un numero acotado
                    # de veces para no gastar peticiones eternamente.
                    if articulo["word_count"] < opciones.min_palabras:
                        resumen["skipped_empty"] += 1
                        salud[fuente.clave]["empty"] += 1
                        estado_fuente.marcar_vacia(url)
                        continue

                    almacen.anadir(articulo)
                    estado_fuente.marcar_vista(url)
                    estado_fuente.marcar_vista(articulo["url"])
                    resumen["saved"] += 1
                    salud[fuente.clave]["saved"] += 1
                    desde_volcado += 1

            if desde_volcado >= VOLCAR_CADA:
                for categoria, cuantas in almacen.volcar().items():
                    resumen["categories"][categoria] = resumen["categories"].get(categoria, 0) + cuantas
                estado.guardar()
                desde_volcado = 0
                log.info("progreso: %s guardadas, %s en cola, %s peticiones",
                         resumen["saved"], estado.pendientes, fetcher.stats["peticiones"])

    finally:
        # Pase lo que pase, lo descargado se guarda y los indices quedan al dia.
        for categoria, cuantas in almacen.volcar().items():
            resumen["categories"][categoria] = resumen["categories"].get(categoria, 0) + cuantas

        indice = almacen.reconstruir_indices(salud)
        resumen["seo"] = seo.construir(
            opciones.data_dir, opciones.site_url, almacen.entradas_sitemap, indice["categories"]
        )
        resumen["total_articles"] = indice["total_articles"]
        resumen["total_categories"] = indice["total_categories"]
        resumen["verticals"] = {v["vertical"]: v["articles"] for v in indice["verticals"]}
        resumen["duration_seconds"] = round(time.monotonic() - empezado, 1)
        resumen["http"] = dict(fetcher.stats)
        resumen["source_health"] = salud
        estado.guardar({"last_run": resumen})
        fetcher.close()

    return resumen
