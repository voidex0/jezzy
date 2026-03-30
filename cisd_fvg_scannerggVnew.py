import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import time
import csv
import os
from datetime import datetime

# ─────────────────────────────────────────────
#  AYARLAR
# ─────────────────────────────────────────────
API_KEY      = "83b7477b9016ae69f96052816ae019a8-2757873f181fb92fbdac05c132d640dc"
PARITELER    = ["EUR_USD", "GBP_USD", "XAU_USD", "USD_CHF", "BTC_USD","NAS100_USD","EUR_JPY","USD_CAD",
                 "SPX500_USD","DE30_EUR","XAG_USD","GBP_JPY","EUR_CHF"]
ZAMAN_DILIMLERI = ["M15", "H1","H4","D"]
CANDLE_COUNT = 200          # Daha fazla mum → daha iyi swing tespiti
SWING_LOOKBACK = 6         # Swing high/low için kaç mum bakılacak (her yöne)
FVG_MAX_AGE  = 30           # FVG'nin geçerli sayılacağı maksimum mum sayısı
CSV_DOSYASI  = "sinyaller.csv"
GRAFIK_KAYDET = True        # True → PNG olarak kaydet, False → ekranda göster
MIN_SKOR     = 65           # Bu skorun altındaki sinyaller gösterilmez ve bildirim gönderilmez
SADECE_SON_SINYAL = True    # True → grafikte sadece en son sinyali göster

# ── KALİTE FİLTRELERİ ─────────────────────────────────────────────────────
EMA200_FILTRE    = True     # True → EMA200 trendi ile uyumsuz sinyalleri ele
MIN_RR           = 1.5      # Minimum Risk/Ödül oranı (altındaki sinyaller elenir)
MIN_FVG_ATR_ORA  = 0.2      # FVG en az ATR'nin bu oranı kadar büyük olmalı (0.2 = %20)
UTC_OFFSET = 3             # Türkiye saati +3UTC

# ── ÇALIŞMA SAATLERİ ──────────────────────────────────────────────────────
# Bot sadece bu saatler arasında tarama yapar (bilgisayarın yerel saati)
# Forex piyasası: 00:00 - 23:59 (hep açık), Hisse endeksleri: 09:30 - 22:00
CALISMA_SAATI_BASLANGIC = 9    # Başlangıç saati (örn: 9 → 09:00)
CALISMA_SAATI_BITIS     = 21   # Bitiş saati     (örn: 21 → 21:00)
TARAMA_ARALIGI_DK       = 15   # Kaç dakikada bir tarama yapılsın

# ── TELEGRAM AYARLARI ─────────────────────────────────────────────────────
# 1) @BotFather'a yaz → /newbot → TOKEN al
# 2) Bota bir mesaj at, sonra tarayicida su URL'i ac:
#    https://api.telegram.org/bot<TOKEN>/getUpdates
#    "chat":{"id": ...} kismindaki sayiyi CHAT_ID'ye yaz
TELEGRAM_TOKEN    = "8435456528:AAHiA8BM-5rMu-mqyMtTg29sRbHJSVnVb90"
TELEGRAM_CHAT_ID  = ["1542779257", "-1003547502811"]  # kişisel + kanal
TELEGRAM_AKTIF    = False   # False yapinca bildirim gonderilmez (sessiz mod)
BILDIRIM_COOLDOWN_DK = 60  # Ayni sembol+dilim icin kac dakikada bir tekrar bildirim

# ── TEST MODU ──────────────────────────────────────────────────────────────
# True → API'ye bağlanmadan sahte veri üretir, sinyal garantilidir
# False → Gerçek OANDA verisini kullanır
TEST_MODU = False


# ─────────────────────────────────────────────
#  VERİ ÇEKİMİ
# ─────────────────────────────────────────────
def get_data(instrument: str, granularity: str) -> pd.DataFrame:
    """OANDA'dan mum verisi çeker."""
    client = API(access_token=API_KEY, environment="practice")
    params = {"count": CANDLE_COUNT, "granularity": granularity}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)

    rows = []
    for candle in r.response["candles"]:
        if candle["complete"]:
            rows.append({
                "time":  candle["time"],
                "open":  float(candle["mid"]["o"]),
                "high":  float(candle["mid"]["h"]),
                "low":   float(candle["mid"]["l"]),
                "close": float(candle["mid"]["c"]),
            })
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.reset_index(drop=True)


