"""
OSM PBF dosyalarındaki camileri/mescitleri 1°x1°'lik JSON karelerine böler.

Kullanım:
    python uret.py --cikti site f_asia.pbf f_europe.pbf ...
    python uret.py --cikti site --deneme kucuk_bolge.pbf

Girdi dosyaları ham Geofabrik bölgeleri de olabilir, `osmium tags-filter` ile
önceden süzülmüş küçük dosyalar da (GitHub Actions ikincisini kullanır).

Çıktı:
    site/t/{enlem}_{boylam}.json   -> {"v":1,"c":[[enlem, boylam, "ad" | null], ...]}
    site/meta.json                 -> oluşturma tarihi, kare sayısı, toplam kayıt
Kare anahtarı floor(enlem)_floor(boylam): 41.03, 28.97 -> "41_28"; -6.2, 106.8 -> "-7_106".
"""

import argparse
import datetime as dt
import json
import math
import os
import shutil
import sys
from collections import defaultdict

import osmium

VERI_SURUMU = 1
KARE_DERECESI = 1
# Aynı caminin hem nokta (place_of_worship) hem bina (building=mosque) olarak
# işaretlendiği durumlar için birleştirme mesafesi.
BIRLESTIRME_M = 40.0
# Tam dünya verisinde bundan az kayıt çıkarsa bir şeyler ters gitmiştir;
# yarım veriyi yayınlayıp eski sağlam veriyi ezmemek için durulur.
ASGARI_TOPLAM = 150_000


def cami_mi(etiketler) -> bool:
    if etiketler.get("building") == "mosque":
        return True
    return (
        etiketler.get("amenity") == "place_of_worship"
        and etiketler.get("religion") == "muslim"
    )


def ad_al(etiketler):
    ad = etiketler.get("name")
    return ad.strip() if ad and ad.strip() else None


class CamiToplayici(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        # (tür, id) -> (enlem, boylam, ad, öncelik). Öncelik: place_of_worship=2, sadece bina=1
        self.kayitlar = {}

    def _ekle(self, anahtar, enlem, boylam, etiketler):
        oncelik = 2 if etiketler.get("amenity") == "place_of_worship" else 1
        self.kayitlar[anahtar] = (enlem, boylam, ad_al(etiketler), oncelik)

    def node(self, n):
        if cami_mi(n.tags) and n.location.valid():
            self._ekle(("n", n.id), n.location.lat, n.location.lon, n.tags)

    def way(self, w):
        if not cami_mi(w.tags):
            return
        noktalar = [(d.lat, d.lon) for d in w.nodes if d.location.valid()]
        if w.is_closed() and len(noktalar) > 1:
            noktalar = noktalar[:-1]  # kapanış düğümü iki kez sayılmasın
        if noktalar:
            e = sum(p[0] for p in noktalar) / len(noktalar)
            b = sum(p[1] for p in noktalar) / len(noktalar)
            self._ekle(("w", w.id), e, b, w.tags)

    def area(self, a):
        # Kapalı yollar zaten way() içinde işlendi; burada yalnızca ilişkiler (multipolygon).
        if a.from_way() or not cami_mi(a.tags):
            return
        noktalar = [(d.lat, d.lon) for dis in a.outer_rings() for d in dis if d.location.valid()]
        if noktalar:
            e = sum(p[0] for p in noktalar) / len(noktalar)
            b = sum(p[1] for p in noktalar) / len(noktalar)
            self._ekle(("r", a.orig_id()), e, b, a.tags)


def mesafe_m(e1, b1, e2, b2):
    x = math.radians(b2 - b1) * math.cos(math.radians((e1 + e2) / 2))
    y = math.radians(e2 - e1)
    return 6_371_000 * math.hypot(x, y)


def birlestir(kayitlar):
    """Birbirine çok yakın, adları çelişmeyen kayıtları teke indirir."""
    # Önce adlı ve place_of_worship olanlar gelsin; onlar korunur.
    sirali = sorted(kayitlar, key=lambda k: (-k[3], k[2] is None))
    hucre = defaultdict(list)  # ~0.0005° ızgara -> tutulan kayıtlar
    tutulan = []
    for e, b, ad, onc in sirali:
        he, hb = int(e / 0.0005), int(b / 0.0005)
        cakisti = False
        for de in (-1, 0, 1):
            for db in (-1, 0, 1):
                for k in hucre[(he + de, hb + db)]:
                    if mesafe_m(e, b, k[0], k[1]) <= BIRLESTIRME_M and (
                        ad is None or k[2] is None or ad == k[2]
                    ):
                        cakisti = True
                        if k[2] is None and ad is not None:
                            k[2] = ad  # bina adsız, nokta adlıysa adı taşı
                        break
                if cakisti:
                    break
            if cakisti:
                break
        if not cakisti:
            yeni = [e, b, ad]
            hucre[(he, hb)].append(yeni)
            tutulan.append(yeni)
    return tutulan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("girdiler", nargs="+")
    ap.add_argument("--cikti", default="site")
    ap.add_argument("--deneme", action="store_true", help="asgari kayıt kontrolünü atla")
    args = ap.parse_args()

    tum = {}
    for yol in args.girdiler:
        t = CamiToplayici()
        # Süzülmüş dosyalar küçük; düğüm konumları bellekte tutulabilir.
        t.apply_file(yol, locations=True, idx="flex_mem")
        print(f"{yol}: {len(t.kayitlar)} kayıt", flush=True)
        tum.update(t.kayitlar)  # bölgeler sınırda çakışırsa OSM kimliği tekilleştirir

    kareler = defaultdict(list)
    for e, b, ad, onc in tum.values():
        kareler[(math.floor(e / KARE_DERECESI), math.floor(b / KARE_DERECESI))].append((e, b, ad, onc))

    toplam = 0
    hedef_t = os.path.join(args.cikti, "t")
    if os.path.isdir(hedef_t):
        shutil.rmtree(hedef_t)
    os.makedirs(hedef_t)
    for (ke, kb), liste in kareler.items():
        camiler = birlestir(liste)
        camiler.sort(key=lambda k: (k[0], k[1]))
        toplam += len(camiler)
        veri = {"v": VERI_SURUMU, "c": [[round(e, 5), round(b, 5), ad] for e, b, ad in camiler]}
        with open(os.path.join(hedef_t, f"{ke}_{kb}.json"), "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, separators=(",", ":"))

    meta = {
        "v": VERI_SURUMU,
        "kare_derecesi": KARE_DERECESI,
        "olusturma": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kare_sayisi": len(kareler),
        "toplam": toplam,
        "kaynak": "© OpenStreetMap katkıcıları, ODbL",
    }
    with open(os.path.join(args.cikti, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))

    if not args.deneme and toplam < ASGARI_TOPLAM:
        sys.exit(f"HATA: yalnızca {toplam} kayıt çıktı (beklenen >= {ASGARI_TOPLAM}); yayınlanmıyor.")


if __name__ == "__main__":
    main()
