import io
import os
import re
import math
import datetime
import json
import requests
import streamlit as st
import pandas as pd
import pydeck as pdk
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Konfigūracija
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Maršruto KM Skaičiuoklė",
    page_icon="🗺️",
    layout="wide",
)

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY") or st.secrets.get("AZURE_MAPS_KEY", "")
BASE_URL = "https://atlas.microsoft.com"

EUROPE_COUNTRY_SET = (
    "AT,BE,BG,CH,CY,CZ,DE,DK,EE,ES,FI,FR,GB,GR,HR,HU,IE,IT,LT,LU,LV,"
    "MT,NL,NO,PL,PT,RO,RS,SE,SI,SK,TR,UA,BY,MD,AL,BA,MK,ME,XK,IS,AD,LI,MC,SM,VA"
)

# ─────────────────────────────────────────────
# Azure Maps funkcijos
# ─────────────────────────────────────────────

def _is_in_europe(lat, lon):
    return 34.0 <= lat <= 72.0 and -12.0 <= lon <= 45.0


COUNTRY_PREFIX = {
    "A": "Austria", "AT": "Austria", "B": "Belgium", "BE": "Belgium", "BG": "Bulgaria",
    "CH": "Switzerland", "CZ": "Czechia", "D": "Germany", "DE": "Germany", "DK": "Denmark",
    "E": "Spain", "ES": "Spain", "EST": "Estonia", "EE": "Estonia", "F": "France", "FR": "France",
    "FIN": "Finland", "FI": "Finland", "GB": "United Kingdom", "UK": "United Kingdom",
    "GR": "Greece", "H": "Hungary", "HU": "Hungary", "HR": "Croatia", "I": "Italy", "IT": "Italy",
    "IRL": "Ireland", "IE": "Ireland", "L": "Luxembourg", "LU": "Luxembourg", "LT": "Lithuania",
    "LV": "Latvia", "N": "Norway", "NO": "Norway", "NL": "Netherlands", "P": "Portugal",
    "PT": "Portugal", "PL": "Poland", "RO": "Romania", "S": "Sweden", "SE": "Sweden",
    "SK": "Slovakia", "SLO": "Slovenia", "SI": "Slovenia",
}

# Šalies kodas + brūkšnys + pašto kodas, pvz. "F - 50880", "F-50880", "D-12345", "NL-1234"
_PREFIX_POSTAL_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3})\s*-\s*(\d{3,6})")


def normalize_address(address: str) -> str:
    """'F - 50880 La Meauffe-' -> '50880 La Meauffe, France'. Kitus adresus grąžina apvalytus."""
    a = address.strip().strip("-–;,").strip()
    m = re.match(r"^([A-Z]{1,3})\s*-\s*(\d{3,6})\s*(.*)$", a)
    if m and m.group(1) in COUNTRY_PREFIX:
        city = m.group(3).strip().strip("-–,").strip()
        return f"{m.group(2)} {city}, {COUNTRY_PREFIX[m.group(1)]}".replace("  ", " ")
    return a


def _simplify_address(address):
    match = re.search(r'([A-Z]{1,2}-\d{4,5}\s+\S+)', address)
    if match:
        return match.group(1)
    parts = re.split(r',\s*|\s+-\s+', address)
    if len(parts) >= 2:
        return parts[-1].strip()
    return None


def geocode(address: str):
    """Grąžina (lat, lon) arba None."""
    if not AZURE_MAPS_KEY or not address.strip():
        return None

    tried = set()
    for query in [normalize_address(address), address, _simplify_address(address)]:
        if not query or query in tried:
            continue
        tried.add(query)
        params = {
            "api-version": "1.0",
            "subscription-key": AZURE_MAPS_KEY,
            "query": query,
            "limit": 5,
            "countrySet": EUROPE_COUNTRY_SET,
        }
        try:
            r = requests.get(f"{BASE_URL}/search/address/json", params=params, timeout=8)
            if r.status_code in (401, 403):
                st.error(f"Azure Maps atmetė raktą (HTTP {r.status_code}). Patikrinkite AZURE_MAPS_KEY Secrets'uose.")
                st.stop()
            if r.status_code == 200:
                for result in r.json().get("results", []):
                    pos = result["position"]
                    if _is_in_europe(pos["lat"], pos["lon"]):
                        return pos["lat"], pos["lon"]
        except Exception:
            pass
    return None


