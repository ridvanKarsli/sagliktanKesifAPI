from __future__ import annotations

import json
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent.parent  # backend/
DATA_DIR = Path(os.environ.get("KESIF_DATA", ROOT / "data"))
RESULTS_DIR = Path(os.environ.get("KESIF_RESULTS", ROOT / "results"))


def configure(data_dir=None, results_dir=None) -> None:
    """Motorun veri/sonuç dizinlerini çalışma zamanında ayarlar (uygulama katmanı ayarlarından çağrılır).

    Modüller dizinleri her çağrıda `config.DATA_DIR` üzerinden okur; bu yüzden import sonrası değiştirmek güvenlidir.
    """
    global DATA_DIR, RESULTS_DIR
    if data_dir is not None:
        DATA_DIR = Path(data_dir)
    if results_dir is not None:
        RESULTS_DIR = Path(results_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# İsteğe bağlı. NCBI API anahtarı hız limitini saniyede 3'ten 10 isteğe çıkarır.
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL")

# Aday sayılacak ilaçlar için en düşük klinik faz (ChEMBL max_phase). 1 = en az Faz 1'e girmiş.
MIN_PHASE = float(os.environ.get("KESIF_MIN_PHASE", "1"))

# İlaç adayı sayılmayacak kimyasallar (küçük harfle etiket). ChEMBL sözlüğü varken bu liste
# yalnızca ek güvenlik; sözlük yokken (demo) ana filtre.
STOP_CHEMICALS = {
    "water",
    "oxygen",
    "carbon dioxide",
    "nitrogen",
    "hydrogen",
    "saline",
    "sodium chloride",
    "glucose",
    "cholesterol",
    "flavonoid",
    "flavonoids",
    "retinoid",
    "retinoids",
    "steroid",
    "steroids",
    # endojen metabolitler, reaktifler, kontrast/boya: gerçek molekül ama yeniden konumlandırma adayı değil
    "atp",
    "adenosine triphosphate",
    "cortisol",
    "h2o2",
    "hydrogen peroxide",
    "pma",
    "phorbol myristate acetate",
    "fdg",
    "fludeoxyglucose f 18",
    "pge2",
    "dinoprostone",
    "glutamate",
    "glutamic acid",
    "l-arginine",
    "arginine",
    "phospholipid",
    "phospholipids",
    "dextrose",
    "helium",
    "h&e",
    "ra",
    "cerulein",
    "ceruletide",
    "streptozotocin",
    "streptozocin",
    "creatinine",
    "urea",
    "uric acid",
    "bilirubin",
    "lactate",
    "lactic acid",
    "glycine",
    "alanine",
    "serine",
    "cysteine",
    "methionine",
    "leucine",
    "lysine",
    "histamine",
    "serotonin",
    "dopamine",
    "melatonin",
    "ethanol",
    "alcohol",
    "nicotine",
    "caffeine",
    # tanı boyaları, kontrast maddeler, elektrolit ve mineraller
    "fluorescein",
    "indocyanine green",
    "gadolinium",
    "technetium",
    "iodine",
    "barium",
    "calcium",
    "potassium",
    "sodium",
    "magnesium",
    "zinc",
    "iron",
    "selenium",
    "copper",
    "chloride",
    "phosphate",
    "bicarbonate",
    "potassium chloride",
    "calcium chloride",
    "magnesium sulfate",
}

# --- İlaç uzayı sınıflandırması (drugspace.py) ---
# ATC sınıfı bu öneklerle başlayan ilaçlar yeniden konumlandırma adayı değildir: tanı ajanları, kontrast
# maddeler, radyofarmasötikler, kan yerine geçenler/elektrolit çözeltileri, mineral takviyeleri, göz tanı boyaları.
# Birden fazla ATC kodu olan ilaç yalnızca TÜM kodları dışlanan sınıftaysa dışlanır (hyaluronik asit gibi).
EXCLUDED_ATC_PREFIXES = ("V04", "V06", "V07", "V08", "V09", "V10", "B05", "A12", "S01J")

# ATC kodu olmayan (ya da sözlük eski olduğu için ATC bilgisi inmemiş) tanı/provokasyon ajanları için ad listesi.
DIAGNOSTIC_NAMES = {
    "methacholine", "methacholine chloride", "histamine phosphate", "mannitol", "fluorescein", "sodium fluorescein",
    "indocyanine green", "iohexol", "iodixanol", "iopromide", "iopamidol", "gadobutrol", "gadoterate",
    "gadopentetate", "technetium", "fludeoxyglucose f 18", "regadenoson", "tuberculin", "ovalbumin",
    "lipopolysaccharide", "secretin", "sincalide", "pentagastrin", "cosyntropin", "corticorelin", "sermorelin",
    "thyrotropin alfa", "protirelin", "gonadorelin", "edrophonium", "tropicamide", "evans blue", "methylene blue",
    "patent blue", "isosulfan blue", "trypan blue", "barium sulfate", "xenon", "helium", "krypton", "nitrogen",
    "carbon", "charcoal, activated", "capsaicin", "sodium chloride", "water", "ethanol", "paraffin", "dextran",
    "polyethylene glycol", "propylene glycol", "dimethyl sulfoxide", "poloxamer", "povidone", "polyvinyl alcohol",
    "carboxymethylcellulose sodium", "sodium alginate", "alginate", "chitosan", "poliglusam", "pectin", "agar",
    "starch", "betadex", "corn oil", "silk fibroin", "gold", "platinum", "silver", "gallium", "cesium",
    "rubidium rb-82", "calcium phosphate, tribasic", "calcium carbonate", "sodium thiosulfate", "aminohippurate sodium",
}

# Vücutta zaten bulunan moleküller: hormonlar, nörotransmitterler, aracı moleküller, metabolitler, plazma
# proteinleri. Hastalık makalesinde geçmeleri çoğunlukla ÖLÇÜM (biyobelirteç) ilişkisidir, ilaç kullanımı değil.
# Aday listesinde "endojen" olarak işaretlenir ve varsayılan görünümde gizlenir; güvenilirlik testinin evrenine
# girmez. (Gerçek tedavi olarak kullanılanlar -insülin, melatonin- "Bilinen tedaviler" sekmesinde yine görünür.)
ENDOGENOUS = {
    # peptit/protein
    "prothrombin", "thrombin", "fibrinogen", "albumin human", "human albumin", "albumin", "insulin human", "insulin",
    "glucagon", "glucagon-like peptide 1", "glucagon-like peptide-1", "glp-1", "human c-peptide", "c-peptide",
    "basic fibroblast growth factor", "basic fibroblast growth factor (human)", "epidermal growth factor",
    "nerve growth factor", "erythropoietin", "interferon", "interferon gamma", "interferon gamma-1b", "interleukin-2",
    "aldesleukin", "hyaluronidase", "collagenase", "factor viii", "factor ix", "antithrombin iii", "transferrin",
    "lactoferrin", "somatotropin", "somatropin", "growth hormone", "calcitonin", "oxytocin", "vasopressin",
    "somatostatin", "ghrelin", "leptin", "adiponectin", "amylin", "amlintide", "substance p", "endothelin",
    "endothelin-1", "angiotensin", "angiotensin i", "angiotensin ii", "angiotensin 1-7", "bradykinin",
    "atrial natriuretic peptide", "brain natriuretic peptide", "natriuretic peptide", "relaxin", "thyrotropin",
    "thyroid-stimulating hormone", "corticotropin", "gastrin", "cholecystokinin", "neuropeptide y",
    "vasoactive intestinal peptide", "galanin", "orexin", "kisspeptin", "parathyroid hormone", "irisin", "resistin",
    "visfatin", "apelin", "osteocalcin", "glucagon-like peptide 2", "gastric inhibitory polypeptide",
    # steroid ve lipid hormonları
    "aldosterone", "cortisol", "hydrocortisone", "cortisone", "corticosterone", "desoxycorticosterone",
    "dehydroepiandrosterone", "pregnenolone", "androstenedione", "testosterone", "dihydrotestosterone", "stanolone",
    "estrone", "estriol", "progesterone", "prostaglandin e2", "dinoprostone",
    "prostacyclin", "epoprostenol", "thromboxane", "leukotriene", "prostaglandin", "arachidonic acid", "ceramide",
    "sphingosine", "cholesterol", "7-dehydrocholesterol", "lanosterol", "squalene",
    # nörotransmitter ve aminler
    "norepinephrine", "epinephrine", "dopamine", "serotonin", "histamine", "acetylcholine", "glutamate",
    "glutamic acid", "gamma-aminobutyric acid", "gaba", "adenosine", "melatonin", "anandamide",
    "2-arachidonoylglycerol", "agmatine", "tyramine", "octopamine",
    # gazlar, küçük metabolitler, kofaktörler, besinler
    "nitric oxide", "hydrogen sulfide", "carbon monoxide", "hydrogen peroxide", "superoxide", "uric acid",
    "creatinine", "urea", "bilirubin", "lactate", "lactic acid", "sodium lactate", "pyruvate", "pyruvic acid",
    "citric acid", "succinic acid", "fumaric acid", "malic acid", "oxaloacetate", "3-hydroxybutyric acid, (+/-)-",
    "beta-hydroxybutyrate", "acetoacetate", "glucose", "fructose", "galactose", "sucrose", "glycogen",
    "glycerol", "glycerin", "triglyceride", "palmitic acid", "oleic acid", "linoleic acid", "stearic acid",
    "acetic acid", "adenosine triphosphate", "atp", "adp",
    "amp", "cyclic amp", "cyclic gmp", "guanine", "adenine", "cytosine", "thymine", "uracil", "inosine",
    "hypoxanthine", "xanthine", "nadide", "nad", "nadh", "flavin adenine dinucleotide", "coenzyme a",
    "heme", "hemin",
    "spermine", "putrescine", "polyamine", "trimethylamine",
    "homocysteine",
    "kynurenine", "kynurenic acid", "quinolinic acid",
    "indoleacetic acid", "heparan sulfate", "dermatan sulfate",
    "sialic acid",
    "sphingomyelin", "phospholipid", "phospholipids", "cardiolipin",
    "bile acid", "cholic acid", "chenodeoxycholic acid",
    "deoxycholic acid", "lithocholic acid", "ammonia", "ammonia solution, strong", "ammonium chloride",
    "hydrogen", "oxygen", "ozone", "sodium", "potassium", "calcium", "magnesium", "zinc", "iron", "copper",
    "selenium", "manganese", "phosphate", "bicarbonate", "sodium bicarbonate", "chloride", "iodine",
    "cobalt", "molybdenum", "fluoride", "silicon", "boron",
}

# Vitaminler, amino asitler, yağ asitleri, bitkisel moleküller, takviyeler: aday olabilirler ama ilaç değil,
# "besin/takviye" sınıfındadır; listede görünür ve etiketlenir. ChEMBL natural_product bayrağı da (onaylı ilaç
# değilse) bu sınıfa alır. Yeniden konumlandırma kanıtı için ilaç sınıfı ayrı değerlendirilir.
NUTRIENT_NAMES = {
    "biotin",
    "folic acid",
    "levomefolic acid",
    "pyridoxine",
    "pyridoxal",
    "pyridoxamine",
    "riboflavin",
    "thiamine",
    "niacinamide",
    "niacin",
    "pantothenic acid",
    "ascorbic acid",
    "retinol",
    "retinal",
    "tocopherol",
    "vitamin e",
    "phylloquinone",
    "phytonadione",
    "menaquinone",
    "menaquinone 6",
    "vitamin k",
    "ubidecarenone",
    "carnitine",
    "acetylcarnitine",
    "choline",
    "betaine",
    "taurine",
    "creatine",
    "fosfocreatine",
    "lutein",
    "zeaxanthin",
    "beta carotene",
    "lycopene",
    "doconexent",
    "docosahexaenoic acid",
    "icosapent",
    "eicosapentaenoic acid",
    "inulin",
    "lipoic acid, alpha",
    "alpha-lipoic acid",
    "lipoic acid",
    "glucosamine",
    "chondroitin sulfate",
    "calcifediol",
    "resveratrol",
    "quercetin",
    "kaempferol",
    "luteolin",
    "rutin",
    "curcumin",
    "genistein",
    "daidzein",
    "isoflavone",
    "chlorogenic acid",
    "caffeic acid",
    "baicalein",
    "icariin",
    "crocin",
    "silibinin",
    "sulforaphane",
    "berberine",
    "epigalocatechin gallate",
    "glycyrrhizin",
    "andrographolide",
    "paeoniflorin",
    "boswellic acid",
    "betulinic acid",
    "ampelopsin",
    "artemisinin",
    "flavone",
    "coumarin",
    "propolis wax",
    "saccharin",
    "sucralose",
    "rebaudioside a",
    "trehalose",
    "n-acetylglucosamine",
    "s-adenosylmethionine",
    "spermidine",
    "oxitriptan",
    "tryptophan",
    "citrulline",
    "l-citrulline",
    "arginine",
    "l-arginine",
    "glutamine",
    "glycine",
    "methionine",
    "cysteine",
    "glutathione",
    "ornithine",
    "proline",
    "alanine",
    "serine",
    "leucine",
    "isoleucine",
    "valine",
    "lysine",
    "histidine",
    "phenylalanine",
    "tyrosine",
    "asparagine",
    "aspartic acid",
    "threonine",
    "amino acids",
    "sodium butyrate",
    "butyric acid",
    "propionic acid",
}

# Köprü (B) olarak kullanılmayacak genler: hastalık sürecinden çok ölçüm/işaretleyici olanlar.
STOP_GENE_IDS = {"1401", "920", "925", "926"}  # CRP, CD4, CD8A, CD8B
STOP_GENE_LABELS = {"c-reactive protein", "crp", "cd4", "cd8", "cd8a", "cd8b"}

# ChEMBL etki tipi -> yön
INHIBITING_ACTIONS = {
    "INHIBITOR",
    "ANTAGONIST",
    "BLOCKER",
    "NEGATIVE MODULATOR",
    "NEGATIVE ALLOSTERIC MODULATOR",
    "ANTISENSE INHIBITOR",
    "DEGRADER",
    "DISRUPTING AGENT",
    "SEQUESTERING AGENT",
    "INVERSE AGONIST",
    "CHELATING AGENT",
    "RNAI INHIBITOR",
    "HYDROLYTIC ENZYME",
}
ACTIVATING_ACTIONS = {
    "AGONIST",
    "ACTIVATOR",
    "POSITIVE MODULATOR",
    "POSITIVE ALLOSTERIC MODULATOR",
    "OPENER",
    "PARTIAL AGONIST",
    "STABILISER",
    "RELEASING AGENT",
    "SUBSTRATE",
}


def load_diseases() -> dict:
    """Pakete gömülü varsayılan hastalık kataloğu (kullanıcı eklemeleri uygulama katmanında, veritabanında tutulur)."""
    path = PACKAGE_DIR / "resources" / "default_diseases.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)
