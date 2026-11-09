# -*- coding: utf-8 -*-
"""
PUSU SİSTEMİ v3 — TVGFBF duyuru takip botu

Kaynak: sitenin kendi JSON API'si (sayfanın JavaScript'i de bunu kullanıyor)
  /api/duyurular    → duyuru kartları (id, title, slug, published_date)
  /api/etkinlikler  → kurs/faaliyet kayıtları
  /api/seminerler   → seminerler
Önce doğrudan HTTP ile okunur. Doğrulama sayfası ("One moment, please...") çıkarsa
gerçek Chromium ile site açılır, doğrulama geçilir ve API tarayıcının içinden çekilir.

Kullanım:
  python pusu.py              → sürekli nöbet (GitHub Actions)
  python pusu.py --listele    → siteyi bir kez oku ve listele (hiçbir şey kaydetmez/göndermez)
  python pusu.py --tek-tur    → tek tur çalış (bildirim gönderir, durum.json'u günceller)
Ortam değişkenleri: NTFY_KANAL (zorunlu, GitHub Secret), BILDIRIM_ONEKI (test için, ör. "TEST")
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ══════════════════════════════ AYARLAR ══════════════════════════════
NTFY_KANAL = (os.environ.get("NTFY_KANAL") or "").replace("﻿", "").strip()
BILDIRIM_ONEKI = (os.environ.get("BILDIRIM_ONEKI") or "").strip()
SITE = "https://tvgfbf.gov.tr"

# ad: (API yolu, sitedeki detay sayfası kökü, kaç saniyede bir okunur)
KAYNAKLAR = {
    "duyurular": ("/api/duyurular?per_page=30&page=1", "/duyurular/", 60),
    "etkinlikler": ("/api/etkinlikler?per_page=100", "/etkinlikler/", 5 * 60),
    "seminerler": ("/api/seminerler?per_page=100", "/seminerler/", 5 * 60),
}
ZORUNLU = "duyurular"
ANAHTAR_ONEKI = {"duyurular": "duyuru", "etkinlikler": "etkinlik", "seminerler": "seminer"}

ONEMLI_KELIMELER = ["kurs", "kademe", "antrenor", "seminer", "vize", "on kayit"]

TUR_ARALIGI_SN = 60
MAKS_CALISMA_SN = 5 * 3600 + 40 * 60   # GitHub 6 saat sınırının altında kal
KOR_DAKIKA = 15                        # bu kadar dk kesintisiz okunamazsa alarm
KOR_HATIRLATMA_SAAT = 6                # kör kalmaya devam ederse hatırlatma aralığı
BAYAT_GUN = 30                         # en yeni duyuru bundan eskiyse "kör" say (son 2 yılda en uzun ara 16 gün)
ESKI_GUN = 10                          # bundan eski tarihli kayıt "yeni" sayılmaz (yeniden yayınlanan eski kayıt)
TOPLU_ESIK = 6                         # aynı anda bundan fazla yeni kayıt = tek özet bildirim
GUNLUK_RAPOR_SAATI = 9
YENI_IP_ESIGI = 3                      # art arda bu kadar engel/bağlantı hatasında yeni sunucu iste
ISTATISTIK_KAYIT_SN = 10 * 60          # tur sayaçları en geç bu aralıkla repoya yazılır
# cron-job.org her :00 ve :30'da yeni çalışma başlatır, eskisi sert kapanır (kapanış kaydı yetişmeyebilir).
# Bu yüzden her yarım saatin son dakikasında ayrıca kaydedilir.
KURULUM_SURUMU = 6                     # değişince "AKTİF" mesajı bir kez gelir
DURUM_SURUMU = 3

DURUM_DOSYASI = Path("durum.json")
DEBUG = Path("debug")
TR = ZoneInfo("Europe/Istanbul")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
DOGRULAMA_IPUCLARI = ("one moment", "checking your browser", "attention required", "just a moment",
                      "sgcaptcha", "captcha", "lütfen bekleyin", "ddos", "security check", "verify you are human")
AG_HATALARI = ("ConnectionError", "Timeout", "ERR_", "doğrulama")
AYLAR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
         "Eylül", "Ekim", "Kasım", "Aralık"]
GITHUB = bool(os.environ.get("GITHUB_ACTIONS"))
CALISMA_NO = os.environ.get("GITHUB_RUN_ID", "yerel")


# ══════════════════════════════ YARDIMCILAR ══════════════════════════════
def simdi():
    return datetime.now(TR)


def log(mesaj):
    print(f"[{simdi():%H:%M:%S}] {mesaj}", flush=True)


def ascii_katla(metin):
    metin = (metin or "").replace("İ", "i").replace("I", "ı").lower()
    return metin.translate(str.maketrans("ıöüşçğâîû", "iouscgaiu"))


def onemli_mi(metin):
    katli = ascii_katla(metin)
    return any(k in katli for k in ONEMLI_KELIMELER)


def tarih_coz(metin):
    """'2026-11-06 21:34:00' / '2026-11-06' → datetime (İstanbul saati) ya da None."""
    if not metin:
        return None
    for bicim in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(metin)[:19], bicim).replace(tzinfo=TR)
        except ValueError:
            continue
    return None


def tarih_yazi(dt, saat=False):
    """Sitedeki biçim: '6 Kasım 2026'."""
    if not dt:
        return "tarih yok"
    yazi = f"{dt.day} {AYLAR[dt.month]} {dt.year}"
    return f"{yazi} {dt:%H:%M}" if saat else yazi


def sure_yazi(saniye):
    dk = int(saniye // 60)
    if dk >= 1440:
        return f"{dk // 1440} gün {dk % 1440 // 60} sa"
    return f"{dk // 60} sa {dk % 60} dk" if dk >= 60 else f"{dk} dk"


def iso(dt=None):
    return (dt or simdi()).isoformat(timespec="seconds")


def iso_coz(metin):
    try:
        return datetime.fromisoformat(metin)
    except Exception:
        return None


# ══════════════════════════════ BİLDİRİM ══════════════════════════════
def bildirim(mesaj, baslik, oncelik=4, etiketler=("loudspeaker",), link=None):
    if BILDIRIM_ONEKI:
        baslik = f"{BILDIRIM_ONEKI} {baslik}"
    veri = {"topic": NTFY_KANAL, "title": baslik, "message": mesaj,
            "priority": oncelik, "tags": list(etiketler)}
    if link:
        veri["click"] = link
        veri["actions"] = [{"action": "view", "label": "Aç", "url": link}]
    for deneme in range(3):
        try:
            r = requests.post("https://ntfy.sh/", json=veri, timeout=20)
            if r.status_code < 300:
                log(f"Bildirim gitti: {baslik}")
                return True
            log(f"ntfy {r.status_code}: {r.text[:120]}")
        except Exception as e:
            log(f"ntfy hatası: {type(e).__name__}")
        time.sleep(3 * (deneme + 1))
    log(f"BİLDİRİM GÖNDERİLEMEDİ: {baslik}")
    return False


# ══════════════════════════════ DURUM (hafıza) ══════════════════════════════
def bos_durum():
    return {"surum": DURUM_SURUMU, "gorulen": {}, "kaynaklar": {}, "son_okuma": None,
            "istatistik": {}, "son_rapor": None, "kurulum_bildirildi": 0, "baz_alindi": False}


def durum_yukle():
    if not DURUM_DOSYASI.exists():
        return bos_durum()
    try:
        d = json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"durum.json okunamadı, sıfırdan başlıyorum: {e}")
        return bos_durum()
    if d.get("surum") == DURUM_SURUMU:
        for k, v in bos_durum().items():
            d.setdefault(k, v)
        return d
    # v2 → v3 geçişi: eski kayıtlar URL/başlık tabanlıydı, yenileri API id'si.
    # İlk başarılı okumada sitedeki her şey sessizce "görüldü" sayılır (bildirim yağmuru olmaz).
    yeni = bos_durum()
    eski_ilk = [v.get("ilk", "") for v in d.get("gorulen", {}).values() if isinstance(v, dict)]
    yeni["gecis"] = {"eski_kayit_sayisi": len(eski_ilk), "eski_son_kayit": max(eski_ilk, default=None)}
    yeni["son_rapor"] = d.get("son_rapor")
    log(f"durum.json v{d.get('surum')} → v{DURUM_SURUMU} geçişi: {len(eski_ilk)} eski kayıt, "
        "ilk okumada mevcut duyurular sessizce baz alınacak")
    return yeni


def istatistik_temizle(ist):
    sinir = (simdi() - timedelta(hours=48)).strftime("%Y-%m-%dT%H")
    return {k: v for k, v in ist.items() if k.split("|")[0] >= sinir}


def birlestir(yerel, uzak):
    """İki çalışmanın (eski iptal edilen + yeni) durumunu anlamca birleştirir."""
    if not isinstance(uzak, dict) or uzak.get("surum") != DURUM_SURUMU:
        return yerel
    sonuc = dict(yerel)
    gorulen = dict(uzak.get("gorulen", {}))
    for k, v in yerel.get("gorulen", {}).items():
        if k not in gorulen or v.get("ilk", "") < gorulen[k].get("ilk", "~"):
            gorulen[k] = v
    sonuc["gorulen"] = gorulen
    ist = dict(uzak.get("istatistik", {}))
    for k, v in yerel.get("istatistik", {}).items():
        a = ist.get(k, [0, 0])
        ist[k] = [max(a[0], v[0]), max(a[1], v[1])]
    sonuc["istatistik"] = istatistik_temizle(ist)
    for alan in ("son_rapor", "kurulum_bildirildi"):
        sonuc[alan] = max(yerel.get(alan) or 0, uzak.get(alan) or 0, key=lambda x: str(x)) or None
    sonuc["baz_alindi"] = bool(yerel.get("baz_alindi") or uzak.get("baz_alindi"))
    so_y, so_u = yerel.get("son_okuma") or {}, uzak.get("son_okuma") or {}
    sonuc["son_okuma"] = so_y if so_y.get("zaman", "") >= so_u.get("zaman", "") else so_u
    if "gecis" in uzak or "gecis" in yerel:
        sonuc["gecis"] = yerel.get("gecis") or uzak.get("gecis")
    return sonuc


def durum_yaz(durum):
    durum["istatistik"] = istatistik_temizle(durum.get("istatistik", {}))
    DURUM_DOSYASI.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def uzak_durum(dal):
    if git("fetch", "--quiet", "origin", dal).returncode != 0:
        return None
    p = git("show", f"origin/{dal}:{DURUM_DOSYASI}")
    try:
        return json.loads(p.stdout) if p.returncode == 0 else None
    except Exception:
        return None


def durum_kaydet(durum, mesaj="Pusu durumu güncellendi"):
    """Dosyaya yazar ve (Actions'ta) repoya push'lar. Çakışmada uzak durumla anlamca birleştirir."""
    durum_yaz(durum)
    if not GITHUB:
        return durum
    dal = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        for deneme in range(4):
            git("add", str(DURUM_DOSYASI))
            if git("diff", "--staged", "--quiet").returncode != 0:
                git("commit", "-q", "-m", mesaj)
            elif deneme == 0:
                return durum
            p = git("push", "-q", "origin", f"HEAD:{dal}")
            if p.returncode == 0:
                log("Hafıza repoya kaydedildi")
                return durum
            # Başkası (önceki çalışma ya da kod güncellemesi) push'lamış: uzaktakini al, birleştir
            uzak = uzak_durum(dal)
            git("reset", "-q", "--hard", f"origin/{dal}")
            durum = birlestir(durum, uzak)
            durum_yaz(durum)
            time.sleep(3)
        log(f"git push başarısız: {p.stderr.strip()[-200:]}")
    except Exception as e:
        log(f"git hatası: {e}")
    return durum


def yeni_calisma_iste():
    """GitHub'a 'beni yeni bir sunucuda yeniden başlat' der. Yeni sunucu = yeni IP."""
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    wf_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    if not (token and repo and wf_ref):
        return False
    dosya = wf_ref.split("@")[0].rsplit("/", 1)[-1]
    try:
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/{dosya}/dispatches",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"ref": os.environ.get("GITHUB_REF_NAME", "main")}, timeout=20)
        if r.status_code == 204:
            log("Engel şüphesi → yeni sunucuda yeni çalışma istendi")
            return True
        log(f"Yeni çalışma isteği reddedildi: {r.status_code}")
    except Exception as e:
        log(f"Yeni çalışma isteği hatası: {e}")
    return False


# ══════════════════════════════ OKUMA ══════════════════════════════
class OkumaHatasi(Exception):
    def __init__(self, sebep, govde=None, dogrulama=False):
        super().__init__(sebep)
        self.govde = govde
        self.dogrulama = dogrulama


def dogrulama_sayfasi_mi(metin):
    bas = ascii_katla((metin or "")[:5000])
    return any(ascii_katla(k) in bas for k in DOGRULAMA_IPUCLARI)


def json_coz(kod, ctype, govde, nereden):
    if kod != 200:
        raise OkumaHatasi(f"{nereden}: HTTP {kod}", govde, dogrulama_sayfasi_mi(govde))
    if "json" not in (ctype or "").lower():
        dogr = dogrulama_sayfasi_mi(govde)
        raise OkumaHatasi(f"{nereden}: " + ("doğrulama sayfası geldi" if dogr else f"JSON değil ({ctype[:40]})"),
                          govde, dogr)
    try:
        veri = json.loads(govde)
    except Exception:
        raise OkumaHatasi(f"{nereden}: bozuk JSON", govde)
    if not isinstance(veri, dict) or not isinstance(veri.get("data"), list):
        raise OkumaHatasi(f"{nereden}: beklenmeyen JSON yapısı", govde)
    return veri


class Tarayici:
    """Yedek yol: gerçek Chromium ile doğrulamayı geçer, API'yi sayfanın içinden çeker."""

    def __init__(self):
        self.pw = self.tarayici = self.baglam = self.sayfa = None

    def acik(self):
        return self.sayfa is not None and not self.sayfa.is_closed()

    def ac(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        argumanlar = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        self.tarayici = self.pw.chromium.launch(headless=True, args=argumanlar)
        surum = self.tarayici.version.split(".")[0]
        self.baglam = self.tarayici.new_context(
            user_agent=UA.replace("140.0.0.0", f"{surum}.0.0.0"), locale="tr-TR",
            timezone_id="Europe/Istanbul", viewport={"width": 1366, "height": 768})
        self.baglam.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        self.sayfa = self.baglam.new_page()
        log(f"Yedek yol: tarayıcı açıldı (Chromium {self.tarayici.version})")
        self.sayfa.goto(f"{SITE}/duyurular", timeout=60000, wait_until="domcontentloaded")
        for _ in range(24):  # doğrulama sayfası en fazla 2 dk beklenir
            if not dogrulama_sayfasi_mi(self.sayfa.title() + " " + self.sayfa.content()[:3000]):
                return
            self.sayfa.wait_for_timeout(5000)
        raise OkumaHatasi("tarayıcı: doğrulama sayfası geçilemedi", self.sayfa.content(), True)

    def getir(self, yol):
        if not self.acik():
            self.ac()
        sonuc = self.sayfa.evaluate(
            """async (u) => { const r = await fetch(u, {headers: {Accept: 'application/json'}, cache: 'no-store'});
                return {kod: r.status, ctype: r.headers.get('content-type') || '', govde: await r.text()}; }""",
            SITE + yol)
        return json_coz(sonuc["kod"], sonuc["ctype"], sonuc["govde"], "tarayıcı")

    def cerezler(self):
        try:
            return self.baglam.cookies()
        except Exception:
            return []

    def ekran_goruntusu(self, dosya):
        try:
            if self.acik():
                self.sayfa.screenshot(path=dosya, full_page=True, timeout=15000)
        except Exception:
            pass

    def kapat(self):
        for nesne in (self.tarayici, self.pw):
            try:
                if nesne is self.pw and nesne:
                    nesne.stop()
                elif nesne:
                    nesne.close()
            except Exception:
                pass
        self.pw = self.tarayici = self.baglam = self.sayfa = None


class Okuyucu:
    def __init__(self):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                                  "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8", "Referer": f"{SITE}/duyurular"})
        self.tarayici = Tarayici()
        self.yontem = "api"

    def getir(self, yol):
        """Önce doğrudan HTTP; olmazsa tarayıcı. (veri, yöntem) döner, olmazsa OkumaHatasi."""
        try:
            r = self.http.get(SITE + yol, timeout=30)
            veri = json_coz(r.status_code, r.headers.get("content-type", ""), r.text, "api")
            if self.tarayici.acik():
                log("Doğrudan API yeniden çalışıyor, tarayıcı kapatılıyor")
                self.tarayici.kapat()
            self.yontem = "api"
            return veri
        except requests.RequestException as e:
            ilk = OkumaHatasi(f"api: {type(e).__name__}")
        except OkumaHatasi as e:
            ilk = e
        try:
            veri = self.tarayici.getir(yol)
        except OkumaHatasi as e:
            raise OkumaHatasi(f"{ilk} | {e}", e.govde or ilk.govde, ilk.dogrulama or e.dogrulama)
        except Exception as e:
            sebep = str(e).strip().splitlines()[0][:120] if str(e).strip() else type(e).__name__
            self.tarayici.kapat()
            raise OkumaHatasi(f"{ilk} | tarayıcı: {sebep}", ilk.govde, ilk.dogrulama)
        if self.yontem != "tarayici":
            log(f"Doğrudan API okunamadı ({ilk}); tarayıcı yolu çalıştı")
            for c in self.tarayici.cerezler():  # doğrulama çerezleriyle doğrudan yol tekrar denenecek
                self.http.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        self.yontem = "tarayici"
        return veri

    def kaynak_oku(self, ad):
        yol, kok, _ = KAYNAKLAR[ad]
        veri = self.getir(yol)
        ogeler = {}
        for x in veri["data"]:
            if x.get("status") not in (None, "aktif") or not x.get("id") or not x.get("title"):
                continue
            tarih = tarih_coz(x.get("published_date") or x.get("created_at"))
            slug = x.get("slug") or ""
            ogeler[f"{ANAHTAR_ONEKI[ad]}:{x['id']}"] = {
                "baslik": " ".join(str(x["title"]).split())[:250],
                "tarih": tarih.strftime("%Y-%m-%d %H:%M") if tarih else None,
                "link": f"{SITE}{kok}{slug}" if slug else f"{SITE}{kok.rstrip('/')}",
                "kategori": x.get("category") or "",
            }
        return ogeler

    def kapat(self):
        self.tarayici.kapat()


