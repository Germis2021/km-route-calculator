import os
import math
import requests
import streamlit as st
import pandas as pd
import pydeck as pdk
import reverse_geocoder as rg
from collections import defaultdict
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

# Numatytosios km kainos pagal šalį (€/km) – galima keisti UI
DEFAULT_PRICES = {
    "DE": 1.25,
    "FR": 1.16,
    "BE": 1.10,
    "NL": 1.15,
    "DK": 1.20,
    "PL": 0.95,
    "CZ": 1.00,
    "AT": 1.18,
    "CH": 1.30,
    "LT": 0.90,
    "LV": 0.90,
    "EE": 0.90,
    "SE": 1.15,
    "NO": 1.35,
    "GB": 1.20,
    "ES": 1.10,
    "IT": 1.15,
    "HU": 0.95,
    "SK": 0.98,
    "RO": 0.88,
}

COUNTRY_FLAGS = {
    "DE": "🇩🇪", "FR": "🇫🇷", "BE": "🇧🇪", "NL": "🇳🇱", "DK": "🇩🇰",
    "PL": "🇵🇱", "CZ": "🇨🇿", "AT": "🇦🇹", "CH": "🇨🇭", "LT": "🇱🇹",
    "LV": "🇱🇻", "EE": "🇪🇪", "SE": "🇸🇪", "NO": "🇳🇴", "GB": "🇬🇧",
    "ES": "🇪🇸", "IT": "🇮🇹", "HU": "🇭🇺", "SK": "🇸🇰", "RO": "🇷🇴",
    "FI": "🇫🇮", "HR": "🇭🇷", "SI": "🇸🇮", "LU": "🇱🇺", "PT": "🇵🇹",
}

# ─────────────────────────────────────────────
# Geocoding / routing funkcijos
# ─────────────────────────────────────────────

def _is_in_europe(lat, lon):
    return 34.0 <= lat <= 72.0 and -12.0 <= lon <= 45.0


def _simplify_address(address):
    import re
    match = re.search(r'([A-Z]{1,2}-\d{4,5}\s+\S+)', address)
    if match:
        return match.group(1)
    parts = re.split(r',\s*|\s+-\s+', address)
    if len(parts) >= 2:
        return parts[-1].strip()
    return None


def geocode(address: str):
    if not AZURE_MAPS_KEY or not address.strip():
        return None
    for query in [address, _simplify_address(address)]:
        if not query:
            continue
        params = {
            "api-version": "1.0",
            "subscription-key": AZURE_MAPS_KEY,
            "query": query,
            "limit": 5,
            "countrySet": EUROPE_COUNTRY_SET,
        }
        try:
            r = requests.get(f"{BASE_URL}/search/address/json", params=params, timeout=8)
            if r.status_code == 200:
                for result in r.json().get("results", []):
                    pos = result["position"]
                    if _is_in_europe(pos["lat"], pos["lon"]):
                        return pos["lat"], pos["lon"]
        except Exception:
            pass
    return None


def route_distance(waypoints):
    if len(waypoints) < 2 or not AZURE_MAPS_KEY:
        return None
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
        r = requests.get(f"{BASE_URL}/route/directions/json", params=params, timeout=20)
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
    result = route_distance([a, b])
    return result["distance_km"] if result else None


# ─────────────────────────────────────────────
# Šalių km skirstymas (reverse geocoder)
# ─────────────────────────────────────────────

def haversine_km(p1, p2):
    """Atstumas tarp dviejų taškų [lon, lat] kilometrais."""
    R = 6371.0
    lat1, lon1 = math.radians(p1[1]), math.radians(p1[0])
    lat2, lon2 = math.radians(p2[1]), math.radians(p2[0])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def km_by_country(path_coords, sample_every=5):
    """
    Paskaičiuoja km kiekvienoje šalyje pagal maršruto taškus.
    sample_every: kas kiek taškų tikrina šalį (greičiui).
    Grąžina {country_code: km}.
    """
    if not path_coords or len(path_coords) < 2:
        return {}

    country_km = defaultdict(float)

    # Imame kas N-ąjį tašką (greičiau, pakankamas tikslumas)
    sampled = path_coords[::sample_every]
    if path_coords[-1] not in sampled:
        sampled.append(path_coords[-1])

    # Batch reverse geocoding (greitas, lokalus)
    coords_latlon = [(pt[1], pt[0]) for pt in sampled]
    results = rg.search(coords_latlon, mode=1, verbose=False)

    for i in range(len(sampled) - 1):
        seg_km = haversine_km(sampled[i], sampled[i + 1])
        country = results[i].get("cc", "?")
        country_km[country] += seg_km

    return dict(country_km)


