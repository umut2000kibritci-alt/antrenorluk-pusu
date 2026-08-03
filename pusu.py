import requests
from bs4 import BeautifulSoup
import os
import time

NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://tvgfbf.gov.tr/duyurular"

ardarda_hata = 0
alarm_verildi = False

oturum = requests.Session()
oturum.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://tvgfbf.gov.tr/',
    'Upgrade-Insecure-Requests': '1',
})

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
    if ardarda_hata >= 10 and not alarm_verildi:
        ntfy_bildirim_gonder(f"Bot 10 turdur siteyi okuyamiyor: {sebep}", baslik="PUSU KOR KALDI")
        alarm_verildi = True

def sayfayi_al():
    for deneme in range(4):
        cevap = oturum.get(URL, verify=False, timeout=30)
        if "One moment" not in cevap.text and "just a moment" not in cevap.text.lower():
            return cevap
        print(f"  Koruma ekrani, {deneme+1}. deneme, bekliyorum...")
        time.sleep(7)
    return cevap

def kontrol_et():
    global ardarda_hata, alarm_verildi
    try:
        cevap = sayfayi_al()
        soup = BeautifulSoup(cevap.content, 'html.parser')

        linkler = []
        for a in soup.find_all('a', href=True):
            if '/duyurular/' in a['href']:
                linkler.append(a['href'].strip())

        if not linkler:
            basarisiz(f"Link yok (HTTP {cevap.status_code}, {len(cevap.content)} byte)")
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
    import urllib3
    urllib3.disable_warnings()
    for tur in range(600):
        print(f"Tur {tur+1} - {time.strftime('%H:%M:%S')}")
        kontrol_et()
        if tur < 599:
            time.sleep(30)        for a in soup.find_all('a', href=True):
            if '/duyurular/' in a['href']:
                linkler.append(a['href'].strip())

        if not linkler:
            print(f"Link bulunamadi. HTTP kodu: {cevap.status_code}, sayfa boyutu: {len(cevap.content)} byte")
            print(cevap.text[:500])
            return

        print(f"OK - {len(set(linkler))} link bulundu")
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
        print(f"Tur {tur+1} - {time.strftime('%H:%M:%S')}")
        kontrol_et()
        if tur < 599:
            time.sleep(30)
