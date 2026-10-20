"""Komut satırı: `kesif --help`.

  kesif migrate            veritabanı şemasını güncelle (alembic upgrade head)
  kesif api                REST API'yi başlat
  kesif worker             analiz işçisini başlat (Redis kuyruğu)
  kesif create-admin       yönetici kullanıcı aç
  kesif run <hastalık>     analizi bu süreçte çalıştır (kuyruksuz; sunucu/işçi gerekmez)
  kesif import-legacy      eski dosya tabanlı kurulumdan hastalık/aday verisini veritabanına al
  kesif doctor             dış veri kaynaklarını denetle
  kesif benchmark          çok hastalıklı yöntem karnesi; birleştirme kararını ölçümle verir
  kesif panel              ölçüm paneli hastalıklarını listele / kataloğa ekle
  kesif run-panel          paneldeki hastalıkları sırayla analiz et (kaldığı yerden sürer)
  kesif label-audit        doğrulama etiketlerinden denetim örneği çıkar / işaretlenmişi içe al
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer

from ...core.settings import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Sağlıktan Keşif sunucusu")


def _alembic_config():
    from alembic.config import Config

    root = Path(__file__).resolve().parents[4]  # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg


@app.command()
def migrate(revision: str = typer.Argument("head")):
    """Veritabanı şemasını verilen sürüme getirir."""
    from alembic import command  # type: ignore[attr-defined]

    command.upgrade(_alembic_config(), revision)
    typer.echo(f"Şema güncel: {revision}")


@app.command()
def downgrade(revision: str = typer.Argument("-1")):
    """Şemayı bir önceki sürüme alır."""
    from alembic import command  # type: ignore[attr-defined]

    command.downgrade(_alembic_config(), revision)


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000, reload: bool = False, workers: int = 1):
    """REST API'yi başlatır."""
    import uvicorn

    uvicorn.run("kesif_server.interfaces.api.main:app", host=host, port=port, reload=reload, workers=workers, proxy_headers=True,
                forwarded_allow_ips="*")


@app.command()
def worker(burst: bool = False):
    """Redis kuyruğundaki analiz işlerini çalıştırır. Birden fazla işçi açılabilir."""
    from rq import Worker

    from ...container import get_container
    from ...infrastructure.queue.redis_queue import RedisQueue

    c = get_container()
    if not isinstance(c.queue, RedisQueue):
        typer.echo("KESIF_QUEUE_BACKEND=redis olmalı (süreç içi kuyrukta işçi API'nin içinde çalışır).", err=True)
        raise typer.Exit(2)
    c.startup()
    w = Worker([c.queue.name], connection=c.queue.connection)
    w.work(burst=burst, with_scheduler=False)


@app.command("create-admin")
def create_admin(email: str, password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
                 name: str = "Yönetici"):
    """Yönetici kullanıcı açar (ilk kullanıcı için kimlik denetimi atlanır)."""
    from ...container import get_container
    from ...domain.entities import Role

    c = get_container()
    c.startup()
    u = c.create_user(c.local_actor(), email, password, name, Role.ADMIN)
    typer.echo(f"Yönetici açıldı: {u.email}")


@app.command()
def seed():
    """Varsayılan hastalık kataloğunu veritabanına yazar."""
    from ...container import get_container

    n = get_container().seed_diseases()
    typer.echo(f"{n} hastalık eklendi.")


@app.command()
def run(disease: str, steps: Optional[List[str]] = typer.Option(None, "--step", "-s", help="fetch,drugs,labels,tune,rank,backtest,novelty,report"),
        cutoff: int = 2018, top: int = 100, force: bool = False):
    """Analizi bu süreçte, kuyruksuz çalıştırır (tek makine / toplu işler için)."""
    from ...container import Container
    from ...domain.entities import DEFAULT_STEPS
    from ...infrastructure.queue.inprocess import SynchronousQueue

    s = get_settings()
    holder: dict = {}
    c = Container(s, queue=SynchronousQueue(lambda rid: holder.__setitem__("run", c.execute_run(rid))))
    c.startup()
    actor = c.local_actor()
    wanted = steps or [st.value for st in DEFAULT_STEPS]
    run_ = c.start_analysis(actor, disease, wanted, None, {"cutoff": cutoff, "top": top, "force": force})
    final = holder.get("run") or c.get_run(run_.id)
    typer.echo(json.dumps({"id": final.id, "status": final.status.value, "error": final.error, "summary": final.summary}, ensure_ascii=False, indent=1))
    if final.status.value != "done":
        raise typer.Exit(1)