def route_distance(waypoints):
    """Kešuotas maršrutas; nesėkmės nekešuojamos."""
    try:
        return _route_cached(tuple(tuple(w) for w in waypoints))
    except _RouteFailed:
        return None


class _RouteFailed(Exception):
    pass


@st.cache_data(ttl=30 * 24 * 3600, show_spinner=False)
def _route_cached(waypoints):
    """
    Grąžina žodyną su distance_km, travel_time_min, path_coords
    arba None jei nepavyko.
    waypoints: [(lat, lon), ...]
    """
    if len(waypoints) < 2 or not AZURE_MAPS_KEY:
        raise _RouteFailed()

    # Pašaliname gretutines koordinates-dublikatus
    clean = [waypoints[0]]
    for wp in waypoints[1:]:
        if wp != clean[-1]:
            clean.append(wp)
    if len(clean) < 2:
        raise _RouteFailed()

    query = ":".join(f"{lat},{lon}" for lat, lon in clean)
    params = {
        "api-version": "1.0",
        "subscription-key": AZURE_MAPS_KEY,
        "query": query,
        "travelMode": "truck",
        "vehicleEngineType": "combustion",
        "routeType": "fastest",
        "traffic": "false",  # be gyvo eismo – tas pats reisas visada duoda tą patį km
    }
    try:
        r = requests.get(f"{BASE_URL}/route/directions/json", params=params, timeout=15)
        if r.status_code != 200:
            raise _RouteFailed()
        data = r.json()
        routes = data.get("routes", [])
        if not routes:
            raise _RouteFailed()
        route = routes[0]
        summary = route["summary"]
        path_coords = []
        for leg in route["legs"]:
            for pt in leg["points"]:
                path_coords.append([pt["longitude"], pt["latitude"]])
        return {
            "distance_km": round(summary["lengthInMeters"] / 1000, 1),
            "travel_time_min": round(summary["travelTimeInSeconds"] / 60, 0),
            "path_coords": path_coords,
        }
    except Exception:
        raise _RouteFailed()


def segment_distance(a, b):
    """Atstumo tarp dviejų taškų skaičiavimas per Azure (2-taškų maršrutas)."""
    result = route_distance([a, b])
    return result["distance_km"] if result else None


def arrow_layer(path_coords, step=10):
    arrows = []
    n = len(path_coords)
    for i in range(step, n - 1, step):
        p1, p2 = path_coords[i - 1], path_coords[i]
        dlon = math.radians(p2[0] - p1[0])
        lat1, lat2 = math.radians(p1[1]), math.radians(p2[1])
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        bearing = math.degrees(math.atan2(x, y))
        arrows.append({"lon": p2[0], "lat": p2[1], "angle": -bearing, "text": "▲"})
    if not arrows:
        return None
    return pdk.Layer(
        "TextLayer",
        pd.DataFrame(arrows),
        get_position="[lon, lat]",
        get_text="text",
        get_angle="angle",
        get_color=[20, 60, 130, 220],
        get_size=13,
        pickable=False,
        billboard=True,
    )


# ─────────────────────────────────────────────
# Šveicarijos tranzito vengimas
# ─────────────────────────────────────────────

# Supaprastintas Šveicarijos kontūras (lon, lat). Tikslumas ~5–10 km, todėl
# tranzitu laikome tik tada, kai maršrute per Šveicariją nuvažiuojama > CH_TRANSIT_MIN_KM.
CH_POLYGON = [
    (5.96, 46.20), (6.12, 46.15), (6.25, 46.30), (6.55, 46.45), (6.80, 46.39), (6.85, 46.12),
    (7.04, 45.92), (7.18, 45.87), (7.66, 45.98), (7.88, 45.92), (8.15, 46.15),
    (8.44, 46.46), (8.70, 46.10), (8.95, 45.83), (9.08, 45.90), (9.30, 46.50),
    (9.55, 46.30), (10.05, 46.23), (10.15, 46.42), (10.47, 46.55), (10.49, 46.94),
    (9.95, 46.90), (9.53, 47.05), (9.60, 47.47), (9.18, 47.66), (8.65, 47.80),
    (8.22, 47.60), (7.59, 47.59), (7.35, 47.43), (6.95, 47.30), (6.45, 46.95),
    (6.10, 46.60),
]
CH_TRANSIT_MIN_KM = 20

