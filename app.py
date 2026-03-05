import os
import math
import datetime
import requests
import streamlit as st
import pandas as pd
import pydeck as pdk
import reverse_geocoder as rg
from collections import defaultdict
from dotenv import load_dotenv
from fpdf import FPDF

load_dotenv()

# ─────────────────────────────────────────────
# Konfigūracija
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Marsruto KM Skaiciuokle",
    page_icon="🗺️",
    layout="wide",
)

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY") or st.secrets.get("AZURE_MAPS_KEY", "")
BASE_URL = "https://atlas.microsoft.com"

EUROPE_COUNTRY_SET = (
    "AT,BE,BG,CH,CY,CZ,DE,DK,EE,ES,FI,FR,GB,GR,HR,HU,IE,IT,LT,LU,LV,"
    "MT,NL,NO,PL,PT,RO,RS,SE,SI,SK,TR,UA,BY,MD,AL,BA,MK,ME,XK,IS,AD,LI,MC,SM,VA"
)

COUNTRY_FLAGS = {
    "DE": "🇩🇪", "FR": "🇫🇷", "BE": "🇧🇪", "NL": "🇳🇱", "DK": "🇩🇰",
    "PL": "🇵🇱", "CZ": "🇨🇿", "AT": "🇦🇹", "CH": "🇨🇭", "LT": "🇱🇹",
    "LV": "🇱🇻", "EE": "🇪🇪", "SE": "🇸🇪", "NO": "🇳🇴", "GB": "🇬🇧",
    "ES": "🇪🇸", "IT": "🇮🇹", "HU": "🇭🇺", "SK": "🇸🇰", "RO": "🇷🇴",
    "FI": "🇫🇮", "HR": "🇭🇷", "SI": "🇸🇮", "LU": "🇱🇺", "PT": "🇵🇹",
}

# Kelių mokesčiai (Maut) EUR/km pagal šalį ir Euro klasę (>7.5t, 4+ ašys)
# Šaltiniai: oficialūs 2024 tarifai
MAUT_RATES = {
    # (šalis): {euro_klase: EUR/km}
    "DE": {"Euro 6": 0.274, "Euro 5": 0.321, "Euro 4": 0.357, "Euro 3": 0.404, "Euro 2-": 0.450},
    "AT": {"Euro 6": 0.176, "Euro 5": 0.198, "Euro 4": 0.220, "Euro 3": 0.242, "Euro 2-": 0.264},
    "CH": {"Euro 6": 0.250, "Euro 5": 0.280, "Euro 4": 0.300, "Euro 3": 0.320, "Euro 2-": 0.350},
    "BE": {"Euro 6": 0.108, "Euro 5": 0.135, "Euro 4": 0.150, "Euro 3": 0.168, "Euro 2-": 0.185},
    "PL": {"Euro 6": 0.080, "Euro 5": 0.095, "Euro 4": 0.110, "Euro 3": 0.125, "Euro 2-": 0.140},
    "CZ": {"Euro 6": 0.095, "Euro 5": 0.115, "Euro 4": 0.130, "Euro 3": 0.148, "Euro 2-": 0.165},
    "SK": {"Euro 6": 0.085, "Euro 5": 0.105, "Euro 4": 0.120, "Euro 3": 0.138, "Euro 2-": 0.155},
    "HU": {"Euro 6": 0.075, "Euro 5": 0.095, "Euro 4": 0.110, "Euro 3": 0.125, "Euro 2-": 0.140},
    "FR": {"Euro 6": 0.120, "Euro 5": 0.145, "Euro 4": 0.165, "Euro 3": 0.185, "Euro 2-": 0.210},
    "ES": {"Euro 6": 0.090, "Euro 5": 0.110, "Euro 4": 0.125, "Euro 3": 0.140, "Euro 2-": 0.155},
    "IT": {"Euro 6": 0.110, "Euro 5": 0.135, "Euro 4": 0.155, "Euro 3": 0.175, "Euro 2-": 0.195},
    "PT": {"Euro 6": 0.095, "Euro 5": 0.115, "Euro 4": 0.130, "Euro 3": 0.148, "Euro 2-": 0.165},
    # Šalys be Maut (vinjetas arba nėra) – tarifas 0
    "NL": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
    "DK": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
    "SE": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
    "LT": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
    "LV": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
    "EE": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
}

