# -*- coding: utf-8 -*-
"""
PUSU SİSTEMİ v2 — TVGFBF duyuru takip botu
GitHub Actions + Playwright (gerçek Chromium) + ntfy.sh bildirimleri
"""

import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ══════════════════════════════ AYARLAR ══════════════════════════════
NTFY_KANAL = os.environ.get("NTFY_KANAL") or "umut_antrenorluk_pusu"
ALAN_ADI = "tvgfbf.gov.tr"
SITE = f"https://{ALAN_ADI}"

# İlk sayfa zorunlu. Diğerleri 404 verirse o çalışma boyunca sessizce atlanır.
SAYFALAR = {
    "Duyurular": f"{SITE}/duyurular",
    "Kurslar": f"{SITE}/kurslar",
    "Anasayfa": f"{SITE}/",
}
ZORUNLU_SAYFA = "Duyurular"

# Linkin ilk klasörü bunlardan birini içeriyorsa duyuru sayılır:
# /duyurular/x, /duyuru-detay/x, /kurslar/x, /haberler/x ...
KOKLER = ["duyuru", "kurs", "haber", "etkinlik", "seminer", "faaliyet"]
HARIC_PARCALAR = {"sayfa", "page", "kategori", "category", "etiket", "tag", "arsiv", "ara", "search"}

# Başlıkta bunlar geçerse en yüksek öncelikle (5) gelir, diğerleri 4
ONEMLI_KELIMELER = ["kurs", "kademe", "antrenor", "seminer", "vize", "on kayit", "fitness"]

TUR_ARALIGI_SN = 45                    # iki tur başlangıcı arası
MAKS_CALISMA_SN = 5 * 3600 + 40 * 60   # GitHub 6 saat sınırının altında kal
KOR_DAKIKA = 15                        # bir sayfa bu kadar dk kesintisiz okunamazsa alarm
KOR_HATIRLATMA_SAAT = 6                # kör kalmaya devam ederse hatırlatma aralığı
TOPLU_ESIK = 6                         # aynı anda bundan fazla yeni kayıt = site yapısı değişti
GUNLUK_RAPOR_SAATI = 9                 # her sabah bu saatten sonra "nöbetteyim" mesajı
KAYNAK_ENGELLE = True                  # resim/font/video indirme (hız)
MAKS_KAYIT = 3000
KURULUM_SURUMU = 4  # değişince "AKTİF" mesajı bir kez tekrar gelir
YAPI_SURUMU = 3     # okuma yöntemi değişince tüm sayfalar sessizce yeniden baz alınır

DURUM_DOSYASI = Path("durum.json")
DEBUG = Path("debug")
TR = ZoneInfo("Europe/Istanbul")
KORUMA_IPUCLARI = ("moment", "checking your browser", "attention required", "lütfen bekleyin",
                   "bir dakika", "ddos", "security check", "just a sec")

DESEN = (rf"^https?://(www\.)?{re.escape(ALAN_ADI)}/"
         rf"[^/?#]*({'|'.join(KOKLER)})[^/?#]*/[^/?#]+")

# ══════════════════════════════ JS ══════════════════════════════
# Sitenin yeni yapısı: duyurular <a> link değil, tıklanınca açılan <button> kartlar.
# Kartın kimliği kapak resmi yolunda: /public/announcement/<slug>/kapak/...
# Detay sayfası: /duyurular/<slug>
JS_ORTAK = r"""
  const re = new RegExp(desen, 'i');
  const SLUG_RE = /\/public\/(?:announcement|announcements|duyuru|duyurular)\/([^\/?#]+)\//i;
  const temiz = s => (s || '').replace(/\s+/g, ' ').trim();
  const BAS = 'h1,h2,h3,h4';
  const disarida = el => el.closest('header, footer, nav');
  // Başlığın kartını bul: yukarı çık, içinde başka başlık olmayan en geniş kutu
  const kartBul = h => {
    let kart = h, el = h;
    for (let i = 0; i < 8 && el.parentElement && el.parentElement !== document.body; i++) {
      el = el.parentElement;
      if (el.querySelectorAll(BAS).length > 1) break;
      kart = el;
    }
    return kart;
  };
"""

JS_SAY = """(desen) => {""" + JS_ORTAK + """
  let n = 0;
  for (const a of document.querySelectorAll('a[href]')) if (re.test(a.href)) n++;
  for (const h of document.querySelectorAll(BAS)) if (!disarida(h) && temiz(h.innerText).length >= 5) n++;
  return n;
}"""

