"""El registro de fuentes y su deteccion de articulos."""
import pytest

from scraper import sources


def test_todas_las_fuentes_estan_bien_formadas():
    for fuente in sources.FUENTES:
        assert fuente.clave and fuente.clave.islower()
        assert fuente.hosts, f"{fuente.clave} no declara hosts"
        assert fuente.vertical in ("news", "sports", "gaming", "tech")
        # Una fuente sin ninguna via de descubrimiento no aportaria nada.
        assert fuente.feeds or fuente.sitemaps or fuente.semillas, fuente.clave


def test_las_claves_no_se_repiten():
    claves = [f.clave for f in sources.FUENTES]
    assert len(claves) == len(set(claves))


def test_un_host_pertenece_a_una_sola_fuente():
    vistos: dict[str, str] = {}
    for fuente in sources.FUENTES:
        for host in fuente.hosts_normalizados:
            assert host not in vistos, f"{host} lo reclaman {vistos.get(host)} y {fuente.clave}"
            vistos[host] = fuente.clave


@pytest.mark.parametrize("clave, url", [
    ("bbc", "https://www.bbc.com/news/articles/c1abc2def3"),
    ("nytimes", "https://www.nytimes.com/2026/08/25/world/europe/algo.html"),
    ("espn", "https://www.espn.com/soccer/story/_/id/12345678/titulo"),
    ("marca", "https://www.marca.com/futbol/real-madrid/2026/08/19/68a1b2c3.html"),
    ("nbcnews", "https://www.nbcnews.com/politics/algo-rcna123456"),
    ("techcrunch", "https://techcrunch.com/2026/08/25/una-startup-cualquiera/"),
    ("toi", "https://timesofindia.indiatimes.com/world/x/articleshow/12345678.cms"),
    ("theverge", "https://www.theverge.com/2026/8/25/123456/un-titular"),
    ("aljazeera", "https://www.aljazeera.com/news/2026/8/25/algo-pasa"),
])
def test_reconoce_sus_articulos(clave, url):
    assert sources.POR_CLAVE[clave].es_articulo(url)


@pytest.mark.parametrize("clave, url", [
    ("bbc", "https://www.bbc.com/news/world"),                    # portada de seccion
    ("espn", "https://www.espn.com/soccer/"),
    ("techcrunch", "https://techcrunch.com/tag/ai/"),             # indice de etiqueta
    ("theverge", "https://www.theverge.com/videos/algo"),
    ("nytimes", "https://www.nytimes.com/2026/08/25/crosswords/algo.html"),
    ("bbc", "https://otrodominio.com/news/articles/abc"),         # otro dominio
    ("ign", "https://www.ign.com/rss/articles/feed"),             # el propio feed
])
def test_descarta_lo_que_no_es_noticia(clave, url):
    assert not sources.POR_CLAVE[clave].es_articulo(url)


def test_heuristica_para_fuentes_sin_patron():
    faceit = sources.POR_CLAVE["faceit"]
    assert not faceit.articulo, "esta prueba cubre justo el caso sin patron propio"
    assert faceit.es_articulo("https://blog.faceit.com/un-titular-bastante-largo-aqui")
    assert not faceit.es_articulo("https://blog.faceit.com/news")


def test_fuente_de_resuelve_por_host():
    assert sources.fuente_de("https://www.espn.com/x").clave == "espn"
    assert sources.fuente_de("https://ge.globo.com/x").clave == "globo"
    assert sources.fuente_de("https://desconocido.test/x") is None