EURO_CLASSES = ["Euro 6", "Euro 5", "Euro 4", "Euro 3", "Euro 2-"]

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


def haversine_km(p1, p2):
    R = 6371.0
    lat1, lon1 = math.radians(p1[1]), math.radians(p1[0])
    lat2, lon2 = math.radians(p2[1]), math.radians(p2[0])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def km_by_country(path_coords, sample_every=5):
    if not path_coords or len(path_coords) < 2:
        return {}
    country_km = defaultdict(float)
    sampled = path_coords[::sample_every]
    if path_coords[-1] not in sampled:
        sampled.append(path_coords[-1])
    coords_latlon = [(pt[1], pt[0]) for pt in sampled]
    results = rg.search(coords_latlon, mode=1, verbose=False)
    for i in range(len(sampled) - 1):
        seg_km = haversine_km(sampled[i], sampled[i + 1])
        country = results[i].get("cc", "?")
        country_km[country] += seg_km
    return dict(country_km)


# ─────────────────────────────────────────────
# PDF generavimas
# ─────────────────────────────────────────────

def _safe(text: str) -> str:
    return (str(text)
            .replace("—", "-")
            .replace("–", "-")
            .replace("€", "EUR")
            .encode("latin-1", errors="replace")
            .decode("latin-1"))


