"""Taxonomia comun a todas las fuentes.

El valor de un agregador multinicho esta en que la portada de Marca y la de
ESPN acaben en el mismo sitio. Cada medio nombra sus secciones a su manera
--``/futbol/``, ``/soccer/``, ``/futebol/``, ``/sport/football/``--, asi que
antes de guardar nada se traduce todo a una sola clave ``vertical/tema``.

La clasificacion mira, de mas fiable a menos:

1. los segmentos de la URL, que el medio elige a conciencia;
2. la seccion que declara el propio articulo (JSON-LD o RSS);
3. sus etiquetas;
4. y, como ultimo recurso, las palabras del titular y la entradilla.

Si nada gana, se usa el tema por defecto de la fuente.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

VERTICALES = {
    "news": "News",
    "sports": "Sports",
    "gaming": "Gaming",
    "tech": "Tech",
}


@dataclass(frozen=True)
class Tema:
    clave: str                       # "deportes/futbol"
    nombre: str                      # "Fútbol"
    alias: frozenset[str] = field(default_factory=frozenset)   # segmentos de URL
    claves: frozenset[str] = field(default_factory=frozenset)  # palabras del texto
    # Un tema "paraguas" es el cajon de una vertical entera: sus alias son
    # palabras como "sport" o "deportes", que aparecen en la URL de cualquier
    # noticia de la seccion. Puntuan menos para que un tema concreto siempre
    # les gane; si no, todo el futbol acabaria en "mas deporte".
    generico: bool = False

    @property
    def vertical(self) -> str:
        return self.clave.split("/", 1)[0]


def _t(clave: str, nombre: str, alias: str, claves: str = "", generico: bool = False) -> Tema:
    """Atajo para declarar un tema con listas separadas por espacios."""
    return Tema(
        clave=clave,
        nombre=nombre,
        alias=frozenset(alias.split()),
        claves=frozenset(claves.split()),
        generico=generico,
    )


# Los alias son segmentos de URL tal y como los escriben los medios; las claves
# son palabras sueltas que se buscan en titular, entradilla y etiquetas. Van en
# espanol, ingles y portugues porque el dataset mezcla los tres idiomas.
TEMAS: tuple[Tema, ...] = (
    # -- Deportes ----------------------------------------------------------
    _t("sports/soccer", "Soccer",
       "futbol football soccer futebol laliga primera-division segunda-division "
       "premier-league champions-league europa-league bundesliga serie-a ligue-1 "
       "liga-francesa copa-del-rey seleccion futbol-internacional futbol-femenino "
       "real-madrid barcelona atletico mls libertadores brasileirao campeonato-brasileiro "
       "world-cup mundial eurocopa concacaf conmebol",
       "futbol football soccer futebol liga laliga champions uefa fifa gol goles goal golo "
       "delantero portero entrenador midfielder striker matchday clasico derbi"),
    _t("sports/basketball", "Basketball",
       "baloncesto basketball basquete nba acb euroliga euroleague basquetbol ncaa wnba",
       "baloncesto basketball basquete nba canasta triple rebote playoffs dunk"),
    _t("sports/nfl", "NFL",
       "nfl american-football futebol-americano superbowl super-bowl",
       "nfl quarterback touchdown superbowl draft"),
    _t("sports/baseball", "Baseball",
       "beisbol baseball mlb besibol",
       "beisbol baseball mlb pitcher home-run innings"),
    _t("sports/tennis", "Tennis",
       "tenis tennis atp wta roland-garros wimbledon us-open australian-open",
       "tenis tennis atp wta grand-slam raqueta"),
    _t("sports/motorsport", "Motorsport",
       "motor formula1 f1 formula-1 motogp motor-racing racing nascar rally indycar",
       "formula1 motogp circuito parrilla pole piloto grand-prix escuderia"),
    _t("sports/golf", "Golf", "golf pga masters-golf ryder-cup", "golf birdie putt green pga"),
    _t("sports/cycling", "Cycling",
       "ciclismo cycling tour-de-france vuelta giro",
       "ciclismo cycling pelotón maillot etapa velodromo"),
    _t("sports/combat", "Combat sports",
       "boxeo boxing mma ufc lucha wrestling artes-marciales",
       "boxeo boxing ufc mma knockout octagono cinturon"),
    _t("sports/cricket", "Cricket",
       "cricket ipl test-cricket",
       "cricket wicket batsman bowler ipl innings"),
    _t("sports/rugby", "Rugby", "rugby rugby-union six-nations", "rugby scrum try lineout"),
    _t("sports/olympics", "Olympics",
       "juegos-olimpicos olympics olimpiadas atletismo athletics natacion swimming "
       "balonmano handball voleibol volleyball hockey esqui",
       "olimpicos olympic medalla podio atletismo natacion"),
    _t("sports/more", "More sport", "deportes sport sports esporte mas-deporte otros-deportes", "",
       generico=True),

    # -- Gamer -------------------------------------------------------------
    _t("gaming/games", "Video games",
       "juegos games gaming videojuegos videogames game reviews playstation xbox "
       "nintendo switch pc-gaming steam",
       "videojuego videojuegos gameplay consola playstation xbox nintendo "
       "steam dlc jugabilidad rpg shooter"),
    _t("gaming/esports", "Esports",
       "esports e-sports faceit competitive csgo cs2 valorant lol league-of-legends "
       "dota overwatch rainbow-six",
       "esports csgo cs2 valorant dota torneo clan roster major hltv"),
    _t("gaming/streaming", "Streaming",
       "streaming streamers twitch creators live",
       "twitch streamer streaming directo subs raid"),

    # -- Tecnologia --------------------------------------------------------
    _t("tech/ai", "Artificial intelligence",
       "ai artificial-intelligence inteligencia-artificial ia machine-learning",
       "inteligencia-artificial openai anthropic chatgpt llm modelo algoritmo machine-learning"),
    _t("tech/gadgets", "Gadgets",
       "gadgets reviews-tech hardware phones smartphones moviles wearables audio laptops tv",
       "iphone android smartphone portatil auriculares pantalla bateria chip procesador"),
    _t("tech/companies", "Companies",
       "startups venture fundraising apps enterprise fintech",
       "startup ronda inversion adquisicion valoracion ipo unicornio"),
    _t("tech/science", "Science & space",
       "ciencia science space espacio nasa spacex clima climate energia transport",
       "espacio nasa spacex cohete satelite investigacion estudio cientificos"),
    _t("tech/software", "Software & security",
       "software security seguridad cyber privacy internet web policy",
       "software actualizacion vulnerabilidad hackers ciberataque privacidad codigo"),
    _t("tech/crypto", "Crypto", "crypto criptomonedas bitcoin blockchain web3",
       "bitcoin ethereum cripto blockchain token wallet"),

    # -- Noticias ----------------------------------------------------------
    _t("news/world", "World",
       "world mundo internacional international mundo-news africa asia europe "
       "middle-east latin-america americas uk us-news india brasil",
       "guerra conflicto frontera gobierno onu tratado refugiados embajada"),
    _t("news/politics", "Politics",
       "politics politica politica-nacional elections elecciones congress senate "
       "white-house parlamento gobierno campaign",
       "elecciones presidente parlamento senado congreso partido ministro campana voto"),
    _t("news/business", "Business",
       "business economia economy negocios finance markets mercados empresas "
       "money economia-y-negocios",
       "economia inflacion mercados bolsa banco empleo pib impuestos aranceles"),
    _t("news/society", "Society",
       "society sociedad sucesos crime justicia justice courts education educacion "
       "inmigracion cotidiano",
       "juicio tribunal policia detenido investigacion protesta manifestacion"),
    _t("news/health", "Health",
       "health salud bienestar medicine wellness",
       "salud hospital virus vacuna medico enfermedad pacientes sanidad"),
    _t("news/culture", "Culture",
       "entertainment cultura culture arts celebrity celebridades cine movies music "
       "musica tv-shows television pop-culture tiramillas famosos",
       "pelicula serie estreno album concierto festival actor actriz oscar"),
)

TEMAS_POR_CLAVE = {tema.clave: tema for tema in TEMAS}

# Tema al que va a parar lo que no encaja en ningun sitio, por vertical.
POR_DEFECTO = {
    "news": "news/world",
    "sports": "sports/more",
    "gaming": "gaming/games",
    "tech": "tech/gadgets",
}

# Pesos de cada senal. La URL manda porque es la unica que el medio elige de
# forma deliberada para cada noticia; el texto es el ultimo recurso.
PESO_URL = 8
PESO_SECCION = 5
PESO_ETIQUETA = 2
PESO_TEXTO = 1
# Cuanto se rebaja un tema paraguas frente a uno concreto.
FACTOR_GENERICO = 0.4

_NO_PALABRA = re.compile(r"[^a-z0-9]+")


def sin_tildes(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto)
    return normal.encode("ascii", "ignore").decode("ascii").lower()


def slug(texto: str) -> str:
    return _NO_PALABRA.sub("-", sin_tildes(texto)).strip("-")


def _palabras(texto: str) -> set[str]:
    return {p for p in _NO_PALABRA.split(sin_tildes(texto)) if len(p) > 2}


def clasificar(
    segmentos: list[str],
    seccion: str = "",
    etiquetas: list[str] | None = None,
    texto: str = "",
    vertical_por_defecto: str = "noticias",
    tema_por_defecto: str | None = None,
    host: list[str] | None = None,
) -> str:
    """Devuelve la clave ``vertical/tema`` que le corresponde a una noticia."""
    puntos: dict[str, int] = {}

    def sumar(clave: str, cuanto: int) -> None:
        if cuanto:
            puntos[clave] = puntos.get(clave, 0) + cuanto

    segmentos_slug = [slug(s) for s in segmentos if s]
    host_slug = [slug(h) for h in (host or []) if h]

    # El subdominio dice de que seccion es la noticia aunque la ruta no diga
    # nada: `sports.yahoo.com/articles/bengals-preseason-mvp.html` es deporte
    # y acababa en actualidad general. Manda sobre la vertical de la fuente
    # --Yahoo publica de todo-- pero solo cuando ningun tema gana por si mismo.
    for etiqueta in host_slug:
        for tema in TEMAS:
            if etiqueta in tema.alias:
                vertical_por_defecto = tema.vertical
                tema_por_defecto = POR_DEFECTO.get(tema.vertical, tema_por_defecto)
                break
        else:
            continue
        break
    seccion_slug = slug(seccion) if seccion else ""
    etiquetas_slug = [slug(e) for e in (etiquetas or []) if e]
    palabras_seccion = _palabras(seccion) if seccion else set()
    palabras_texto = _palabras(texto) if texto else set()
    palabras_etiquetas = {p for e in (etiquetas or []) for p in _palabras(e)}

    for tema in TEMAS:
        # 1. Segmentos de la URL. Los ultimos segmentos son mas especificos
        #    (/sport/football/ -> football manda sobre sport).
        for posicion, segmento in enumerate(segmentos_slug):
            if segmento in tema.alias:
                sumar(tema.clave, PESO_URL + posicion)

        # 1b. Subdominio. Pesa la mitad que un segmento de la ruta y solo
        #     para temas concretos: `sports.yahoo.com` dice que la noticia es
        #     de deportes, pero no que sea de "mas deporte" en vez de futbol.
        #     Para eso esta la vertical de respaldo, mas abajo.
        if not tema.generico:
            for etiqueta in host_slug:
                if etiqueta in tema.alias:
                    sumar(tema.clave, PESO_URL // 2)

        # 2. Seccion declarada por el articulo. Se prueba entera y por
        #    palabras: los medios la escriben con su propia marca delante
        #    --"Yahoo Sports", "BBC Sport"-- y comparada entera no encajaba
        #    con ningun alias.
        if seccion_slug and (seccion_slug in tema.alias or seccion_slug in tema.claves):
            sumar(tema.clave, PESO_SECCION)
        elif palabras_seccion & tema.alias:
            sumar(tema.clave, PESO_SECCION - 1)

        # 3. Etiquetas.
        for etiqueta in etiquetas_slug:
            if etiqueta in tema.alias:
                sumar(tema.clave, PESO_ETIQUETA)
        sumar(tema.clave, PESO_ETIQUETA * len(palabras_etiquetas & tema.claves))

        # 4. Titular y entradilla.
        sumar(tema.clave, PESO_TEXTO * len(palabras_texto & tema.claves))

        if tema.generico and tema.clave in puntos:
            puntos[tema.clave] = int(puntos[tema.clave] * FACTOR_GENERICO)

    if puntos:
        # Empate: gana el tema del vertical de la fuente, y si no el alfabetico,
        # para que la clasificacion sea reproducible entre ejecuciones.
        mejor = max(
            puntos.items(),
            key=lambda par: (
                par[1],
                par[0].startswith(vertical_por_defecto + "/"),
                par[0],
            ),
        )
        if mejor[1] >= PESO_TEXTO * 2:
            return mejor[0]

    if tema_por_defecto and tema_por_defecto in TEMAS_POR_CLAVE:
        return tema_por_defecto
    return POR_DEFECTO.get(vertical_por_defecto, "news/world")


def nombre_tema(clave: str) -> str:
    tema = TEMAS_POR_CLAVE.get(clave)
    if tema:
        return tema.nombre
    return clave.rsplit("/", 1)[-1].replace("-", " ").capitalize()


def nombre_vertical(clave: str) -> str:
    return VERTICALES.get(clave, clave.capitalize())
