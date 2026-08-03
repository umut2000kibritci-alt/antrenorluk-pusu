import requests
import os
import time
from playwright.sync_api import sync_playwright

NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://tvgfbf.gov.tr/duyurular"

ardarda_hata = 0
alarm_verildi = False


def ntfy_bildirim_gonder(mesaj, baslik="YENI FITNESS DUYURUSU!"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_KANAL}",
            data=mesaj.encode('utf-8'),
            headers={"Title": baslik, "Priority": "5", "Tags": "rotating_light,muscle"},
            timeout=20
        )
    except Exception as e:
        print(f"Bildirim gonderilemedi: {e}")


def basarisiz(sebep):
    global ardarda_hata, alarm_verildi
    ardarda_hata += 1
    print(f"BASARISIZ ({ardarda_hata}) - {sebep}")
    if ardarda_hata >= 5 and not alarm_verildi:
        ntfy_bildirim_gonder(f"Bot 5 turdur siteyi okuyamiyor: {sebep}", baslik="PUSU KOR KALDI")
        alarm_verildi = True


def linkleri_al(sayfa):
    sayfa.goto(URL, timeout=60000, wait_until="domcontentloaded")
    for _ in range(8):
        baslik = (sayfa.title() or "").lower()
        if "moment" in baslik:
            sayfa.wait_for_timeout(5000)
        else:
            break
    hrefler = sayfa.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    return [h.strip() for h in hrefler if h and '/duyurular/' in h]


def kontrol_et(sayfa):
    global ardarda_hata, alarm_verildi
    try:
        linkler = linkleri_al(sayfa)

        if not linkler:
            basarisiz("Link yok, koruma ekrani asilamadi")
            return

        print(f"OK - {len(set(linkler))} link bulundu")
        ardarda_hata = 0
        alarm_verildi = False

        yeni_metin = "\n".join(sorted(set(linkler)))

        if not os.path.exists("son_duyuru.txt"):
            ntfy_bildirim_gonder("Sistem kuruldu, pusu aktif!", baslik="PUSU BASLADI")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)
            return

        with open("son_duyuru.txt", "r", encoding="utf-8") as f:
            eski_metin = f.read().strip()

        eklenenler = set(linkler) - set(eski_metin.split("\n"))

        if eklenenler:
            for link in eklenenler:
                tam_link = link if link.startswith("http") else "https://tvgfbf.gov.tr" + link
                ntfy_bildirim_gonder(f"YENI DUYURU VAR!\n\n{tam_link}\n\nHemen bak!")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)

    except Exception as e:
        basarisiz(str(e)[:120])


if __name__ == "__main__":
    with sync_playwright() as p:
        tarayici = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        baglam = tarayici.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="tr-TR",
            viewport={"width": 1366, "height": 768},
        )
        sayfa = baglam.new_page()
        for tur in range(600):
            print(f"Tur {tur+1} - {time.strftime('%H:%M:%S')}")
            kontrol_et(sayfa)
            if tur < 599:
                time.sleep(30)
