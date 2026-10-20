"""API 없이 화면을 확인하기 위한 예시 데이터. 실제 수집 코드(스냅샷 비교·엑셀 가져오기)를 그대로 거친다."""
import copy
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import collector
import db
from import_history import import_file

random.seed(7)


def build(conn):
    base = date.today() - timedelta(days=1)
    start = base - timedelta(days=75)
    accounts = [("1000001", "sample_a", "샘플광고주A_검색/배너"), ("1000002", "sample_b", "샘플광고주B_검색광고")]
    for cid, login, name in accounts:
        conn.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?)", (cid, login, name, datetime.now().isoformat()))
    conn.commit()

    # 구조: 광고주별 캠페인 2개 × 광고그룹 3개
    plans = {
        "1000001": [("cmp-a1", "🟠경쟁력 주력 상품_매체별", ["00.통합검색", "01.쇼핑검색", "02.플러스스토어"]),
                    ("cmp-a2", "매체별_상품 지면 (벌크)", ["00.통합검색", "01.쇼핑검색", "03.추천지면"])],
        "1000002": [("cmp-b1", "[전체] 카테고리", ["귀걸이", "목걸이", "반지"]),
                    ("cmp-b2", "[집중] 진주귀걸이", ["진주귀걸이_PC", "진주귀걸이_MO"])],
    }
    # 광고그룹별 효과 시나리오: (변경일 offset, 변경 후 배율)
    effects = {}
    snaps = {}
    for cid, camps in plans.items():
        snap = {"taken_at": "", "campaigns": {}, "adgroups": {}, "keywords": {}, "ads": {}}
        for ci, (camp_id, cname, groups) in enumerate(camps):
            snap["campaigns"][camp_id] = {"name": cname, "budget": 30000, "use_budget": True, "lock": False, "reg": None, "edit": None}
            for gi, gname in enumerate(groups):
                gid = f"grp-{cid[-1]}{ci}{gi}"
                snap["adgroups"][gid] = {"name": gname, "campaign_id": camp_id, "bid": 300, "budget": None, "use_budget": False,
                                         "lock": False, "pc_weight": 100, "mo_weight": 100, "cnts_bid": None, "use_cnts_bid": False,
                                         "reg": None, "edit": None, "targets": {"TIME_WEEKLY_TARGET": "[all]"}}
                for ki in range(4):
                    kid = f"kwd-{gid}-{ki}"
                    snap["keywords"][kid] = {"kw": f"{gname.split('.')[-1]} 키워드{ki + 1}", "adgroup_id": gid, "bid": 300,
                                             "use_group_bid": True, "lock": False, "reg": None, "edit": None}
                effects[(cid, gid)] = (random.uniform(0.6, 1.6), random.uniform(20, 120))  # 기본 규모
        snaps[cid] = snap

    conn_rows = []
    for cid, snap in snaps.items():
        collector.save_entities(conn, cid, snap)

    # --- 변경 시나리오 (API 스냅샷 비교로 감지되는 것) ---
    def day(n):
        return base - timedelta(days=n)

    scenario_api = [
        # (광고주, 며칠 전, 함수, 그룹, 효과배율)
        ("1000001", 20, "add_keywords", "grp-100", 1.55),
        ("1000001", 12, "bid_up", "grp-101", 1.30),
        ("1000002", 25, "budget_down", "cmp-b2", 0.65),
        ("1000002", 9, "add_keywords", "grp-200", 1.05),
        ("1000002", 3, "group_off", "grp-211", 0.0),
    ]
    lift = {}  # (cid, gid) -> [(date, multiplier)]
    for cid, n, kind, target, mult in scenario_api:
        prev = copy.deepcopy(snaps[cid])
        cur = snaps[cid]
        when = datetime.combine(day(n), datetime.min.time()).replace(hour=15, minute=random.randint(0, 59))
        iso = (when - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        prev["taken_at"] = (when - timedelta(hours=20)).isoformat() + "+09:00"
        if kind == "add_keywords":
            g = cur["adgroups"][target]
            for ki in range(6):
                kid = f"kwd-{target}-new{ki}"
                cur["keywords"][kid] = {"kw": f"{g['name'].split('.')[-1]} 신규{ki + 1}", "adgroup_id": target, "bid": 350,
                                        "use_group_bid": False, "lock": False, "reg": iso, "edit": iso}
            groups = [target]
        elif kind == "bid_up":
            cur["adgroups"][target]["bid"] = 420
            cur["adgroups"][target]["edit"] = iso
            groups = [target]
        elif kind == "budget_down":
            cur["campaigns"][target]["budget"] = 15000
            cur["campaigns"][target]["edit"] = iso
            groups = [g for g, v in cur["adgroups"].items() if v["campaign_id"] == target]
        elif kind == "group_off":
            cur["adgroups"][target]["lock"] = True
            cur["adgroups"][target]["edit"] = iso
            groups = [target]
        cur["taken_at"] = (when + timedelta(hours=16)).isoformat() + "+09:00"
        db.insert_events(conn, collector.diff_snapshots(cid, prev, cur))
        for gid in groups:
            lift.setdefault((cid, gid), []).append((day(n), mult, kind == "add_keywords"))
        collector.save_entities(conn, cid, cur)

    # --- 광고센터 변경이력 엑셀(CSV) 가져오기 (스크린샷과 같은 형식) ---
    d_t = day(16)
    csv_rows = ["변경일자,변경 유형,변경자,캠페인,광고그룹,변경 내용"]
    for gname, gid in [("03.추천지면", "grp-112"), ("01.쇼핑검색", "grp-111"), ("00.통합검색", "grp-110")]:
        csv_rows.append(f"{d_t:%Y.%m.%d}. 17:28,타겟팅 변경,mp3599:naver,매체별_상품 지면 (벌크),{gname},요일/시간 타겟팅을 변경했습니다.")
        lift.setdefault(("1000001", gid), []).append((d_t, 0.7 if gid != "grp-110" else 1.25, False))
    csv_rows.append(f"{day(5):%Y.%m.%d}. 10:02,확장소재 추가,SYSTEM,-,-,ext-a001-02-000000517377476(쇼핑 상품 부가 정보) 확장 소재를 등록했습니다.")
    csv_rows.append(f"{day(5):%Y.%m.%d}. 11:40,입찰가 변경,mp3599:naver,🟠경쟁력 주력 상품_매체별,02.플러스스토어,기본 입찰가를 300원에서 500원으로 변경했습니다.")
    lift.setdefault(("1000001", "grp-102"), []).append((day(5), 1.4, False))
    tmp = Path(tempfile.mkdtemp()) / "sample_a__history.csv"
    tmp.write_text("\n".join(csv_rows), encoding="utf-8-sig")
    import_file(conn, tmp, "1000001")

    # --- 일별 성과 생성 ---
    rows = []
    for cid, snap in snaps.items():
        acct = {}
        for gid, g in snap["adgroups"].items():
            scale, cpc = effects[(cid, gid)]
            kws = [k for k, v in snap["keywords"].items() if v["adgroup_id"] == gid]
            d = start
            while d <= base:
                m = scale * (1 + 0.15 * (d.weekday() < 5))
                new_kw_mult = 1.0
                for (cd, mult, is_kw) in lift.get((cid, gid), []):
                    if d > cd:
                        ramp = min(1.0, (d - cd).days / 3)
                        m *= 1 + (mult - 1) * ramp
                        if is_kw:
                            new_kw_mult = mult
                m *= random.uniform(0.85, 1.15)
                imp = int(1800 * m)
                clk = int(imp * random.uniform(0.018, 0.03))
                cost = round(clk * cpc * 3)
                conv = round(clk * random.uniform(0.02, 0.05), 1)
                conv_amt = round(conv * random.uniform(28000, 42000))
                rec = (imp, clk, cost, conv, conv_amt)
                rows.append((cid, "adgroup", gid, d.isoformat(), *rec))
                for lvl, eid in (("campaign", g["campaign_id"]), ("account", cid)):
                    a = acct.setdefault((lvl, eid, d.isoformat()), [0, 0, 0, 0, 0])
                    for i in range(5):
                        a[i] += rec[i]
                # 키워드별 분배 (신규 키워드는 추가일 이후에만 성과)
                live = [k for k in kws if not snap["keywords"][k]["reg"] or collector.to_kst(snap["keywords"][k]["reg"]).date() < d]
                if live:
                    w = [random.random() + 0.2 for _ in live]
                    sw = sum(w)
                    for k, wi in zip(live, w):
                        f = wi / sw
                        rows.append((cid, "keyword", k, d.isoformat(), int(imp * f), int(clk * f), round(cost * f), round(conv * f, 1), round(conv_amt * f)))
                db.mark_stat_day(conn, cid, d.isoformat())
                d += timedelta(days=1)
        for (lvl, eid, ds), v in acct.items():
            rows.append((cid, lvl, eid, ds, *v))
    db.upsert_stats(conn, rows)
    return base.isoformat()
