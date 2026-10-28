import hashlib
import json
from pathlib import Path
import requests
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf
from supabase import Client, create_client

st.set_page_config(page_title="ETF Trend Dashboard", page_icon="📈", layout="wide")
st.title("ETF Trend Dashboard")
st.caption("Confronta l’andamento degli ETF. I prezzi sono ricalcolati a 100 all’inizio del periodo scelto.")

DEFAULT_TICKERS = ["RBOT.MI", "VWCE.MI", "IVDF.MI", "VALL.AS", "IBCI.MI", "CSBGE3.MI", "XEQ6.MI"]
PERIODS = {
    "1 mese": "1mo",
    "3 mesi": "3mo",
    "6 mesi": "6mo",
    "Da inizio anno": "ytd",
    "1 anno": "1y",
    "5 anni": "5y",
    "Massimo disponibile": "max",
}
CATEGORIES = ["Azionario", "Obbligazionario", "Oro/commodity", "Liquidità", "Multiasset", "Altro"]
CATEGORY_COLORS = {
    "Azionario": "#2563eb",
    "Obbligazionario": "#16a34a",
    "Oro/commodity": "#d97706",
    "Liquidità": "#0891b2",
    "Multiasset": "#ebd725",
    "Altro": "#7c3aed",
}
DEFAULT_CATEGORIES = {
    "RBOT.MI": "Azionario", "VWCE.MI": "Azionario", "IVDF.MI": "Azionario",
    "VALL.AS": "Azionario", "IBCI.MI": "Obbligazionario",
    "CSBGE3.MI": "Obbligazionario", "XEQ6.MI": "Multiasset",
}

def category_shades(category: str, count: int) -> list[str]:
    base = CATEGORY_COLORS[category].lstrip("#")
    rgb = tuple(int(base[index:index + 2], 16) for index in (0, 2, 4))
    white_mix = [0.66 - (0.66 * index / max(count - 1, 1)) for index in range(count)]
    shades = []
    for mix in white_mix:
        shade = tuple(round(channel * (1 - mix) + 255 * mix) for channel in rgb)
        shades.append("#" + "".join(f"{channel:02x}" for channel in shade))
    return shades

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_ROLE_KEY"])

