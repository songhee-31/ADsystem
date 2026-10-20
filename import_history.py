"""광고센터 [도구 > 변경 이력 관리]에서 내려받은 엑셀/CSV를 가져온다.

파일은 data/inbox/<광고주 로그인ID 또는 CUSTOMER_ID>/ 폴더에 넣거나,
파일 이름 앞에 '<로그인ID>__' 를 붙여 data/inbox/ 에 바로 넣으면 된다.
가져온 파일은 data/imported/ 로 옮겨진다.
"""
import csv
import io
import re
import shutil
from datetime import datetime
from pathlib import Path

import config
import db

COLS = {
    "ts": ["변경일자", "변경일시", "일시"],
    "change_type": ["변경 유형", "변경유형"],
    "actor": ["변경자"],
    "campaign": ["캠페인"],
    "adgroup": ["광고그룹"],
    "detail": ["변경 내용", "변경내용"],
}


def categorize(change_type, detail=""):
    s = f"{change_type} {detail}"
    if "타겟팅" in s:
        return "타겟팅"
    if "예산" in s:
        return "예산"
    if "입찰" in s or "가중치" in s:
        return "입찰가"
    if "ON" in s.upper() and "OFF" in s.upper() or "중지" in s or "활성" in s:
        return "ON·OFF"
    if "키워드" in s:
        return "키워드"
    if "확장소재" in s or "소재" in s:
        return "소재"
    if "캠페인" in change_type or "광고그룹" in change_type:
        return "구조"
    return "기타"


def _read_rows(path: Path):
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("엑셀(.xlsx)을 읽으려면 먼저 실행하세요:  pip install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [["" if v is None else v for v in r] for r in wb.active.iter_rows(values_only=True)]
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    dialect = "excel-tab" if text.count("\t") > text.count(",") else "excel"
    return list(csv.reader(io.StringIO(text), dialect=dialect))


def _find_header(rows):
    for i, r in enumerate(rows[:20]):
        cells = [str(c).strip() for c in r]
        if any(c in COLS["ts"] for c in cells) and any(c in COLS["detail"] for c in cells):
            idx = {}
            for key, names in COLS.items():
                for n in names:
                    if n in cells:
                        idx[key] = cells.index(n)
                        break
            return i, idx
    raise ValueError("헤더(변경일자 / 변경 내용)를 찾지 못했습니다.")


def _parse_ts(v):
    if isinstance(v, datetime):
        return v
    s = re.sub(r"\s+", " ", str(v).strip()).rstrip(".")
    for fmt in ("%Y.%m.%d. %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y.%m.%d. %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _account_for(path: Path, conn):
    """폴더명 또는 '로그인ID__파일명' 에서 광고주를 찾는다."""
    candidates = []
    if path.parent != config.INBOX_DIR:
        candidates.append(path.parent.name)
    if "__" in path.stem:
        candidates.append(path.stem.split("__", 1)[0])
    for c in candidates:
        row = conn.execute("SELECT customer_id FROM accounts WHERE customer_id=? OR login_id=? OR name=?", (c, c, c)).fetchone()
        if row:
            return row["customer_id"]
        if c.isdigit():
            return c
    return None


def import_file(conn, path: Path, customer_id: str):
    rows = _read_rows(path)
    h, idx = _find_header(rows)
    names = {}
    for r in conn.execute("SELECT level, entity_id, name, campaign_id FROM entities WHERE customer_id=? AND level IN ('campaign','adgroup')", (customer_id,)):
        names.setdefault((r["level"], r["name"]), (r["entity_id"], r["campaign_id"]))

    def cell(r, key):
        i = idx.get(key)
        return str(r[i]).strip() if i is not None and i < len(r) and r[i] not in (None, "") else ""

    events = []
    for r in rows[h + 1:]:
        ts = _parse_ts(cell(r, "ts"))
        if not ts:
            continue
        camp = cell(r, "campaign")
        grp = cell(r, "adgroup")
        camp = "" if camp == "-" else camp
        grp = "" if grp == "-" else grp
        change_type = cell(r, "change_type")
        detail = cell(r, "detail")
        cid = names.get(("campaign", camp), ("", ""))[0]
        gid, gcid = names.get(("adgroup", grp), ("", ""))
        if gid and not cid:
            cid = gcid
        events.append({
            "uid": db.event_uid("import", customer_id, ts.isoformat(), change_type, camp, grp, detail),
            "customer_id": customer_id, "ts": ts.strftime("%Y-%m-%d %H:%M"), "date": ts.date().isoformat(),
            "category": categorize(change_type, detail), "change_type": change_type, "actor": cell(r, "actor"),
            "campaign_id": cid, "campaign_name": camp, "adgroup_id": gid, "adgroup_name": grp,
            "keyword_ids": "", "detail": detail, "source": "import",
        })
    return db.insert_events(conn, events), len(events)


def import_inbox(conn, log=print):
    files = [p for p in config.INBOX_DIR.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")]
    for p in files:
        cid = _account_for(p, conn)
        if not cid:
            log(f"   ? {p.name}: 어느 광고주 파일인지 모릅니다. inbox/<로그인ID>/ 폴더에 넣어 주세요.")
            continue
        try:
            new, total = import_file(conn, p, cid)
        except ValueError as e:
            log(f"   ✖ {p.name}: {e}")
            continue
        dest = config.IMPORTED_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{cid}_{p.name}"
        shutil.move(str(p), dest)
        log(f"   ✔ {p.name}: {total}행 중 새 이력 {new}건 추가")