def get_mock_data(instrument: str, granularity: str) -> pd.DataFrame:
    """
    API'ye bağlanmadan CISD+FVG sinyali garantilenmiş sahte veri üretir.
    97 yükselen mum + son 3 mumda BEARISH CISD+FVG örüntüsü yerleştirilir.
    """
    import numpy as np
    rng   = np.random.default_rng(seed=7)
    base  = {"EUR_USD": 1.0500, "GBP_USD": 1.2200, "XAU_USD": 2200.0,
             "USD_CHF": 0.8800, "BTC_USD": 60000.0}
    price = base.get(instrument, 1.1000)
    pip   = price * 0.0008
    rows  = []
    now   = pd.Timestamp.now("UTC").floor("min")
    freq  = {"M15": 15, "H1": 60, "H4": 240, "D": 1440}.get(granularity, 15)

    # 97 yükselen mum (uptrend)
    for k in range(97):
        ts = now - pd.Timedelta(minutes=freq * (100 - k))
        o  = price
        c  = price + rng.uniform(0.2, 0.8) * pip
        h  = c + rng.uniform(0.05, 0.20) * pip
        l  = o - rng.uniform(0.02, 0.08) * pip
        rows.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        price = c

    # i-2: güçlü boğa mumu (swing top yakınında)
    o2 = price;        c2 = price + pip * 1.5
    h2 = c2 + pip * 0.4;  l2 = o2 - pip * 0.05
    rows.append({"time": now - pd.Timedelta(minutes=freq * 2),
                 "open": o2, "high": h2, "low": l2, "close": c2})

    # i-1: küçük boğa
    o1 = c2;           c1 = c2 + pip * 0.3
    h1 = c1 + pip * 0.2;  l1 = o1 - pip * 0.05
    rows.append({"time": now - pd.Timedelta(minutes=freq),
                 "open": o1, "high": h1, "low": l1, "close": c1})

    # Swing low hesapla (end=idx → sadece geçmiş, find_swing_low ile aynı mantık)
    df_temp  = pd.DataFrame(rows)
    center   = len(df_temp) - 1                        # i-1 index
    s        = max(0, center - SWING_LOOKBACK)
    psw_low  = float(df_temp.loc[s:center, "low"].min())

    # i: büyük ayı — close < psw_low  +  FVG: l2 > high
    c0 = psw_low - pip * 3.0
    h0 = min(l2 - pip * 0.3, c1 - pip * 0.1)          # FVG garantisi
    l0 = c0 - pip * 0.2
    o0 = c1
    # now - 1dk: zaman filtresi sınırında kalmamak için 1 dakika geri al
    rows.append({"time": now - pd.Timedelta(minutes=1),
                 "open": o0, "high": h0, "low": l0, "close": c0})

    return pd.DataFrame(rows).reset_index(drop=True)


# ─────────────────────────────────────────────
#  YARDIMCI: SWING HIGH / LOW
# ─────────────────────────────────────────────
def find_swing_high(df: pd.DataFrame, idx: int, lookback: int = SWING_LOOKBACK) -> float:
    """idx'e kadar olan pencerede en yüksek high'ı döndürür (sadece geçmiş)."""
    start = max(0, idx - lookback)
    return float(df.loc[start:idx, "high"].max())


def find_swing_low(df: pd.DataFrame, idx: int, lookback: int = SWING_LOOKBACK) -> float:
    """idx'e kadar olan pencerede en düşük low'u döndürür (sadece geçmiş)."""
    start = max(0, idx - lookback)
    return float(df.loc[start:idx, "low"].min())


# ─────────────────────────────────────────────
#  YARDIMCI: FVG GEÇERLİLİK KONTROLÜ
# ─────────────────────────────────────────────
def is_fvg_still_valid(df: pd.DataFrame, fvg_top: float, fvg_bottom: float,
                        fvg_idx: int, direction: str) -> bool:
    """
    FVG bölgesi oluştuktan sonra fiyat o bölgeye geri döndü mü?
    Geri dönmediyse FVG hâlâ geçerlidir (doldurulmamış).
    """
    for j in range(fvg_idx + 1, len(df)):
        candle_high = float(df.iloc[j]["high"])
        candle_low  = float(df.iloc[j]["low"])
        if direction == "BEARISH":
            # Bearish FVG: fiyat yukarı çıkıp boşluğa girerse dolmuş sayılır
            if candle_high >= fvg_bottom:
                return False
        else:
            # Bullish FVG: fiyat aşağı inip boşluğa girerse dolmuş sayılır
            if candle_low <= fvg_bottom:
                return False
    return True