# Kandidatai apvažiavimui; pasirenkamas trumpiausias, kuris nekerta Šveicarijos.
# Taškas nustatomas per geocoding (adresas), o koordinatės – tik atsarginis variantas.
AVOID_CH_VIA = [
    ("Brenerį", "6156 Gries am Brenner, Austria", (47.0370, 11.4820)),
    ("Monblano tunelį", "74400 Chamonix-Mont-Blanc, France", (45.9237, 6.8694)),
    ("Frejus tunelį", "73500 Modane, France", (45.2000, 6.6700)),
]


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def _via_point(address: str, fallback: tuple) -> tuple:
    found = geocode(address)
    return tuple(found) if found else fallback


def _in_ch(lat, lon):
    inside = False
    pts = CH_POLYGON
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def ch_km(path_coords):
    """Kiek km maršruto eina per Šveicariją (path_coords: [[lon, lat], ...])."""
    total = 0.0
    for p, q in zip(path_coords, path_coords[1:]):
        if _in_ch((p[1] + q[1]) / 2, (p[0] + q[0]) / 2):
            total += _haversine_km(p[0], p[1], q[0], q[1])
    return total


def _route_via(a, via, b):
    """Maršrutas a → via → b kaip dvi atskiros atkarpos (patikimiau nei 3 taškai vienoje užklausoje)."""
    r1 = route_distance([a, via])
    r2 = route_distance([via, b])
    if not r1 or not r2:
        return None
    return {
        "distance_km": round(r1["distance_km"] + r2["distance_km"], 1),
        "travel_time_min": r1["travel_time_min"] + r2["travel_time_min"],
        "path_coords": r1["path_coords"] + r2["path_coords"],
    }


def segment_route(a, b, avoid_ch):
    """Grąžina (route_dict, pastaba, diagnostika). Jei reikia, apvažiuoja Šveicariją."""
    base = route_distance([a, b])
    if not base or not avoid_ch:
        return base, "", ""
    if _in_ch(*a) or _in_ch(*b):
        return base, "", ""
    base_ch = ch_km(base["path_coords"])
    if base_ch <= CH_TRANSIT_MIN_KM:
        return base, "", ""
    best, best_name = None, None
    diag = [f"tiesiai: {base['distance_km']:.0f} km, per CH {base_ch:.0f} km"]
    for name, via_addr, fallback in AVOID_CH_VIA:
        r = _route_via(a, _via_point(via_addr, fallback), b)
        if not r:
            diag.append(f"{name}: Azure negrąžino maršruto")
            continue
        r_ch = ch_km(r["path_coords"])
        diag.append(f"{name}: {r['distance_km']:.0f} km, per CH {r_ch:.0f} km")
        if r_ch <= CH_TRANSIT_MIN_KM and (best is None or r["distance_km"] < best["distance_km"]):
            best, best_name = r, name
    if best:
        return best, f"per {best_name} (tiesiai per CH būtų {base['distance_km']:.0f} km)", " | ".join(diag)
    return base, "⚠️ per Šveicariją – apvažiavimo rasti nepavyko", " | ".join(diag)


# ─────────────────────────────────────────────
# PDF ataskaita
# ─────────────────────────────────────────────

_FONT_URLS = {
    "": "https://raw.githubusercontent.com/matplotlib/matplotlib/main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf",
    "B": "https://raw.githubusercontent.com/matplotlib/matplotlib/main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf",
}
_FONT_LOCAL = {
    "": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "B": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}


@st.cache_resource(show_spinner=False)
def _unicode_fonts():
    """Grąžina {'': kelias, 'B': kelias} su lietuviškas raides palaikančiu šriftu arba None."""
    paths = {}
    for style, url in _FONT_URLS.items():
        local = _FONT_LOCAL[style]
        if os.path.exists(local):
            paths[style] = local
            continue
        target = f"/tmp/DejaVuSans{'-Bold' if style else ''}.ttf"
        if not os.path.exists(target):
            try:
                r = requests.get(url, timeout=15)
                if r.status_code != 200:
                    return None
                with open(target, "wb") as f:
                    f.write(r.content)
            except Exception:
                return None
        paths[style] = target
    return paths


_TRANSLIT = str.maketrans("ąčęėįšųūžĄČĘĖĮŠŲŪŽ→—–", "aceeisuuzACEEISUUZ>--")