@app.command("import-legacy")
def import_legacy(legacy_root: Path = typer.Argument(..., help="Eski kurulum kökü (data/ ve results/ içeren klasör)")):
    """Eski dosya tabanlı kurulumun hastalık tanımlarını, parametrelerini, indirilen makale önbelleğini ve tüm
    sonuçlarını veritabanına aktarır (legacy_root: data/ ve results/ klasörlerini içeren dizin)."""
    from ...container import get_container
    from ...domain.entities import Disease

    c = get_container()
    c.startup()
    ws = c.workspace_impl
    typer.echo(f"{legacy_root} → {ws.root} kopyalanıyor…")
    ws.import_tree(legacy_root)
    user_file = ws.data_dir / "diseases.json"
    removed_file = ws.data_dir / "diseases_removed.json"
    added = 0
    with c.uow() as uow:
        if user_file.exists():
            for key, cfg in json.loads(user_file.read_text(encoding="utf-8")).items():
                if uow.diseases.get(key) is None:
                    uow.diseases.add(Disease(key=key, label=cfg.get("label", key), name_en=cfg.get("name_en", key),
                                             mesh_id=cfg.get("mesh_id", ""), pubmed_query=cfg.get("pubmed_query", "")))
                    added += 1
        if removed_file.exists():
            for key in json.loads(removed_file.read_text(encoding="utf-8")):
                d = uow.diseases.get(key)
                if d:
                    d.is_removed = True
                    uow.diseases.update(d)
        uow.commit()
        diseases = uow.diseases.list()
    imported = {}
    typer.echo(f"ortak sözlükler → veritabanı: {ws.save()}")
    for d in diseases:
        if (ws.results_dir / d.key).exists() or (ws.data_dir / d.key).exists():
            settings_file = ws.data_dir / d.key / "settings.json"
            if settings_file.exists():
                with c.uow() as uow:
                    dd = uow.diseases.get(d.key)
                    dd.params = c.engine.validate_params(json.loads(settings_file.read_text(encoding="utf-8")))
                    uow.diseases.update(dd)
                    uow.commit()
            imported[d.key] = {**c.sync_results(d.key, None), "files": ws.save(d.key)}
    typer.echo(f"{added} hastalık eklendi; aktarım: {json.dumps(imported, ensure_ascii=False)}")
    typer.echo("Eski data/ ve results/ klasörleri artık silinebilir; her şey veritabanında.")


@app.command()
def doctor():
    """Dış veri kaynaklarını (makale, ilaç, klinik çalışma veritabanları) denetler."""

    from kesif_engine import health

    ok = True
    for r in health.check_all():
        ok &= bool(r["ok"])
        typer.echo(f"{'✓' if r['ok'] else '✗'} {r['name']:<28} {r['ms']:>6} ms  {r['detail']}")
    if not ok:
        sys.exit(1)


