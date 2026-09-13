# V24 5 Yıllık Backtest — GPT × Claude Çift Denetimli Yürütme Planı

Son oluşturma: 2026-08-31

> 2026-09-05 prospective amendment: remaining execution migrated to Codex/Astra
> by user decision. The named Claude requirement below is historical and is
> superseded for remaining work by [ASTRA_EXECUTION_AUTHORIZATION.md](ASTRA_EXECUTION_AUTHORIZATION.md).
> All technical evidence gates remain mandatory.

## Amaç

2021-08 .. 2026-07 arasındaki 60 aylık tarihsel BIST100 çalışmasını, açıkça kabul edilmiş KAP sürüm-enumeration riski altında deneysel olarak tamamlamak; ardından aynı hattı production-grade otoriteye yükseltecek paralel hardening işini sürdürmek.

Bu planın çıktısı yalnız bir görev listesi değildir. Her aşama için karar, uygulama, bağımsız denetim, mutasyon testi, kanıt ve merge kapısı tanımlar.

## Çalışma modeli: GPT × Claude

GitHub ortak karar ve kanıt zemini olacaktır.

Her ana iş paketi şu sırayla ilerler:

1. **Durum senkronizasyonu** — aktif dalın HEAD'i, açık PR/issue'lar, ilgili sözleşme ve son CI kontrol edilir.
2. **Karar paketi** — problem, seçenekler, riskler, önerilen karar ve kabul ölçütleri issue/PR üzerinde yazılır.
3. **Birinci taraf uygulama** — GPT veya Claude kodu/veri paketini hazırlar.
4. **İkinci taraf bağımsız denetim** — diğer taraf ilk tarafın açıklamasına güvenmeden gerçek diff, test, kaynak, PIT sınırı ve mutasyon noktalarını denetler.
5. **Uyuşmazlık çözümü** — taraflar farklı karar verirse üretim davranışı değişmez; uyuşmazlık GitHub'da açık karar kaydıyla çözülmeden merge edilmez.
6. **Kanıt kapısı** — hedef test + birleşik PIT paketi + tam regresyon + gerekiyorsa BANK v4.7 + GitHub CI.
7. **Merge ve durum güncellemesi** — yalnız kabul ölçütleri tamamlanınca `v24-real-data-work` dalına merge edilir ve durum belgesi güncellenir.

### Zorunlu karşı-denetim soruları

Her aşamada bağımsız denetçi en az şunları cevaplar:

- Bu değişiklik hangi gerçek problemi çözüyor?
- Hangi üretim davranışları kesinlikle değişmedi?
- Gelecek veri / current-state / restatement hindsight sızıntısı mümkün mü?
- Eksik veri sessiz fallback/neutral-fill ile kapatılıyor mu?
- Bu test hangi hatalı mutasyonu gerçekten kırıyor?
- Kaynak/provenance SHA ve kapsam iddiaları gerçek dosyayla uyuşuyor mu?
- Sonuç deterministik mi?
- Merge edilirse geri alma sınırı nedir?

## Profil ayrımı

### A. EXPERIMENTAL_RISK_ACCEPTED_5Y

Amaç: kullanıcının açıkça kabul ettiği `SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED` riski altında 5 yıllık gerçek veriyle sonucu görmek.

Kurallar:
- Risk sonuç artifact'ında görünür olacak.
- Production-grade / authoritative / yatırım başarısı iddiası yapılmayacak.
- Fixture/current fallback yasak kalacak.
- Her ay/hisse ya skor ya açık ret üretecek.

### B. AUTHORITATIVE_PIT_5Y

Amaç: Issue #24'ün formal closure kriterlerini karşılayıp aynı hattı production-grade hale getirmek.

Bu profil A'nın tamamlanmasını bloklamaz; paralel hardening hattıdır.

---

# Ana iş paketleri

## P0 — Durum ve koordinasyon zemini

**Hedef:** GitHub'ın güncel gerçeğiyle tek yürütme zemini oluşturmak.

Yapılacaklar:
- `v24-real-data-work` HEAD ve `main` HEAD kaydı.
- PR #27, Issue #24 ve KAP source-capture PR #28 sonrası gerçek durumun CURRENT_PROJECT_STATUS'a işlenmesi.
- Yetkilendirilmiş timing profile `TOTAL_RASYO_MONTHLY_OPEN_V1` artık açık iş olarak gösterilmeyecek.
- 28 KAP arşivi / 16,624 notification / 5,489 target report / 209 ticker source-capture kaydı referanslanacak.
- Bu plan ana yürütme sözleşmesi olarak bağlanacak.