def get_azure_static_map(path_coords, points, width=1000, height=620):
    """Statinis Azure žemėlapis (PNG) su maršrutu ir stotelėmis. Grąžina bytes arba None."""
    if not path_coords or len(path_coords) < 2:
        return None
    max_points = 100
    if len(path_coords) > max_points:
        step = (len(path_coords) - 1) / (max_points - 1)
        path_coords = [path_coords[int(i * step)] for i in range(max_points)]
    lons = [p[0] for p in path_coords] + [c[1] for _, c in points]
    lats = [p[1] for p in path_coords] + [c[0] for _, c in points]
    pad_lon = max(0.2, (max(lons) - min(lons)) * 0.08)
    pad_lat = max(0.1, (max(lats) - min(lats)) * 0.08)
    bbox = f"{min(lons) - pad_lon},{min(lats) - pad_lat},{max(lons) + pad_lon},{max(lats) + pad_lat}"
    path_param = "lc4682B4|lw4|la0.85||" + "|".join(f"{lon:.5f} {lat:.5f}" for lon, lat in path_coords)
    pins_param = "default|coE53935||" + "|".join(
        f"'{i + 1}'{c[1]:.5f} {c[0]:.5f}" for i, (_, c) in enumerate(points)
    )
    base = {
        "api-version": "1.0",
        "subscription-key": AZURE_MAPS_KEY,
        "bbox": bbox,
        "width": width,
        "height": height,
        "path": path_param,
    }
    for extra in ({"pins": pins_param}, {}):
        try:
            r = requests.get(f"{BASE_URL}/map/static/png", params={**base, **extra}, timeout=20)
            if r.status_code == 200 and r.content:
                return r.content
        except Exception:
            pass
    return None


