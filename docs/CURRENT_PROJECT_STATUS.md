# Total Rasyo Hesaplayıcı — Güncel Proje Durumu

> 2026-09-05: Kalan işler kullanıcı kararıyla Codex/Astra'ya devredildi.
> [Yetki ve ikinci denetim sözleşmesi](ASTRA_EXECUTION_AUTHORIZATION.md) bundan
> sonraki işler için aşağıdaki Claude adıyla denetim şartının yerini alır.
> Teknik kapanışlar henüz ilan edilmedi; entegrasyon dalı `codex/astra-v24-finalize`.

Son doğrulama: **2026-09-02**

Bu belge, sohbet sayfaları değişse bile projenin hedefini ve son doğrulanmış durumunu kaybetmemek için tek başlangıç noktasıdır.

## Tek kanonik GitHub deposu

Depo: `zxc28tarik/TOTAL-RASYO-HESAPLAYICI`

| Hat | Dal | Doğrulanmış baş | Anlamı |
|---|---|---|---|
| Üretim | `main` | `adf3f810914066af7f29087b98aa62efb95a26a1` | PR #22 ile yapılan erken terfi PR #23/revert ile geri alındı; kararlı üretim ağacı korunuyor |
| Aktif tarihsel geliştirme | `v24-real-data-work` | `dd2028953bd24f4cd11d0beb2fda93b86ad94930` | P1 PR #38'in güncel base'i; 5Y tarihsel çalışma hattı |
| P1 çalışma dalı | `codex/p1-kap-semantic-family-coverage` | `dd9113a11b07963c57d5564cd641c73617e66eda` | PR #38; GPT PASS, fresh Claude bağımsız denetimi bekleniyor |

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

Kaynak byte/provenance zinciri GitHub release + küçük repo evidence dosyalarıyla korunuyor.

- dönem: 2019Q3 .. 2026Q2;
- 28 KAP bulk archive;
- 16,624 archive member/report;
- 5,489 target historical report;
- 209 target historical ticker;
- archive bazında exact filename + SHA256 + byte size + member count korunuyor;
- raw ZIP'ler normal Git history'ye alınmıyor;
- public release byte doğrulamasında 26 eski manifest exact-match + 2 açık current-snapshot drift (`KAP_2025_Y.zip`, `KAP_2026_6A.zip`) kayıtlı.

Formal PIT blocker:
`SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED`.

Bu nedenle authoritative profil için:
- `historical_version_enumeration_complete=false`;
- `pit_materialization_authorized=false`;
- `real_60_cutoff_scoring_authorized=false`.

Ancak kullanıcı açık risk kabulü verdiği için **deneysel 5 yıllık çalışma bu blocker yüzünden durdurulmayacak**. Risk açıkça etiketlenecek:
`PASS_WITH_EXPLICIT_VERSION_ENUMERATION_RISK` / `EXPERIMENTAL_RISK_ACCEPTED_5Y`.

Deneysel source-presence çalışmasında 6000 hücrenin 5997'sinde kaynak bulundu, 3 hücre not-found-with-risk kaldı.

## P1 — KAP semantic family coverage

### PR #38

Durum: **OPEN / DRAFT / GPT PASS / CLAUDE FRESH REVIEW PENDING**.

Güncel head:
`dd9113a11b07963c57d5564cd641c73617e66eda`

Tamamlanan kapsam:
- fail-closed archive identity doğrulaması: exact set + SHA256 + member_count + duplicate-input;
- deterministic technical schema discovery;
- BANK / participation-bank targeted discovery + exact mapping + gerçek KAP regression;
- INSURANCE / FINANCIAL gerçek namespace discovery;
- INSURANCE / FINANCIAL exact role+row+label+context mapping;
- GYO routing P1 CI kapısına bağlandı;
- yanlış role/row/label/context ve epoch collision mutation testleri;
- gerçek arşiv evidence ve SHA/receipt zinciri.

INSURANCE / FINANCIAL full-28 discovery:
- 28/28 archive;
- 994 hedef rapor;
- 71 source entity;
- 10 technical role;
- 917 exact `(role,row,label)` identity;
- discovery SHA256: `ce0687bd9f418f20c2cffcf5e095cfc11882a0b3f065331416017d738340513a`.

Şema sonucu:
- INSURANCE çekirdek identity'leri 2019Q3..2026Q2 boyunca stabil;
- FINANCIAL epoch 1: 2019Q3..2021Q4;
- FINANCIAL epoch 2: 2022Q1..2026Q2;
- doğrulanmış örnek kaymalar: assets `37→40`, equity `58→62`, capital `59→63`, net income `77→81`.

Exact mapping profilleri:
- `KAP_BULK_INSURANCE_EXACT_LABEL_V1`;
- `KAP_BULK_FINANCIAL_EXACT_LABEL_V1`.

Kritik semantik sınır:
- balance facts: `CURRENT + INSTANT`; FINANCIAL için ayrıca `context_member=Toplam`;
- duration facts: `CURRENT + YTD`;
- TTM bulk adapter içinde uydurulmaz, downstream PIT-safe dönemlerden türetilir.

Gerçek KAP 2021Q1 positive regression:
- ANSGR: PASS;
- ANHYT: PASS;
- CRDFA: PASS;
- ISFIN: PASS.

Güncel-head CI run `33595979842`:
- P1 targeted contracts: **110 passed, 7 skipped**;
- full regression: **1936 passed, 7 skipped, 32 warnings**;
- BANK v4.7: **277 passed, 1 xfailed, 1 warning**;
- workflow: **SUCCESS**.

GPT kararı: **PASS**.

P1'in tek merge kapısı: current head üzerinde **fresh Claude independent PASS**. Bu gelmeden PR #38 ready/merge yapılmaz.

## HOLDING/GYO deneysel değerleme hattı

Gerçek KAP book-equity proxy çalışması:
- 993/993 hücre exact KAP equity+capital source;
- 981 deneysel M2 score;
- 12 kontrollü rejection: INVES 3, KLRHO 6, ASGYO 3;
- rejection sebebi pre-cutoff güvenli fiyat yokluğu;
- ikinci üretim bit-level aynı;
- canonical NAD ve production profile değiştirilmedi.

Gerçek tarihsel NAD bulunmazsa bu sonuç yalnız `EXPERIMENTAL_HOLDING_BOOK_EQUITY_TWO_AXIS_V1` / `EXPERIMENTAL_GYO_BOOK_EQUITY_TWO_AXIS_V1` etiketiyle deneysel hatta kullanılabilir.

## Açık işler — zorunlu sıra

Ana yürütme sözleşmesi:
`docs/V24_5Y_BACKTEST_DUAL_REVIEW_EXECUTION_PLAN.md`

1. **P0** — durum/koordinasyon ve çift-denetim zemini.
2. **P1** — teknik implementasyon GPT PASS; fresh Claude denetimi ve ardından kontrollü PR #38 merge kararı bekleniyor.
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