JS_HAZIR = """(desen) => {""" + JS_ORTAK + """
  for (const h of document.querySelectorAll('button ' + BAS.split(',').join(', button ') + ', article h3, a h3'))
    if (temiz(h.innerText).length >= 5) return true;
  return false;
}"""

JS_TOPLA = """(desen) => {""" + JS_ORTAK + r"""
  const kartlar = [];
  // A) Başlık içeren kartlar (buton, link, div fark etmez)
  for (const h of document.querySelectorAll(BAS)) {
    if (disarida(h)) continue;
    const baslik = temiz(h.innerText);
    if (baslik.length < 5) continue;
    const kart = kartBul(h);
    let slug = '', link = '';
    for (const img of kart.querySelectorAll('img[src], [style*="url("]')) {
      const kaynak = img.getAttribute('src') || img.getAttribute('style') || '';
      const m = kaynak.match(SLUG_RE);
      if (m) { slug = decodeURIComponent(m[1]); break; }
    }
    const a = [kart, ...kart.querySelectorAll('a[href]')].find(x => x.href && re.test(x.href)) || kart.closest('a[href]');
    if (a && re.test(a.href)) link = a.href;
    const tarihEl = [...kart.querySelectorAll('p, span, time, small')].map(x => temiz(x.innerText))
      .find(t => /^\d{1,2} [A-Za-zÇĞİÖŞÜçğıöşü]+ \d{4}$/.test(t) || /^\d{4}-\d{2}-\d{2}$/.test(t));
    kartlar.push({baslik, slug, link, tarih: tarihEl || ''});
  }
  // B) Kart dışında kalan düz duyuru linkleri (Kurslar sayfası gibi)
  for (const a of document.querySelectorAll('a[href]')) {
    if (disarida(a) || !re.test(a.href)) continue;
    const h = a.querySelector(BAS);
    kartlar.push({baslik: temiz(h ? h.innerText : a.innerText).slice(0, 200), slug: '', link: a.href, tarih: ''});
  }
  return kartlar;
}"""

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""


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


def link_normalle(href):
    try:
        u = urlparse(href)
    except Exception:
        return None
    if u.scheme not in ("http", "https"):
        return None
    host = u.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host != ALAN_ADI:
        return None
    parcalar = [p for p in u.path.split("/") if p]
    if len(parcalar) < 2:
        return None
    if not any(k in parcalar[0].lower() for k in KOKLER):
        return None
    if any(p.lower() in HARIC_PARCALAR for p in parcalar):
        return None
    sorgu = "&".join(x for x in u.query.split("&") if x and not x.lower().startswith("utm_"))
    return urlunparse(("https", ALAN_ADI, "/" + "/".join(parcalar), "", sorgu, ""))


def slug_baslik(link):
    from urllib.parse import unquote
    parca = unquote(urlparse(link).path.rstrip("/").split("/")[-1])
    parca = re.sub(r"[-_]+", " ", parca).strip()
    return parca[:1].upper() + parca[1:] if parca else ""


TARIH_EKI = re.compile(r"\s\((\d{1,2} \S+ \d{4}|\d{4}-\d{2}-\d{2})\)$")


def saf(baslik):
    """Karşılaştırma için: sondaki tarih ekini at, harfleri sadeleştir."""
    return ascii_katla(TARIH_EKI.sub("", baslik or "")).strip()