@app.command()
def benchmark(diseases: Optional[List[str]] = typer.Option(None, "--disease", "-d", help="Yalnızca bu hastalıklar (varsayılan: derlemi hazır olanların hepsi)"),
              apply: bool = typer.Option(True, help="Ölçüm sonucu birleştirme kararını kaydet"),
              rerank: bool = typer.Option(False, help="Karardan sonra ölçülen hastalıkları yeniden sırala")):
    """Derlemi hazır hastalıkları 2012/2015/2018 kesitlerinde test eder; yöntemleri güven aralığıyla karşılaştırır ve
    birleştirme kararını yalnızca ölçülen iyileşme anlamlıysa değiştirir. Sonuç veritabanına yazılır."""
    import logging

    from kesif_engine import pipeline

    from ...container import get_container

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    c = get_container()
    c.startup()
    ws = c.workspace_impl
    keys = diseases or [v.disease.key for v in c.list_diseases() if not v.disease.is_removed]
    for key in keys:
        ws.prepare(key)
    ready = [k for k in pipeline.ready_diseases() if k in keys]
    if not ready:
        typer.echo("Derlemi ve doğrulama etiketi hazır hastalık yok; önce analiz çalıştırın.", err=True)
        raise typer.Exit(2)
    result = pipeline.step_benchmark(ready, apply=apply)
    ws.save()
    for key in ready:
        ws.save(key)
    sc = result["scorecard"]
    typer.echo(f"\n{result['n_groups']} grup ({len(result['fit_diseases'])} hastalık × {len(result['cutoffs'])} kesit); hold-out: {', '.join(result['hold_out']) or 'yok'}")
    for m in result["methods"] + ["frequency", "random"]:
        ap = sc[m]["ap"]
        extra = f"  fark {sc[m]['vs_base']['mean']:+.4f} [{sc[m]['vs_base']['lo']:+.4f}, {sc[m]['vs_base']['hi']:+.4f}]" if "vs_base" in sc[m] else ""
        typer.echo(f"  {m:<10} AP {ap['mean']:.4f}  %90 GA [{ap['lo']:.4f}, {ap['hi']:.4f}]{extra}")
    d = result["decision"]
    typer.echo(f"\nKarar: {d.get('verdict')} → {d.get('method')}  ({d.get('reason')})")
    g = result.get("gate_1") or {}
    if g:
        ch = g["checks"]
        typer.echo(f"\nKapı 1: {'GEÇTİ' if g['passed'] else 'henüz değil'}")
        typer.echo(f"  {'✓' if ch['diseases']['ok'] else '✗'} hastalık sayısı {ch['diseases']['value']} / {ch['diseases']['target']}")
        le = ch["label_error"]
        typer.echo(f"  {'✓' if le['ok'] else '✗'} etiket hatası {le['value'] if le['value'] is not None else 'denetlenmedi'}"
                   f" ({le['n_checked']} satır, {le['audited_diseases']} hastalık denetlendi; hedef < {le['target']})")
        for b, v in ch["beats_baselines"]["baselines"].items():
            typer.echo(f"  {'✓' if v['ok'] else '✗'} {b} taban çizgisine göre fark {v['mean']:+.4f} [{v['lo']:+.4f}, {v['hi']:+.4f}]")
    if rerank and result.get("applied"):
        for key in ready:
            with c.uow() as uow:
                params = uow.diseases.get(key).params or {}
            c.rerank(c.local_actor(), key, params, None)
            typer.echo(f"  yeniden sıralandı: {key}")


@app.command()
def panel(add: bool = typer.Option(False, "--add", help="Katalogda olmayan panel hastalıklarını ekle")):
    """Ölçüm paneli: farklı hastalık türlerinden 32 hastalık (6'sı karara katılmayan hold-out)."""
    from kesif_engine import pipeline

    from ...container import get_container
    from ...domain.entities import Disease

    c = get_container()
    c.startup()
    added = 0
    with c.uow() as uow:
        for p in pipeline.load_panel():
            exists = uow.diseases.get(p["key"])
            mark = "hold-out" if p.get("hold_out") else ""
            typer.echo(f"  {'✓' if exists else '·'} {p['key']:<24} {p['label']:<36} {mark}")
            if add and not exists:
                uow.diseases.add(Disease(key=p["key"], label=p["label"], name_en=p["name_en"], mesh_id=p["mesh_id"],
                                         pubmed_query=p["pubmed_query"]))
                added += 1
        uow.commit()
    if add:
        typer.echo(f"{added} hastalık eklendi. Her biri için analiz çalıştırın (kesif run <anahtar>), sonra: kesif benchmark")


