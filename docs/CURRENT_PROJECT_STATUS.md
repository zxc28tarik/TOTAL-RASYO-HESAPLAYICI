# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

Son doğrulama: **2026-08-31**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: `zxc28tarik/TOTAL-RASYO-HESAPLAYICI`

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `adf3f810914066af7f29087b98aa62efb95a26a1` | PR #22 ile yapılan erken terfi PR #23/revert ile geri alındı; kararlı üretim ağacı korunuyor |
| Aktif tarihsel geliştirme | `v24-real-data-work` | `9528c6093cce8facd830d614b14c275dc2878476` | PR #28 KAP kaynak-capture kimliği korundu; GPT×Claude çift-denetimli 5Y yürütme planı eklendi |

Aktif dal ile `main` körlemesine birleştirilmez. Tarihsel çalışma bütün ilgili kapıları geçmeden `main` üretim terfisi yapılmaz.

## Şu anki hedef

2021-08 .. 2026-07 arasındaki **60 aylık BIST100 point-in-time 5 yıllık backtest**.

Aylık sözleşme:
- tarih-doğru BIST100 üyeleri;
- yalnız cutoff anında bilinebilen veri;
- `TOTAL_RASYO_MONTHLY_OPEN_V1` timing profile;
- önceki gözlenen XU100 işlem seansı sonu cutoff (normal 18:10; kilitli yarım günler 12:40);
- signal-day execution accounting 10:00, price basis `DAILY_OPEN`;
- same-day ve cutoff-sonrası overnight bilgi yasak;
- ayda 2× geçerli net asgari ücret katkısı;
- maksimum 6 hisse;
- AL / İZLE / UZAK sözleşmesi;
- alış, satış, nakit, holdings, katkı, NAV ve XU100 benchmark yayımlanır.

## Kapanmış temel katmanlar