def sure_yazi(saniye):
    dk = int(saniye // 60)
    return f"{dk // 60} sa {dk % 60} dk" if dk >= 60 else f"{dk} dk"


# ══════════════════════════════ BİLDİRİM ══════════════════════════════
def bildirim(mesaj, baslik, oncelik=5, etiketler=("rotating_light",), link=None):
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
            log(f"ntfy hatası: {e}")
        time.sleep(3 * (deneme + 1))
    return False


# ══════════════════════════════ DURUM (hafıza) ══════════════════════════════
def durum_yukle():
    if DURUM_DOSYASI.exists():
        try:
            d = json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
            if d.get("surum") == 2:
                if d.get("yapi_surumu") != YAPI_SURUMU:
                    for s in d.get("sayfalar", {}).values():
                        s["baz_alindi"] = False
                    d["yapi_surumu"] = YAPI_SURUMU
                return d
        except Exception as e:
            log(f"durum.json okunamadı, sıfırdan başlıyorum: {e}")
    return {"surum": 2, "yapi_surumu": YAPI_SURUMU, "gorulen": {}, "sayfalar": {},
            "son_rapor": None, "kurulum_bildirildi": False}


def durum_yaz(durum):
    gorulen = durum["gorulen"]
    if len(gorulen) > MAKS_KAYIT:
        sirali = sorted(gorulen.items(), key=lambda kv: kv[1].get("ilk", ""))
        durum["gorulen"] = dict(sirali[-MAKS_KAYIT:])
    DURUM_DOSYASI.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def durum_kaydet(durum, mesaj="Pusu durumu güncellendi"):
    """Dosyaya yazar ve hemen repoya push'lar; çalışma iptal edilse bile hafıza kaybolmaz."""
    durum_yaz(durum)
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    dal = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        git("add", str(DURUM_DOSYASI))
        if git("diff", "--staged", "--quiet").returncode == 0:
            return
        git("commit", "-m", mesaj)
        for _ in range(3):
            if git("pull", "--rebase", "-X", "theirs", "origin", dal).returncode != 0:
                git("rebase", "--abort")
            p = git("push", "origin", f"HEAD:{dal}")
            if p.returncode == 0:
                log("Hafıza repoya kaydedildi")
                return
            time.sleep(5)
        log(f"git push başarısız: {p.stderr.strip()[-200:]}")
    except Exception as e:
        log(f"git hatası: {e}")


# ══════════════════════════════ TARAYICI ══════════════════════════════
def tarayici_ac(p):
    argumanlar = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
    try:
        tarayici = p.chromium.launch(channel="chromium", headless=True, args=argumanlar)  # yeni headless mod
    except Exception:
        tarayici = p.chromium.launch(headless=True, args=argumanlar)
    ana_surum = tarayici.version.split(".")[0]
    baglam = tarayici.new_context(
        user_agent=f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   f"Chrome/{ana_surum}.0.0.0 Safari/537.36",
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        viewport={"width": 1366, "height": 768},
        extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"},
    )
    baglam.add_init_script(STEALTH_JS)
    if KAYNAK_ENGELLE:
        baglam.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font")
                     else r.continue_())
    log(f"Tarayıcı açıldı (Chromium {tarayici.version})")
    return tarayici, baglam


def koruma_mi(sayfa):
    try:
        baslik = (sayfa.title() or "").lower()
    except Exception:
        return False
    return any(k in baslik for k in KORUMA_IPUCLARI)


class SayfaYok(Exception):
    pass


def sayfa_oku(sayfa, url):
    yanit = sayfa.goto(url, timeout=60000, wait_until="domcontentloaded")
    kod = yanit.status if yanit else None

    # 1) Koruma ekranı ("One moment, please...") varsa geçmesini bekle
    for _ in range(12):
        if not koruma_mi(sayfa):
            break
        sayfa.wait_for_timeout(5000)
    if koruma_mi(sayfa):
        raise RuntimeError(f"Koruma ekranı geçilemedi ({sayfa.title()[:50]})")

    if kod in (404, 410):
        raise SayfaYok(f"HTTP {kod}")

    # 2) İçerik JavaScript ile sonradan geliyor: kartlar görünene kadar bekle
    try:
        sayfa.wait_for_function(JS_HAZIR, arg=DESEN, timeout=25000, polling=500)
    except PWTimeout:
        pass

    # 3) Liste tamamen dolsun: kart sayısı iki ölçümde aynı kalana kadar bekle
    onceki = -1
    for _ in range(10):
        n = sayfa.evaluate(JS_SAY, DESEN)
        if n == onceki and n > 0:
            break
        onceki = n
        sayfa.wait_for_timeout(1500)

    ogeler = {}
    for k in sayfa.evaluate(JS_TOPLA, DESEN):
        baslik = (k["baslik"] or "").strip()[:200]
        if k["link"]:
            anahtar = link_normalle(k["link"])
        elif k["slug"]:
            anahtar = f"{SITE}/duyurular/{k['slug']}"
        else:
            anahtar = f"baslik:{baslik}" if len(baslik) >= 5 else None
        if not anahtar:
            continue
        if not baslik:
            baslik = slug_baslik(anahtar)
        if k.get("tarih"):
            baslik = f"{baslik} ({k['tarih']})" if baslik else k["tarih"]
        if len(baslik) > len(ogeler.get(anahtar, "")):
            ogeler[anahtar] = baslik

    # Aynı duyuru hem kartla hem sade başlıkla geldiyse sade olanı at
    gercek = {saf(v) for k, v in ogeler.items() if not k.startswith("baslik:")}
    ogeler = {k: v for k, v in ogeler.items() if not (k.startswith("baslik:") and saf(k[7:]) in gercek)}

    if not ogeler:
        raise RuntimeError(f"İçerik bulunamadı (HTTP {kod}, sayfa başlığı: {(sayfa.title() or '-')[:50]})")
    duyuru_sayisi = sum(1 for k in ogeler if not k.startswith("baslik:"))
    return ogeler, ("kart" if duyuru_sayisi else "yedek")