# ─────────────────────────────────────────────
# Žemėlapio sluoksniai
# ─────────────────────────────────────────────

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


def parse_addresses(text: str) -> list:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return []
    if len(lines) == 1 and "\t" in lines[0]:
        return [a.strip() for a in lines[0].split("\t") if a.strip()]
    addresses = []
    for line in lines:
        if "\t" in line:
            addresses.extend([a.strip() for a in line.split("\t") if a.strip()])
        else:
            addresses.append(line)
    return addresses


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

st.title("🗺️ Maršruto KM Skaičiuoklė")
st.caption("Įklijuokite adresus → km pagal šalis → sąnaudų skaičiavimas")

if not AZURE_MAPS_KEY:
    st.error("⚠️ AZURE_MAPS_KEY nenustatytas.")
    st.stop()

# ── Įvestis ──
col_input, col_right = st.columns([3, 1])

with col_input:
    raw_text = st.text_area(
        "📋 Adresai – po vieną per eilutę arba visa Excel eilutė (Tab atskirti)",
        height=180,
        placeholder=(
            "Hamburg, Germany\n"
            "B-9750 Zingem\n"
            "F-51350 Cormontreuil\n"
            "F-95520 Osny\n\n"
            "Arba visa eilutė iš Excel:\n"
            "Hamburg, Germany\tB-9750 Zingem\tF-51350 Cormontreuil\tF-95520 Osny"
        ),
    )

with col_right:
    client_km = st.number_input("📄 Kliento km (palyginimui)", min_value=0, value=0, step=10)
    st.write("")
    calculate = st.button("🧮 Skaičiuoti", type="primary", use_container_width=True)

# ── Kainų nustatymai ──
with st.expander("💶 Km kainos pagal šalį (€/km) – redaguojamos", expanded=False):
    st.caption("Numatytosios kainos – galite keisti prieš skaičiuodami.")
    price_cols = st.columns(5)
    user_prices = {}
    sorted_countries = sorted(DEFAULT_PRICES.keys())
    for idx, cc in enumerate(sorted_countries):
        flag = COUNTRY_FLAGS.get(cc, "")
        col = price_cols[idx % 5]
        user_prices[cc] = col.number_input(
            f"{flag} {cc}",
            min_value=0.0,
            max_value=5.0,
            value=DEFAULT_PRICES[cc],
            step=0.01,
            format="%.2f",
            key=f"price_{cc}",
        )

st.divider()

