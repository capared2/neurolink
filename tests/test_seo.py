"""Sitemaps: las rutas tienen que ser exactamente las del sitio."""
from xml.etree import ElementTree

from scraper.seo import construir

ESPACIO = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _articulo(id_, categoria="deportes/futbol", cuando="2026-08-25T10:00:00Z"):
    return {"id": id_, "category": categoria, "title": f"Titular {id_}",
            "published_at": cuando, "modified_at": None, "language": "es"}


def _locs(ruta):
    raiz = ElementTree.parse(ruta).getroot()
    return [loc.text for loc in raiz.iter(f"{ESPACIO}loc")]


def test_escribe_todos_los_sitemaps(tmp_path):
    manifiesto = construir(
        tmp_path, "https://gigantum.net",
        [_articulo("a1"), _articulo("b1", "noticias/mundo")],
        [{"category": "deportes/futbol"}, {"category": "noticias/mundo"}],
    )
    seo = tmp_path / "seo"
    assert (seo / "sitemap.xml").exists()
    assert (seo / "sitemap-secciones.xml").exists()
    assert (seo / "sitemap-news.xml").exists()
    assert (seo / "sitemap-noticias-0001.xml").exists()
    assert manifiesto["site_url"] == "https://gigantum.net"


def test_las_rutas_son_las_del_sitio(tmp_path):
    construir(tmp_path, "https://gigantum.net", [_articulo("a1")],
              [{"category": "deportes/futbol"}])

    noticias = _locs(tmp_path / "seo" / "sitemap-noticias-0001.xml")
    assert noticias == ["https://gigantum.net/noticia/deportes/futbol/a1"]

    secciones = _locs(tmp_path / "seo" / "sitemap-secciones.xml")
    assert "https://gigantum.net/" in secciones
    assert "https://gigantum.net/temas" in secciones
    assert "https://gigantum.net/deportes" in secciones          # la vertical entera
    assert "https://gigantum.net/deportes/futbol" in secciones   # y el tema


def test_google_news_solo_lleva_lo_reciente(tmp_path):
    from datetime import datetime, timedelta, timezone
    reciente = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    viejo = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")

    construir(tmp_path, "https://gigantum.net",
              [_articulo("nueva", cuando=reciente), _articulo("vieja", cuando=viejo)],
              [{"category": "deportes/futbol"}])

    locs = _locs(tmp_path / "seo" / "sitemap-news.xml")
    assert any("nueva" in u for u in locs)
    assert not any("vieja" in u for u in locs)


def test_el_indice_agrupa_a_los_demas(tmp_path):
    construir(tmp_path, "https://gigantum.net", [_articulo("a1")], [{"category": "deportes/futbol"}])
    contenido = (tmp_path / "seo" / "sitemap.xml").read_text()
    assert "sitemap-secciones.xml" in contenido
    assert "sitemap-news.xml" in contenido
    assert "sitemap-noticias-0001.xml" in contenido


def test_el_titular_se_escapa(tmp_path):
    """Un & sin escapar deja el XML invalido y Google descarta el sitemap entero."""
    articulo = _articulo("a1")
    articulo["title"] = "Marks & Spencer <cierra> tiendas"
    from datetime import datetime, timezone
    articulo["published_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    construir(tmp_path, "https://gigantum.net", [articulo], [{"category": "noticias/economia"}])
    ElementTree.parse(tmp_path / "seo" / "sitemap-news.xml")   # revienta si no es valido