def debug_kaydet(sayfa, ad, etiket):
    try:
        DEBUG.mkdir(exist_ok=True)
        dosya = DEBUG / f"{ad}_{etiket}_{simdi():%H%M%S}"
        sayfa.screenshot(path=f"{dosya}.png", full_page=True, timeout=15000)
        Path(f"{dosya}.html").write_text(sayfa.content(), encoding="utf-8")
    except Exception as e:
        log(f"Debug kaydedilemedi: {e}")


# ══════════════════════════════ ANA MANTIK ══════════════════════════════
class Pusu:
    def __init__(self):
        self.durum = durum_yukle()
        self.ilk_hata = {}          # sayfa -> ilk hatanın zamanı (bu çalışmada)
        self.hata_sayisi = {}       # sayfa -> art arda hata
        self.devre_disi = set()
        self.ilk_okuma_yapildi = set()
        self.son_sonuc = {}         # sayfa -> True/False
        self.son_ogeler = {}        # sayfa -> son okunan liste
        self.kor_yeni, self.kor_hatirlat, self.duzelen = [], [], []

    def s(self, ad):
        return self.durum["sayfalar"].setdefault(ad, {"baz_alindi": False, "kor": False})

    # ── yeni kayıtları işle ──
    def isle(self, ad, url, ogeler):
        s, gorulen = self.s(ad), self.durum["gorulen"]
        zaman = simdi().isoformat(timespec="seconds")
        bilinen_basliklar = {saf(x.get("baslik")) for x in gorulen.values()} - {""}
        yeniler = {k: v for k, v in ogeler.items() if k not in gorulen and saf(v) not in bilinen_basliklar}
        for k, v in ogeler.items():  # başlığı bilinen ama anahtarı yeni olanları sessizce ekle
            if k not in gorulen and k not in yeniler:
                gorulen[k] = {"baslik": v, "ilk": zaman, "sayfa": ad}
        guncellendi = False
        for k, v in ogeler.items():
            if k in gorulen and v and v != gorulen[k].get("baslik"):
                gorulen[k]["baslik"] = v
                guncellendi = True

        if not s["baz_alindi"]:
            for k, v in ogeler.items():
                gorulen.setdefault(k, {"baslik": v, "ilk": zaman, "sayfa": ad})
            s["baz_alindi"] = True
            log(f"{ad}: {len(ogeler)} kayıt baz alındı (ilk tanışma, bildirim yok)")
            return True

        if not yeniler:
            return guncellendi

        if len(yeniler) > TOPLU_ESIK:
            ozet = "\n".join(f"• {v or k}" for k, v in list(yeniler.items())[:5])
            bildirim(f"{ad} sayfasında aynı anda {len(yeniler)} yeni kayıt çıktı. Site yapısı değişmiş "
                     f"olabilir, hepsini baz aldım. Yine de bir göz at:\n\n{ozet}",
                     baslik="PUSU: Toplu değişiklik", oncelik=4, etiketler=["warning"], link=url)
        else:
            for k, v in yeniler.items():
                link = url if k.startswith("baslik:") else k
                onemli = onemli_mi(f"{v} {link}")
                log(f"YENİ ({ad}): {v} -> {link}")
                bildirim(f"{v or 'Başlık okunamadı'}\n\n{link}",
                         baslik="YENI KURS / ANTRENORLUK DUYURUSU!" if onemli else "Yeni TVGFBF duyurusu",
                         oncelik=5 if onemli else 4,
                         etiketler=["rotating_light", "muscle"] if onemli else ["loudspeaker"],
                         link=link)

        for k, v in yeniler.items():
            gorulen[k] = {"baslik": v, "ilk": zaman, "sayfa": ad}
        return True

    # ── başarı / hata takibi ──
    def basarili(self, ad):
        self.hata_sayisi[ad] = 0
        self.ilk_hata.pop(ad, None)
        s = self.s(ad)
        s["son_basari"] = simdi().isoformat(timespec="seconds")
        if s.get("kor"):
            try:
                gecen = (simdi() - datetime.fromisoformat(s["kor_baslangic"])).total_seconds()
                self.duzelen.append(f"{ad} ({sure_yazi(gecen)} kördü)")
            except Exception:
                self.duzelen.append(ad)
            s.update(kor=False, kor_baslangic=None, son_kor_bildirim=None)
            return True
        return False

    def basarisiz(self, ad, sebep):
        self.hata_sayisi[ad] = self.hata_sayisi.get(ad, 0) + 1
        self.ilk_hata.setdefault(ad, time.monotonic())
        gecen = time.monotonic() - self.ilk_hata[ad]
        log(f"{ad}: BAŞARISIZ ({self.hata_sayisi[ad]}. kez, {sure_yazi(gecen)}) - {sebep}")
        if gecen < KOR_DAKIKA * 60:
            return False

        s, zaman = self.s(ad), simdi()
        if not s.get("kor"):
            s.update(kor=True, kor_baslangic=zaman.isoformat(timespec="seconds"),
                     son_kor_bildirim=zaman.isoformat(timespec="seconds"))
            self.kor_yeni.append(f"• {ad}: {sebep}")
            return True
        try:
            son = datetime.fromisoformat(s["son_kor_bildirim"])
        except Exception:
            son = zaman
        if (zaman - son).total_seconds() >= KOR_HATIRLATMA_SAAT * 3600:
            s["son_kor_bildirim"] = zaman.isoformat(timespec="seconds")
            self.kor_hatirlat.append(f"• {ad}: {sebep}")
            return True
        return False

    def uyarilari_gonder(self):
        if self.kor_yeni:
            bildirim(f"En az {KOR_DAKIKA} dakikadır okunamayan sayfalar:\n" + "\n".join(self.kor_yeni) +
                     "\n\nEkran görüntüsü: GitHub → Actions → son çalışma → Artifacts → pusu-debug",
                     baslik="PUSU KÖR KALDI", oncelik=4, etiketler=["warning"])
        if self.kor_hatirlat:
            bildirim("Hâlâ okunamıyor:\n" + "\n".join(self.kor_hatirlat),
                     baslik="PUSU hâlâ kör", oncelik=3, etiketler=["warning"])
        if self.duzelen:
            bildirim("Tekrar okunuyor: " + ", ".join(self.duzelen) + ". Nöbete devam.",
                     baslik="PUSU tekrar görüyor", oncelik=3, etiketler=["white_check_mark"])
        self.kor_yeni, self.kor_hatirlat, self.duzelen = [], [], []

    # ── bilgilendirme mesajları ──
    def kurulum_bildir(self):
        if self.durum.get("kurulum_bildirildi") == KURULUM_SURUMU or not self.s(ZORUNLU_SAYFA)["baz_alindi"]:
            return False
        ornek = [v for k, v in self.son_ogeler.get(ZORUNLU_SAYFA, {}).items()
                 if v and not k.startswith("baslik:")][:5]
        satirlar = "\n".join(f"• {t}" for t in ornek) or "• (başlık okunamadı)"
        bildirim(f"Takipte {len(self.durum['gorulen'])} kayıt var. Sitede gördüğüm ilk duyurular:\n\n"
                 f"{satirlar}\n\nBunlar sitedekiyle aynıysa her şey yolunda.",
                 baslik="PUSU v2 AKTİF", oncelik=3, etiketler=["white_check_mark", "muscle"])
        self.durum["kurulum_bildirildi"] = KURULUM_SURUMU
        self.durum["son_rapor"] = simdi().date().isoformat()  # aynı gün rapor tekrarlamasın
        return True

    def gunluk_rapor(self):
        z = simdi()
        bugun = z.date().isoformat()
        if not self.durum.get("kurulum_bildirildi") or z.hour < GUNLUK_RAPOR_SAATI \
                or self.durum.get("son_rapor") == bugun:
            return False
        satirlar = []
        for ad in SAYFALAR:
            if ad in self.devre_disi:
                satirlar.append(f"➖ {ad} (sitede yok, atlanıyor)")
            else:
                satirlar.append(f"{'✅' if self.son_sonuc.get(ad) else '❌'} {ad}")
        bildirim("Pusu nöbette.\n" + "\n".join(satirlar) + f"\nTakipteki kayıt: {len(self.durum['gorulen'])}",
                 baslik="Günlük kontrol", oncelik=2, etiketler=["eyes"])
        self.durum["son_rapor"] = bugun
        return True

    # ── tek tur ──
    def tur(self, sayfa):
        degisti = False
        ozet = []
        for ad, url in SAYFALAR.items():
            if ad in self.devre_disi:
                continue
            try:
                ogeler, mod = sayfa_oku(sayfa, url)
            except SayfaYok as e:
                if ad == ZORUNLU_SAYFA:
                    degisti |= self.basarisiz(ad, str(e))
                    self.son_sonuc[ad] = False
                    ozet.append(f"{ad}: HATA")
                else:
                    log(f"{ad}: {e} → bu sayfa sitede yok, bu çalışmada atlanıyor")
                    self.devre_disi.add(ad)
                continue
            except Exception as e:
                sebep = str(e).strip().splitlines()[0][:150] if str(e).strip() else type(e).__name__
                degisti |= self.basarisiz(ad, sebep)
                self.son_sonuc[ad] = False
                ozet.append(f"{ad}: HATA")
                n = self.hata_sayisi[ad]
                if not sayfa.is_closed() and (n in (1, 5) or n % 25 == 0):
                    debug_kaydet(sayfa, ad, "hata")
                continue

            self.son_sonuc[ad] = True
            self.son_ogeler[ad] = ogeler
            ozet.append(f"{ad}: {sum(1 for k in ogeler if not k.startswith('baslik:'))} duyuru"
                        + (" (yedek mod)" if mod == "yedek" else ""))
            if ad not in self.ilk_okuma_yapildi:
                self.ilk_okuma_yapildi.add(ad)
                log(f"{ad} okundu ({mod} modu), bulunanlar:")
                for k, v in list(ogeler.items())[:30]:
                    log(f"   • {v[:70]}  →  {k}")
                debug_kaydet(sayfa, ad, "ok")
            degisti |= self.basarili(ad)
            degisti |= self.isle(ad, url, ogeler)

        self.uyarilari_gonder()
        degisti |= self.kurulum_bildir()
        degisti |= self.gunluk_rapor()
        if degisti:
            durum_kaydet(self.durum)
        return ozet

    # ── ana döngü ──
    def calis(self):
        baslangic = time.monotonic()
        with sync_playwright() as p:
            tarayici, baglam = tarayici_ac(p)
            sayfa = baglam.new_page()
            tur_no, tam_hata = 0, 0
            try:
                while time.monotonic() - baslangic < MAKS_CALISMA_SN:
                    tur_no += 1
                    t0 = time.monotonic()
                    ozet = self.tur(sayfa)
                    log(f"Tur {tur_no} | " + " | ".join(ozet) + f" | {time.monotonic() - t0:.0f} sn")

                    tam_hata = tam_hata + 1 if ozet and all("HATA" in x for x in ozet) else 0
                    if tam_hata and tam_hata % 3 == 0:
                        log("Üst üste tam başarısız tur, tarayıcı sıfırlanıyor")
                        try:
                            tarayici.close()
                        except Exception:
                            pass
                        tarayici, baglam = tarayici_ac(p)
                        sayfa = baglam.new_page()
                    elif sayfa.is_closed():
                        sayfa = baglam.new_page()

                    time.sleep(max(5, TUR_ARALIGI_SN - (time.monotonic() - t0)))
                log("Maksimum çalışma süresi doldu, temiz kapanış")
            finally:
                durum_yaz(self.durum)
                try:
                    tarayici.close()
                except Exception:
                    pass


def _durdur(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _durdur)
    log(f"PUSU v2 başlıyor | kanal: {NTFY_KANAL} | sayfalar: {', '.join(SAYFALAR)}")
    try:
        Pusu().calis()
    except KeyboardInterrupt:
        log("Durduruldu (yeni çalışma devraldı), hafıza kaydedildi")
