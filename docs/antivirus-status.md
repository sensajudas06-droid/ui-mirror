# 1.9.11 Setup: VirusTotal kaydı

Bu belge son kaydedilen taramayı aktarır. Yeni bir tarama veya güvenlik garantisi
değildir; rapordaki sonuçlar zamanla değişebilir.

- Dosya: `Blackwood-UIMirror-Setup-1.9.11.exe`
- Boyut: 18.265.464 bayt
- SHA-256: `13CD9B3B86F5F1E655AC702773BC0F774387104A8851C95C6D2EB9769685D7E3`
- Son kaydedilen analiz: 27.10.2026 23:10:26 (Europe/Istanbul)
- Sonuç: **1/69**
- Uyarı: Trapmine, `Suspicious.low.ml.score`
- [VirusTotal raporu](https://www.virustotal.com/gui/file/13cd9b3b86f5f1e655ac702773bc0f774387104a8851c95c6d2eb9769685d7e3/detection)

Kalan uyarının kesin tetikleyicisi belirlenemedi. İnceleme sırasında gerçek
bağımlılık ve kurulum kusurları giderildi: istek kimlik bilgilerinin kaynak
adrese sınırlandırılması, kilitli bağımlılıklar, yerel derleme girdilerinin
doğrulanması ve farklı kurulum paketlerinin dolu klasöre yazılmasının engellenmesi.
Bu düzeltmelerin Trapmine kararının sebebi olduğu kanıtlanmadı.

Paket incelemesinde Setup içindeki 972 dosya derleme hazırlık klasörüyle,
33 yerel derleme girdisi kayıtlı kaynak hash'leriyle karşılaştırıldı. NSIS
bileşenleri resmi 3.13 dağıtımıyla eşleşti. NSIS CRC değeri tutarlıydı.
38 testin 37'si geçti; Windows sembolik bağlantı yetkisi gerektiren bir test
atlandı. Bu kontroller tüm davranışların güvenli olduğunu kanıtlamaz.

Setup dijital imzalı değildir. Yukarıdaki rapor yalnızca belirtilen dosyaya
aittir. Bu depodan yeniden derlenen bir EXE aynı hash'i veya aynı antivirüs
sonucunu üretmek zorunda değildir. Derleme makinesinin özel yollarını taşıyan
ham loglar, TOC dosyaları ve manifestler kaynak yayınına dahil edilmemiştir.

Kaynak kod ve derleme betikleri bağımsız inceleme için sunulmaktadır. Kesin
sebebi bilinmeyen bu uyarı, burada kesin bir yanlış pozitif olarak tanımlanmaz.