def generate_pdf(result, info, map_png=None) -> bytes:
    from fpdf import FPDF

    fonts = _unicode_fonts()
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    if fonts:
        pdf.add_font("U", "", fonts[""])
        pdf.add_font("U", "B", fonts["B"])
        family = "U"

        def t(x):
            return str(x)
    else:
        family = "Helvetica"

        def t(x):
            return str(x).translate(_TRANSLIT).encode("latin-1", "replace").decode("latin-1")

    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font(family, "B", 16)
    pdf.cell(W, 9, t("Maršruto km patikra"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(W, 5, t(f"Sugeneruota {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    def kv(label, value, bold=False):
        pdf.set_font(family, "", 10)
        pdf.cell(45, 6, t(label))
        pdf.set_font(family, "B" if bold else "", 10)
        pdf.cell(W - 45, 6, t(value), new_x="LMARGIN", new_y="NEXT")

    kv("Klientas:", info.get("client") or "—")
    kv("Reiso / užsakymo Nr.:", info.get("trip_ref") or "—")
    pdf.ln(2)

    total = result["total_km"]
    dh = result.get("deadhead_km")
    kv("Mūsų km (keliais):", f"{total:.1f} km", bold=True)
    if dh is not None:
        kv("  iš jų tuščia rida:", f"{dh:.1f} km")
        kv("  su kroviniu:", f"{total - dh:.1f} km")
    else:
        kv("Tuščia rida:", "neįskaičiuota (nenurodyta ankstesnė iškrovimo vieta)")

    status = info.get("status")
    if info.get("client_km"):
        kv("Kliento km:", f"{info['client_km']} km")
        kv("Skirtumas:", f"{info['diff']:+.1f} km ({info['diff_pct']:+.1f}%)")
        kv("Tolerancija:", info.get("tol_txt", ""))
    if status:
        pdf.ln(2)
        ok = status == "Atitinka"
        pdf.set_fill_color(*((220, 245, 225) if ok else (250, 220, 220)))
        pdf.set_text_color(*((20, 110, 40) if ok else (170, 30, 30)))
        pdf.set_font(family, "B", 12)
        pdf.cell(W, 9, t(("ATITINKA" if ok else "NEATITINKA")), fill=True, align="C",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # Stotelių lentelė
    pdf.set_font(family, "B", 11)
    pdf.cell(W, 7, t("Maršrutas"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 9)
    pdf.set_fill_color(238, 238, 238)
    with pdf.table(
        col_widths=(8, 62, 22, 24, 64),
        text_align=("CENTER", "LEFT", "RIGHT", "RIGHT", "LEFT"),
        line_height=5,
        first_row_as_headings=True,
    ) as table:
        head = table.row()
        for h in ("Nr.", "Adresas", "Iki sek. km", "Kaupiamasis", "Pastaba"):
            head.cell(t(h))
        for r in result["rows"]:
            row = table.row()
            row.cell(t(r["Nr."]))
            row.cell(t(r["Adresas"]))
            row.cell(t(r["Iki sekančio (km)"]))
            row.cell(t(r["Kaupiamasis (km)"]))
            row.cell(t(r["Pastaba"].replace("🚚 ", "").replace("⚠️ ", "! ")))

    if map_png:
        pdf.ln(4)
        img_w = W
        img_h = img_w * 620 / 1000
        if pdf.get_y() + img_h > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.image(io.BytesIO(map_png), x=pdf.l_margin, w=img_w)

    pdf.ln(3)
    pdf.set_font(family, "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(W, 4, t(
        "Atstumai apskaičiuoti Azure Maps (sunkvežimio maršrutas, be dabartinio eismo). "
        + ("Šveicarijos tranzitas vengiamas. " if info.get("avoid_ch") else "")
        + "Galimi nedideli nuokrypiai dėl maršruto pasirinkimo."
    ))
    return bytes(pdf.output())


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

st.title("🗺️ Maršruto KM Skaičiuoklė")
st.caption("Įklijuokite adresus → gaukite atstumus keliais, palyginkite su kliento km ir patvirtinkite")

if not AZURE_MAPS_KEY:
    st.error("⚠️ AZURE_MAPS_KEY nenustatytas. Pridėkite jį į .env arba Streamlit Secrets.")
    st.stop()

CLIENTS_FILE = "clients.json"


def _load_saved_clients() -> list:
    try:
        with open(CLIENTS_FILE, encoding="utf-8") as f:
            return [c for c in json.load(f) if c]
    except Exception:
        return []


def _save_client(name: str):
    name = (name or "").strip()
    if not name:
        return
    clients = _load_saved_clients()
    if name not in clients:
        clients.append(name)
        try:
            with open(CLIENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(clients, key=str.lower), f, ensure_ascii=False)
        except Exception:
            pass


def client_options() -> list:
    names = set(_load_saved_clients())
    names.update(r.get("Klientas", "") for r in st.session_state["approved"])
    return sorted((n for n in names if n), key=str.lower)


if "approved" not in st.session_state:
    st.session_state["approved"] = []

col_input, col_compare = st.columns([3, 1])

with col_input:
    deadhead = st.text_input(
        "🚚 Ankstesnio reiso paskutinė iškrovimo vieta (tuščia rida nuo)",
        placeholder="pvz. LT - 91100 Klaipėda  – iš ankstesnio reiso Excel eilutės paskutinė stotelė",
        help="Įrašykite, kur sunkvežimis paskutinį kartą iškrovė prieš šį reisą. "
             "Tada km skaičiuojami nuo ten iki pasikrovimo (tuščia rida) ir toliau per visas stoteles.",
    )
    raw_text = st.text_area(
        "📋 Adresai – po vieną per eilutę, visa Excel eilutė, arba vienoje eilutėje "
        "(atskirti ; arba šalies kodu, pvz. F - 50880 ...)",
        height=160,
        placeholder=(
            "CH - 1908 Riddes I - 15067 Novi Ligure- D - 63526 Erlensee\n"
            "arba visa Excel eilutė (Ctrl+C → Ctrl+V)"
        ),
    )
    cc1, cc2 = st.columns([1, 1])
    with cc1:
        client_name = st.selectbox(
            "👤 Klientas",
            options=client_options(),
            index=None,
            placeholder="Pasirinkite arba įrašykite naują",
            accept_new_options=True,
            help="Naują klientą tiesiog įrašykite – po patvirtinimo jis atsiras sąraše.",
        )
    with cc2:
        trip_ref = st.text_input("🔖 Reiso / užsakymo Nr. (nebūtina)", placeholder="pvz. 2026-1045")

with col_compare:
    client_km = st.number_input("📄 Kliento nurodyti km", min_value=0, value=0, step=10)
    t1, t2 = st.columns([2, 1])
    with t1:
        tol_value = st.number_input("Tolerancija ±", min_value=0.0, value=20.0, step=5.0)
    with t2:
        tol_unit = st.selectbox("Vnt.", ["km", "%"], label_visibility="visible")
    avoid_ch = st.checkbox(
        "Vengti Šveicarijos tranzito",
        value=True,
        help="Tikrinama kiekviena atkarpa tarp dviejų stotelių atskirai. Pvz. Riddes (CH) → Novi Ligure (IT) → "
             "Erlensee (DE): pirma atkarpa prasideda Šveicarijoje, todėl nekeičiama; antra (IT → DE) "
             "nukreipiama aplink Šveicariją (per Brenerį / Monblaną / Frejus – trumpiausią variantą).",
    )
    calculate = st.button("🧮 Skaičiuoti", type="primary", width="stretch")

st.divider()


def _split_by_postal_prefix(chunk: str) -> list:
    """Jei vienoje vietoje keli adresai 'F - 50880 X- F - 77550 Y', suskaido pagal šalies+pašto kodą."""
    starts = [m.start() for m in _PREFIX_POSTAL_RE.finditer(chunk) if m.group(1) in COUNTRY_PREFIX]
    if len(starts) < 2:
        return [chunk]
    bounds = starts + [len(chunk)]
    head = chunk[:starts[0]].strip(" -–;,")
    parts = [head] if head else []
    parts += [chunk[bounds[i]:bounds[i + 1]] for i in range(len(starts))]
    return parts


def parse_addresses(text: str) -> list:
    """
    Supranta:
    1. Vienas adresas per eilutę (Enter)
    2. Visa Excel eilutė (Tab atskirti langeliai)
    3. Viena eilutė su ; arba | skyrikliais
    4. Viena eilutė, kurioje adresai su šalies kodu: 'F - 50880 La Meauffe- F - 77550 Réau'
    """
    chunks = []
    for line in text.strip().splitlines():
        for part in re.split(r"[\t;|]", line):
            chunks.extend(_split_by_postal_prefix(part))
    cleaned = [c.strip().strip("-–,").strip() for c in chunks]
    return [c for c in cleaned if c]


def run_calculation(addresses, avoid_ch, has_deadhead=False):
    st.markdown("#### 📍 Ieškomi adresai...")
    geocode_results = []
    progress = st.progress(0)
    for i, addr in enumerate(addresses):
        geocode_results.append((addr, geocode(addr)))
        progress.progress((i + 1) / len(addresses))
    progress.empty()

    failed = [addr for addr, r in geocode_results if r is None]
    valid_pairs = [(addr, r) for addr, r in geocode_results if r is not None]
    if has_deadhead and geocode_results[0][1] is None:
        has_deadhead = False  # tuščios ridos vieta nerasta – skaičiuojama be jos
    if len(valid_pairs) < 2:
        return {"error": "Nepakanka rastų adresų maršrutui skaičiuoti.", "failed": failed}

    st.markdown("#### 🛣️ Skaičiuojami atstumai...")
    rows, paths, diagnostics = [], [], []
    deadhead_km = 0.0
    cumulative = 0.0
    seg_progress = st.progress(0)
    for i, (addr, coord) in enumerate(valid_pairs):
        seg_km, note, diag = None, "", ""
        if i < len(valid_pairs) - 1:
            route, note, diag = segment_route(coord, valid_pairs[i + 1][1], avoid_ch)
            if diag:
                diagnostics.append(f"{i + 1}→{i + 2}: {diag}")
            if route:
                seg_km = route["distance_km"]
                paths.append(route["path_coords"])
            else:
                note = "⚠️ maršruto gauti nepavyko"
        if seg_km:
            cumulative += seg_km
        if has_deadhead and i == 0:
            deadhead_km = seg_km or 0.0
            note = ("🚚 tuščia rida iki pasikrovimo" + (f" · {note}" if note else ""))
        rows.append({
            "Nr.": i + 1,
            "Adresas": addr,
            "Koordinatės": f"{coord[0]:.4f}, {coord[1]:.4f}",
            "Iki sekančio (km)": f"{seg_km:.1f}" if seg_km else "—",
            "Kaupiamasis (km)": f"{cumulative:.1f}",
            "Pastaba": note,
        })
        seg_progress.progress((i + 1) / len(valid_pairs))
    seg_progress.empty()

    return {
        "rows": rows,
        "paths": paths,
        "points": valid_pairs,
        "total_km": round(cumulative, 1),
        "deadhead_km": round(deadhead_km, 1) if has_deadhead else None,
        "failed": failed,
        "addresses": addresses,
        "diagnostics": diagnostics,
    }


def pdf_filename(info) -> str:
    parts = ["km_patikra", info.get("client") or "", info.get("trip_ref") or "",
             datetime.date.today().isoformat()]
    name = "_".join(p for p in parts if p)
    return re.sub(r"[^\w\-]+", "_", name) + ".pdf"


def trip_pdf(result, info) -> bytes:
    if "map_png" not in result:
        all_path = [pt for path in result["paths"] for pt in path]
        result["map_png"] = get_azure_static_map(all_path, result["points"])
    return generate_pdf(result, info, result.get("map_png"))


if calculate:
    addresses = parse_addresses(raw_text) if raw_text.strip() else []
    dh = deadhead.strip().strip("-–;,").strip()
    if dh:
        addresses = [dh] + addresses
    if len(addresses) < 2:
        st.warning("Reikia bent 2 adresų.")
        st.session_state.pop("result", None)
    else:
        st.session_state["result"] = run_calculation(addresses, avoid_ch, has_deadhead=bool(dh))
        st.session_state["result"]["trip_ref"] = trip_ref
        st.session_state["result"]["client"] = (client_name or "").strip()
        st.session_state["result"]["avoid_ch"] = avoid_ch
        st.rerun()

result = st.session_state.get("result")

if result:
    for addr in result.get("failed", []):
        st.warning(f"⚠️ Nerastas: **{addr}**")
    if result.get("error"):
        st.error(result["error"])
    else:
        st.markdown("### 📊 Rezultatai")
        st.dataframe(pd.DataFrame(result["rows"]), hide_index=True, width="stretch")
        if result.get("diagnostics"):
            with st.expander("🔎 Šveicarijos apvažiavimo patikra"):
                for line in result["diagnostics"]:
                    st.text(line)

        total_km = result["total_km"]
        dh_km = result.get("deadhead_km")
        if dh_km is None:
            st.warning("🚚 Nenurodyta ankstesnio reiso iškrovimo vieta – tuščia rida į km neįskaičiuota.")
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("📏 Iš viso km (keliais)", f"{total_km:.1f} km")
        if dh_km is not None:
            mc1.caption(f"iš jų tuščia rida {dh_km:.1f} km · su kroviniu {total_km - dh_km:.1f} km")

        status = None
        info = {
            "client": result.get("client") or "",
            "trip_ref": result.get("trip_ref") or "",
            "avoid_ch": result.get("avoid_ch", True),
            "client_km": client_km,
        }
        if client_km > 0:
            diff = total_km - client_km
            allowed = tol_value if tol_unit == "km" else client_km * tol_value / 100
            diff_pct = diff / client_km * 100
            mc2.metric("📄 Kliento km", f"{client_km} km")
            mc3.metric("📐 Skirtumas", f"{diff:+.1f} km ({diff_pct:+.1f}%)")
            tol_txt = f"±{tol_value:g} {tol_unit}" + (f" = ±{allowed:.0f} km" if tol_unit == "%" else "")
            if abs(diff) <= allowed:
                status = "Atitinka"
                st.success(f"✅ **Atitinka** – skirtumas {diff:+.1f} km telpa į toleranciją ({tol_txt}).")
            else:
                status = "Neatitinka"
                st.error(f"❌ **Neatitinka** – skirtumas {diff:+.1f} km viršija toleranciją ({tol_txt}).")

            ref = info["trip_ref"]
            client = info["client"]
            info.update({"diff": diff, "diff_pct": diff_pct, "tol_txt": tol_txt, "status": status})
            label = "✔️ Patvirtinti" if status == "Atitinka" else "✔️ Patvirtinti vis tiek"
            b1, b2, _ = st.columns([1, 1, 2])
            with b1:
                approve = st.button(label, type="primary" if status == "Atitinka" else "secondary", width="stretch")
            with b2:
                pdf_bytes = trip_pdf(result, info)
                st.download_button("📄 Atsisiųsti PDF", pdf_bytes, file_name=pdf_filename(info),
                                   mime="application/pdf", width="stretch")
            if approve:
                st.session_state["approved"].append({
                    "Laikas": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Klientas": client,
                    "Reisas": ref,
                    "Maršrutas": " → ".join(result["addresses"]),
                    "Mūsų km": total_km,
                    "Tuščia rida km": dh_km if dh_km is not None else "",
                    "Kliento km": client_km,
                    "Skirtumas km": round(diff, 1),
                    "Tolerancija": tol_txt,
                    "Statusas": status,
                })
                _save_client(client)
                st.session_state["last_pdf"] = (pdf_filename(info), pdf_bytes)
                st.session_state.pop("result", None)
                st.rerun()
        else:
            st.info("Įveskite kliento km, kad galėtumėte palyginti ir patvirtinti.")
            st.download_button("📄 Atsisiųsti PDF", trip_pdf(result, info), file_name=pdf_filename(info),
                               mime="application/pdf")

        st.markdown("### 🗺️ Maršrutas žemėlapyje")
        pts = result["points"]
        all_path = [pt for path in result["paths"] for pt in path]
        center_lat = sum(c[0] for _, c in pts) / len(pts)
        center_lon = sum(c[1] for _, c in pts) / len(pts)
        layers = [
            pdk.Layer(
                "PathLayer",
                [{"path": p} for p in result["paths"]],
                get_path="path",
                get_width=50,
                width_min_pixels=2,
                width_max_pixels=5,
                get_color=[70, 130, 180, 220],
            ),
            pdk.Layer(
                "ScatterplotLayer",
                pd.DataFrame([
                    {"lat": lat, "lon": lon, "name": f"{i+1}. {addr}"}
                    for i, (addr, (lat, lon)) in enumerate(pts)
                ]),
                get_position="[lon, lat]",
                get_color=[220, 50, 50, 220],
                get_radius=500,
                radius_min_pixels=6,
                radius_max_pixels=14,
                stroked=True,
                get_line_color=[255, 255, 255, 255],
                line_width_min_pixels=1,
                pickable=True,
            ),
        ]
        arr = arrow_layer(all_path)
        if arr:
            layers.append(arr)
        st.pydeck_chart(pdk.Deck(
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=5, pitch=0),
            layers=layers,
            tooltip={"text": "{name}"},
        ))

# ─────────────────────────────────────────────
# Patvirtinti reisai
# ─────────────────────────────────────────────
with st.expander("📂 Tęsti nuo anksčiau atsisiųsto CSV"):
    st.caption("Įkėlus anksčiau atsisiųstą CSV, patvirtinti reisai ir klientų sąrašas atkuriami.")
    up = st.file_uploader("CSV failas", type=["csv"], label_visibility="collapsed")
    if up is not None and st.session_state.get("_loaded_csv") != up.name + str(up.size):
        try:
            df_in = pd.read_csv(up, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
            rows_in = df_in.to_dict("records")
            existing = {(r.get("Laikas"), r.get("Maršrutas"), str(r.get("Reisas"))) for r in st.session_state["approved"]}
            added = 0
            for r in rows_in:
                key = (r.get("Laikas"), r.get("Maršrutas"), str(r.get("Reisas")))
                if key not in existing:
                    st.session_state["approved"].append(r)
                    existing.add(key)
                    added += 1
                _save_client(r.get("Klientas", ""))
            st.session_state["_loaded_csv"] = up.name + str(up.size)
            st.success(f"Įkelta reisų: {added}")
            st.rerun()
        except Exception as e:
            st.error(f"Nepavyko nuskaityti CSV: {e}")

approved = st.session_state["approved"]
if approved:
    st.divider()
    st.markdown(f"### ✅ Patvirtinti reisai ({len(approved)})")
    if st.session_state.get("last_pdf"):
        fn, data = st.session_state["last_pdf"]
        st.download_button(f"📄 Paskutinio patvirtinto reiso PDF ({fn})", data, file_name=fn,
                           mime="application/pdf")
    st.caption("Sąrašas laikomas tik šioje naršyklės sesijoje – prieš uždarant atsisiųskite CSV.")
    df_ok = pd.DataFrame(approved)
    if "Klientas" in df_ok.columns:
        filt_opts = sorted({c for c in df_ok["Klientas"].astype(str) if c}, key=str.lower)
        filt = st.multiselect("Rodyti tik klientus", filt_opts, placeholder="Visi klientai")
        if filt:
            df_ok = df_ok[df_ok["Klientas"].isin(filt)]
    st.dataframe(df_ok, hide_index=True, width="stretch")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            "⬇️ Atsisiųsti CSV",
            pd.DataFrame(approved).to_csv(index=False, sep=";").encode("utf-8-sig"),
            file_name=f"patvirtinti_reisai_{datetime.date.today()}.csv",
            mime="text/csv",
            width="stretch",
        )
    with c2:
        if st.button("🗑️ Išvalyti sąrašą", width="stretch"):
            st.session_state["approved"] = []
            st.rerun()
