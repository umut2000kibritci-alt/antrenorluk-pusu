import requests
from bs4 import BeautifulSoup
import os

# NTFY KANAL ADINI BURAYA YAZ (Örnek: umut_antrenorluk_pusu)
NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://www.tvgfbf.gov.tr/duyurular"

def ntfy_bildirim_gonder(mesaj):
    requests.post(
        f"https://ntfy.sh/{NTFY_KANAL}",
        data=mesaj.encode('utf-8'),
        headers={
            "Title": "🚨 FITNESS DUYURUSU DEĞİŞTİ!",
            "Priority": "5",
            "Tags": "rotating_light,muscle"
        }
    )

def kontrol_et():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        cevap = requests.get(URL, headers=headers)
        soup = BeautifulSoup(cevap.content, 'html.parser')
        
        # Sitedeki ilk duyuruyu çekiyoruz
        ilk_duyuru = soup.find('h3')
        
        if not ilk_duyuru:
            return
            
        yeni_metin = ilk_duyuru.text.strip()
        
        # Eski duyuruyla karşılaştır
        eski_metin = ""
        if os.path.exists("son_duyuru.txt"):
            with open("son_duyuru.txt", "r", encoding="utf-8") as f:
                eski_metin = f.read().strip()
                
        if yeni_metin != eski_metin:
            # Bildirim gönder
            ntfy_bildirim_gonder(f"Sitede değişiklik var!\n\n{yeni_metin}\n\nSisteme koş: {URL}")
            
            # Yeni duyuruyu kaydet
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)
                
    except Exception as e:
        pass

if __name__ == "__main__":
    kontrol_et()