**Kapanış:** belge gerçeği kod/PR/issue gerçeğiyle uyuşuyor ve iki taraf aynı sırayı kabul ediyor.

## P1 — KAP semantik aile kapsamını tamamlama

**Hedef:** 28 arşivdeki hedef raporların tüm sektör ailelerini Total Rasyo girdisine dönüştürebilecek semantik katmanı tamamlamak.

Kapsam:
- NONFIN / HOLDING mevcut kurallarını bağımsız denetle.
- BANK.
- Katılım bankası.
- INSURANCE.
- Diğer FINANCIAL teknik şemaları.
- GYO routing/semantik ayrımını doğrula.

Kurallar:
- Boyut bağlamı doğrulanmadan label eşlemesi kabul edilmez.
- Bir alan tahmin edilmez; `UNSUPPORTED_SCHEMA` / explicit rejection korunur.
- Teknik parser reject ile semantik unsupported ayrımı korunur.
- PR #27 doğrudan merge edilmeden önce aktif dalın yeni HEAD'i ile yeniden tabanlanmalı/uyumluluk denetlenmeli.

**Kabul kapıları:**
- Gerçek arşiv örnekleriyle her aile için pozitif test.
- Yanlış role/dimension/fact label mutasyonları kırılır.
- Hedef ticker/period kapsam raporu çıkar.
- Tam regresyon + BANK v4.7 + GitHub CI.

## P2 — Değerleme ve fiyat boşlukları

### P2-A HOLDING/GYO M2

Mevcut deneysel kanıt:
- 993/993 hücrede exact KAP book equity + capital source.
- 981 deneysel M2.
- Canonical NAD üretim profili değiştirilmedi.

Karar kapısı:
1. Tarihsel gerçek NAD kaynağı bulunursa canonical tarihsel M2.
2. Bulunamazsa `EXPERIMENTAL_*_BOOK_EQUITY_TWO_AXIS_V1` açık risk profili 5Y deney için kullanılabilir.

**Kural:** Proxy hiçbir zaman sessizce canonical NAD gibi etiketlenmez.

### P2-B 12 pre-cutoff fiyat boşluğu

Bilinen retler:
- INVES: 3 ay
- KLRHO: 6 ay
- ASGYO: 3 ay

İş:
- Mevcut resmi Borsa/THB source zinciriyle cutoff öncesi güvenli fiyat aranır.
- Bulunamazsa ret korunur; signal-day/open future leakage yapılmaz.

**Kabul:** 12 hücre ya authoritative pre-cutoff price ya explicit rejection.

## P3 — Risk-kabul edilmiş gerçek KAP PIT materialization

**Hedef:** 28 arşiv + semantik adapter + tarihsel universe + timing policy kullanarak 60 cutoff için deneysel gerçek finansal input setini üretmek.

Profil: `EXPERIMENTAL_RISK_ACCEPTED_5Y`.

Kurallar:
- `PASS_WITH_EXPLICIT_VERSION_ENUMERATION_RISK` etiketi artifact'ın üst seviyesinde bulunur.
- 6,000 month+ticker hücrenin tamamı score-input-ready veya explicit rejection sınıfında bulunur.
- Current ticker/current sector/current financial fallback yok.
- Cutoff sonrası yayın görünmez.
- Source archive/member/notification/semantic lineage korunur.

**Kabul:** 6000/6000 exhaustiveness, kaynak hashleri, rejection reason dağılımı, deterministik ikinci üretim.

## P4 — Gerçek 60-cutoff Total Rasyo artifact

**Hedef:** M1 + M2 + M3 + Ek4 + Ek1 + Ek9'u mevcut production combiner ile 60 ay için üretmek.

Üretim ağırlıkları değişmez:
- M2 0.40
- M1 0.18
- M3 0.12
- Ek4 0.16
- Ek1 0.08
- Ek9 0.06
- veto: `good_count < 5`, factor 0.60

Kurallar:
- `compute_total_rasyo` yeniden yazılmaz.
- Modül eksik/rejected ise sessiz neutral-fill yapılmaz.
- Her cutoff için deterministik skor sırası: skor azalan, ticker artan.

