"""Utilitaires : configuration, dates, lecture/écriture des fichiers JSON."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CONFIG.get("timezone", "Europe/Paris"))


def now_local() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now_local().date()


def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def days_between(d1: date, d2: date):
    d = d1
    while d <= d2:
        yield d
        d += timedelta(days=1)


def num(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def r2(v):
    return None if v is None else round(v, 2)


def load_json(rel: str, default=None):
    p = DATA / rel
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(rel: str, obj) -> None:
    p = DATA / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def upsert(rel: str, new_rows: list, keys: tuple) -> None:
    """Ajoute ou remplace des lignes d'un historique (clé = tuple de champs)."""
    rows = load_json(rel, []) or []
    new_keys = {tuple(r[k] for k in keys) for r in new_rows}
    rows = [r for r in rows if tuple(r.get(k) for k in keys) not in new_keys] + new_rows
    rows.sort(key=lambda r: tuple(str(r.get(k)) for k in keys))
    save_json(rel, rows)


def write_meta(source: str) -> None:
    meta = load_json("meta.json", {}) or {}
    meta.update({
        "maj": now_local().isoformat(timespec="seconds"),
        "source": source,
        "magasins": [{"code": s["code"], "label": s["label"], "type": s["type"]} for s in CONFIG["stores"]],
        "entrepots": [{"code": s["code"], "label": s["label"], "type": s.get("type", "Entrepôt")} for s in CONFIG.get("stock_extra", [])],
    })
    save_json("meta.json", meta)
