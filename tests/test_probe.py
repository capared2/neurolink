"""El doctor: lo que se ejecuta cuando una fuente deja de publicar."""
from scraper import probe

from tests.fake_site import CANCHA, DIARIO, PIXEL


def test_una_fuente_sana_sale_limpia(fetcher, sitio):
    informe = probe.revisar(DIARIO, fetcher)
    assert informe["ok"]
    assert informe["home_ok"]
    assert informe["urls_rss"] == 4
    assert len(informe["feeds_ok"]) == 1
    assert not informe["problemas"]
    assert all(m["estado"] == "ok" for m in informe["muestra"])


def test_avisa_del_feed_caido(fetcher, sitio):
    del sitio.paginas["https://diario.test/rss.xml"]
    informe = probe.revisar(DIARIO, fetcher)
    assert "https://diario.test/rss.xml" in informe["feeds_ko"]


def test_avisa_cuando_se_descubre_pero_no_hay_cuerpo(fetcher, sitio):
    """El sintoma tipico de un rediseño: el selector del cuerpo dejo de valer."""
    for ruta in ("/futbol/1001-la-cumbre-del-futbol-sin-acuerdo",
                 "/futbol/1002-victoria-en-el-clasico"):
        sitio.anadir(f"https://cancha.test{ruta}",
                     "<html><head><title>Un titular que sigue estando ahi</title>"
                     "<meta property='og:title' content='Un titular que sigue estando ahi'>"
                     "</head><body><div id='app'></div></body></html>")

    informe = probe.revisar(CANCHA, fetcher)
    assert not informe["ok"]
    assert any("cuerpo" in p for p in informe["problemas"])


def test_avisa_cuando_no_se_descubre_nada(fetcher, sitio):
    sitio.paginas.clear()
    informe = probe.revisar(PIXEL, fetcher)
    assert not informe["ok"]
    assert any("no se descubre" in p for p in informe["problemas"])


def test_el_informe_se_lee(fetcher):
    texto = probe.formatear([probe.revisar(DIARIO, fetcher), probe.revisar(PIXEL, fetcher)])
    assert "fuentes en pie" in texto
    assert "diario" in texto


def test_un_feed_muerto_no_da_por_perdida_a_la_fuente(fetcher, sitio):
    """Lo que decide es si la fuente rinde, no si todo lo declarado responde.

    diario.test anuncia otro feed desde su portada, así que aunque el declarado
    se caiga sigue descubriendo y parseando. Confundir "hay algo que arreglar"
    con "está muerta" convertiría la revisión semanal en ruido.
    """
    del sitio.paginas["https://diario.test/rss.xml"]

    informe = probe.revisar(DIARIO, fetcher)
    assert informe["ok"], informe["problemas"]
    assert informe["urls_rss"] > 0
    assert "https://diario.test/rss.xml" in informe["feeds_ko"]
    assert any("ningun feed declarado" in a for a in informe["avisos"])


def test_distingue_no_dejar_descargar_de_no_encontrar_el_cuerpo(fetcher, sitio):
    """Un medio que bloquea y otro que se rediseñó se arreglan de formas distintas."""
    for ruta in ("/futbol/1001-la-cumbre-del-futbol-sin-acuerdo",
                 "/futbol/1002-victoria-en-el-clasico"):
        del sitio.paginas[f"https://cancha.test{ruta}"]

    informe = probe.revisar(CANCHA, fetcher)
    assert not informe["ok"]
    assert any("no deja descargarlas" in p for p in informe["problemas"])