def debug_kaydet(okuyucu, ad, govde):
    try:
        DEBUG.mkdir(exist_ok=True)
        dosya = DEBUG / f"{ad}_hata_{simdi():%m%d_%H%M%S}"
        if govde:
            Path(f"{dosya}.html").write_text(str(govde), encoding="utf-8")
        okuyucu.tarayici.ekran_goruntusu(f"{dosya}.png")
    except Exception as e:
        log(f"Debug kaydedilemedi: {e}")


def en_yeniler(ogeler, n=3):
    return sorted(ogeler.values(), key=lambda v: v.get("tarih") or "", reverse=True)[:n]


# ══════════════════════════════ ANA MANTIK ══════════════════════════════
class Pusu:
    def __init__(self, tek_tur=False):
        self.durum = durum_yukle()
        self.okuyucu = Okuyucu()
        self.son_kontrol = {}
        self.hata_sayisi = {}
        self.engel_sayisi = 0
        self.yeni_ip_istendi = False
        self.son_ist_kayit = time.monotonic()
        self.ist_kirli = False
        self.son_iptal_oncesi = None
        self.son_ogeler = {}
        self.kor_yeni, self.kor_hatirlat, self.duzelen = [], [], []
        self.tek_tur = tek_tur

    def k(self, ad):
        return self.durum["kaynaklar"].setdefault(ad, {"kor": False})

    # ── tur sayaçları (son 24 saat raporu için) ──
    def say(self, basarili):
        anahtar = f"{simdi():%Y-%m-%dT%H}|{CALISMA_NO}"
        a = self.durum["istatistik"].setdefault(anahtar, [0, 0])
        a[0 if basarili else 1] += 1
        self.ist_kirli = True

    def son_24_saat(self):
        sinir = (simdi() - timedelta(hours=24)).strftime("%Y-%m-%dT%H")
        ok = hata = 0
        for k, (a, b) in self.durum["istatistik"].items():
            if k.split("|")[0] > sinir:
                ok, hata = ok + a, hata + b
        return ok, hata

    # ── yeni kayıtları işle ──
    def isle(self, ad, ogeler):
        gorulen, zaman = self.durum["gorulen"], iso()
        degisti = False
        for k, v in ogeler.items():  # başlık/tarih düzeltmeleri sessizce güncellenir
            if k in gorulen and (gorulen[k].get("baslik"), gorulen[k].get("tarih")) != (v["baslik"], v["tarih"]):
                gorulen[k].update(baslik=v["baslik"], tarih=v["tarih"], link=v["link"])
                degisti = True
        yeniler = {k: v for k, v in ogeler.items() if k not in gorulen}
        if not yeniler:
            return degisti

        baz = not self.durum.get("baz_alindi") or not self.k(ad).get("baz_alindi")
        for k, v in yeniler.items():
            gorulen[k] = {**v, "ilk": zaman}
        if baz:
            self.k(ad)["baz_alindi"] = True
            log(f"{ad}: {len(yeniler)} kayıt baz alındı (ilk tanışma, bildirim yok)")
            return True

        sinir = simdi() - timedelta(days=ESKI_GUN)
        taze = {}
        for k, v in yeniler.items():
            t = tarih_coz(v["tarih"])
            if t and t < sinir:
                log(f"{ad}: eski tarihli kayıt sessizce eklendi: {v['baslik'][:80]} ({v['tarih']})")
            else:
                taze[k] = v
        if not taze:
            return True

        if len(taze) > TOPLU_ESIK:
            satirlar = "\n".join(f"• {tarih_yazi(tarih_coz(v['tarih']))} · {v['baslik'][:90]}"
                                 for v in en_yeniler(taze, 8))
            onemli = any(onemli_mi(v["baslik"]) for v in taze.values())
            bildirim(f"{ad} kaynağında aynı anda {len(taze)} yeni kayıt:\n\n{satirlar}",
                     baslik=f"PUSU: {len(taze)} yeni kayıt" + (" (kurs/antrenörlük var!)" if onemli else ""),
                     oncelik=5 if onemli else 4, etiketler=["rotating_light"] if onemli else ["loudspeaker"],
                     link=f"{SITE}/duyurular")
            return True
        for v in sorted(taze.values(), key=lambda v: v.get("tarih") or ""):
            onemli = onemli_mi(v["baslik"])
            log(f"YENİ ({ad}): {v['baslik']} [{v['tarih']}] → {v['link']}")
            bildirim(f"{v['baslik']}\n\nYayın: {tarih_yazi(tarih_coz(v['tarih']), saat=True)}\n{v['link']}",
                     baslik="YENI KURS / ANTRENORLUK DUYURUSU!" if onemli else "Yeni TVGFBF duyurusu",
                     oncelik=5 if onemli else 4,
                     etiketler=["rotating_light", "muscle"] if onemli else ["loudspeaker"],
                     link=v["link"])
        return True

    # ── sağlık: başarı / körlük ──
    def basarili(self, ad):
        self.hata_sayisi[ad] = 0
        k = self.k(ad)
        degisti = k.pop("hata_baslangic", None) is not None
        k["son_basari"] = iso()
        k.pop("son_hata", None)
        if k.get("kor"):
            bas = iso_coz(k.get("kor_baslangic") or "")
            self.duzelen.append(f"{ad} ({sure_yazi((simdi() - bas).total_seconds())} kördü)" if bas else ad)
            k.update(kor=False, kor_baslangic=None, son_kor_bildirim=None)
            return True
        return degisti

    def basarisiz(self, ad, sebep):
        self.hata_sayisi[ad] = self.hata_sayisi.get(ad, 0) + 1
        k, zaman = self.k(ad), simdi()
        degisti = k.get("son_hata") != sebep
        k["son_hata"] = sebep
        if not k.get("hata_baslangic"):
            k["hata_baslangic"] = iso(zaman)
            degisti = True
        bas = iso_coz(k["hata_baslangic"]) or zaman
        gecen = (zaman - bas).total_seconds()
        log(f"{ad}: KÖR/BAŞARISIZ ({self.hata_sayisi[ad]}. kez, {sure_yazi(gecen)} süredir) - {sebep}")
        if gecen < KOR_DAKIKA * 60:
            return degisti
        if not k.get("kor"):
            k.update(kor=True, kor_baslangic=k["hata_baslangic"], son_kor_bildirim=iso(zaman))
            self.kor_yeni.append(f"• {ad}: {sebep}")
            return True
        son = iso_coz(k.get("son_kor_bildirim") or "") or zaman
        if (zaman - son).total_seconds() >= KOR_HATIRLATMA_SAAT * 3600:
            k["son_kor_bildirim"] = iso(zaman)
            self.kor_hatirlat.append(f"• {ad}: {sebep} ({sure_yazi(gecen)} süredir)")
            return True
        return degisti

    def uyarilari_gonder(self):
        son = (self.durum.get("son_okuma") or {}).get("zaman")
        son_yazi = f"\nSon başarılı okuma: {tarih_yazi(iso_coz(son), saat=True)}" if son else ""
        if self.kor_yeni:
            bildirim(f"En az {KOR_DAKIKA} dakikadır site okunamıyor:\n" + "\n".join(self.kor_yeni) + son_yazi +
                     "\n\nAyrıntı: GitHub → Actions → son çalışma → log ve Artifacts → pusu-debug",
                     baslik="PUSU KÖR KALDI", oncelik=4, etiketler=["warning"], link=f"{SITE}/duyurular")
        if self.kor_hatirlat:
            bildirim("Hâlâ okunamıyor:\n" + "\n".join(self.kor_hatirlat) + son_yazi,
                     baslik="PUSU hâlâ kör", oncelik=3, etiketler=["warning"], link=f"{SITE}/duyurular")
        if self.duzelen:
            bildirim("Tekrar okunuyor: " + ", ".join(self.duzelen) + ". Nöbete devam.",
                     baslik="PUSU tekrar görüyor", oncelik=3, etiketler=["white_check_mark"])
        self.kor_yeni, self.kor_hatirlat, self.duzelen = [], [], []

    # ── bilgilendirme mesajları ──
    def en_yeni_satirlari(self):
        en_yeni = (self.durum.get("son_okuma") or {}).get("en_yeni") or []
        return "\n".join(f"{i}) {tarih_yazi(tarih_coz(v['tarih']))} · {v['baslik'][:110]}"
                         for i, v in enumerate(en_yeni, 1)) or "(hiç okunamadı)"

    def kurulum_bildir(self):
        if self.durum.get("kurulum_bildirildi") == KURULUM_SURUMU or not self.durum.get("baz_alindi"):
            return False
        mesaj = (f"Yeni okuma yöntemi: sitenin kendi veri kaynağı (API). Takipte {len(self.durum['gorulen'])} kayıt.\n\n"
                 f"Sitede gördüğüm en yeni duyurular:\n{self.en_yeni_satirlari()}\n\n"
                 "Bunlar tvgfbf.gov.tr/duyurular sayfasının en üstündekilerle aynıysa her şey yolunda.")
        eski_son = (self.durum.get("gecis") or {}).get("eski_son_kayit")
        if eski_son:
            sinir = iso_coz(eski_son)
            kacan = [v for v in self.durum["gorulen"].values()
                     if onemli_mi(v["baslik"]) and tarih_coz(v.get("tarih")) and sinir and tarih_coz(v["tarih"]) > sinir]
            if kacan:
                kacan.sort(key=lambda v: v["tarih"], reverse=True)
                mesaj += (f"\n\nEski bot {tarih_yazi(sinir)} sonrasını göremiyordu. O tarihten beri yayınlanan "
                          f"{len(kacan)} kurs/antrenörlük duyurusu:\n" +
                          "\n".join(f"• {tarih_yazi(tarih_coz(v['tarih']))} · {v['baslik'][:90]}" for v in kacan[:10]))
        if not bildirim(mesaj, baslik="PUSU v3 AKTİF", oncelik=4, etiketler=["white_check_mark", "muscle"],
                        link=f"{SITE}/duyurular"):
            return False  # gitmediyse sonraki turda tekrar denenir
        self.durum["kurulum_bildirildi"] = KURULUM_SURUMU
        return True

    def gunluk_rapor(self):
        z = simdi()
        bugun = z.date().isoformat()
        if z.hour < GUNLUK_RAPOR_SAATI or self.durum.get("son_rapor") == bugun or not self.durum.get("baz_alindi"):
            return False
        ok, hata = self.son_24_saat()
        so = self.durum.get("son_okuma") or {}
        son = iso_coz(so.get("zaman") or "")
        satirlar = []
        for ad in KAYNAKLAR:
            k = self.k(ad)
            if k.get("hata_baslangic"):
                satirlar.append(f"❌ {ad}: {k.get('son_hata', 'okunamıyor')[:100]}")
            else:
                satirlar.append(f"✅ {ad}")
        gecikme = f" ({sure_yazi((z - son).total_seconds())} önce)" if son else ""
        gitti = bildirim(f"Sitede şu an gördüğüm en yeni 3 duyuru:\n{self.en_yeni_satirlari()}\n\n"
                 f"Son başarılı okuma: {tarih_yazi(son, saat=True) if son else 'YOK'}{gecikme}\n"
                 f"Son 24 saat: {ok} başarılı / {hata} başarısız tur\n"
                 f"Okuma yolu: {'doğrudan API' if so.get('yontem') == 'api' else 'tarayıcı (yedek)'}\n"
                 + "\n".join(satirlar) + f"\nTakipteki kayıt: {len(self.durum['gorulen'])}\n\n"
                 "Dokun → duyurular sayfası açılır, en üstteki 3 duyuruyla karşılaştır.",
                 baslik="Günlük kontrol" + (" ⚠️" if hata > ok or any("❌" in s for s in satirlar) else ""),
                 oncelik=2 if ok and hata <= ok else 4, etiketler=["eyes"], link=f"{SITE}/duyurular")
        if not gitti:
            return False  # gitmediyse sonraki turda tekrar denenir
        self.durum["son_rapor"] = bugun
        return True

    # ── tek tur ──
    def tur(self):
        degisti = False
        ozet = []
        for ad, (_, _, aralik) in KAYNAKLAR.items():
            if not self.tek_tur and time.monotonic() - self.son_kontrol.get(ad, -1e9) < aralik - 2:
                continue
            self.son_kontrol[ad] = time.monotonic()
            try:
                ogeler = self.okuyucu.kaynak_oku(ad)
                if ad == ZORUNLU and not ogeler:
                    raise OkumaHatasi("API yanıt verdi ama 0 duyuru döndü")
            except OkumaHatasi as e:
                if ad == ZORUNLU:
                    self.say(False)
                    if e.dogrulama or any(h in str(e) for h in AG_HATALARI):
                        self.engel_sayisi += 1
                degisti |= self.basarisiz(ad, str(e)[:200])
                ozet.append(f"{ad}: KÖR")
                n = self.hata_sayisi[ad]
                if n in (1, 5) or n % 30 == 0:
                    debug_kaydet(self.okuyucu, ad, e.govde)
                continue

            self.son_ogeler[ad] = ogeler
            degisti |= self.isle(ad, ogeler)
            yeni_ = en_yeniler(ogeler, 1)
            ozet.append(f"{ad}: {len(ogeler)} kayıt (en yeni {yeni_[0]['tarih'] if yeni_ else '-'})")

            if ad == ZORUNLU:
                self.engel_sayisi = 0
                ilk = en_yeniler(ogeler, 1)[0]
                t = tarih_coz(ilk["tarih"])
                if not t or simdi() - t > timedelta(days=BAYAT_GUN):
                    # Okundu ama veri mantıksız derecede eski: önbellek/yanlış kaynak olabilir
                    self.say(False)
                    degisti |= self.basarisiz(ad, f"en yeni duyuru {tarih_yazi(t)} tarihli "
                                                  f"({BAYAT_GUN}+ gün eski) - veri bayat olabilir, siteyi kontrol et")
                    ozet[-1] += " BAYAT"
                    continue
                self.say(True)
                self.durum["son_okuma"] = {"zaman": iso(), "yontem": self.okuyucu.yontem,
                                           "en_yeni": en_yeniler(ogeler, 3)}
                self.durum["baz_alindi"] = True
            degisti |= self.basarili(ad)

        if (self.engel_sayisi >= YENI_IP_ESIGI and not self.yeni_ip_istendi and GITHUB):
            self.yeni_ip_istendi = yeni_calisma_iste()

        self.uyarilari_gonder()
        degisti |= self.kurulum_bildir()
        degisti |= self.gunluk_rapor()
        z = simdi()
        yarim_saat = f"{z:%Y%m%d%H}{z.minute // 30}"
        iptal_oncesi = z.minute % 30 == 29 and self.son_iptal_oncesi != yarim_saat
        if degisti or (self.ist_kirli and (iptal_oncesi or time.monotonic() - self.son_ist_kayit > ISTATISTIK_KAYIT_SN)):
            self.durum = durum_kaydet(self.durum)
            self.son_ist_kayit, self.ist_kirli = time.monotonic(), False
            if iptal_oncesi:
                self.son_iptal_oncesi = yarim_saat
        return ozet

    def ilk_okuma_logu(self):
        for ad, ogeler in self.son_ogeler.items():
            log(f"{ad}: {len(ogeler)} kayıt okundu (yöntem: {self.okuyucu.yontem}). En yeniler:")
            for v in en_yeniler(ogeler, 10 if ad == ZORUNLU else 3):
                log(f"   • {v['tarih']} | {v['baslik'][:80]} → {v['link']}")

    # ── ana döngü ──
    def calis(self):
        baslangic = time.monotonic()
        tur_no = 0
        try:
            while time.monotonic() - baslangic < MAKS_CALISMA_SN:
                tur_no += 1
                t0 = time.monotonic()
                ozet = self.tur()
                log(f"Tur {tur_no} | " + " | ".join(ozet) + f" | {time.monotonic() - t0:.1f} sn")
                if tur_no == 1:
                    self.ilk_okuma_logu()
                if self.tek_tur:
                    break
                time.sleep(max(5, TUR_ARALIGI_SN - (time.monotonic() - t0)))
            else:
                log("Maksimum çalışma süresi doldu, temiz kapanış")
        finally:
            self.durum = durum_kaydet(self.durum, "Pusu hafızası (kapanış)") if GITHUB else self.durum
            durum_yaz(self.durum)
            self.okuyucu.kapat()


