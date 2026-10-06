import os
import re
import math
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
    """
    Grąžina žodyną su distance_km, travel_time_min, path_coords
    arba None jei nepavyko.
    waypoints: [(lat, lon), ...]
    """
    if len(waypoints) < 2 or not AZURE_MAPS_KEY:
        return None

    # Pašaliname gretutines koordinates-dublikatus
    clean = [waypoints[0]]
    for wp in waypoints[1:]:
        if wp != clean[-1]:
            clean.append(wp)
    if len(clean) < 2:
        return None

    query = ":".join(f"{lat},{lon}" for lat, lon in clean)
    params = {
        "api-version": "1.0",
        "subscription-key": AZURE_MAPS_KEY,
        "query": query,
        "travelMode": "truck",
        "vehicleEngineType": "combustion",
        "routeType": "fastest",
    }
    try:
        r = requests.get(f"{BASE_URL}/route/directions/json", params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        routes = data.get("routes", [])
        if not routes:
            return None
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
        return None


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
# UI
# ─────────────────────────────────────────────

st.title("🗺️ Maršruto KM Skaičiuoklė")
st.caption("Įklijuokite adresus → gaukite atstumus keliais ir žemėlapį")

if not AZURE_MAPS_KEY:
    st.error("⚠️ AZURE_MAPS_KEY nenustatytas. Pridėkite jį į .env arba Streamlit Secrets.")
    st.stop()

col_input, col_compare = st.columns([3, 1])

with col_input:
    raw_text = st.text_area(
        "📋 Adresai – po vieną per eilutę, visa Excel eilutė, arba vienoje eilutėje (atskirti ; arba šalies kodu, pvz. F - 50880 ...)",
        height=220,
        placeholder=(
            "Variantas 1 – po vieną per eilutę:\n"
            "Hamburg, Germany\n"
            "B-9750 Zingem\n"
            "F-51350 Cormontreuil\n"
            "F-95520 Osny\n\n"
            "Variantas 2 – visa Excel eilutė (Ctrl+C → Ctrl+V):\n"
            "Hamburg, Germany\tB-9750 Zingem\tF-51350 Cormontreuil\tF-95520 Osny"
        ),
    )

with col_compare:
    client_km = st.number_input(
        "📄 Kliento nurodyti km",
        min_value=0,
        value=0,
        step=10,
        help="Įveskite kliento pasiūlytą atstumą palyginimui",
    )
    st.write("")
    calculate = st.button("🧮 Skaičiuoti", type="primary", use_container_width=True)

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


if calculate and raw_text.strip():
    addresses = parse_addresses(raw_text)

    if len(addresses) < 2:
        st.warning("Reikia bent 2 adresų.")
        st.stop()

    # 1. Geocoding
    st.markdown("#### 📍 Geocoding...")
    coords = []
    geocode_results = []
    progress = st.progress(0)

    for i, addr in enumerate(addresses):
        with st.spinner(f"Ieškoma: {addr}"):
            result = geocode(addr)
            geocode_results.append((addr, result))
            if result:
                coords.append(result)
            progress.progress((i + 1) / len(addresses))

    progress.empty()

    failed = [(addr, r) for addr, r in geocode_results if r is None]
    if failed:
        for addr, _ in failed:
            st.warning(f"⚠️ Nerastas: **{addr}**")

    valid_pairs = [(addr, r) for addr, r in geocode_results if r is not None]

    if len(valid_pairs) < 2:
        st.error("Nepakanka rastų adresų maršrutui skaičiuoti.")
        st.stop()

    # 2. Segmentų atstumai
    st.markdown("#### 🛣️ Skaičiuojami atstumai...")
    rows = []
    cumulative = 0.0
    all_coords = [r for _, r in valid_pairs]

    segment_progress = st.progress(0)
    for i in range(len(valid_pairs)):
        addr, coord = valid_pairs[i]
        if i < len(valid_pairs) - 1:
            with st.spinner(f"Segmentas {i+1}→{i+2}..."):
                seg_km = segment_distance(all_coords[i], all_coords[i + 1])
        else:
            seg_km = None

        if seg_km:
            cumulative += seg_km

        rows.append({
            "Nr.": i + 1,
            "Adresas": addr,
            "Koordinatės": f"{coord[0]:.4f}, {coord[1]:.4f}",
            "Iki sekančio (km)": f"{seg_km:.1f}" if seg_km else "—",
            "Kaupiamasis (km)": f"{cumulative:.1f}",
        })
        segment_progress.progress((i + 1) / len(valid_pairs))

    segment_progress.empty()

    # 3. Rezultatų lentelė
    st.divider()
    st.markdown("### 📊 Rezultatai")

    total_km = cumulative
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)

    # Suvestinė
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("📏 Iš viso km (keliais)", f"{total_km:.1f} km")
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        mc2.metric("📄 Kliento km", f"{client_km} km")
        color = "normal" if abs(diff) <= 20 else "inverse"
        mc3.metric("📐 Skirtumas", f"{sign}{diff:.1f} km", delta_color=color)

    # 4. Žemėlapis
    st.divider()
    st.markdown("### 🗺️ Maršrutas žemėlapyje")

    with st.spinner("Skaičiuojamas pilnas maršrutas žemėlapiui..."):
        full_route = route_distance(all_coords)

    if full_route:
        center_lat = sum(c[0] for c in all_coords) / len(all_coords)
        center_lon = sum(c[1] for c in all_coords) / len(all_coords)

        view_state = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=6, pitch=0)

        layers = [
            pdk.Layer(
                "PathLayer",
                [{"path": full_route["path_coords"]}],
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
                    for i, (addr, (lat, lon)) in enumerate(valid_pairs)
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

        arr = arrow_layer(full_route["path_coords"])
        if arr:
            layers.append(arr)

        st.pydeck_chart(pdk.Deck(
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
            initial_view_state=view_state,
            layers=layers,
            tooltip={"text": "{name}"},
        ))
    else:
        st.warning("Nepavyko gauti pilno maršruto žemėlapiui. Taškai vis tiek rodomi aukščiau.")