# ─────────────────────────────────────────────
#  SINYAL SKORU HESAPLAMA
# ─────────────────────────────────────────────
def calculate_score(df: pd.DataFrame, i: int, direction: str,
                    fvg_size: float, atr: float) -> dict:
    """
    0–100 arası puan + açıklama üretir.
    Kriterler:
      • FVG büyüklüğü (ATR'ye oranı)           → max 30 puan
      • CISD mumunun gövde büyüklüğü            → max 25 puan
      • Hacim proxy (gövde/fitil oranı)         → max 20 puan
      • FVG tazeliği (ne kadar önce oluştu)     → max 25 puan
    """
    curr = df.iloc[i]
    details = []
    score   = 0

    curr_close = float(curr["close"])
    curr_open  = float(curr["open"])
    curr_high  = float(curr["high"])
    curr_low   = float(curr["low"])

    # 1) FVG büyüklüğü
    fvg_ratio = fvg_size / atr if atr > 0 else 0.0
    fvg_pts   = min(30, int(fvg_ratio * 30))
    score    += fvg_pts
    details.append(f"FVG büyüklüğü ({fvg_ratio:.2f}× ATR) → +{fvg_pts}")

    # 2) CISD mumu gövdesi
    body     = abs(curr_close - curr_open)
    body_pts = min(25, int((body / atr) * 25)) if atr > 0 else 0
    score   += body_pts
    details.append(f"CISD gövdesi ({body/atr:.2f}× ATR) → +{body_pts}")

    # 3) Gövde / fitil oranı (güçlü kapanış göstergesi)
    total_range = curr_high - curr_low
    if total_range > 0:
        body_ratio = body / total_range
        vol_pts    = min(20, int(body_ratio * 20))
    else:
        body_ratio = 0.0
        vol_pts    = 0
    score    += vol_pts
    details.append(f"Gövde/fitil oranı ({body_ratio:.0%}) → +{vol_pts}")

    # 4) FVG tazeliği (i-2'deki FVG, ne kadar yakınsa o kadar iyi)
    # i-2 indeksi zaten en yakın FVG, ama ilerleyen taramalarda farklı olabilir
    freshness_pts = 25   # 3-mum yapısında her zaman taze
    score        += freshness_pts
    details.append(f"FVG tazeliği → +{freshness_pts}")

    # Kalite etiketi
    if score >= 75:
        label = "[***] GUCLU"
    elif score >= 50:
        label = "[**] ORTA"
    else:
        label = "[*] ZAYIF"

    return {"score": score, "score_label": label, "details": details}


