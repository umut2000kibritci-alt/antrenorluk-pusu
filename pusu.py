import requests
from bs4 import BeautifulSoup
import os

NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://tvgfbf.gov.tr/duyurular"

def ntfy_bildirim_gonder(mesaj, baslik="YENI FITNESS DUYURUSU!"):
    requests.post(
        f"https://ntfy.sh/{NTFY_KANAL}",
        data=mesaj.encode('utf-8'),
        headers={
            "Title": baslik,
            "Priority": "5",
            "Tags": "rotating_light,muscle"
        }
    )

def kontrol_et():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        cevap = None
        for deneme in range(3):
            try:
                cevap = requests.get(URL, headers=headers, verify=False, timeout=30)
                break
            except Exception:
                if deneme == 2:
                    raise
        soup = BeautifulSoup(cevap.content, 'html.parser')

        linkler = []
        for a in soup.find_all('a', href=True):
            if '/duyurular/' in a['href']:
                linkler.append(a['href'].strip())

        if not linkler:
            ntfy_bildirim_gonder("Sayfada duyuru linki bulunamadi. Site yapisi degismis olabilir, kontrol et.", baslik="PUSU SORUN")
            return

        yeni_metin = "\n".join(sorted(set(linkler)))

        if not os.path.exists("son_duyuru.txt"):
            ntfy_bildirim_gonder("Sistem kuruldu, site okunuyor, pusu aktif!", baslik="PUSU BASLADI")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)
            return

        with open("son_duyuru.txt", "r", encoding="utf-8") as f:
            eski_metin = f.read().strip()

        eski_linkler = set(eski_metin.split("\n"))
        yeni_linkler = set(linkler)
        eklenenler = yeni_linkler - eski_linkler

        if eklenenler:
            for link in eklenenler:
                tam_link = link if link.startswith("http") else "https://tvgfbf.gov.tr" + link
                ntfy_bildirim_gonder(f"YENI DUYURU VAR!\n\n{tam_link}\n\nHemen bak!")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)

    except Exception as e:
        ntfy_bildirim_gonder(f"Bot hata verdi: {e}", baslik="PUSU HATA")
        print(f"Hata: {e}")

if __name__ == "__main__":
    import urllib3
    import time
    urllib3.disable_warnings()
    for tur in range(10):
        kontrol_et()
        if tur < 9:
            time.sleep(30)