# ── Skaičiavimas ──
if calculate and raw_text.strip():
    addresses = parse_addresses(raw_text)

    if len(addresses) < 2:
        st.warning("Reikia bent 2 adresų.")
        st.stop()

    # 1. Geocoding
    with st.status("📍 Geocoding adresai...", expanded=True) as status:
        geocode_results = []
        for i, addr in enumerate(addresses):
            st.write(f"🔍 {addr}")
            result = geocode(addr)
            geocode_results.append((addr, result))
            if not result:
                st.warning(f"⚠️ Nerastas: {addr}")
        status.update(label="✅ Geocoding baigtas", state="complete")

    valid_pairs = [(addr, r) for addr, r in geocode_results if r is not None]
    if len(valid_pairs) < 2:
        st.error("Nepakanka rastų adresų.")
        st.stop()

    all_coords = [r for _, r in valid_pairs]

    # 2. Pilnas maršrutas
    with st.spinner("🛣️ Skaičiuojamas maršrutas..."):
        full_route = route_distance(all_coords)

    if not full_route:
        st.error("Nepavyko gauti maršruto iš Azure Maps.")
        st.stop()

    total_km = full_route["distance_km"]
    path_coords = full_route["path_coords"]

    # 3. Segmentų atstumai (tarp stotelių)
    with st.spinner("📏 Skaičiuojami tarpiniai atstumai..."):
        seg_rows = []
        cumulative = 0.0
        for i in range(len(valid_pairs)):
            addr, coord = valid_pairs[i]
            if i < len(valid_pairs) - 1:
                seg_km = segment_distance(all_coords[i], all_coords[i + 1])
            else:
                seg_km = None
            if seg_km:
                cumulative += seg_km
            seg_rows.append({
                "Nr.": i + 1,
                "Adresas": addr,
                "Iki sekančio (km)": f"{seg_km:.1f}" if seg_km else "—",
                "Kaupiamasis (km)": f"{cumulative:.1f}",
            })

    # 4. KM pagal šalis
    with st.spinner("🌍 Skirstoma pagal šalis..."):
        country_km_raw = km_by_country(path_coords, sample_every=5)

    # Normalizuojame iki real total km
    raw_total = sum(country_km_raw.values())
    if raw_total > 0:
        factor = total_km / raw_total
        country_km = {cc: round(km * factor, 1) for cc, km in sorted(country_km_raw.items(), key=lambda x: -x[1])}
    else:
        country_km = {}

    # ── Rezultatai ──
    st.divider()
    st.markdown("### 📊 Stotelių lentelė")
    st.dataframe(pd.DataFrame(seg_rows), hide_index=True, use_container_width=True)

    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("📏 Iš viso km (keliais)", f"{total_km:.1f} km")
    mc2.metric("⏱️ Trukmė", f"{int(full_route['travel_time_min'] // 60)}h {int(full_route['travel_time_min'] % 60)}min")
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        mc3.metric("📐 Skirtumas nuo kliento", f"{sign}{diff:.1f} km",
                   delta=f"{client_km} km kliento", delta_color="off")

    # ── Šalių suvestinė ir kainų skaičiavimas ──
    if country_km:
        st.divider()
        st.markdown("### 🌍 KM pagal šalis ir sąnaudos")

        country_rows = []
        total_cost = 0.0
        for cc, km in country_km.items():
            price = user_prices.get(cc, 0.0)
            cost = round(km * price, 2)
            total_cost += cost
            flag = COUNTRY_FLAGS.get(cc, "")
            country_rows.append({
                "Šalis": f"{flag} {cc}",
                "KM": f"{km:.1f}",
                "Kaina €/km": f"{price:.2f}",
                "Suma €": f"{cost:.2f}",
            })

        # Iš viso eilutė
        country_rows.append({
            "Šalis": "**VISO**",
            "KM": f"**{total_km:.1f}**",
            "Kaina €/km": "—",
            "Suma €": f"**{total_cost:.2f}**",
        })

        st.dataframe(pd.DataFrame(country_rows), hide_index=True, use_container_width=True)

        cost_cols = st.columns(3)
        cost_cols[0].metric("💰 Bendra suma", f"{total_cost:.2f} €")
        if total_km > 0:
            cost_cols[1].metric("📊 Vid. kaina/km", f"{total_cost/total_km:.3f} €/km")
        if client_km > 0 and total_km > 0:
            client_cost = round(client_km * (total_cost / total_km), 2)
            cost_cols[2].metric("📄 Kliento km suma", f"{client_cost:.2f} €")

    # ── Žemėlapis ──
    st.divider()
    st.markdown("### 🗺️ Maršrutas žemėlapyje")

    center_lat = sum(c[0] for c in all_coords) / len(all_coords)
    center_lon = sum(c[1] for c in all_coords) / len(all_coords)
    view_state = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=6, pitch=0)

    layers = [
        pdk.Layer(
            "PathLayer",
            [{"path": path_coords}],
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

    arr = arrow_layer(path_coords)
    if arr:
        layers.append(arr)

    st.pydeck_chart(pdk.Deck(
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=view_state,
        layers=layers,
        tooltip={"text": "{name}"},
    ))
