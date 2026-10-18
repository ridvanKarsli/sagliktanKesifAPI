"""Sağlıktan Keşif motoru: literatür tabanlı ilaç yeniden konumlandırma hipotezi üretimi (Swanson ABC yaklaşımı).

Hastalık (A) → köprü genler (B) → ilaçlar (C): hastalık literatüründe öne çıkan genler bulunur, bu genlere etki eden ve
hastalıkla henüz ilişkilendirilmemiş ilaçlar puanlanır, yöntem geçmiş veriyle doğrulanır.

Katmanlar (bağımlılık yalnızca aşağı doğru; tests/engine/test_architecture.py denetler):

    pipeline/     orkestrasyon: adımlar, derlem (Corpus), durum, parametre kalıcılığı, çıktı okuma — dışarıya açık kapı
    insights/     çıktı: yenilik kontrolü, keşif özeti, rapor
    health.py     dış veri kaynaklarının sağlık kontrolü
    evaluation/   doğruluk: doğrulama etiketleri, geriye dönük test, parametre ayarı
    scoring/      algoritma: puanlama, köprü gen seçimi, özgüllük, yön, ilaç sınıfları
    catalog/      hastalık tanımları ve hastalık sözlüğü
    extraction/   makalelerden ilaç çıkarma: kimyasal → ilaç eşlemesi, metin taraması
    sources/      dış veri kaynağı istemcileri: makale, işaretleme, ilaç, gen, klinik çalışma
    core/         ayarlar ve kelime listeleri, HTTP istemcisi, önbellek, dizin düzeni

Motor web, kullanıcı ya da veritabanı bilmez; dosya tabanlı çalışır. Sunucu (`kesif_server`) onu yalnızca
`kesif_engine.pipeline` ve birkaç kaynak/sözlük modülü üzerinden, kendi adaptörüyle kullanır.
"""

__version__ = "1.0.0"