def listele():
    """Siteyi bir kez okur ve ekrana basar. Hiçbir şey kaydetmez, bildirim göndermez."""
    o = Okuyucu()
    try:
        for i, ad in enumerate(KAYNAKLAR):
            if i:
                time.sleep(3)
            ogeler = o.kaynak_oku(ad)
            print(f"\n=== {ad}: {len(ogeler)} kayıt (yöntem: {o.yontem}) ===")
            for v in en_yeniler(ogeler, 30):
                print(f"  {v['tarih']} | {'[ÖNEMLİ] ' if onemli_mi(v['baslik']) else ''}{v['baslik'][:90]}\n"
                      f"      {v['link']}")
    finally:
        o.kapat()


def baslangicta_esitle():
    """Actions: önceki (iptal edilen) çalışmanın son kaydını al, yerel durumla birleştir."""
    if not GITHUB:
        return
    time.sleep(15)
    uzak = uzak_durum(os.environ.get("GITHUB_REF_NAME", "main"))
    if uzak:
        durum_yaz(birlestir(durum_yukle(), uzak))


def _durdur(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    for akis in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe karakter/ok işareti hatası olmasın
        try:
            akis.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--listele" in sys.argv:
        listele()
        sys.exit(0)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", NTFY_KANAL):
        # Değeri loga basmadan sadece biçim bilgisini ver
        log(f"HATA: NTFY_KANAL {'tanımlı değil' if not NTFY_KANAL else f'geçersiz ({len(NTFY_KANAL)} karakter; sadece harf, rakam, - ve _ olmalı)'}. "
            "GitHub → Settings → Secrets and variables → Actions → NTFY_KANAL")
        sys.exit(1)
    signal.signal(signal.SIGTERM, _durdur)
    log(f"PUSU v3 başlıyor | kaynaklar: {', '.join(KAYNAKLAR)} | çalışma: {CALISMA_NO}")
    baslangicta_esitle()
    try:
        Pusu(tek_tur="--tek-tur" in sys.argv).calis()
    except KeyboardInterrupt:
        log("Durduruldu (yeni çalışma devraldı), hafıza kaydedildi")
