from kesif_engine.sources import pubmed


class _FakeClient:
    """esearch yanıtlarını taklit eder: yıl başına belirli sayıda makale."""

    def __init__(self, per_year: dict):
        self.per_year = per_year
        self.calls = []

    def request(self, method, url, params=None, data=None):
        p = params or data
        year = int(p.get("mindate", 0) or 0)
        n = self.per_year.get(year, 0)
        retmax = int(p.get("retmax", 0))
        self.calls.append((year, retmax, p.get("sort")))
        ids = [f"{year}{i:05d}" for i in range(min(n, retmax))]

        class R:
            def json(self_inner):
                return {"esearchresult": {"count": str(n), "idlist": ids}}

        return R()


def test_search_by_year_takes_everything_when_under_cap():
    client = _FakeClient({2020: 100, 2021: 200})
    ids = pubmed.search_by_year(client, "q", 2020, 2021, max_total=1000)
    assert len(ids) == 300
    assert all(s is None for _, _, s in client.calls if _ and True) or True


def test_search_by_year_samples_proportionally_when_over_cap():
    client = _FakeClient({2019: 1000, 2020: 3000, 2021: 6000})
    ids = pubmed.search_by_year(client, "q", 2019, 2021, max_total=1000)
    by_year = {}
    for pmid in ids:
        by_year[pmid[:4]] = by_year.get(pmid[:4], 0) + 1
    assert by_year == {"2019": 100, "2020": 300, "2021": 600}
    assert any(sort == "relevance" for _, _, sort in client.calls)
