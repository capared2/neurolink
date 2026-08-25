"""Descubrimiento: RSS, sitemaps y crawl, cada uno con su techo."""
from scraper import discovery

from tests.fake_site import CANCHA, DIARIO, PIXEL


def test_rss_devuelve_los_articulos(fetcher):
    encontradas = discovery.desde_feeds(DIARIO, fetcher)
    assert "https://diario.test/2026/08/25/cumbre-sin-acuerdo" in encontradas
    assert len(encontradas) == 4


def test_tambien_se_usa_el_feed_que_anuncia_la_portada(fetcher):
    """El seguro para el dia en que un medio mueva su RSS de sitio."""
    feeds = discovery.feeds_de(DIARIO, fetcher)
    assert "https://diario.test/rss-mundo.xml" in feeds


def test_sitemap(fetcher):
    encontradas = discovery.desde_sitemaps(PIXEL, fetcher)
    assert len(encontradas) == 2
    assert all(u.startswith("https://pixel.test/news/") for u in encontradas)


def test_crawl_coge_articulos_y_no_secciones(fetcher):
    encontradas = discovery.desde_crawl(CANCHA, fetcher, profundidad=0)
    assert "https://cancha.test/futbol/1002-victoria-en-el-clasico" in encontradas
    assert "https://cancha.test/futbol" not in encontradas


def test_el_tope_por_fuente_se_respeta(fetcher):
    assert len(discovery.desde_feeds(DIARIO, fetcher, tope=1)) <= 1


def test_descubrir_junta_las_vias(fetcher):
    encontradas = discovery.descubrir(CANCHA, fetcher, ["rss", "crawl"])
    assert len(encontradas) == 2


def test_una_fuente_sin_feeds_no_falla(fetcher):
    assert discovery.desde_feeds(PIXEL, fetcher) == set()