def generate_pdf(seg_rows, valid_pairs, country_rows, total_km, transport_cost,
                 maut_total, grand_total, client_km, full_route_min,
                 client_price_per_km, euro_class) -> bytes:

    class PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 13)
            self.set_fill_color(40, 80, 150)
            self.set_text_color(255, 255, 255)
            self.cell(0, 10, "Marsruto KM Ataskaita", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
            self.set_font("Helvetica", "", 8)
            self.cell(0, 6, f"Sugeneruota: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Euro klase: {euro_class}  |  Kaina/km: {client_price_per_km:.2f} EUR", new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            self.cell(0, 10, f"Puslapis {self.page_no()}", align="C")

    pdf = PDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_left_margin(10)
    pdf.set_right_margin(10)

    # ── Suvestinė ──
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 240, 255)
    pdf.cell(0, 7, "SUVESTINE", fill=True, new_x="LMARGIN", new_y="NEXT")

    travel_h = int(full_route_min // 60)
    travel_m = int(full_route_min % 60)

    summary_items = [
        ("Is viso km (keliais):", f"{total_km:.1f} km"),
        ("Trukme:", f"{travel_h}h {travel_m}min"),
        ("Transporto suma:", f"{transport_cost:.2f} EUR  ({client_price_per_km:.2f} EUR/km x {total_km:.1f} km)"),
        ("Keliu mokesciai (Maut):", f"{maut_total:.2f} EUR  (Euro klase: {euro_class})"),
        ("BENDRA SUMA:", f"{grand_total:.2f} EUR"),
    ]
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        summary_items.insert(2, ("Kliento km:", f"{client_km} km  (skirtumas: {sign}{diff:.1f} km)"))

    for label, value in summary_items:
        is_total = label == "BENDRA SUMA:"
        pdf.set_font("Helvetica", "B" if is_total else "B", 9)
        if is_total:
            pdf.set_fill_color(210, 230, 210)
            pdf.cell(70, 6, _safe(label), fill=True)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 6, _safe(value), fill=True, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(70, 6, _safe(label))
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, _safe(value), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)

    # ── Stotelių lentelė ──
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 240, 255)
    pdf.cell(0, 7, "STOTELIU LENTELE", fill=True, new_x="LMARGIN", new_y="NEXT")

    col_w = [10, 85, 45, 35, 35]
    headers = ["Nr.", "Adresas", "Koordinates", "Iki sekancio (km)", "Kaupiamasis (km)"]

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(210, 225, 245)
    for w, h in zip(col_w, headers):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    fill_row = False
    for row in seg_rows:
        idx = row["Nr."] - 1
        addr, coord = valid_pairs[idx] if idx < len(valid_pairs) else ("", None)
        coord_str = f"{coord[0]:.4f}, {coord[1]:.4f}" if coord else "-"
        pdf.set_fill_color(245, 249, 255) if fill_row else pdf.set_fill_color(255, 255, 255)
        pdf.cell(col_w[0], 5.5, _safe(row["Nr."]), border=1, fill=fill_row, align="C")
        pdf.cell(col_w[1], 5.5, _safe(row["Adresas"])[:60], border=1, fill=fill_row)
        pdf.cell(col_w[2], 5.5, _safe(coord_str), border=1, fill=fill_row, align="C")
        pdf.cell(col_w[3], 5.5, _safe(row["Iki sekancio (km)"]), border=1, fill=fill_row, align="C")
        pdf.cell(col_w[4], 5.5, _safe(row["Kaupiamasis (km)"]), border=1, fill=fill_row, align="C")
        pdf.ln()
        fill_row = not fill_row

    pdf.ln(4)

    # ── Šalių + Maut lentelė ──
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 240, 255)
    pdf.cell(0, 7, f"KM PAGAL SALIS IR KELIU MOKESCIAI ({euro_class})", fill=True, new_x="LMARGIN", new_y="NEXT")

    cc_col_w = [35, 30, 30, 30, 30]
    cc_headers = ["Salis", "KM", "Maut EUR/km", "Maut suma EUR", "Transport. suma EUR"]

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(210, 225, 245)
    for w, h in zip(cc_col_w, cc_headers):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    fill_row = False
    for row in country_rows:
        is_total = str(row.get("total_row", False))
        pdf.set_fill_color(220, 230, 245) if row.get("total_row") else (pdf.set_fill_color(245, 249, 255) if fill_row else pdf.set_fill_color(255, 255, 255))
        pdf.set_font("Helvetica", "B" if row.get("total_row") else "", 8)
        cc_label = _safe(str(row["Salis"]).encode("ascii","ignore").decode())
        pdf.cell(cc_col_w[0], 5.5, cc_label, border=1, fill=True, align="C")
        pdf.cell(cc_col_w[1], 5.5, _safe(row["KM"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[2], 5.5, _safe(row["Maut EUR/km"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[3], 5.5, _safe(row["Maut EUR"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[4], 5.5, _safe(row["Transport. EUR"]), border=1, fill=True, align="C")
        pdf.ln()
        fill_row = not fill_row

    return bytes(pdf.output())


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
st.caption("Įklijuokite adresus → km pagal šalis → transporto ir kelių mokesčių skaičiavimas")

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
    client_price_per_km = st.number_input(
        "💶 Kliento kaina (EUR/km)",
        min_value=0.0,
        value=1.16,
        step=0.01,
        format="%.2f",
        help="Kliento mokama kaina už 1 km",
    )
    euro_class = st.selectbox(
        "🚛 Vilkiko Euro klasė",
        options=EURO_CLASSES,
        index=0,
        help="Euro 6 = mažiausi kelių mokesčiai",
    )
    client_km = st.number_input("📄 Kliento nurodyti km (palyginimui)", min_value=0, value=0, step=10)
    st.write("")
    calculate = st.button("🧮 Skaičiuoti", type="primary", use_container_width=True)

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
        for addr in addresses:
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

    # 3. Segmentų atstumai
    with st.spinner("📏 Skaičiuojami tarpiniai atstumai..."):
        seg_rows = []
        cumulative = 0.0
        for i in range(len(valid_pairs)):
            addr, coord = valid_pairs[i]
            seg_km = segment_distance(all_coords[i], all_coords[i + 1]) if i < len(valid_pairs) - 1 else None
            if seg_km:
                cumulative += seg_km
            seg_rows.append({
                "Nr.": i + 1,
                "Adresas": addr,
                "Koordinates": f"{coord[0]:.4f}, {coord[1]:.4f}",
                "Iki sekancio (km)": f"{seg_km:.1f}" if seg_km else "-",
                "Kaupiamasis (km)": f"{cumulative:.1f}",
            })

    # 4. KM pagal šalis
    with st.spinner("🌍 Skirstoma pagal šalis..."):
        country_km_raw = km_by_country(path_coords, sample_every=5)

    raw_total = sum(country_km_raw.values())
    if raw_total > 0:
        factor = total_km / raw_total
        country_km = {cc: round(km * factor, 1) for cc, km in sorted(country_km_raw.items(), key=lambda x: -x[1])}
    else:
        country_km = {}

    # 5. Skaičiavimai
    transport_cost = round(total_km * client_price_per_km, 2)

    maut_rows = []
    maut_total = 0.0
    for cc, km in country_km.items():
        maut_rate = MAUT_RATES.get(cc, {}).get(euro_class, 0.0)
        maut_cost = round(km * maut_rate, 2)
        transport_cc = round(km * client_price_per_km, 2)
        maut_total += maut_cost
        flag = COUNTRY_FLAGS.get(cc, "")
        maut_rows.append({
            "Salis": f"{flag} {cc}",
            "KM": f"{km:.1f}",
            "Maut EUR/km": f"{maut_rate:.3f}" if maut_rate > 0 else "-",
            "Maut EUR": f"{maut_cost:.2f}" if maut_cost > 0 else "-",
            "Transport. EUR": f"{transport_cc:.2f}",
            "total_row": False,
        })

    maut_total = round(maut_total, 2)
    grand_total = round(transport_cost + maut_total, 2)

    # Iš viso eilutė
    maut_rows.append({
        "Salis": "VISO",
        "KM": f"{total_km:.1f}",
        "Maut EUR/km": "-",
        "Maut EUR": f"{maut_total:.2f}",
        "Transport. EUR": f"{transport_cost:.2f}",
        "total_row": True,
    })

    # ── Rezultatai ──
    st.divider()
    st.markdown("### 📊 Stotelių lentelė")
    st.dataframe(pd.DataFrame(seg_rows), hide_index=True, use_container_width=True)

    m1, m2, m3 = st.columns(3)
    m1.metric("📏 Iš viso km", f"{total_km:.1f} km")
    m2.metric("⏱️ Trukmė", f"{int(full_route['travel_time_min']//60)}h {int(full_route['travel_time_min']%60)}min")
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        m3.metric("📐 Skirtumas nuo kliento", f"{sign}{diff:.1f} km", delta=f"{client_km} km kliento", delta_color="off")

    st.divider()
    st.markdown(f"### 🌍 KM pagal šalis ir kelių mokesčiai ({euro_class})")

    display_rows = [{k: v for k, v in r.items() if k != "total_row"} for r in maut_rows]
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚛 Transporto suma", f"{transport_cost:.2f} EUR",
              help=f"{client_price_per_km:.2f} EUR/km × {total_km:.1f} km")
    c2.metric("🛣️ Kelių mokesčiai (Maut)", f"{maut_total:.2f} EUR",
              help=f"Pagal {euro_class}")
    c3.metric("💰 BENDRA SUMA", f"{grand_total:.2f} EUR")
    if client_km > 0:
        client_transport = round(client_km * client_price_per_km, 2)
        c4.metric("📄 Kliento km suma", f"{client_transport:.2f} EUR",
                  help=f"{client_km} km × {client_price_per_km:.2f} EUR/km")

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

    # ── PDF ──
    st.divider()
    st.markdown("### 📥 Ataskaita")
    try:
        pdf_bytes = generate_pdf(
            seg_rows=seg_rows,
            valid_pairs=valid_pairs,
            country_rows=maut_rows,
            total_km=total_km,
            transport_cost=transport_cost,
            maut_total=maut_total,
            grand_total=grand_total,
            client_km=client_km,
            full_route_min=full_route["travel_time_min"],
            client_price_per_km=client_price_per_km,
            euro_class=euro_class,
        )
        fname = f"marsrutas_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        st.download_button(
            label="📄 Atsisiųsti PDF ataskaitą",
            data=pdf_bytes,
            file_name=fname,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(f"PDF generavimo klaida: {e}")
