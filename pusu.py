import requests
from bs4 import BeautifulSoup
import os

# KANAL ADINI YİNE KENDİNKİYLE DEĞİŞTİR
NTFY_KANAL = "umut_antrenorluk_pusu"
URL = "https://tvgfbf.gov.tr/duyurular"

def ntfy_bildirim_gonder(mesaj, baslik="🚨 FITNESS DUYURUSU DEĞİŞTİ!"):
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
        # Bot olduğumuzu gizlemek için tarayıcı kimliğini güncelledik
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # timeout=10 ekledik: 10 saniyede cevap gelmezse asılı kalmayacak, işlemi kesecek
        cevap = requests.get(URL, headers=headers, verify=False, timeout=10)
        soup = BeautifulSoup(cevap.content, 'html.parser')
        
        ilk_duyuru = soup.find('h3')
        if not ilk_duyuru:
            ilk_duyuru = soup.find('a')
            
        if not ilk_duyuru:
            print("Sitede metin bulunamadı.")
            return
            
        yeni_metin = ilk_duyuru.text.strip()
        
        if not os.path.exists("son_duyuru.txt"):
            ntfy_bildirim_gonder("Sistem başarıyla kuruldu. Site okunabiliyor, pusu aktif!", baslik="✅ PUSU BAŞLADI")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)
            return

        with open("son_duyuru.txt", "r", encoding="utf-8") as f:
            eski_metin = f.read().strip()
                
        if yeni_metin != eski_metin:
            ntfy_bildirim_gonder(f"Sitede değişiklik var!\n\nYeni Yazı: {yeni_metin}\n\nSisteme koş: {URL}")
            with open("son_duyuru.txt", "w", encoding="utf-8") as f:
                f.write(yeni_metin)
                
    except Exception as e:
        print(f"Hata oluştu: {e}")

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings() 
    kontrol_et()
