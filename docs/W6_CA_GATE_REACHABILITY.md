# W6 — KAP kurumsal işlem envanteriyle peer kapısının yeniden açılması

Tarih: 2026-09-14. Denetim head'i (envanter tamamlandığında): bkz. commit geçmişi.
Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W5/§W6.

W5, defterin GENEL yeniden açma koşulunu şöyle tanımlamıştı: *"tarihli,
kaynağı hash'e bağlı, boş olmayan bir aralığı boş kanıtlayabilen bir kurumsal
işlem envanteri."* Bu denetim tam olarak bu envanteri üretti ve peer
kapısının yeniden erişilebilirliğini ölçtü.

**Sonuç: 60/60 cutoff'ta peer kapısı artık erişilebilir** (W5'te 0/60 idi).
Bu denetim **hiçbir M2 üretmez** — yalnız kohort kapısının artık geçilebilir
olduğunu kanıtlar. Gerçek M2'nin materyalize edilmesi ayrı, daha büyük bir
adımdır (üretim valuation combiner'ının çağrılması, dönem/FOLLOW ekseninin
doğrulanması, her hücrenin kendi sözleşmelerinden geçmesi) ve bu denetimin
kapsamı dışındadır.

## 1. Sonuç özeti

| Kalem | W5 (önceki) | W6 (bu denetim) |
|---|---:|---:|
| Kapsama tamlığı | zero-interval, tek kaynak | **gap-free, 2016-05→2026-07** |
| Erişilebilir cutoff | 0 / 60 | **60 / 60** |
| Cutoff başına sertifikalanabilir peer | maks 1 | **21–36** (gereken: 6) |
| M2 üretildi mi | Hayır | **Hayır** — kapsam dışı |
| Hedef test / mutasyon | 58 / 20-20 | **32 PASS / 11-11 KILLED** |

## 2. Neden önceki iki yol reddedilmişti, üçüncüsü neden çalıştı

Defterde ve P2 araştırmasında zaten iki yol denenip reddedilmişti:

1. **KAP kurumsal işlem takvimi** — ileriye dönük. 2023-06 için `[]`, 2026-09
   için dolu döndü. Tarihsel arşiv değil.
2. **Geniş aralıklı tek sorgu** (2009→cutoff) — HTTP 500.

`data/backtest_sources/p2_action_research_v1/` ve `p7_version_research_v1/`
içinde daha önce **kanıtlanmış** üçüncü bir yol vardı ama hiç ölçeklendirilmemişti:
piyasa geneli (`mkkMemberOidList: []`), tarih sınırlı `POST` isteği resmî
`disclosure/members/byCriteria` uç noktasına — gerçek tarihsel satırlar
döndürüyor.

