import requests
from bs4 import BeautifulSoup
import os
import time

NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://tvgfbf.gov.tr/duyurular"

hata_bildirildi = False

def ntfy_bildirim_gonder(mesaj, baslik="YENI FITNESS DUYURUSU!"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_KANAL}",
            data=mesaj.encode('utf-8'),
            headers={
                "Title": baslik,
                "Priority": "5",
                "Tags": "rotating_light,muscle"
            },
            timeout=20
        )
    except Exception as e:
        print(f"Bildirim gonderilemedi: {e}")

def kontrol_et():
    global hata_bildirildi
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        cevap = requests.get(URL, headers=headers, verify=False, timeout=30)
        soup = BeautifulSoup(cevap.content, 'html.parser')

        linkler = []
        for a in soup.find_all('a', href=True):
            if '/duyurular/' in a['href']:
                linkler.append(a['href'].strip())

        if not linkler:
            print("Link bulunamadi.")
            return

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

        hata_bildirildi = False

    except Exception as e:
        print(f"Hata: {e}")
        if not hata_bildirildi:
            ntfy_bildirim_gonder(f"Siteye ulasilamiyor: {e}", baslik="PUSU HATA")
            hata_bildirildi = True

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()
    for tur in range(600):
        kontrol_et()
        if tur < 599:
            time.sleep(30)