@app.command("run-panel")
def run_panel(only: Optional[List[str]] = typer.Option(None, "--only", help="Yalnızca bu panel hastalıkları"),
              redo: bool = typer.Option(False, help="Analizi tamamlanmış hastalıkları da yeniden çalıştır"),
              cutoff: int = 2018):
    """Ölçüm panelindeki hastalıkları sırayla, bu süreçte analiz eder. Eksik olanlar kataloğa eklenir; tamamlanmış
    olanlar atlanır, bir hastalık hata verirse sonrakine geçilir. Gece boyu çalıştırmak için uygundur."""
    import time

    from kesif_engine import pipeline

    from ...container import Container
    from ...domain.entities import DEFAULT_STEPS, Disease
    from ...infrastructure.queue.inprocess import SynchronousQueue

    s = get_settings()
    holder: dict = {}
    c = Container(s, queue=SynchronousQueue(lambda rid: holder.__setitem__("run", c.execute_run(rid))))
    c.startup()
    actor = c.local_actor()
    panel = [p for p in pipeline.load_panel() if not only or p["key"] in only]
    with c.uow() as uow:
        for p in panel:
            if uow.diseases.get(p["key"]) is None:
                uow.diseases.add(Disease(key=p["key"], label=p["label"], name_en=p["name_en"], mesh_id=p["mesh_id"],
                                         pubmed_query=p["pubmed_query"]))
        uow.commit()
    done: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []
    for i, p in enumerate(panel, 1):
        view = c.get_disease(p["key"])
        if not redo and view.status.get("has_backtest") and view.status.get("has_labels"):
            skipped.append(p["key"])
            continue
        typer.echo(f"[{i}/{len(panel)}] {p['label']} …")
        t0 = time.monotonic()
        holder.pop("run", None)
        try:
            run_ = c.start_analysis(actor, p["key"], [st.value for st in DEFAULT_STEPS], None, {"cutoff": cutoff})
            final = holder.get("run") or c.get_run(run_.id)
            ok = final.status.value == "done"
        except Exception as exc:  # noqa: BLE001
            ok, final = False, None
            typer.echo(f"    hata: {exc}", err=True)
        took = int(time.monotonic() - t0)
        (done if ok else failed).append(p["key"])
        typer.echo(f"    {'tamam' if ok else 'başarısız'} ({took // 60} dk {took % 60} sn)"
                   + (f": {final.error}" if final is not None and not ok and final.error else ""))
    typer.echo(f"\nTamamlanan {len(done)}, atlanan {len(skipped)}, başarısız {len(failed)}"
               + (f" ({', '.join(failed)})" if failed else ""))
    typer.echo("Sonraki adım: kesif benchmark")


@app.command("label-audit")
def label_audit(disease: str, n: int = typer.Option(50, help="Örnek satır sayısı"),
                out: Optional[Path] = typer.Option(None, "--out", help="Örneğin yazılacağı CSV (varsayılan: <hastalık>_etiket_denetimi.csv)"),
                import_file: Optional[Path] = typer.Option(None, "--import", help="İşaretlenmiş CSV'yi içe al")):
    """Doğrulama etiketlerinin elle denetimi. Önce örnek çıkarın; her satırdaki bağlantıdan çalışmayı açıp ilacın
    gerçekten TEST EDİLEN ilaç olup olmadığına bakın ve dogru_mu sütununa 'evet' ya da 'hayır' yazın. Sonra --import
    ile geri verin: yanlışlar ölçümden çıkarılır, hata oranı yöntem karnesine (Kapı 1) girer."""
    from kesif_engine import pipeline

    from ...container import get_container

    c = get_container()
    c.startup()
    ws = c.workspace_impl
    ws.prepare(disease)
    if import_file:
        result = pipeline.import_label_audit(disease, import_file.read_text(encoding="utf-8-sig"))
        ws.save(disease)
        typer.echo(f"{disease}: {result['n_checked']} satır denetlendi, {result['n_wrong']} yanlış; hata oranı {result['error_rate']}")
        typer.echo("Yanlış bulunan ilaçlar bir sonraki geriye dönük testten itibaren ölçüme girmez.")
        return
    try:
        text = pipeline.export_label_audit(disease, n)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    target = out or Path(f"{disease}_etiket_denetimi.csv")
    target.write_text(text, encoding="utf-8-sig")
    typer.echo(f"{text.count(chr(10)) - 1} satır yazıldı: {target}")
    typer.echo(f"İşaretledikten sonra: kesif label-audit {disease} --import {target}")


if __name__ == "__main__":
    app()