# ─────────────────────────────────────────────
#  ANA ANALİZ: CISD + FVG
# ─────────────────────────────────────────────
def analyze_cisd_fvg(df: pd.DataFrame, granularity: str = "M15") -> list[dict]:
    """
    Geliştirilmiş CISD & FVG taraması.
    CISD: Fiyatın önceki swing high/low'u kırması.
    FVG : İki mum arasındaki doldurulamayan boşluk.
    """
    if len(df) < SWING_LOOKBACK * 2 + 3:
        return []

    # ATR hesapla (sinyal skoru için)
    highs  = df["high"]
    lows   = df["low"]
    closes = df["close"]
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift()).abs(),
        (lows  - closes.shift()).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    # EMA200 hesapla (trend filtresi için)
    ema200 = closes.ewm(span=200, adjust=False).mean()

    results = []
    # Pencere: i-2 (FVG başlangıcı), i-1 (referans), i (CISD mumu)
    for i in range(SWING_LOOKBACK + 2, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        old  = df.iloc[i - 2]

        # ── Swing seviyeleri ──────────────────────────────────────────────
        prev_swing_high = find_swing_high(df, i - 1)
        prev_swing_low  = find_swing_low(df, i - 1)

        # ── BEARISH CISD + FVG ───────────────────────────────────────────
        # CISD: Önce swing high kırılır, sonra fiyat o seviyenin altında kapanır
        # FVG : old.low > curr.high (arada boşluk var)
        is_bearish_cisd = curr["close"] < prev_swing_high
        has_bearish_fvg = old["low"] > curr["high"]

        # ── EMA200 Trend Filtresi: Bearish sinyal için fiyat EMA200 altında olmalı
        # Test modunda bypass edilir (mock veri uptrend olduğundan bearish elenir)
        ema200_bearish_ok = TEST_MODU or (not EMA200_FILTRE) or (float(curr["close"]) < float(ema200.iloc[i]))

        if is_bearish_cisd and has_bearish_fvg and ema200_bearish_ok:
            fvg_top    = old["low"]
            fvg_bottom = curr["high"]
            fvg_size   = fvg_top - fvg_bottom

            # ── FVG Minimum Büyüklük Filtresi
            if fvg_size < atr * MIN_FVG_ATR_ORA:
                pass  # FVG çok küçük, atla
            elif is_fvg_still_valid(df, fvg_top, fvg_bottom, i, "BEARISH"):
                score_data = calculate_score(df, i, "BEARISH", fvg_size, atr)
                # SL: FVG üst bandının hafif üzeri
                sl = float(fvg_top) + atr * 0.2
                sl_mesafe = sl - float(curr["close"])
                tp = float(curr["close"]) - max(sl_mesafe * MIN_RR, atr * MIN_RR)
                rr = round((float(curr["close"]) - tp) / sl_mesafe, 2) if sl_mesafe > 0 else 0

                # ── R/R Minimum Filtresi
                if rr < MIN_RR:
                    pass  # R/R yetersiz, atla
                else:
                    results.append({
                        "time":        curr["time"],
                        "type":        "BEARISH 🔴",
                        "direction":   "BEARISH",
                        "price":       curr["close"],
                        "fvg_top":     fvg_top,
                        "fvg_bottom":  fvg_bottom,
                        "fvg_zone":    f"{fvg_bottom:.5f} – {fvg_top:.5f}",
                        "swing_level": prev_swing_low,
                        "sl":          sl,
                        "tp":          tp,
                        "rr":          rr,
                        "score":       score_data["score"],
                        "score_label": score_data["score_label"],
                        "score_detail":score_data["details"],
                        "candle_idx":  i,
                        "ema200":      float(ema200.iloc[i]),
                    })

        # ── BULLISH CISD + FVG ───────────────────────────────────────────
        # CISD: Önce swing low kırılır, sonra fiyat o seviyenin üstünde kapanır
        # FVG : old.high < curr.low (arada boşluk var)
        is_bullish_cisd = curr["close"] > prev_swing_low
        has_bullish_fvg = old["high"] < curr["low"]

        # ── EMA200 Trend Filtresi: Bullish sinyal için fiyat EMA200 üstünde olmalı
        # Test modunda bypass edilir
        ema200_bullish_ok = TEST_MODU or (not EMA200_FILTRE) or (float(curr["close"]) > float(ema200.iloc[i]))

        if is_bullish_cisd and has_bullish_fvg and ema200_bullish_ok:
            fvg_top    = curr["low"]
            fvg_bottom = old["high"]
            fvg_size   = fvg_top - fvg_bottom

            # ── FVG Minimum Büyüklük Filtresi
            if fvg_size < atr * MIN_FVG_ATR_ORA:
                pass  # FVG çok küçük, atla
            elif is_fvg_still_valid(df, fvg_top, fvg_bottom, i, "BULLISH"):
                score_data = calculate_score(df, i, "BULLISH", fvg_size, atr)
                # SL: FVG alt bandının hafif altı
                sl = float(fvg_bottom) - atr * 0.2
                sl_mesafe = float(curr["close"]) - sl
                tp = float(curr["close"]) + max(sl_mesafe * MIN_RR, atr * MIN_RR)
                rr = round((tp - float(curr["close"])) / sl_mesafe, 2) if sl_mesafe > 0 else 0

                # ── R/R Minimum Filtresi
                if rr < MIN_RR:
                    pass  # R/R yetersiz, atla
                else:
                    results.append({
                        "time":        curr["time"],
                        "type":        "BULLISH 🟢",
                        "direction":   "BULLISH",
                        "price":       curr["close"],
                        "fvg_top":     fvg_top,
                        "fvg_bottom":  fvg_bottom,
                        "fvg_zone":    f"{fvg_bottom:.5f} – {fvg_top:.5f}",
                        "swing_level": prev_swing_high,
                        "sl":          sl,
                        "tp":          tp,
                        "rr":          rr,
                        "score":       score_data["score"],
                        "score_label": score_data["score_label"],
                        "score_detail":score_data["details"],
                        "candle_idx":  i,
                        "ema200":      float(ema200.iloc[i]),
                    })

    # Minimum skor filtresi
    results = [s for s in results if s["score"] >= MIN_SKOR]

    # Zaman filtresi: zaman dilimine göre dinamik pencere.
    # M15 → 30dk, H1 → 2sa, H4 → 8sa, D → 2gün
    # Bu sayede büyük zaman dilimlerindeki sinyaller haksız yere elenmez.
    tf_pencere = {"M15": 15, "H1": 60, "H4": 240, "D": 1440}
    pencere_dk = tf_pencere.get(granularity, TARAMA_ARALIGI_DK * 2)
    sinyal_penceresi = pd.Timestamp.now("UTC").replace(tzinfo=None) - pd.Timedelta(minutes=pencere_dk)
    results = [s for s in results if pd.Timestamp(s["time"]).replace(tzinfo=None) >= sinyal_penceresi]

    return results


# ─────────────────────────────────────────────
#  CSV KAYIT
# ─────────────────────────────────────────────
def save_to_csv(signals: list[dict], symbol: str, tf: str):
    """Sinyalleri CSV dosyasına ekler (append modu)."""
    file_exists = os.path.isfile(CSV_DOSYASI)
    with open(CSV_DOSYASI, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["tarih", "sembol", "dilim", "yon", "kapanis",
                      "fvg_alt", "fvg_ust", "swing_seviyesi",
                      "skor", "skor_etiketi"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for s in signals:
            writer.writerow({
                "tarih":          (s["time"] + pd.Timedelta(hours=UTC_OFFSET)).strftime("%Y-%m-%d %H:%M"),
                "sembol":         symbol,
                "dilim":          tf,
                "yon":            s["direction"],
                "kapanis":        f"{s['price']:.5f}",
                "fvg_alt":        f"{s['fvg_bottom']:.5f}",
                "fvg_ust":        f"{s['fvg_top']:.5f}",
                "swing_seviyesi": f"{s['swing_level']:.5f}",
                "skor":           s["score"],
                "skor_etiketi":   s["score_label"],
            })


# ─────────────────────────────────────────────
#  MATPLOTLİB GRAFİK
# ─────────────────────────────────────────────
def plot_signals(df: pd.DataFrame, signals: list[dict], symbol: str, tf: str):
    """Gelişmiş grafik: mumlar + FVG kutusu + SL/TP çizgileri + bilgi paneli."""
    df_plot = df.tail(60).reset_index(drop=True)
    offset  = len(df) - len(df_plot)

    fig, ax = plt.subplots(figsize=(18, 8))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    # ── Izgara ───────────────────────────────────────────────────────────
    ax.grid(axis="y", color="#1e2a3a", linewidth=0.5, zorder=0)

    # ── Mumlar ───────────────────────────────────────────────────────────
    for idx, row in df_plot.iterrows():
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        color = "#26a69a" if c >= o else "#ef5350"
        # Gövde
        ax.bar(idx, abs(c - o) if abs(c - o) > 0 else 0.00001,
               bottom=min(o, c), color=color, width=0.7, zorder=2, alpha=0.9)
        # Fitil
        ax.plot([idx, idx], [l, h], color=color, linewidth=0.8, zorder=2)

    # ── Sinyal seç ───────────────────────────────────────────────────────
    gosterilecek = signals
    if SADECE_SON_SINYAL and signals:
        gosterilecek = [max(signals, key=lambda x: x["score"])]

    for sig in gosterilecek:
        plot_idx = sig["candle_idx"] - offset
        if not (0 <= plot_idx < len(df_plot)):
            continue

        is_bull  = sig["direction"] == "BULLISH"
        clr      = "#26a69a" if is_bull else "#ef5350"
        sl_clr   = "#ff5252"
        tp_clr   = "#69f0ae"

        fvg_top    = float(sig["fvg_top"])
        fvg_bottom = float(sig["fvg_bottom"])
        price      = float(sig["price"])
        sl         = float(sig["sl"])
        tp         = float(sig["tp"])

        # ── FVG kutusu (belirgin) ────────────────────────────────────────
        fvg_x_start = max(0, (plot_idx - 2) / len(df_plot))
        fvg_x_end   = min(1.0, (len(df_plot)) / len(df_plot))
        ax.axhspan(fvg_bottom, fvg_top,
                   xmin=fvg_x_start, xmax=fvg_x_end,
                   color="#f9a825", alpha=0.15, zorder=1)
        ax.axhline(fvg_top,    color="#f9a825", linewidth=1.2,
                   linestyle="--", alpha=0.9, zorder=3)
        ax.axhline(fvg_bottom, color="#f9a825", linewidth=1.2,
                   linestyle="--", alpha=0.9, zorder=3)
        ax.text(1, fvg_top + (fvg_top - fvg_bottom) * 0.1,
                "FVG", color="#f9a825", fontsize=7,
                transform=ax.get_yaxis_transform(), ha="left", va="bottom")

        # ── Sinyal mumunu vurgula (sarı çerçeve) ─────────────────────────
        row = df_plot.iloc[plot_idx]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        rect_bottom = min(o, c)
        rect_height = abs(c - o) if abs(c - o) > 0 else 0.00001
        rect = plt.Rectangle((plot_idx - 0.4, rect_bottom), 0.8, rect_height,
                              linewidth=1.5, edgecolor="#f9a825",
                              facecolor="none", zorder=5)
        ax.add_patch(rect)

        # ── SL çizgisi ───────────────────────────────────────────────────
        ax.axhline(sl, color=sl_clr, linewidth=1.2, linestyle=":", alpha=0.9, zorder=3)
        ax.text(1, sl, f" SL {sl:.5f}", color=sl_clr, fontsize=7,
                transform=ax.get_yaxis_transform(), ha="left", va="center")

        # ── TP çizgisi ───────────────────────────────────────────────────
        ax.axhline(tp, color=tp_clr, linewidth=1.2, linestyle=":", alpha=0.9, zorder=3)
        ax.text(1, tp, f" TP {tp:.5f}", color=tp_clr, fontsize=7,
                transform=ax.get_yaxis_transform(), ha="left", va="center")

        # ── Yön oku ──────────────────────────────────────────────────────
        arrow_dy = (fvg_top - fvg_bottom) * 3
        ax.annotate("",
            xy=(plot_idx, price + (arrow_dy if is_bull else -arrow_dy)),
            xytext=(plot_idx, price),
            arrowprops=dict(arrowstyle="-|>", color=clr, lw=2.5,
                            mutation_scale=15),
            zorder=6)

        # ── Bilgi paneli (sağ üst) ───────────────────────────────────────
        yon_str = "BULLISH ▲" if is_bull else "BEARISH ▼"
        panel = (
            f"  {yon_str}\n"
            f"  Giriş : {price:.5f}\n"
            f"  SL    : {sl:.5f}\n"
            f"  TP    : {tp:.5f}\n"
            f"  R/R   : 1 : {sig['rr']:.1f}\n"
            f"  Skor  : {sig['score']}/100\n"
            f"  {sig['score_label']}"
        )
        ax.text(0.01, 0.98, panel,
                transform=ax.transAxes,
                color=clr, fontsize=8.5, va="top", ha="left",
                fontfamily="monospace",
                bbox=dict(boxstyle="round,pad=0.5",
                          facecolor="#0d1117", edgecolor=clr,
                          linewidth=1.5, alpha=0.95),
                zorder=10)

    # ── Başlık & eksen ───────────────────────────────────────────────────
    ax.set_title(f"  {symbol}  |  {tf}   —   CISD & FVG Tarayıcı  "
                 f"|  {datetime.now().strftime('%d/%m/%Y  %H:%M')}",
                 color="white", fontsize=12, pad=10, loc="left",
                 fontweight="bold")
    ax.tick_params(colors="#666", labelsize=7)
    for spine in ax.spines.values():
        spine.set_color("#1e2a3a")
    ax.yaxis.tick_right()
    ax.yaxis.set_tick_params(labelcolor="#aaa")
    ax.set_xlim(-1, len(df_plot) + 1)

    # X ekseni tarihleri
    xticks = list(range(0, len(df_plot), 10))
    ax.set_xticks(xticks)
    ax.set_xticklabels(
        [(df_plot.iloc[x]["time"] + pd.Timedelta(hours=UTC_OFFSET)).strftime("%H:%M\n%d/%m") for x in xticks],
        color="#666", fontsize=7)

    # Lejant
    legend_handles = [
        mpatches.Patch(color="#26a69a", label="Bullish"),
        mpatches.Patch(color="#ef5350", label="Bearish"),
        mpatches.Patch(color="#f9a825", label="FVG"),
        mpatches.Patch(color="#69f0ae", label="TP"),
        mpatches.Patch(color="#ff5252", label="SL"),
    ]
    ax.legend(handles=legend_handles, facecolor="#0d1117",
              edgecolor="#333", labelcolor="white", fontsize=8,
              loc="upper right", bbox_to_anchor=(0.99, 0.99))

    plt.tight_layout()

    if GRAFIK_KAYDET:
        import uuid
        fname = f"grafik_{symbol}_{tf}_{uuid.uuid4().hex[:8]}.png"
        plt.savefig(fname, dpi=140, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        print(f"   📊 Grafik kaydedildi: {fname}")
        plt.close()
        return fname
    else:
        plt.show()
        plt.close()
        return None


# ─────────────────────────────────────────────
#  TELEGRAM BİLDİRİM
# ─────────────────────────────────────────────

# Cooldown takibi: { "EUR_USD_M15": datetime, ... }
_son_bildirim: dict = {}

# ── Kalıcı sinyal hafızası ────────────────────────────────────────────────────
# Gönderilen sinyal ID'leri bir dosyaya yazılır. Program yeniden başlasa bile
# aynı sinyal (sembol+dilim+mum zamanı) bir daha gönderilmez.
GONDERILEN_DOSYA = "gonderilen_sinyaller.txt"

def _gonderilen_yukle() -> set:
    """Daha önce gönderilmiş sinyal ID'lerini dosyadan yükler."""
    if not os.path.isfile(GONDERILEN_DOSYA):
        return set()
    with open(GONDERILEN_DOSYA, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def _gonderilen_kaydet(sinyal_id: str):
    """Yeni gönderilen sinyal ID'sini dosyaya ekler."""
    with open(GONDERILEN_DOSYA, "a", encoding="utf-8") as f:
        f.write(sinyal_id + "\n")

# Program başlarken hafızayı dosyadan yükle
_gonderilen_sinyaller: set = _gonderilen_yukle()


def telegram_gonder(mesaj: str, grafik_path: str | None = None) -> bool:
    """
    Telegram'a metin + opsiyonel grafik gönderir.
    TELEGRAM_CHAT_ID liste veya tek string olabilir.
    Başarılıysa True, hata varsa False döner.
    """
    import urllib.request
    import urllib.parse
    import json
    import re

    if not TELEGRAM_AKTIF:
        return False

    # Liste veya tek string destekle
    chat_ids = TELEGRAM_CHAT_ID if isinstance(TELEGRAM_CHAT_ID, list) else [TELEGRAM_CHAT_ID]

    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    temiz_mesaj = re.sub(r'<[^>]+>', '', mesaj).replace('&amp;', '&')
    basarili = False

    for chat_id in chat_ids:
        try:
            if grafik_path and os.path.isfile(grafik_path):
                boundary = "----FormBoundary7MA4YWxkTrZu0gW"
                with open(grafik_path, "rb") as f:
                    foto_data = f.read()

                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                    f"{chat_id}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                    f"{temiz_mesaj}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="photo"; filename="grafik.png"\r\n'
                    f"Content-Type: image/png\r\n\r\n"
                ).encode() + foto_data + f"\r\n--{boundary}--\r\n".encode()

                req = urllib.request.Request(
                    f"{base}/sendPhoto",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    method="POST"
                )
            else:
                payload = json.dumps({
                    "chat_id": chat_id,
                    "text": temiz_mesaj,
                }).encode()
                req = urllib.request.Request(
                    f"{base}/sendMessage",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )

            urllib.request.urlopen(req, timeout=10)
            print(f"  ✈ Telegram gönderildi → {chat_id}")
            basarili = True

        except Exception as e:
            print(f"  ⚠ Telegram hata ({chat_id}): {e}")

    return basarili


def bildirim_gonder(sig: dict, symbol: str, tf: str, grafik_path: str | None = None):
    """Cooldown ve tekrar kontrolü yaparak Telegram bildirimi gönderir."""
    anahtar = f"{symbol}_{tf}"
    simdi   = datetime.now()

    # Aynı sinyali tekrar gönderme (sembol + dilim + sinyal zamanı)
    sinyal_id = f"{symbol}_{tf}_{sig['time']}"
    if sinyal_id in _gonderilen_sinyaller:
        print(f"  ⏭ Bu sinyal zaten gönderildi, atlanıyor.")
        return

    # Cooldown kontrolü
    if anahtar in _son_bildirim:
        gecen = (simdi - _son_bildirim[anahtar]).total_seconds() / 60
        if gecen < BILDIRIM_COOLDOWN_DK:
            kalan = int(BILDIRIM_COOLDOWN_DK - gecen)
            print(f"  🔕 Bildirim cooldown'da ({kalan} dk kaldı).")
            return

    yon = "BEARISH SATIS" if sig["direction"] == "BEARISH" else "BULLISH ALIS"
    zaman = (sig["time"] + pd.Timedelta(hours=UTC_OFFSET)).strftime("%H:%M  %d/%m/%Y")

    mesaj = (
        f"{'🔴' if sig['direction'] == 'BEARISH' else '🟢'} {symbol} | {tf}\n"
        f"Yön    : {yon}\n"
        f"Giriş  : {sig['price']:.5f}\n"
        f"Stop   : {sig['sl']:.5f}\n"
        f"TP     : {sig['tp']:.5f}\n"
        f"R/R    : 1:{sig['rr']:.2f}\n"
        f"EMA200 : {sig.get('ema200', 0):.5f}\n"
        f"Skor   : {sig['score']}/100\n"
        f"Zaman  : {zaman}"
    )

    basarili = telegram_gonder(mesaj, grafik_path)
    if basarili:
        _son_bildirim[anahtar] = simdi
        _gonderilen_sinyaller.add(sinyal_id)
        _gonderilen_kaydet(sinyal_id)   # Dosyaya da yaz — yeniden başlatmada korunur
        print(f"  ✈ Telegram bildirimi gönderildi.")
        if grafik_path and os.path.isfile(grafik_path):
            os.remove(grafik_path)


def _gonderilen_temizle():
    """7 günden eski sinyal ID'lerini dosyadan temizler (dosyanın şişmesini önler)."""
    if not os.path.isfile(GONDERILEN_DOSYA):
        return
    simdi = datetime.utcnow()
    gecerli = []
    with open(GONDERILEN_DOSYA, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # ID formatı: SEMBOL_TF_<pd.Timestamp>  — tarihi son parçadan parse et
            try:
                zaman_str = "_".join(line.split("_")[2:])
                t = pd.Timestamp(zaman_str).replace(tzinfo=None)
                if (simdi - t.to_pydatetime()).days < 7:
                    gecerli.append(line)
            except Exception:
                gecerli.append(line)  # parse edilemiyorsa sil deme
    with open(GONDERILEN_DOSYA, "w", encoding="utf-8") as f:
        f.write("\n".join(gecerli) + ("\n" if gecerli else ""))
    # Bellek setini de güncelle
    _gonderilen_sinyaller.clear()
    _gonderilen_sinyaller.update(gecerli)


# ─────────────────────────────────────────────
#  TARAMA DÖNGÜSÜ
# ─────────────────────────────────────────────
def run_scanner():
    _gonderilen_temizle()   # Her tarama döngüsünde eski kayıtları temizle
    print(f"\n{'═' * 65}")
    print(f"  CISD & FVG TARAMASI  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Filtreler → EMA200: {'✅' if EMA200_FILTRE else '❌'}  |  Min R/R: {MIN_RR}  |  Min FVG: {MIN_FVG_ATR_ORA}×ATR  |  Min Skor: {MIN_SKOR}")
    print(f"{'═' * 65}")

    for symbol in PARITELER:
        for tf in ZAMAN_DILIMLERI:
            try:
                df      = get_mock_data(symbol, tf) if TEST_MODU else get_data(symbol, tf)
                signals = analyze_cisd_fvg(df, tf)

                if signals:
                    # En yüksek skorlu sinyal
                    best = max(signals, key=lambda x: x["score"])

                    print(f"\n  [{symbol}] [{tf}]  {best['type']}  {best['score_label']}")
                    print(f"  ├─ Kapanış   : {best['price']:.5f}")
                    print(f"  ├─ EMA200    : {best.get('ema200', 0):.5f}")
                    print(f"  ├─ FVG Bölge : {best['fvg_zone']}")
                    print(f"  ├─ Swing Lvl : {best['swing_level']:.5f}")
                    print(f"  ├─ R/R       : 1:{best['rr']:.2f}")
                    print(f"  ├─ Skor      : {best['score']}/100")
                    print(f"  ├─ Detay     :")
                    for d in best["score_detail"]:
                        print(f"  │    {d}")
                    print(f"  └─ Zaman     : {best['time']}")

                    # CSV kaydet (tüm sinyaller)
                    save_to_csv(signals, symbol, tf)
                    print(f"  💾 {len(signals)} sinyal CSV'ye eklendi.")

                    # Grafik çiz + yolu al (Telegram'a eklemek için)
                    grafik_yolu = plot_signals(df, signals, symbol, tf)

                    # Telegram bildirimi
                    bildirim_gonder(best, symbol, tf, grafik_yolu)

                else:
                    print(f"  [{symbol}] [{tf}]  → Sinyal yok.")

            except Exception as e:
                print(f"  [{symbol}] [{tf}]  ⚠ Hata: {e}")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 CISD & FVG Tarayıcı başlatıldı.")
    print(f"   Çalışma saatleri : {CALISMA_SAATI_BASLANGIC:02d}:00 – {CALISMA_SAATI_BITIS:02d}:00")
    print(f"   Tarama aralığı   : {TARAMA_ARALIGI_DK} dakika\n")

    while True:
        simdi = datetime.now()
        saat  = simdi.hour
        dakika = simdi.minute
        gun   = simdi.weekday()  # 0=Pazartesi, 6=Pazar

        # Pazartesi günü 16:30'dan sonra çalışma
        pazartesi_kapali = (gun == 0 and (saat > 16 or (saat == 16 and dakika >= 30)))

        if pazartesi_kapali:
            print(f"💤 [{simdi.strftime('%H:%M')}] Pazartesi 16:30 sonrası — bot durduruldu. Bekleniyor...")
        elif CALISMA_SAATI_BASLANGIC <= saat < CALISMA_SAATI_BITIS:
            run_scanner()
            print(f"\n⏳ Bir sonraki tarama {TARAMA_ARALIGI_DK} dakika içinde...\n")
        else:
            print(f"💤 [{simdi.strftime('%H:%M')}] Çalışma saati dışı "
                  f"({CALISMA_SAATI_BASLANGIC:02d}:00–{CALISMA_SAATI_BITIS:02d}:00). Bekleniyor...")

        time.sleep(TARAMA_ARALIGI_DK * 60)