"""네이버 SA 변경 효과 추적판

사용법
  python main.py run       매일 실행: API 수집 → 엑셀 이력 가져오기 → 화면 생성
  python main.py import    data/inbox 의 변경이력 엑셀/CSV만 가져오기 + 화면 생성
  python main.py build     수집 없이 화면만 다시 만들기
  python main.py accounts  연결된 광고주 목록 확인
  python main.py demo      API 없이 예시 데이터로 화면 미리보기
  python main.py setkey    API 키 등록/변경 (암호화 저장, 화면에 표시 안 됨)
  python main.py migrate-keys  .env 에 평문으로 있던 키를 암호화 저장소로 옮기고 .env 에서 지움
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import config
import db


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(config.DATA_DIR / "run.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def cmd_accounts():
    import collector
    for a in collector.list_accounts():
        print(f"{a['customer_id']}\t{a['login_id']}\t{a['name']}")


def cmd_run():
    import collector
    from import_history import import_inbox
    conn = db.connect()
    today = date.today().isoformat()
    t0 = time.time()
    log("광고주 목록 조회")
    accounts = collector.list_accounts(log)
    now = datetime.now().isoformat(timespec="minutes")
    for a in accounts:
        conn.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?)", (a["customer_id"], a["login_id"], a["name"], now))
    conn.commit()
    log(f"광고주 {len(accounts)}개 수집 시작")
    with ThreadPoolExecutor(max_workers=config.WORKERS) as ex:
        for msgs in ex.map(lambda a: collector.run_account(conn, a, today), accounts):
            for m in msgs:
                log(m)
    log("변경이력 엑셀 가져오기")
    import_inbox(conn, log)
    cmd_build(conn)
    log(f"완료 ({time.time() - t0:.0f}초)")


def cmd_import():
    from import_history import import_inbox
    conn = db.connect()
    import_inbox(conn, log)
    cmd_build(conn)


def cmd_build(conn=None, base_day=None):
    from analyze import Analyzer
    conn = conn or db.connect()
    if not base_day:
        row = conn.execute("SELECT MAX(date) FROM stat_days").fetchone()
        base_day = row[0] or (date.today() - timedelta(days=1)).isoformat()
    data = Analyzer(conn, base_day).run()
    data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["owner"] = config.OWNER_NAME
    tpl = (config.BASE_DIR / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload)
    out = config.OUTPUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    dated = config.OUTPUT_DIR / f"change_impact_{base_day}.html"
    dated.write_text(html, encoding="utf-8")
    log(f"화면 생성: {out}  (변경 {len(data['actions'])}건, 광고주 {len(data['accounts'])}개)")
    return out


def cmd_demo():
    import demo
    config.DB_PATH = config.DATA_DIR / "demo.db"
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    conn = db.connect()
    base = demo.build(conn)
    out = cmd_build(conn, base)
    demo_out = config.OUTPUT_DIR / "demo.html"
    demo_out.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    out.unlink()
    (config.OUTPUT_DIR / f"change_impact_{base}.html").unlink()
    log(f"예시 화면: {demo_out}")


def cmd_setkey():
    import secret_store
    secret_store.setup_interactive()


def cmd_migrate_keys():
    """.env 의 평문 키 → 암호화 저장소, 그리고 .env 에서 해당 줄 삭제."""
    import os
    import secret_store
    keys = ("NAVER_API_KEY", "NAVER_SECRET_KEY", "NAVER_CUSTOMER_ID")
    vals = [os.environ.get(k) for k in keys]
    if not all(vals):
        print(".env 에 옮길 키가 없습니다. python main.py setkey 로 등록하세요.")
        return
    secret_store.save(*vals)
    env = config.BASE_DIR / ".env"
    lines = env.read_text(encoding="utf-8-sig").splitlines()
    kept = [l for l in lines if l.split("=", 1)[0].strip() not in keys]
    env.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"암호화 저장 완료 ({secret_store.CRED_PATH.name}), .env 에서 키 삭제 · 키 {secret_store.mask(vals[0])}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": cmd_run, "import": cmd_import, "build": cmd_build, "accounts": cmd_accounts, "demo": cmd_demo,
     "setkey": cmd_setkey, "migrate-keys": cmd_migrate_keys}.get(
        cmd, lambda: print(__doc__))()