def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def load_user_profile(client: Client, token: str):
    result = (
        client.table("user_profiles")
        .select("id, display_name, tickers")
        .eq("token_hash", token_digest(token))
        .eq("active", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None

def load_user_settings(client: Client, user_id: str) -> dict:
    result = (
        client.table("etf_settings")
        .select("ticker, category, pmc")
        .eq("user_id", user_id)
        .execute()
    )
    return {row["ticker"]: row for row in (result.data or [])}

def save_user_settings(client: Client, user_id: str, tickers: tuple[str, ...], categories: dict, pmc_values: dict):
    rows = []
    for ticker in tickers:
        rows.append({
            "user_id": user_id,
            "ticker": ticker,
            "category": categories.get(ticker, "Altro"),
            "pmc": float(pmc_values.get(ticker, 0.0)),
        })
    if rows:
        client.table("etf_settings").upsert(rows, on_conflict="user_id,ticker").execute()

@st.cache_data(ttl=3600, show_spinner=False)
def load_prices(tickers: tuple[str, ...], period: str) -> pd.DataFrame:
    series = []
    for ticker in tickers:
        try:
            history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if not history.empty and "Close" in history:
                close = history["Close"].dropna().rename(ticker)
                if not close.empty:
                    series.append(close)
        except Exception:
            continue
    if not series:
        return pd.DataFrame()
    return pd.concat(series, axis=1).sort_index()

# @st.cache_data(ttl=86400, show_spinner=False)
# def load_fund_names(tickers: tuple[str, ...]) -> dict[str, str]:
    # names = {}
    # for ticker in tickers:
        # try:
            # info = yf.Ticker(ticker).info
            # name = info.get("shortName") or info.get("longName")
            # names[ticker] = str(name).strip() if name else "Nome non disponibile"
        # except Exception:
            # names[ticker] = "Nome non disponibile"
    # return names

@st.cache_data(ttl=86400, show_spinner=False)
def load_fund_names(tickers: tuple[str, ...]) -> dict[str, str]:
    names = {}

    for ticker in tickers:
        try:
            response = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={
                    "q": ticker,
                    "quotesCount": 10,
                    "newsCount": 0,
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()

            quotes = response.json().get("quotes", [])

            # Prima prova a trovare il simbolo esatto.
            match = next(
                (
                    item for item in quotes
                    if item.get("symbol", "").upper() == ticker.upper()
                ),
                None,
            )

            # Se non è presente, cerca un risultato classificato come ETF.
            if match is None:
                match = next(
                    (
                        item for item in quotes
                        if item.get("quoteType") == "ETF"
                    ),
                    None,
                )

            name = (
                (match or {}).get("shortname")
                or (match or {}).get("longname")
                or (match or {}).get("displayName")
            )

            names[ticker] = str(name).strip() if name else ticker

        except Exception:
            # Se la ricerca fallisce, il dashboard continua a funzionare.
            names[ticker] = ticker

    return names

# Accesso tramite link personale. Il token completo non viene salvato nel database.
token = st.query_params.get("access", "")
if isinstance(token, list):
    token = token[0] if token else ""
if not token:
    st.error("Link personale mancante. Chiedi all’amministratore un link di accesso.")
    st.stop()

try:
    db = get_supabase()
    profile = load_user_profile(db, token)
except Exception:
    st.error("Non riesco a collegarmi al database. Riprova più tardi o contatta l’amministratore.")
    st.stop()

if not profile:
    st.error("Link non valido o disattivato. Chiedi all’amministratore un nuovo link.")
    st.stop()

user_id = profile["id"]
display_name = profile.get("display_name") or "Utente"
st.caption(f"Profilo: {display_name}")

saved_settings = load_user_settings(db, user_id)
saved_tickers = profile.get("tickers") or DEFAULT_TICKERS
saved_tickers = list(dict.fromkeys(str(t).strip().upper() for t in saved_tickers if str(t).strip()))

with st.sidebar:
    st.header("Impostazioni")
    with st.form("ticker_form"):
        ticker_text = st.text_input(
            "Ticker Yahoo Finance (separati da virgola)",
            value=", ".join(saved_tickers),
            help="Esempio: VWCE.DE, SWDA.MI",
        )
        save_tickers = st.form_submit_button("Salva ticker", type="primary", use_container_width=True)
    if save_tickers:
        new_tickers = list(dict.fromkeys(part.strip().upper() for part in ticker_text.split(",") if part.strip()))
        if not new_tickers:
            st.warning("Inserisci almeno un ticker.")
        elif len(new_tickers) > 12:
            st.warning("Puoi inserire al massimo 12 ticker.")
        else:
            try:
                db.table("user_profiles").update({"tickers": new_tickers}).eq("id", user_id).execute()
                st.rerun()
            except Exception:
                st.error("Non è stato possibile salvare i ticker.")
    st.caption("I dati di mercato provengono da Yahoo Finance; la disponibilità può variare per ticker.")

tickers = tuple(saved_tickers)
if not tickers:
    st.info("Inserisci almeno un ticker nelle impostazioni.")
    st.stop()
if len(tickers) > 12:
    st.warning("Per mantenere il grafico leggibile, inserisci al massimo 12 ticker.")
    st.stop()

with st.sidebar.expander("Categorie degli ETF", expanded=True):
    st.caption("Le categorie sono salvate nel tuo profilo.")
    categories = {}
    for ticker in tickers:
        default_category = saved_settings.get(ticker, {}).get("category") or DEFAULT_CATEGORIES.get(ticker, "Altro")
        state_key = f"category_{user_id}_{ticker}"
        if state_key not in st.session_state:
            st.session_state[state_key] = default_category if default_category in CATEGORIES else "Altro"
        categories[ticker] = st.selectbox(ticker, CATEGORIES, key=state_key)

with st.sidebar.expander("Prezzo medio di carico (PMC)", expanded=True):
    st.caption("Il PMC è personale ed è salvato nel tuo profilo.")
    pmc_values = {}
    for ticker in tickers:
        try:
            default_pmc = float(saved_settings.get(ticker, {}).get("pmc") or 0.0)
        except (TypeError, ValueError):
            default_pmc = 0.0
        pmc_key = f"pmc_{user_id}_{ticker}"
        if pmc_key not in st.session_state:
            st.session_state[pmc_key] = default_pmc
        pmc_values[ticker] = st.number_input(
            f"PMC {ticker}", min_value=0.0, step=0.01, format="%.2f", key=pmc_key,
            help="Prezzo medio pagato per una quota, nella valuta della quotazione.",
        )

# Persistenza server-side. Le righe vengono sempre associate all'ID del profilo validato.
try:
    save_user_settings(db, user_id, tickers, categories, pmc_values)
except Exception:
    st.warning("Non è stato possibile salvare alcune impostazioni. Controlla la connessione e riprova.")

with st.sidebar:
    period_label = st.selectbox("Periodo", list(PERIODS.keys()), index=4)
    refresh = st.button("Aggiorna dati", type="primary", use_container_width=True)
if refresh:
    load_prices.clear()
    load_fund_names.clear()

with st.spinner("Caricamento dei dati da Yahoo Finance…"):
    prices = load_prices(tickers, PERIODS[period_label])

if prices.empty:
    st.error("Non sono riuscito a scaricare dati. Controlla i ticker e la connessione Internet.")
    st.stop()

missing = [ticker for ticker in tickers if ticker not in prices.columns]
if missing:
    st.warning("Nessun dato trovato per: " + ", ".join(missing) + ". Verifica i simboli Yahoo Finance.")

normalized = prices.copy()
for ticker in normalized.columns:
    valid = normalized[ticker].dropna()
    if not valid.empty and valid.iloc[0] != 0:
        normalized[ticker] = normalized[ticker] / valid.iloc[0] * 100

ticker_colors = {}
for category in CATEGORIES:
    members = [ticker for ticker in normalized.columns if categories.get(ticker, "Altro") == category]
    shades = category_shades(category, len(members))
    ticker_colors.update(zip(members, shades))

st.subheader("ETF da visualizzare")
toggle_cols = st.columns(len(normalized.columns))
visible_tickers = []
for column, ticker in zip(toggle_cols, normalized.columns):
    if column.checkbox(ticker, value=True, key=f"visible_{user_id}_{ticker}"):
        visible_tickers.append(ticker)

if visible_tickers:
    fig = px.line(
        normalized[visible_tickers],
        x=normalized.index,
        y=visible_tickers,
        labels={"x": "Data", "value": "Indice (inizio = 100)", "variable": "ETF"},
        title=f"Andamento relativo — {period_label.lower()}",
        color_discrete_map=ticker_colors,
    )
    fig.update_layout(hovermode="x unified", legend_title_text="ETF", margin=dict(l=10, r=10, t=55, b=10))
    fig.update_yaxes(title="Prezzo normalizzato (100 = inizio)")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Attiva almeno un ETF per visualizzarne il grafico.")

latest = prices.ffill().iloc[-1]
returns = {}
for ticker in prices.columns:
    valid = prices[ticker].dropna()
    if not valid.empty and valid.iloc[0] != 0:
        returns[ticker] = (latest[ticker] / valid.iloc[0] - 1) * 100
fund_names = load_fund_names(tuple(prices.columns))
cols = st.columns(max(1, len(returns)))
for col, (ticker, change) in zip(cols, returns.items()):
    category = categories.get(ticker, "Altro")
    with col.container(border=True):
        st.metric(
            ticker,
            f"{latest[ticker]:.2f}",
            f"{change:+.2f}% nel periodo"
        )

        first_price = prices[ticker].dropna().iloc[0]
        normalized_quote = latest[ticker] / first_price * 100
        pmc = pmc_values.get(ticker, 0.0)

        if pmc > 0:
            normalized_pmc = pmc / first_price * 100
            distance_from_pmc = (latest[ticker] / pmc - 1) * 100

            st.caption(
                f"PMC standardizzato: {normalized_pmc:.2f} · "
                f"prezzo attuale: {normalized_quote:.2f} "
                f"({distance_from_pmc:+.2f}% dal PMC)"
            )
        else:
            st.caption(
                f"PMC standardizzato: inseriscilo nella barra laterale · "
                f"prezzo attuale: {normalized_quote:.2f}"
            )

        st.caption(
            fund_names.get(ticker, "Nome non disponibile")
        )

        st.markdown(
            f'<span style="color:{ticker_colors.get(ticker, CATEGORY_COLORS["Altro"])};'
            f'font-weight:600">● {category}</span>',
            unsafe_allow_html=True,
        )

    # with col.container(border=True):
        # col.metric(ticker, f"{latest[ticker]:.2f}", f"{change:+.2f}% nel periodo")
        # first_price = prices[ticker].dropna().iloc[0]
        # normalized_quote = latest[ticker] / first_price * 100
        # pmc = pmc_values.get(ticker, 0.0)
        # if pmc > 0:
            # normalized_pmc = pmc / first_price * 100
            # distance_from_pmc = (latest[ticker] / pmc - 1) * 100
            # col.caption(f"PMC standardizzato: {normalized_pmc:.2f} · prezzo attuale: {normalized_quote:.2f} ({distance_from_pmc:+.2f}% dal PMC)")
        # else:
            # col.caption(f"PMC standardizzato: inseriscilo nella barra laterale · prezzo attuale: {normalized_quote:.2f}")
        # col.caption(fund_names.get(ticker, "Nome non disponibile"))
        # col.markdown(
            # f'<span style="color:{ticker_colors.get(ticker, CATEGORY_COLORS["Altro"])};font-weight:600">● {category}</span>',
            # unsafe_allow_html=True,
        # )

st.caption("Prezzi rettificati per dividendi e operazioni societarie (auto-adjust). Dati indicativi, non in tempo reale.")
