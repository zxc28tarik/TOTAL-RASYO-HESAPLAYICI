# W6-C — rapor zinciriyle boşluk küçültme: sistem genelinde ölçüm

Tarih: 2026-09-14. Çalışma dalı: `claude/inspiring-cannon-ecxilb`.
Kanonik pano: [Issue #37](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/issues/37) ·
Sözleşme: [ACTIVE_EXECUTION_LEDGER.md](ACTIVE_EXECUTION_LEDGER.md) §W6.

W7-A'nın bulduğu blocker'ı ("son sertifikalı gözlem ile cutoff arası boşluk
kanıtsız") tek bir örnek grupla (SMRTG'nin 8 peer'i, 2023-07-31 cutoff'u)
değil, **60 ay × tüm NONFIN adaylarında** ölçtük. Yöntem: şirketin **kendi**
art arda gelen çeyrek raporları — her biri ayrı, gerçek geçmiş tarihli resmi
KAP belgesi — aynı pay sayısını tekrarlıyorsa, bu tekrar sertifikalı
tabanı ileri tarihe taşıyabilir mi?

**Sonuç dürüstçe belirtilmeli: SMRTG örneği iyimserdi. Sistem genelinde
yöntem işe yarıyor ama mütevazı ölçüde — sorunu çözmüyor.**

## 1. Sonuç özeti

| Kalem | Değer |
|---|---:|
| Toplam NONFIN aday hücre | 4.203 |
| Dış sertifikalı gözlemi olan hücre | 3.030 |
| Gözlemi hiç olmayan hücre | 1.173 |
| Rapor zinciriyle **herhangi** bir doğrulama bulunan | **648 / 3.030 (%21)** |
| Boşluğu küçülen hücre | 648 (aynı) |
| Boşluk medyanı **önce** | 815 gün |
| Boşluk medyanı **sonra** (iyimser okuma) | **534 gün** |
| Boşluk medyanı **sonra** (muhafazakâr okuma) | 534 gün |

**AKSEN'in kendi örneği** (634→84 gün) doğru ve tekrarlanabilir — ama bu,
3.030 sertifikalı hücrenin yalnız **648'inde (%21)** böyle bir zincir
bulunabiliyor, ve bulunduğunda bile **sistem medyanı** hâlâ 534 gün (1,5
yıl) civarında kalıyor. SMRTG'nin 8 peer'i, tesadüfen hepsi aynı çeyrekte
(2023 Ç1) rapor vermiş ve hepsinde zincir çalışmıştı — bu **temsili bir
örnek değilmiş**.

## 2. Yöntem

Her (cutoff, ticker) NONFIN aday hücresi için:

1. **Dış çapa:** W5/W6'nın kullandığı güvenli KAP pay-sınıfı gözlemi
   (`EXPLICIT_CLASS_NOMINALS_RECONCILED`), cutoff öncesi en son tarihli olanı.
2. **Rapor zinciri:** şirketin kendi çeyrek raporları (her biri kendi
   `published_at` tarihiyle, cutoff'tan önce), sırayla tarandı. Çapanın pay
   sayısıyla **birebir** eşleşen her rapor zinciri uzatıyor; ilk uyuşmayan
   rapor (gerçek bir değişikliği gösterebileceği için) zinciri durduruyor.
3. **İki farklı okuma, ikisi de raporlanıyor, hiçbiri seçilmiyor:**
   - `basis_date_published_at`: son doğrulayan raporun **yayın tarihi**
     (iyimser — şirketin bir sermaye değişikliğini derhal ayrı bildirdiği
     varsayılır).
   - `basis_date_period_end`: son doğrulayan raporun **dönem sonu**
     (muhafazakâr — rapor yalnız kendi dönem sonu için kanıt sayılır).
   - Hangisinin doğru olduğu bir **veri sorusu değil, yöntem kararı** — bu
     denetim seçmiyor, açık bırakıyor.

## 3. Neden yalnız %21

648/3.030 rakamının düşük olmasının ana sebepleri:

- Birçok hücrede dış çapa zaten **son çeyrek raporundan daha yeni** (örn.
  yakın zamanda sertifikalanmış bir pay-sınıfı bildirimi) — zincirin
  uzatacağı bir şey yok.
- Bazı tickerlerde raporlar çapa tarihinden sonra **gerçekten farklı** bir
  pay sayısı gösteriyor (muhtemelen gerçek bir sermaye değişikliği) — zincir
  ilk uyuşmazlıkta duruyor, bu doğru ve beklenen davranış.
- Bazı tickerlerde CORE artifact'ta o dönem için raporlanmış çeyrek veri
  hiç yok.

## 4. Ne değişmedi

**Blocker aynı kalıyor.** Rapor zinciri boşluğu küçültse de sıfıra
indirmiyor — sistem medyanı hâlâ 534 gün. W7-A'nın bulduğu üretim kapısı
(`published_at <= cutoff`) hâlâ geçerli ve hâlâ aşılamıyor: bu denetim
**hiçbir M2 üretmedi, üretim kodunu değiştirmedi.**

## 5. Değişmeyenler

Canlı 131 CORE / 48 M2 / 11 Ek9 / 2 Total / 805 ret aynen korunuyor.

## 6. Doğrulama

16 hedef test PASS, 8/8 mutasyon KILLED, `--check` Python 3.11/3.12/3.13'te
bayt düzeyinde aynı receipt.

```bash
python scripts/audit_w6c_report_corroborated_basis.py --apply
python scripts/audit_w6c_report_corroborated_basis.py --check
python -m pytest -q tests/test_w6c_report_corroborated_basis.py
python scripts/audit_w6c_mutations.py --output data/audit/w6c_report_corroborated_basis_v1/mutations.json
```

Artifact'lar: `data/audit/w6c_report_corroborated_basis_v1/{rows,summary,
verdict,mutations,receipt}.json`.

## 7. Sıradaki

Bu yöntem tek başına yeterli değil. Kalan ~534 günlük tipik boşluğu
kapatmak için ya (a) dönemin kendisinde yayımlanmış, arşivlenmiş ayrı bir
tamlık kaynağı gerekiyor (W7-A'nın belirttiği gibi — hâlâ bulunamadı), ya
da (b) `basis_date_published_at` okumasının kabul edilebilir olduğuna dair
açık bir yönetişim kararı (bu da boşluğu yalnız medyan 534 güne indirir,
sıfıra değil).