- 60 aylık XU100 sinyal takvimi: KAPALI.
- Tarihsel BIST100 evreni: KAPALI.
- Ticker lineage: KAPALI.
- Aylık üye execution price coverage: KAPALI (6000/6000 temel execution price kaynağı).
- Asgari ücret kaynağı: KİLİTLİ.
- Kurumsal aksiyon semantiği: KAPALI.
- PIT CORE+VAL/RSC/M1: KAPALI.
- PIT M2 altı family replay motorları: KAPALI.
- PIT M3 ve gerçek M3 source package: KAPALI.
- PIT Ek4: KAPALI.
- PIT Ek1 + `good_count_ge8`: KAPALI.
- PIT Ek9: KAPALI.
- Birleşik 60-cutoff Total Rasyo replay/ranking motoru: KAPALI.
- `TOTAL_RASYO_MONTHLY_OPEN_V1` cutoff/execution timing policy: **KAPALI / YETKİLENDİRİLDİ** (PR #21).
- V24-G readiness implementasyonu: UYGULAMA KAPALI; gerçek veri READY henüz yok.

## Gerçek KAP kaynak durumu

### Public KAP collector / inventory

- PR #25: fail-closed individual notification collector contract.
- PR #26: fail-closed 6000-cell Public KAP source inventory.
- Issue #24 formal production blocker olarak OPEN kalır.

### 28 dönemlik KAP toplu finansal capture

PR #28 ile kaynak kimliği/provenance aktif dala kalıcılaştırıldı.

- dönem: 2019Q3 .. 2026Q2;
- 28 KAP bulk archive;
- 16,624 unique notification/report;
- 5,489 target historical report;
- 209 target historical ticker;
- archive bazında exact filename + SHA256 + byte size + member count korunuyor;
- raw ZIP'ler normal Git history'ye alınmıyor.

Formal PIT blocker:
`SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED`.

Bu nedenle authoritative profil için:
- `historical_version_enumeration_complete=false`;
- `pit_materialization_authorized=false`;
- `real_60_cutoff_scoring_authorized=false`.

Ancak kullanıcı açık risk kabulü verdiği için **deneysel 5 yıllık çalışma bu blocker yüzünden durdurulmayacak**. Risk açıkça etiketlenecek:
`PASS_WITH_EXPLICIT_VERSION_ENUMERATION_RISK` / `EXPERIMENTAL_RISK_ACCEPTED_5Y`.

Deneysel source-presence çalışmasında 6000 hücrenin 5997'sinde kaynak bulundu, 3 hücre not-found-with-risk kaldı.

## Açık PR

### PR #27 — KAP bulk financial parser

Durum: OPEN / DRAFT.

Mevcut kapsam:
- bulk HTML parser;
- exact-label semantic adapter;
- NONFIN/HOLDING mapping;
- resumable archive checkpoint;
- gerçek `KAP_2021_3A` pilotu 466/466 classify;
- BANK / participation bank / INSURANCE / diğer FINANCIAL semantiği henüz bilinçli olarak `UNSUPPORTED_SCHEMA`.

PR #27 doğrudan merge edilmeden önce yeni aktif HEAD'e göre yeniden denetlenmeli. Sonraki ana teknik iş diğer sektör ailelerinin dimension-context doğrulanmış semantik mapping'idir.

## HOLDING/GYO deneysel değerleme hattı

Gerçek KAP book-equity proxy çalışması:
- 993/993 hücre exact KAP equity+capital source;
- 981 deneysel M2 score;
- 12 kontrollü rejection: INVES 3, KLRHO 6, ASGYO 3;
- rejection sebebi pre-cutoff güvenli fiyat yokluğu;
- ikinci üretim bit-level aynı;
- canonical NAD ve production profile değiştirilmedi.

Gerçek tarihsel NAD bulunmazsa bu sonuç yalnız `EXPERIMENTAL_HOLDING_BOOK_EQUITY_TWO_AXIS_V1` / `EXPERIMENTAL_GYO_BOOK_EQUITY_TWO_AXIS_V1` etiketiyle deneysel hatta kullanılabilir.

## Açık işler — yeni zorunlu sıra

Ana yürütme sözleşmesi:
`docs/V24_5Y_BACKTEST_DUAL_REVIEW_EXECUTION_PLAN.md`

1. **P0** — durum/koordinasyon ve çift-denetim zemini.
2. **P1** — BANK, participation bank, INSURANCE, diğer FINANCIAL ve GYO routing dahil KAP semantic family coverage.
3. **P2** — HOLDING/GYO değerleme kararı + 12 pre-cutoff price gap.
4. **P3** — risk-kabul edilmiş 6000-cell gerçek KAP PIT materialization.
5. **P4** — gerçek 60-cutoff Total Rasyo score/ranking artifact.
6. **P5** — aylık 1–6 hisselik portfolio/trade ledger + XU100 backtest.
7. **P6** — bağımsız final audit, V24-G ve kontrollü terfi kararı.
8. **P7 (paralel)** — superseded historical KAP version enumeration; Issue #24 authoritative closure.

## Çift denetim kuralı

Her ana P1–P7 işi GitHub issue/PR üzerinde:
- GPT önerisi;
- Claude bağımsız önerisi/denetimi;
- uyuşmazlık çözümü;
- hedef test;
- mutasyon kanıtı;
- full regression/CI;
- source SHA/provenance
ile kapanır.

Claude doğrudan bu ChatGPT oturumundan çağrılamıyorsa GitHub issue/PR ortak devir noktasıdır. Claude'un bağımsız review/commit/yorum kanıtı gelmeden çift-denetim kapısı PASS sayılmaz.

## Production Total Rasyo ağırlıkları

- M2: 0.40
- M1: 0.18
- M3: 0.12
- Ek4: 0.16
- Ek1: 0.08
- Ek9: 0.06
- veto threshold: `good_count < 5`
- veto factor: 0.60

Bu matematik 5Y yürütme sırasında değiştirilmez.
