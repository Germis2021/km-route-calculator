import io
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
import stripe

load_dotenv()

# ─────────────────────────────────────────────
# Konfigūracija
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Marsruto KM Skaiciuokle",
    page_icon="🗺️",
    layout="wide",
)

# Streamlit brendo paslėpimas + lang switcher (top-left, active border, dark inactive)
def _inject_lang_switcher_css():
    lang = st.session_state.get("lang", "LT")
    # Target first column of first row (lang buttons); first button = LT, second = EN
    sel_first = '[data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:first-of-type [data-testid="stHorizontalBlock"] [data-testid="column"]:first-of-type button'
    sel_second = '[data-testid="stHorizontalBlock"]:first-of-type [data-testid="column"]:first-of-type [data-testid="stHorizontalBlock"] [data-testid="column"]:last-of-type button'
    active_style = "border: 2px solid #4a9eff !important; border-radius: 4px !important; background-color: #1e3a5f !important;"
    inactive_style = "background-color: #262730 !important; color: #9ca3af !important; border: 1px solid #374151 !important;"
    if lang == "LT":
        css = f"{sel_first} {{{active_style}}} {sel_second} {{{inactive_style}}}"
    else:
        css = f"{sel_first} {{{inactive_style}}} {sel_second} {{{active_style}}}"
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display: none;}
[data-testid="stToolbar"] {display: none;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stStatusWidget"] {display: none;}
</style>
""", unsafe_allow_html=True)

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY") or st.secrets.get("AZURE_MAPS_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8501")
STRIPE_PRICE_TRIAL = os.getenv("STRIPE_PRICE_TRIAL")
STRIPE_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY")
STRIPE_PRICE_YEARLY = os.getenv("STRIPE_PRICE_YEARLY")
TRIAL_ROUTE_LIMIT = 10
BASE_URL = "https://atlas.microsoft.com"

# Pending redirect and language (before any UI)
if "pending_redirect_url" not in st.session_state:
    st.session_state["pending_redirect_url"] = None
if "lang" not in st.session_state:
    st.session_state["lang"] = "LT"

# ─────────────────────────────────────────────
# Translations (LT / EN)
# ─────────────────────────────────────────────

def _t(key: str, **kwargs) -> str:
    lang = st.session_state.get("lang", "LT")
    s = _TEXTS.get(lang, _TEXTS["LT"]).get(key, key)
    return s.format(**kwargs) if kwargs else s

_TEXTS = {
    "LT": {
        "landing_description": (
            "Transporto įmonėse dažnai tenka tikrinti klientų pateiktus reisų atsiskaitymus. "
            "Tradiciškai tai reiškia — kiekvienas adresas suvedamas į Google Maps rankiniu būdu, km dauginami iš kainos, "
            "skaičiuojami kelių mokesčiai... ir visa tai kiekvienam reisui atskirai.\n\n"
            "RouteCalc leidžia tiesiog nukopijuoti adresų eilutę iš Excel ir per sekundes gauti: km pagal šalis, "
            "Maut kelių mokesčius pagal vilkiko Euro klasę ir PDF ataskaitą palyginimui su kliento credit note.\n\n"
            "⚠️ **Svarbu:** Ši programėlė skirta greitam sutikrinimui su kliento credit note po įvykdyto reiso — ne kelionės planavimui. "
            "Kelių mokesčių tarifai paremti viešai skelbiamais oficialiais duomenimis ir gali nežymiai skirtis priklausomai nuo konkretaus greitkelio atkarpos ar sezono. "
            "Galimi nedideli atstumo nukrypimai (~1-3%) dėl maršruto optimizavimo skirtumų."
        ),
        "how_it_works": (
            "Transporto įmonėse dažnai tenka tikrinti klientų pateiktus reisų atsiskaitymus. "
            "Tradiciškai tai reiškia — kiekvienas adresas suvedamas į Google Maps rankiniu būdu, km dauginami iš kainos, "
            "skaičiuojami kelių mokesčiai... ir visa tai kiekvienam reisui atskirai.\n\n"
            "RouteCalc leidžia tiesiog nukopijuoti adresų eilutę iš Excel ir per sekundes gauti: km pagal šalis, "
            "Maut kelių mokesčius pagal vilkiko Euro klasę ir PDF ataskaitą palyginimui su kliento credit note.\n\n"
            "⚠️ **Svarbu:** Ši programėlė skirta greitam sutikrinimui su kliento credit note po įvykdyto reiso — ne kelionės planavimui. "
            "Kelių mokesčių tarifai paremti viešai skelbiamais oficialiais duomenimis ir gali nežymiai skirtis priklausomai nuo konkretaus greitkelio atkarpos ar sezono. "
            "Galimi nedideli atstumo nukrypimai (~1-3%) dėl maršruto optimizavimo skirtumų."
        ),
        "disclaimer": "⚠️ Atstumai skaičiuojami pagal žemėlapių duomenis. Galimi nedideli nukrypimai (~1-3%) dėl maršruto optimizavimo skirtumų.",
        "landing_title": "🗺️ RouteCalc – Maršruto KM skaičiuoklė",
        "pricing": "Kainodara",
        "trial_plan": "Nemokamas bandymas",
        "trial_days": "7 dienos",
        "trial_desc": "Pilna prieiga, 10 maršrutų limitas.",
        "btn_trial": "Pradėti nemokamai 7 dienas",
        "monthly_plan": "Mėnesinis",
        "monthly_price": "19 EUR/men",
        "monthly_desc": "Neriboti maršrutai.",
        "btn_monthly": "Mėnesinis 19 EUR/men",
        "yearly_plan": "Metinis",
        "yearly_price": "149 EUR/metus",
        "yearly_desc": "Sutaupykite ~35%.",
        "btn_yearly": "Metinis 149 EUR/metus",
        "after_payment": "Po apmokėjimo būsite nukreipti atgal į programą.",
        "continue_with_email": "Jau turite prenumeratą? Prisijunkite el. paštu",
        "email_placeholder": "el. paštas",
        "continue_btn": "Prisijungti",
        "email_not_found": "Nerastas aktyvus abonementas šiuo el. paštu. Patikrinkite adresą arba įsigykite prenumeratą.",
        "enter_email": "Įveskite el. paštą.",
        "footer_contact": "📧 Klausimams ir sąskaitoms faktūroms: vikteko@gmail.com",
        "error_stripe": "Nepavyko pradėti. Patikrinkite STRIPE_* kintamuosius.",
        "expired_title": "🗺️ RouteCalc",
        "expired_warning": "Prenumerata pasibaigė.",
        "btn_manage_expired": "Valdyti prenumeratą (atnaujinti, pakeisti planą)",
        "error_portal": "Nepavyko atidaryti portalo.",
        "redirecting": "Nukreipiama į mokėjimą...",
        "trial_days_left": "Trial: {days_left} dienų liko",
        "pro_monthly": "PRO Mėnesinis",
        "pro_yearly": "PRO Metinis",
        "routes_used": "Maršrutų naudota: {used}/{limit}",
        "btn_manage": "Valdyti prenumeratą",
        "main_title": "🗺️ Maršruto KM Skaičiuoklė",
        "main_caption": "Įklijuokite adresus → km pagal šalis → transporto ir kelių mokesčių skaičiavimas",
        "azure_error": "⚠️ AZURE_MAPS_KEY nenustatytas.",
        "trial_limit_msg": "Išnaudojote {limit} nemokamus maršrutus. Atnaujinkite į Pro.",
        "btn_upgrade": "Atnaujinti į Pro",
        "addresses_label": "📋 Adresai – po vieną per eilutę arba visa Excel eilutė (Tab atskirti)",
        "client_price_label": "💶 Kliento kaina (EUR/km)",
        "client_price_help": "Kliento mokama kaina už 1 km",
        "euro_class_label": "🚛 Vilkiko Euro klasė",
        "euro_class_help": "Euro 6 = mažiausi kelių mokesčiai",
        "client_km_label": "📄 Kliento nurodyti km (palyginimui)",
        "btn_calculate": "🧮 Skaičiuoti",
        "need_two": "Reikia bent 2 adresų.",
        "geocoding": "📍 Geocoding adresai...",
        "geocoding_done": "✅ Geocoding baigtas",
        "not_found": "⚠️ Nerastas: {addr}",
        "not_enough": "Nepakanka rastų adresų.",
        "route_spinner": "🛣️ Skaičiuojamas maršrutas...",
        "route_error": "Nepavyko gauti maršruto iš Azure Maps.",
        "segment_spinner": "📏 Skaičiuojami tarpiniai atstumai...",
        "country_spinner": "🌍 Skirstoma pagal šalis...",
        "stops_table": "📊 Stotelių lentelė",
        "total_km": "📏 Iš viso km",
        "duration": "⏱️ Trukmė",
        "diff_client": "📐 Skirtumas nuo kliento",
        "km_client": "{km} km kliento",
        "country_maut": "🌍 KM pagal šalis ir kelių mokesčiai ({euro_class})",
        "transport_sum": "🚛 Transporto suma",
        "maut_sum": "🛣️ Kelių mokesčiai (Maut)",
        "grand_total": "💰 BENDRA SUMA",
        "client_km_sum": "📄 Kliento km suma",
        "map_header": "🗺️ Maršrutas žemėlapyje",
        "report_header": "📥 Ataskaita",
        "btn_download_pdf": "📄 Atsisiųsti PDF ataskaitą",
        "pdf_error": "PDF generavimo klaida: {e}",
        "pdf_title": "Marsruto KM Ataskaita",
        "pdf_page": "Puslapis",
        "pdf_summary": "SUVESTINE",
        "pdf_stops": "STOTELIU LENTELE",
        "pdf_country_maut": "KM PAGAL SALIS IR KELIU MOKESCIAI",
        "pdf_total_km": "Is viso km (keliais):",
        "pdf_duration": "Trukme:",
        "pdf_transport": "Transporto suma:",
        "pdf_maut": "Keliu mokesciai (Maut):",
        "pdf_grand_total": "BENDRA SUMA:",
        "pdf_client_km": "Kliento km:",
        "pdf_address": "Adresas",
        "pdf_coords": "Koordinates",
        "pdf_to_next": "Iki sekancio (km)",
        "pdf_cumulative": "Kaupiamasis (km)",
        "pdf_country": "Salis",
        "pdf_km": "KM",
        "pdf_maut_km": "Maut EUR/km",
        "pdf_maut_eur": "Maut suma EUR",
        "pdf_transport_eur": "Transport. suma EUR",
        "pdf_total_row": "VISO",
        "pdf_header_subtitle": "Sugeneruota: {date}  |  Euro klase: {euro_class}  |  Kaina/km: {price:.2f} EUR",
        "pdf_map_title": "Maršrutas žemėlapyje",
        "pdf_nr": "Nr.",
        "pdf_maut_note": "Euro klase: {euro_class}",
        "pdf_client_km_diff": "skirtumas: {sign}{diff:.1f} km",
    },
    "EN": {
        "landing_description": (
            "Transport companies often need to verify client freight invoices. "
            "Traditionally this means — manually entering each address into Google Maps, multiplying km by rate, "
            "calculating road tolls... for every single trip.\n\n"
            "RouteCalc lets you simply copy-paste an address line from Excel and instantly get: km by country, "
            "Maut road tolls by truck Euro class, and a PDF report to compare against client credit note.\n\n"
            "⚠️ **Important:** This tool is designed for quick verification against client credit notes after a completed trip — not for journey planning. "
            "Road toll rates are based on publicly available official data and may vary slightly depending on the specific motorway section or season. "
            "Minor distance deviations (~1-3%) may occur due to routing differences."
        ),
        "how_it_works": (
            "Transport companies often need to verify client freight invoices. "
            "Traditionally this means — manually entering each address into Google Maps, multiplying km by rate, "
            "calculating road tolls... for every single trip.\n\n"
            "RouteCalc lets you simply copy-paste an address line from Excel and instantly get: km by country, "
            "Maut road tolls by truck Euro class, and a PDF report to compare against client credit note.\n\n"
            "⚠️ **Important:** This tool is designed for quick verification against client credit notes after a completed trip — not for journey planning. "
            "Road toll rates are based on publicly available official data and may vary slightly depending on the specific motorway section or season. "
            "Minor distance deviations (~1-3%) may occur due to routing differences."
        ),
        "disclaimer": "⚠️ Distances are calculated based on map data. Minor deviations (~1-3%) may occur due to routing differences.",
        "landing_title": "🗺️ RouteCalc – Route KM Calculator",
        "pricing": "Pricing",
        "trial_plan": "Free trial",
        "trial_days": "7 days",
        "trial_desc": "Full access, 10 routes limit.",
        "btn_trial": "Start free 7-day trial",
        "monthly_plan": "Monthly",
        "monthly_price": "19 EUR/mo",
        "monthly_desc": "Unlimited routes.",
        "btn_monthly": "Monthly 19 EUR/mo",
        "yearly_plan": "Yearly",
        "yearly_price": "149 EUR/year",
        "yearly_desc": "Save ~35%.",
        "btn_yearly": "Yearly 149 EUR/year",
        "after_payment": "After payment you will be redirected back to the app.",
        "continue_with_email": "Already have a subscription? Continue with email",
        "email_placeholder": "email",
        "continue_btn": "Continue",
        "email_not_found": "No active subscription found for this email. Check the address or purchase a plan.",
        "enter_email": "Please enter your email.",
        "footer_contact": "📧 For inquiries and invoices: vikteko@gmail.com",
        "error_stripe": "Failed to start. Check STRIPE_* variables.",
        "expired_title": "🗺️ RouteCalc",
        "expired_warning": "Subscription expired.",
        "btn_manage_expired": "Manage subscription (renew, change plan)",
        "error_portal": "Failed to open portal.",
        "redirecting": "Redirecting to payment...",
        "trial_days_left": "Trial: {days_left} days left",
        "pro_monthly": "PRO Monthly",
        "pro_yearly": "PRO Yearly",
        "routes_used": "Routes used: {used}/{limit}",
        "btn_manage": "Manage subscription",
        "main_title": "🗺️ Route KM Calculator",
        "main_caption": "Paste addresses → km by country → transport and road toll calculation",
        "azure_error": "⚠️ AZURE_MAPS_KEY not set.",
        "trial_limit_msg": "You have used {limit} free routes. Upgrade to Pro.",
        "btn_upgrade": "Upgrade to Pro",
        "addresses_label": "📋 Addresses – one per line or full Excel row (Tab separated)",
        "client_price_label": "💶 Client price (EUR/km)",
        "client_price_help": "Price per km paid by client",
        "euro_class_label": "🚛 Truck Euro class",
        "euro_class_help": "Euro 6 = lowest road tolls",
        "client_km_label": "📄 Client stated km (for comparison)",
        "btn_calculate": "🧮 Calculate",
        "need_two": "At least 2 addresses required.",
        "geocoding": "📍 Geocoding addresses...",
        "geocoding_done": "✅ Geocoding complete",
        "not_found": "⚠️ Not found: {addr}",
        "not_enough": "Not enough addresses found.",
        "route_spinner": "🛣️ Calculating route...",
        "route_error": "Failed to get route from Azure Maps.",
        "segment_spinner": "📏 Calculating segment distances...",
        "country_spinner": "🌍 Splitting by country...",
        "stops_table": "📊 Stops table",
        "total_km": "📏 Total km",
        "duration": "⏱️ Duration",
        "diff_client": "📐 Difference from client",
        "km_client": "{km} km client",
        "country_maut": "🌍 KM by country and road tolls ({euro_class})",
        "transport_sum": "🚛 Transport cost",
        "maut_sum": "🛣️ Road tolls (Maut)",
        "grand_total": "💰 TOTAL",
        "client_km_sum": "📄 Client km total",
        "map_header": "🗺️ Route on map",
        "report_header": "📥 Report",
        "btn_download_pdf": "📄 Download PDF report",
        "pdf_error": "PDF generation error: {e}",
        "pdf_title": "Route KM Report",
        "pdf_page": "Page",
        "pdf_summary": "SUMMARY",
        "pdf_stops": "STOPS TABLE",
        "pdf_country_maut": "KM BY COUNTRY AND ROAD TOLLS",
        "pdf_total_km": "Total km (by road):",
        "pdf_duration": "Duration:",
        "pdf_transport": "Transport cost:",
        "pdf_maut": "Road tolls (Maut):",
        "pdf_grand_total": "TOTAL:",
        "pdf_client_km": "Client km:",
        "pdf_address": "Address",
        "pdf_coords": "Coordinates",
        "pdf_to_next": "To next (km)",
        "pdf_cumulative": "Cumulative (km)",
        "pdf_country": "Country",
        "pdf_km": "KM",
        "pdf_maut_km": "Maut EUR/km",
        "pdf_maut_eur": "Maut amount EUR",
        "pdf_transport_eur": "Transport amount EUR",
        "pdf_total_row": "TOTAL",
        "pdf_header_subtitle": "Generated: {date}  |  Euro class: {euro_class}  |  Price/km: {price:.2f} EUR",
        "pdf_map_title": "Route map",
        "pdf_nr": "No.",
        "pdf_maut_note": "Euro class: {euro_class}",
        "pdf_client_km_diff": "difference: {sign}{diff:.1f} km",
    },
}

if st.session_state.get("pending_redirect_url"):
    url = st.session_state["pending_redirect_url"]
    st.session_state["pending_redirect_url"] = None
    st.markdown(
        f'<meta http-equiv="refresh" content="0;url={url}"/>',
        unsafe_allow_html=True,
    )
    st.info(_t("redirecting"))
    st.stop()

# ─────────────────────────────────────────────
# Stripe auth (be DB – Stripe saugo viską)
# ─────────────────────────────────────────────

def _get_subscription_info(customer_id: str):
    """Returns (is_active, badge_key, badge_kwargs, subscription_obj|None). Badge is built in UI with _t(badge_key, **badge_kwargs)."""
    if not STRIPE_SECRET_KEY or not customer_id:
        return False, None, None, None
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        subs = stripe.Subscription.list(
            customer=customer_id,
            status="all",
            expand=["data.items.data.price"],
            limit=5,
        )
        for sub in subs.get("data", []):
            if sub["status"] in ("active", "trialing"):
                trial_end = sub.get("trial_end")
                interval = None
                for item in sub.get("items", {}).get("data", []):
                    price = item.get("price") or {}
                    rec = price.get("recurring") or {}
                    interval = rec.get("interval")
                    break
                if sub["status"] == "trialing" and trial_end:
                    end = datetime.datetime.utcfromtimestamp(trial_end).date()
                    days_left = max(0, (end - datetime.datetime.utcnow().date()).days)
                    return True, "trial_days_left", {"days_left": days_left}, sub
                if interval == "month":
                    return True, "pro_monthly", {}, sub
                if interval == "year":
                    return True, "pro_yearly", {}, sub
                return True, "pro_monthly", {}, sub  # fallback
        return False, None, None, None
    except stripe.StripeError:
        return False, None, None, None


def _handle_checkout_redirect():
    """Iš URL ?session_id=... ištraukia customer_id, email ir subscription status; išsaugo į session_state."""
    session_id = st.query_params.get("session_id")
    if not session_id or not STRIPE_SECRET_KEY:
        return
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        session = stripe.checkout.Session.retrieve(
            session_id, expand=["customer", "subscription"]
        )
        cid = session.get("customer")
        if isinstance(cid, dict):
            cid = cid.get("id")
        if cid:
            st.session_state["stripe_customer_id"] = cid
            email = (session.get("customer_details") or {}).get("email")
            if not email and isinstance(session.get("customer"), dict):
                email = (session.get("customer") or {}).get("email")
            if not email and cid:
                cust = stripe.Customer.retrieve(cid)
                email = getattr(cust, "email", None) or (cust.get("email") if isinstance(cust, dict) else None)
            if email:
                st.session_state["stripe_customer_email"] = email
            sub = session.get("subscription")
            if isinstance(sub, dict) and sub.get("status") in ("active", "trialing"):
                st.session_state["subscription_status"] = sub.get("status")
    except stripe.StripeError:
        pass


def _create_checkout_session(plan: str) -> str | None:
    """Sukuria Stripe Checkout Session ir grąžina redirect URL. plan: trial | monthly | yearly."""
    if not STRIPE_SECRET_KEY or not APP_BASE_URL:
        return None
    plan = plan.lower().strip()
    if plan not in ("trial", "monthly", "yearly"):
        return None
    if plan == "trial":
        price_id = STRIPE_PRICE_TRIAL or STRIPE_PRICE_MONTHLY
    elif plan == "monthly":
        price_id = STRIPE_PRICE_MONTHLY
    else:
        price_id = STRIPE_PRICE_YEARLY
    if not price_id:
        return None
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        kwargs = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{APP_BASE_URL}/?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": APP_BASE_URL + "/",
            "allow_promotion_codes": True,
        }
        if plan == "trial":
            kwargs["subscription_data"] = {"trial_period_days": 7}
        session = stripe.checkout.Session.create(**kwargs)
        return session.url
    except stripe.StripeError:
        return None


def _create_portal_session(customer_id: str) -> str | None:
    """Sukuria Stripe Customer Portal session ir grąžina redirect URL."""
    if not STRIPE_SECRET_KEY or not APP_BASE_URL or not customer_id:
        return None
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=APP_BASE_URL + "/",
        )
        return session.url
    except stripe.StripeError:
        return None


def _is_subscribed_and_badge():
    """
    Tikrina subscription kiekviename puslapio užkrovime.
    Returns: (show_calculator: bool, badge_text: str|None, subscription_obj|None).
    """
    if "stripe_customer_id" not in st.session_state:
        st.session_state["stripe_customer_id"] = None
    if "stripe_customer_email" not in st.session_state:
        st.session_state["stripe_customer_email"] = None
    if "subscription_status" not in st.session_state:
        st.session_state["subscription_status"] = None
    if "routes_used" not in st.session_state:
        st.session_state["routes_used"] = 0

    _handle_checkout_redirect()
    customer_id = st.session_state.get("stripe_customer_id")
    if not customer_id:
        return False, None, None, None

    is_active, badge_key, badge_kwargs, sub = _get_subscription_info(customer_id)
    if is_active:
        if sub and sub.get("status") in ("active", "trialing"):
            st.session_state["subscription_status"] = sub.get("status")
        return True, badge_key, badge_kwargs, sub
    return False, None, None, None


def _find_customer_by_email(email: str) -> tuple[str, str] | None:
    """Returns (customer_id, email) if a Stripe customer with this email has an active/trialing subscription, else None."""
    if not STRIPE_SECRET_KEY or not (email or "").strip():
        return None
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        customers = stripe.Customer.list(email=email.strip(), limit=5)
        for c in customers.get("data", []):
            cid = c.get("id")
            if not cid:
                continue
            is_active, _, _, _ = _get_subscription_info(cid)
            if is_active:
                return (cid, c.get("email") or email.strip())
        return None
    except stripe.StripeError:
        return None


def _render_landing():
    """Landing page with language switcher, description, disclaimer and pricing."""
    # Language switcher top left
    lang_col, _ = st.columns([1, 5])
    with lang_col:
        lt_btn, en_btn = st.columns(2)
        with lt_btn:
            if st.button("LT", key="lang_lt", use_container_width=True):
                st.session_state["lang"] = "LT"
                st.rerun()
        with en_btn:
            if st.button("EN", key="lang_en", use_container_width=True):
                st.session_state["lang"] = "EN"
                st.rerun()
    _inject_lang_switcher_css()
    st.title(_t("landing_title"))
    st.markdown(_t("landing_description"))
    st.divider()
    st.markdown(f"**{_t('continue_with_email')}**")
    email_col, btn_col = st.columns([2, 1])
    with email_col:
        login_email = st.text_input(
            _t("email_placeholder"),
            key="landing_email_input",
            placeholder="you@example.com",
            label_visibility="collapsed",
        )
    with btn_col:
        st.write("")
        st.write("")
        if st.button(_t("continue_btn"), key="landing_continue_btn", use_container_width=True):
            if login_email and login_email.strip():
                result = _find_customer_by_email(login_email.strip())
                if result:
                    cid, em = result
                    st.session_state["stripe_customer_id"] = cid
                    st.session_state["stripe_customer_email"] = em
                    st.rerun()
                else:
                    st.error(_t("email_not_found"))
            else:
                st.warning(_t("enter_email"))
    st.divider()
    st.subheader(_t("pricing"))
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"#### {_t('trial_plan')}")
        st.markdown(f"**{_t('trial_days')}**")
        st.markdown(_t("trial_desc"))
        if st.button(_t("btn_trial"), type="primary", key="btn_trial", use_container_width=True):
            url = _create_checkout_session("trial")
            if url:
                st.session_state["pending_redirect_url"] = url
                st.rerun()
            else:
                st.error(_t("error_stripe"))
    with col2:
        st.markdown(f"#### {_t('monthly_plan')}")
        st.markdown(f"**{_t('monthly_price')}**")
        st.markdown(_t("monthly_desc"))
        if st.button(_t("btn_monthly"), key="btn_monthly", use_container_width=True):
            url = _create_checkout_session("monthly")
            if url:
                st.session_state["pending_redirect_url"] = url
                st.rerun()
            else:
                st.error(_t("error_stripe"))
    with col3:
        st.markdown(f"#### {_t('yearly_plan')}")
        st.markdown(f"**{_t('yearly_price')}**")
        st.markdown(_t("yearly_desc"))
        if st.button(_t("btn_yearly"), key="btn_yearly", use_container_width=True):
            url = _create_checkout_session("yearly")
            if url:
                st.session_state["pending_redirect_url"] = url
                st.rerun()
            else:
                st.error(_t("error_stripe"))
    st.divider()
    st.caption(_t("after_payment"))


def _render_subscription_expired(customer_id: str):
    """Subscription expired – show portal button."""
    st.title(_t("expired_title"))
    st.warning(_t("expired_warning"))
    if st.button(_t("btn_manage_expired"), type="primary", key="btn_portal_expired"):
        url = _create_portal_session(customer_id)
        if url:
            st.session_state["pending_redirect_url"] = url
            st.rerun()
        else:
            st.error(_t("error_portal"))
    st.stop()


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

# Atnaujinta: 2025-03 | Šaltiniai: Toll Collect (DE), Viapass (BE), ASFINAG (AT), Vejdirektoratet (DK)
# DK: km mokestis nuo 2025-01-01 (pakeitė Eurovignetą)
# Sekantis peržiūrėjimas: 2026-01
# Kelių mokesčiai (Maut) EUR/km pagal šalį ir Euro klasę (>7.5t, 4+ ašys)
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
    "DK": {"Euro 6": 0.139, "Euro 5": 0.162, "Euro 4": 0.181, "Euro 3": 0.200, "Euro 2-": 0.220},
    # Šalys be Maut (vinjetas arba nėra) – tarifas 0
    "NL": {"Euro 6": 0.0, "Euro 5": 0.0, "Euro 4": 0.0, "Euro 3": 0.0, "Euro 2-": 0.0},
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


def get_azure_static_map(path_coords: list, width: int = 1024, height: int = 768) -> bytes | None:
    """Fetch a static PNG map image from Azure Maps with the route path drawn. path_coords: list of [lon, lat]. Returns PNG bytes or None."""
    if not path_coords or len(path_coords) < 2 or not AZURE_MAPS_KEY:
        return None
    # API limits 100 points per path; sample evenly if needed
    max_points = 100
    if len(path_coords) > max_points:
        step = (len(path_coords) - 1) / (max_points - 1)
        path_coords = [path_coords[int(i * step)] for i in range(max_points)]
    lons = [p[0] for p in path_coords]
    lats = [p[1] for p in path_coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    # Small padding for bbox
    pad_lon = max(0.1, (max_lon - min_lon) * 0.05)
    pad_lat = max(0.05, (max_lat - min_lat) * 0.05)
    bbox = f"{min_lon - pad_lon},{min_lat - pad_lat},{max_lon + pad_lon},{max_lat + pad_lat}"
    path_str = "|".join(f"{lon} {lat}" for lon, lat in path_coords)
    path_param = f"lc4682B4|lw4|la0.8||{path_str}"  # blue line, width 4
    params = {
        "api-version": "2024-04-01",
        "subscription-key": AZURE_MAPS_KEY,
        "bbox": bbox,
        "width": width,
        "height": height,
        "path": path_param,
    }
    try:
        r = requests.get(f"{BASE_URL}/map/static", params=params, timeout=15)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


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
                 client_price_per_km, euro_class, lang: str = "LT",
                 static_map_png: bytes | None = None) -> bytes:
    L = _TEXTS.get(lang, _TEXTS["LT"])
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf_title = L["pdf_title"]
    pdf_subtitle = L["pdf_header_subtitle"].format(date=date_str, euro_class=euro_class, price=client_price_per_km)
    pdf_page_label = L["pdf_page"]

    class PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 13)
            self.set_fill_color(40, 80, 150)
            self.set_text_color(255, 255, 255)
            self.cell(0, 10, _safe(pdf_title), align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
            self.set_font("Helvetica", "", 8)
            self.cell(0, 6, _safe(pdf_subtitle), new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            self.cell(0, 10, f"{_safe(pdf_page_label)} {self.page_no()}", align="C")

    pdf = PDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_left_margin(10)
    pdf.set_right_margin(10)

    # ── Suvestinė ──
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 240, 255)
    pdf.cell(0, 7, L["pdf_summary"], fill=True, new_x="LMARGIN", new_y="NEXT")

    travel_h = int(full_route_min // 60)
    travel_m = int(full_route_min % 60)

    grand_label = L["pdf_grand_total"]
    summary_items = [
        (L["pdf_total_km"], f"{total_km:.1f} km"),
        (L["pdf_duration"], f"{travel_h}h {travel_m}min"),
        (L["pdf_transport"], f"{transport_cost:.2f} EUR  ({client_price_per_km:.2f} EUR/km x {total_km:.1f} km)"),
        (L["pdf_maut"], f"{maut_total:.2f} EUR  ({L['pdf_maut_note'].format(euro_class=euro_class)})"),
        (grand_label, f"{grand_total:.2f} EUR"),
    ]
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        summary_items.insert(2, (L["pdf_client_km"], f"{client_km} km  ({L['pdf_client_km_diff'].format(sign=sign, diff=diff)})"))

    for label, value in summary_items:
        is_total = label == grand_label
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
    pdf.cell(0, 7, L["pdf_stops"], fill=True, new_x="LMARGIN", new_y="NEXT")

    col_w = [10, 85, 45, 35, 35]
    headers = [L["pdf_nr"], L["pdf_address"], L["pdf_coords"], L["pdf_to_next"], L["pdf_cumulative"]]

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
    pdf.cell(0, 7, f"{L['pdf_country_maut']} ({euro_class})", fill=True, new_x="LMARGIN", new_y="NEXT")

    cc_col_w = [35, 30, 30, 30, 30]
    cc_headers = [L["pdf_country"], L["pdf_km"], L["pdf_maut_km"], L["pdf_maut_eur"], L["pdf_transport_eur"]]

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
        cc_label = _safe((L["pdf_total_row"] if row.get("total_row") else str(row["Salis"])).encode("ascii", "ignore").decode())
        pdf.cell(cc_col_w[0], 5.5, cc_label, border=1, fill=True, align="C")
        pdf.cell(cc_col_w[1], 5.5, _safe(row["KM"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[2], 5.5, _safe(row["Maut EUR/km"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[3], 5.5, _safe(row["Maut EUR"]), border=1, fill=True, align="C")
        pdf.cell(cc_col_w[4], 5.5, _safe(row["Transport. EUR"]), border=1, fill=True, align="C")
        pdf.ln()
        fill_row = not fill_row

    if static_map_png:
        pdf.add_page(orientation="L")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(230, 240, 255)
        pdf.cell(0, 7, L["pdf_map_title"], fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        try:
            img = io.BytesIO(static_map_png)
            # A4 landscape: 297mm x 210mm; use width 277mm to fit with margins
            pdf.image(img, w=277)
        except Exception:
            pass

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

show_calc, badge_key, badge_kwargs, subscription_obj = _is_subscribed_and_badge()
customer_id = st.session_state.get("stripe_customer_id")

if not show_calc and customer_id:
    _render_subscription_expired(customer_id)

if not show_calc:
    _render_landing()
    st.stop()

is_trial = subscription_obj and subscription_obj.get("status") == "trialing"
routes_used = st.session_state.get("routes_used", 0)
plan_badge = _t(badge_key, **badge_kwargs) if badge_key else None

# ── Prenumerata aktyvi: rodyti kalkuliatorių ──
if plan_badge:
    badge_col, portal_col, _ = st.columns([1, 1, 4])
    with badge_col:
        st.markdown(f"**{plan_badge}**")
        if is_trial:
            st.caption(_t("routes_used", used=routes_used, limit=TRIAL_ROUTE_LIMIT))
    with portal_col:
        if st.button(_t("btn_manage"), key="btn_portal", use_container_width=True):
            url = _create_portal_session(customer_id)
            if url:
                st.session_state["pending_redirect_url"] = url
                st.rerun()
            else:
                st.error(_t("error_portal"))

# Language switcher (calculator view) top left
lang_calc, _ = st.columns([1, 5])
with lang_calc:
    lt_c, en_c = st.columns(2)
    with lt_c:
        if st.button("LT", key="lang_lt_calc", use_container_width=True):
            st.session_state["lang"] = "LT"
            st.rerun()
    with en_c:
        if st.button("EN", key="lang_en_calc", use_container_width=True):
            st.session_state["lang"] = "EN"
            st.rerun()
_inject_lang_switcher_css()
st.title(_t("main_title"))
st.caption(_t("main_caption"))

if not AZURE_MAPS_KEY:
    st.error(_t("azure_error"))
    st.stop()

# Trial limitas
if is_trial and routes_used >= TRIAL_ROUTE_LIMIT:
    st.error(_t("trial_limit_msg", limit=TRIAL_ROUTE_LIMIT))
    if st.button(_t("btn_upgrade"), type="primary", key="btn_upgrade"):
        url = _create_portal_session(customer_id)
        if url:
            st.session_state["pending_redirect_url"] = url
            st.rerun()
        else:
            st.error(_t("error_portal"))
    st.stop()

# ── Įvestis ──
col_input, col_right = st.columns([3, 1])

with col_input:
    raw_text = st.text_area(
        _t("addresses_label"),
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
        _t("client_price_label"),
        min_value=0.0,
        value=1.16,
        step=0.01,
        format="%.2f",
        help=_t("client_price_help"),
    )
    euro_class = st.selectbox(
        _t("euro_class_label"),
        options=EURO_CLASSES,
        index=0,
        help=_t("euro_class_help"),
    )
    client_km = st.number_input(_t("client_km_label"), min_value=0, value=0, step=10)
    st.write("")
    calculate = st.button(_t("btn_calculate"), type="primary", use_container_width=True)

st.divider()

# ── Skaičiavimas ──
if calculate and raw_text.strip():
    addresses = parse_addresses(raw_text)

    if len(addresses) < 2:
        st.warning(_t("need_two"))
        st.stop()

    # 1. Geocoding
    with st.status(_t("geocoding"), expanded=True) as status:
        geocode_results = []
        for addr in addresses:
            st.write(f"🔍 {addr}")
            result = geocode(addr)
            geocode_results.append((addr, result))
            if not result:
                st.warning(_t("not_found", addr=addr))
        status.update(label=_t("geocoding_done"), state="complete")

    valid_pairs = [(addr, r) for addr, r in geocode_results if r is not None]
    if len(valid_pairs) < 2:
        st.error(_t("not_enough"))
        st.stop()

    all_coords = [r for _, r in valid_pairs]

    # 2. Pilnas maršrutas
    with st.spinner(_t("route_spinner")):
        full_route = route_distance(all_coords)

    if not full_route:
        st.error(_t("route_error"))
        st.stop()

    if is_trial:
        st.session_state["routes_used"] = st.session_state.get("routes_used", 0) + 1

    total_km = full_route["distance_km"]
    path_coords = full_route["path_coords"]

    # 3. Segmentų atstumai
    with st.spinner(_t("segment_spinner")):
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
    with st.spinner(_t("country_spinner")):
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
    st.markdown(f"### {_t('stops_table')}")
    st.dataframe(pd.DataFrame(seg_rows), hide_index=True, use_container_width=True)

    m1, m2, m3 = st.columns(3)
    m1.metric(_t("total_km"), f"{total_km:.1f} km")
    m2.metric(_t("duration"), f"{int(full_route['travel_time_min']//60)}h {int(full_route['travel_time_min']%60)}min")
    if client_km > 0:
        diff = total_km - client_km
        sign = "+" if diff > 0 else ""
        m3.metric(_t("diff_client"), f"{sign}{diff:.1f} km", delta=_t("km_client", km=client_km), delta_color="off")

    st.divider()
    st.markdown(f"### {_t('country_maut', euro_class=euro_class)}")

    display_rows = [
        {k: (_t("pdf_total_row") if k == "Salis" and r.get("total_row") else v) for k, v in r.items() if k != "total_row"}
        for r in maut_rows
    ]
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(_t("transport_sum"), f"{transport_cost:.2f} EUR",
              help=f"{client_price_per_km:.2f} EUR/km × {total_km:.1f} km")
    c2.metric(_t("maut_sum"), f"{maut_total:.2f} EUR",
              help=f"{euro_class}")
    c3.metric(_t("grand_total"), f"{grand_total:.2f} EUR")
    if client_km > 0:
        client_transport = round(client_km * client_price_per_km, 2)
        c4.metric(_t("client_km_sum"), f"{client_transport:.2f} EUR",
                  help=f"{client_km} km × {client_price_per_km:.2f} EUR/km")

    # ── Žemėlapis ──
    st.divider()
    st.markdown(f"### {_t('map_header')}")

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
    st.markdown(f"### {_t('report_header')}")
    try:
        static_map_png = get_azure_static_map(path_coords)
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
            lang=st.session_state.get("lang", "LT"),
            static_map_png=static_map_png,
        )
        fname = f"marsrutas_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        st.download_button(
            label=_t("btn_download_pdf"),
            data=pdf_bytes,
            file_name=fname,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(_t("pdf_error", e=str(e)))

st.caption(_t("footer_contact"))
