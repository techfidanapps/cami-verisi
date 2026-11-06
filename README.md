# cami-verisi

Namaz Vakti uygulamasının "Cami bul" özelliği için dünya cami/mescit verisi.

- Kaynak: Geofabrik OSM bölge dosyaları → `osmium tags-filter` → `uret.py`
- Çıktı: `t/{floor(enlem)}_{floor(boylam)}.json` (1°×1° kareler), `meta.json`
- Kare biçimi: `{"v":1,"c":[[enlem, boylam, "ad" | null], ...]}`
- Kare yoksa (404) o bölgede kayıtlı cami yok demektir.
- GitHub Actions her ayın 3'ünde yeniden üretip GitHub Pages'e yayınlar.

Veri © OpenStreetMap katkıcıları, ODbL 1.0 lisanslıdır.
