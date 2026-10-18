"""Komut satırı: `kesif --help`.

  kesif migrate            veritabanı şemasını güncelle (alembic upgrade head)
  kesif api                REST API'yi başlat
  kesif worker             analiz işçisini başlat (Redis kuyruğu)
  kesif create-admin       yönetici kullanıcı aç
  kesif run <hastalık>     analizi bu süreçte çalıştır (kuyruksuz; sunucu/işçi gerekmez)
  kesif import-legacy      eski dosya tabanlı kurulumdan hastalık/aday verisini veritabanına al
  kesif doctor             dış veri kaynaklarını denetle
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
    from kesif_engine import services

    ok = True
    for r in services.check_all():
        ok &= bool(r["ok"])
        typer.echo(f"{'✓' if r['ok'] else '✗'} {r['name']:<28} {r['ms']:>6} ms  {r['detail']}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    app()
