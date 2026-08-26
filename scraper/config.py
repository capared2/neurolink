"""Ajustes globales del scraper.

Todo lo que hay aqui es un valor por defecto: la CLI puede cambiarlo, y cada
fuente puede afinar los suyos en ``sources.py``.
"""
from __future__ import annotations

import os

# Dominio publico del sitio que consume el dataset (VORTEX). Solo se usa para
# construir los sitemaps, que se generan aqui y no en el frontend.
SITE_URL = os.environ.get("SITE_URL", "https://gigantum.net")

# En formato "compatible", que es el que usan los rastreadores serios: sigue
# diciendo quien es y enlaza el repositorio, pero muchas protecciones antibots
# rechazan de plano cualquier cosa que no empiece por "Mozilla/5.0" y devuelven
# 403 antes de mirar nada mas. Con la identificacion anterior, tres medios
# --ESPN, NYT y The Hill-- no dejaban descargar ni un articulo, aunque su
# robots.txt no lo prohibiera.
#
# No se finge ser un navegador: si un medio sigue rechazando esto, es que no
# quiere que se le lea, y lo que toca es apagar esa fuente.
DEFAULT_USER_AGENT = os.environ.get(
    "NEUROLINK_USER_AGENT",
    "Mozilla/5.0 (compatible; neurolink/1.0; +https://github.com/capared2/neurolink)",
)

# Espera minima entre peticiones AL MISMO HOST. Con veinte fuentes, un limite
# global serializaria la ejecucion entera: cada host lleva su propio reloj.
DEFAULT_DELAY = 1.0
DEFAULT_WORKERS = 8
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3

# Articulos por fichero JSON. El frontend parsea el fichero entero para pintar
# una sola noticia, y Cloudflare Workers corta a los 10 ms de CPU por peticion
# en el plan gratuito: esto es un limite de rendimiento, no de estilo.
#
# Medido sobre el dataset real: con 100 el fichero mayor llegaba a 965 KB --unos
# 3 ms solo de parseo, un tercio del presupuesto para no hacer nada util-- y con
# 50 se queda en la mitad. Si se sube, hay que volver a medir.
DEFAULT_SHARD_SIZE = 50

# Segundos de ejecucion. El workflow programado corre cada dos horas y el
# runner de GitHub corta a las 6 h; 50 minutos deja margen de sobra.
DEFAULT_TIME_BUDGET = 3000

# Descubrir es solo el medio: nunca puede comerse la ejecucion entera, o el run
# terminaria sin guardar una sola noticia.
DISCOVERY_SHARE = 0.35

# Cuerpo minimo para dar una noticia por buena. Por debajo de esto casi siempre
# es un directo, una galeria o un video sin transcripcion.
DEFAULT_MIN_WORDS = 60

# Reintentos antes de abandonar una URL.
DEFAULT_MAX_FAILURES = 3
DEFAULT_EMPTY_RETRIES = 2

# Techos del descubrimiento por fuente, para que ninguna monopolice el run.
MAX_SITEMAP_DEPTH = 3
MAX_CRAWL_PAGES = 60
MAX_URLS_POR_FUENTE = 600

# Cuantas noticias entran en la portada ligera y en la agrupacion por historia.
LATEST_LIMIT = 160
PORTADA_LIMIT = 60

# Parametros de sitemaps (limite del protocolo: 50.000 URLs por fichero).
URLS_POR_SITEMAP = 25_000
HORAS_GOOGLE_NEWS = 48
MAX_GOOGLE_NEWS = 1_000