**Kabul:** tam 60 cutoff; her historical member score/rejection; iki bağımsız üretim bit-level aynı; combined PIT tests + full regression.

## P5 — Aylık 1–6 hisselik portföy backtesti

**Hedef:** Kullanıcının asıl sorusunu cevaplayan gerçek işlem defteri.

Sözleşme:
- 2021-08 .. 2026-07, 60 aylık signal.
- O ay gerçek BIST100 evreni.
- `TOTAL_RASYO_MONTHLY_OPEN_V1` timing.
- execution: signal day 10:00 accounting / `DAILY_OPEN` basis.
- her ay 2× geçerli net asgari ücret katkısı.
- maksimum 6 hisse; minimum zorunlu değil.
- AL: alınabilir/tutulabilir.
- İZLE: mevcut pozisyon tutulabilir, yeni katkı verilmez.
- UZAK: satılır.
- corporate action/ticker lineage motorları aynen kullanılır.
- cash, shares, buys, sells, holdings, contribution, cumulative contribution, NAV, XU100 benchmark kaydedilir.

Karar kapısı:
- 1–6 seçim politikasının deterministik tie-break ve nakit tahsis kuralı, kodlamadan önce GPT ve Claude tarafından aynı karar kaydında onaylanır.

**Kabul:** 60 aylık trade ledger + NAV + XU100 + aylık score/decision snapshot; lookahead mutasyonları kırılır.

## P6 — Son denetim, V24-G ve kontrollü terfi

**Hedef:** deneysel 5Y artifact'ını kapatmak; production terfi için doğru sınırı belirlemek.

Denetim:
- İkinci taraf tüm final artifact lineage'ını bağımsız yeniden hesaplar veya hash/contract replay ile doğrular.
- Full regression.
- BANK v4.7.
- GitHub CI.
- V24-G readiness.
- `CURRENT_PROJECT_STATUS.md` ve machine-readable evidence güncellenir.

Karar:
- Experimental risk profili ile sonuç yayımlanabilir ancak `AUTHORITATIVE` diye etiketlenemez.
- `main` terfisi yalnız production gate'leri gerçekten sağlanırsa yapılır; aksi halde çalışma `v24-real-data-work` üzerinde kalır.

## P7 — Paralel production hardening: historical version enumeration

**Hedef:** `SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED` riskini ortadan kaldırmak ve Issue #24'ü formal olarak kapatmak.

Araştırılacak yollar:
- immutable historical KAP notification exports,
- official distribution/API enumeration,
- archived disclosure-version identifiers,
- publication/version timestamp zinciri.

**Kabul:** Issue #24 closure kriterlerinin tamamı; aynı 60 cutoff inputlarının hindsight olmadan authoritative yeniden üretimi.

P7, P1–P6 deneysel hattını bloklamaz.

---

# Bağımlılık sırası

```text
P0
 └─> P1
      ├─> P2-A
      ├─> P2-B
      └─> P3
           └─> P4
                └─> P5
                     └─> P6

P7 = P0'dan sonra paralel hardening hattı
```

P2-A ve P2-B, P1 tamamlanırken paralel yürüyebilir. P3 için gerekli sektör-semantiği ve değerleme kararlarının kilitlenmiş olması gerekir.

# Merge disiplini

Her Pn için ayrı feature branch/PR kullanılacaktır. Bir PR mümkünse tek iş paketini kapatır. `v24-real-data-work` dışında doğrudan `main` hedeflenmez.

Her PR açıklamasında:
- GPT kararı,
- Claude bağımsız kararı,
- uyuşmazlık varsa çözüm kaydı,
- test/mutasyon kanıtı,
- SHA/provenance,
- değişmeyen sözleşmeler,
- kalan riskler
bulunmalıdır.

Claude doğrudan bu oturumdan çağrılamıyorsa aynı GitHub issue/PR, Claude'un bağımsız inceleme zemini olacaktır. Claude'un yorumu/commit'i gelmeden çift-denetim kapısı `PASS` sayılmaz.

# Tamamlanma tanımı

## Deneysel 5 yıllık sonuç tamamlandı
P0–P6 PASS ve final artifact açıkça `EXPERIMENTAL_RISK_ACCEPTED_5Y` etiketli.

## Production-grade 5 yıllık sonuç tamamlandı
P0–P7 PASS, Issue #24 CLOSED, authoritative PIT source/version lineage eksiksiz ve V24-G READY.