**İlk denemede kritik bir kusur bulundu:** ay başına tek istek, yanıtı tam
**2.000 satırda** kesiyor (en yeniden en eskiye sıralı) — kalabalık bir ayın
eski yarısı sessizce düşüyor. Bu, aynı sınıftan bir kanıt-eksikliği hatasıydı
(toplu arşivlerin W10'da bulunan aynı kusuru). Çözüm: **uyarlamalı pencereleme**
— her pencere kapasiteye yaklaşınca otomatik ikiye bölünür, tamlık varsayılmaz,
inşa yoluyla kanıtlanır.

## 3. Yakalama

`scripts/capture_kap_monthly_ca_inventory.py`:

- Pencere: 2016-05-01 → 2026-07-31 (en erken kullanılabilir pay-sınıfı
  gözleminden son cutoff'a kadar, bir ay tampon payıyla).
- 7 günlük başlangıç parçaları; 1.900 satırı geçen her pencere otomatik
  ikiye bölünür, kapasiteye ulaşana kadar tekrar tekrar.
- **595 pencere, 0 hata, 0 çözülemeyen kapasite aşımı, 0 boşluk.**
- 649.244 toplam satır; her istek/yanıt çifti diskte ve hash'e bağlı.

```json
{"windows_ok": 595, "windows_failed": 0, "windows_at_cap_unsplittable": 0,
 "total_rows": 649244, "total_ca_rows": 132668, "gaps": [], "complete": true}
```

## 4. Eşleştirme: `disclosureType` değil, konu metni

Aynı gerçek olay (KLRHO'nun 2023-04-17 sermaye artırım tescili) ham veride
**hem `disclosureType: "CA"` hem `disclosureType: "ODA"`** altında görüldü.
Yalnız `disclosureType` filtrelemesi bu olayın bir kısmını kaçırırdı.

Bunun yerine **fail-closed** bir konu-metni eşleştirmesi kullanıldı:

```
sermaye artır, sermaye azalt, sermaye artırımı, sermaye azaltımı,
birleşme, bölünme, pay grubu
```

Aşırı kapsayıcı olmak yalnız bir sertifikasyonu reddedebilir — asla yanlışlıkla
gerçek bir değişikliği olan bir pencereyi sertifikalamaz. Çoklu-kod satırları
(`stockCodes: "DENIZ, DNZ"` gibi) kontrol edildi — hepsi aynı şirketin farklı
enstrüman kodları, yanlış eşleşme riski yok.

## 5. Sertifikasyon mantığı

Her NONFIN aday hücresi için, en son cutoff-öncesi pay-sınıfı gözleminden
cutoff'a kadar olan açık-kapalı aralık `(gözlem, cutoff]`:

- **Kapsanmamışsa** → `INVENTORY_COVERAGE_GAP`, sertifikalanmaz.
- **İçinde eşleşen bir olay varsa** → `ACTION_DETECTED_IN_WINDOW`, sertifikalanmaz.
- **Aksi halde** → `NO_ACTION_IN_COVERED_WINDOW`, sertifikalanır.

KLRHO doğrulaması: 2023-04-17 olayı, onu içeren pencereyi (2022-07-07→2023-05-31)
doğru şekilde engelliyor; sonraki bağımsız pencereyi (2023-08-02→...) engellemiyor.

## 6. Sonuç dağılımı

| Neden | Hücre |
|---|---:|
| `NO_ACTION_IN_COVERED_WINDOW` (sertifikalı) | 1.735 |
| `ACTION_DETECTED_IN_WINDOW` | 1.291 |
| `NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION` | 1.173 |
| `ZERO_OR_NEGATIVE_LENGTH_INTERVAL` | 4 |

60 cutoff'un her birinde 21–36 arası sertifikalanabilir peer var; kapı için
gereken yalnız 6 (5 peer + hedefin kendisi). **Hiçbir eşik gevşetilmedi.**

## 7. Bu denetimin YAPMADIĞI şeyler

- **Hiçbir M2 değeri üretmedi.** Kohort kapısının artık geçilebilir olduğunu
  gösterir, ama gerçek bir tarihsel M2 üretmek üretim valuation combiner'ının
  çağrılmasını, dönem-doğru revenue/EBIT/FOLLOW türetmesini ve her hücrenin
  kendi kalan sözleşmelerinden (derivation-profile uyumu, coverage eşikleri,
  vb.) geçmesini gerektirir — bunların hepsi ayrı, kapsamlı bir sonraki adımdır.
- Hiçbir hücre onarılmadı, hiçbir skor materyalize edilmedi.
- `minimum_peer_count`, veto veya coverage eşikleri değişmedi.
- Üretim kodu (`src/`, `sql/`, `config/`) değişmedi.

## 8. Değişmeyenler

Canlı 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret ve RGYAS 46,6021011290 ·
TABGD 43,9061751217 aynen korunuyor. Ağırlıklar, veto ve eşikler değişmedi.

## 9. Yeniden üretim

```bash
python scripts/capture_kap_monthly_ca_inventory.py --sleep 1.2 --chunk-days 7
python scripts/audit_w6_ca_gate_reachability.py --apply
python scripts/audit_w6_ca_gate_reachability.py --check
python -m pytest -q tests/test_w6_ca_gate_reachability.py   # 32 test
python scripts/audit_w6_mutations.py --output data/audit/w6_ca_gate_reachability_v1/mutations.json
```

Artifact'lar: `data/audit/w6_ca_gate_reachability_v1/{coverage,certifications,
systemic_bound,verdict,mutations,receipt}.json`,
`data/backtest_sources/kap_monthly_ca_inventory_v1/` (595 istek/yanıt çifti +
`capture_manifest.json`). Hash modu `LF_CANONICAL_SHA256_V1`; receipt manifest
hash'ine bağlı — envanter değişirse receipt otomatik geçersiz olur.

Python **3.11, 3.12 ve 3.13** altında receipt birebir yeniden üretiliyor.

## 10. Sıradaki

Peer kapısı artık açık. Bu, W6'nın **asıl işinin** (gerçek revenue/EBIT/FOLLOW
türetmesi, cohort planı, her hücrenin production combiner'a verilmesi) önündeki
engeli kaldırdı ama W6'yı bitirmedi. Defterin W6 kabul ölçütü hâlâ karşılanmadı:
*"Tarihsel gerçek M2 sayısı, cohort dağılımı, rejection dağılımı, provenance ve
deterministik ikinci üretim yayımlanır."* Bu, ayrı bir yürütme turu gerektirir.
