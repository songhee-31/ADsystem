"""API 수집: ① 계정 구조 스냅샷 → 전날과 비교해 변경 감지  ② 일별 성과(대용량 보고서)."""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import config
import db
from naver_api import NaverAPI, NaverAPIError, manager_customer_id

KST = timezone(timedelta(hours=9))
CONV_TYPES = [c.strip() for c in os.environ.get("CONV_TYPES", "purchase").split(",") if c.strip()]
REFETCH_RECENT_DAYS = 3  # 전환은 늦게 잡히므로 최근 며칠은 매일 다시 받는다

TARGET_NAMES = {
    "TIME_WEEKLY_TARGET": "요일/시간",
    "REGIONAL_TARGET": "지역",
    "MEDIA_TARGET": "매체",
    "PC_MOBILE_TARGET": "PC/모바일",
    "SEX_TARGET": "성별",
    "AGE_TARGET": "연령",
    "NON_SEARCH_KEYWORD_TARGET": "검색어 외 노출",
    "RESTRICT_KEYWORD_TARGET": "제외 키워드",
}
CRITERION_NAMES = {"SD": "요일/시간", "AG": "연령", "GN": "성별", "RL": "지역"}


def describe_criteria(tp, before, after):
    """타겟팅 조건 변화를 사람이 읽는 문장으로. before/after: {code: [이름, 가중치, 제외여부, 사용여부]}"""
    parts = []
    for code in sorted(set(before) | set(after)):
        b, a = before.get(code), after.get(code)
        name = (a or b)[0]
        if tp == "SD":
            name = name.replace("요일 ", " ").replace("시부터 ", "~").replace("시까지", "시")
        if b and not a:
            parts.append(f"{name} 해제")
        elif a and not b:
            parts.append(f"{name} {'제외' if a[2] else '추가'}")
        elif b[2] != a[2] or b[3] != a[3]:
            parts.append(f"{name} {'제외' if a[2] or not a[3] else '노출'}로 변경")
        elif b[1] != a[1]:
            parts.append(f"{name} 가중치 {b[1]}%→{a[1]}%")
    label = CRITERION_NAMES.get(tp, tp)
    if not parts:
        return f"{label} 타겟팅 변경"
    return f"{label}: " + ", ".join(parts[:5]) + (f" 외 {len(parts) - 5}건" if len(parts) > 5 else "")


def to_kst(ts):
    """API의 regTm/editTm(UTC ISO)을 KST datetime으로."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


# ---------------------------------------------------------------- 스냅샷

def take_snapshot(api: NaverAPI, log=print):
    snap = {"taken_at": datetime.now(KST).isoformat(timespec="minutes"),
            "campaigns": {}, "adgroups": {}, "keywords": {}, "ads": {}}
    for c in api.campaigns():
        snap["campaigns"][c["nccCampaignId"]] = {
            "name": c.get("name"), "budget": c.get("dailyBudget"), "use_budget": c.get("useDailyBudget"),
            "lock": c.get("userLock"), "reg": c.get("regTm"), "edit": c.get("editTm"),
        }
    target_ok = True
    for g in api.adgroups():
        gid = g["nccAdgroupId"]
        item = {
            "name": g.get("name"), "campaign_id": g.get("nccCampaignId"), "bid": g.get("bidAmt"),
            "budget": g.get("dailyBudget"), "use_budget": g.get("useDailyBudget"), "lock": g.get("userLock"),
            "pc_weight": g.get("pcNetworkBidWeight"), "mo_weight": g.get("mobileNetworkBidWeight"),
            "cnts_bid": g.get("contentsNetworkBidAmt"), "use_cnts_bid": g.get("useCntsNetworkBidAmt"),
            "reg": g.get("regTm"), "edit": g.get("editTm"), "targets": {},
            # 네이버 자동입찰(머신러닝 ML 또는 자동입찰 전략 사용 중)이면 입찰가를 시스템이 바꾼다
            "auto_bid": g.get("systemBiddingType") not in (None, "", "NONE")
                        or bool((g.get("autobidStrategy") or {}).get("isAutobidActive")),
        }
        neg = {}  # 제외키워드 {키워드: 등록시각(ISO, UTC)}
        if target_ok:
            try:
                for t in api.targets(gid):
                    tp = t.get("targetTp")
                    if tp == "RESTRICT_KEYWORD_TARGET":
                        # 쇼핑 검색광고의 제외키워드: 키워드별 등록 시각(date, epoch 초)이 있다
                        for r in t.get("target") or []:
                            if r.get("keyword"):
                                ts = r.get("date")
                                neg[r["keyword"]] = (datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
                                                     if isinstance(ts, (int, float)) else t.get("editTm") or "")
                    elif tp:
                        item["targets"][tp] = json.dumps(t.get("target"), ensure_ascii=False, sort_keys=True)
            except NaverAPIError as e:
                log(f"   (타겟팅 조회 불가 → 타겟팅 변경은 엑셀 업로드로만 반영) {e}")
                target_ok = False
        try:
            # 파워링크 등의 제외키워드
            for r in api.get(f"/ncc/adgroups/{gid}/restricted-keywords", {"type": "KEYWORD_PLUS_RESTRICT"}) or []:
                if r.get("keyword") and not r.get("delFlag"):
                    neg.setdefault(r["keyword"], r.get("regTm") or "")
            item["neg_kw"] = neg
        except NaverAPIError:
            if neg:
                item["neg_kw"] = neg
        # 요일/시간·연령·성별·지역 타겟팅(criterion). 마지막 수정 시각(editTm)도 함께 저장
        try:
            crit = {}
            for c in api.get(f"/ncc/criterion/{gid}") or []:
                if c.get("delFlag"):
                    continue
                tp = c.get("type")
                d = crit.setdefault(tp, {"items": {}, "edit": ""})
                d["items"][c.get("dictionaryCode")] = [c.get("codeName"), c.get("bidWeight"), bool(c.get("negative")), bool(c.get("enable", True))]
                d["edit"] = max(d["edit"], c.get("editTm") or "", c.get("regTm") or "")
            item["criteria"] = crit
        except NaverAPIError:
            pass
        snap["adgroups"][gid] = item
        for k in api.keywords(gid):
            snap["keywords"][k["nccKeywordId"]] = {
                "kw": k.get("keyword"), "adgroup_id": gid, "bid": k.get("bidAmt"),
                "use_group_bid": k.get("useGroupBidAmt"), "lock": k.get("userLock"),
                "reg": k.get("regTm"), "edit": k.get("editTm"),
            }
        for a in api.ads(gid):
            ad = a.get("ad") or {}
            ref = a.get("referenceData") or {}
            title = ad.get("headline") or ref.get("productName") or ad.get("description") or a.get("type")
            snap["ads"][a["nccAdId"]] = {
                "adgroup_id": gid, "type": a.get("type"), "lock": a.get("userLock"), "title": title,
                "reg": a.get("regTm"), "edit": a.get("editTm"),
            }
    return snap


def snapshot_path(customer_id, day):
    return config.SNAPSHOT_DIR / str(customer_id) / f"{day}.json"


def latest_snapshot(customer_id, before_day):
    folder = config.SNAPSHOT_DIR / str(customer_id)
    if not folder.exists():
        return None
    files = sorted(p for p in folder.glob("*.json") if p.stem < before_day)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _onoff(lock):
    return "OFF" if lock else "ON"


def _fmt_won(v):
    return "-" if v in (None, "") else f"{int(v):,}원"


def diff_snapshots(customer_id, prev, cur):
    """두 스냅샷을 비교해 변경 이벤트 리스트를 만든다. 같은 광고그룹·같은 분류·같은 날은 하나로 묶는다."""
    prev_time = datetime.fromisoformat(prev["taken_at"])
    cur_time = datetime.fromisoformat(cur["taken_at"])
    fallback_day = (cur_time - timedelta(days=1)).date() if (cur_time - prev_time).days >= 1 else prev_time.date()

    def when(item, added=False):
        t = to_kst(item.get("reg")) if added else (to_kst(item.get("edit")) or to_kst(item.get("reg")))
        if t and prev_time - timedelta(hours=1) <= t <= cur_time + timedelta(hours=1):
            return t
        return datetime.combine(fallback_day, datetime.min.time(), KST).replace(hour=12)

    camps = {**prev["campaigns"], **cur["campaigns"]}
    groups = {**prev["adgroups"], **cur["adgroups"]}
    buckets = defaultdict(lambda: {"lines": [], "kw_ids": [], "ts": None})

    def add(ts, category, change_type, campaign_id, adgroup_id, line, kw_id=None):
        key = (ts.date().isoformat(), category, change_type, campaign_id or "", adgroup_id or "")
        b = buckets[key]
        b["lines"].append(line)
        if kw_id:
            b["kw_ids"].append(kw_id)
        b["ts"] = max(b["ts"], ts) if b["ts"] else ts

    # 캠페인
    for cid, c in cur["campaigns"].items():
        p = prev["campaigns"].get(cid)
        if not p:
            add(when(c, True), "구조", "캠페인 추가", cid, None, f"캠페인 '{c['name']}' 추가")
            continue
        if p.get("lock") != c.get("lock"):
            add(when(c), "ON·OFF", "캠페인 ON/OFF", cid, None, f"캠페인 {_onoff(p['lock'])} → {_onoff(c['lock'])}")
        if (p.get("budget"), p.get("use_budget")) != (c.get("budget"), c.get("use_budget")):
            add(when(c), "예산", "캠페인 예산 변경", cid, None,
                f"일예산 {_fmt_won(p['budget']) if p.get('use_budget') else '제한없음'} → "
                f"{_fmt_won(c['budget']) if c.get('use_budget') else '제한없음'}")
    for cid, p in prev["campaigns"].items():
        if cid not in cur["campaigns"]:
            add(datetime.combine(fallback_day, datetime.min.time(), KST), "구조", "캠페인 삭제", cid, None, f"캠페인 '{p['name']}' 삭제")

    # 광고그룹
    for gid, g in cur["adgroups"].items():
        p = prev["adgroups"].get(gid)
        cid = g.get("campaign_id")
        if not p:
            add(when(g, True), "구조", "광고그룹 추가", cid, gid, f"광고그룹 '{g['name']}' 추가")
            continue
        t = when(g)
        if p.get("lock") != g.get("lock"):
            add(t, "ON·OFF", "광고그룹 ON/OFF", cid, gid, f"광고그룹 {_onoff(p['lock'])} → {_onoff(g['lock'])}")
        if "auto_bid" in p and p["auto_bid"] != g.get("auto_bid"):
            add(t, "입찰가", "자동입찰 설정 변경", cid, gid, f"자동입찰 {'켬' if g.get('auto_bid') else '끔'}")
        if p.get("bid") != g.get("bid"):
            add(t, "입찰가", "기본 입찰가 변경", cid, gid, f"기본 입찰가 {_fmt_won(p['bid'])} → {_fmt_won(g['bid'])}")
        if (p.get("pc_weight"), p.get("mo_weight")) != (g.get("pc_weight"), g.get("mo_weight")):
            add(t, "입찰가", "입찰 가중치 변경", cid, gid,
                f"PC {p.get('pc_weight')}% → {g.get('pc_weight')}%, 모바일 {p.get('mo_weight')}% → {g.get('mo_weight')}%")
        if (p.get("cnts_bid"), p.get("use_cnts_bid")) != (g.get("cnts_bid"), g.get("use_cnts_bid")):
            add(t, "입찰가", "콘텐츠 매체 입찰가 변경", cid, gid, f"콘텐츠 입찰가 {_fmt_won(p.get('cnts_bid'))} → {_fmt_won(g.get('cnts_bid'))}")
        if (p.get("budget"), p.get("use_budget")) != (g.get("budget"), g.get("use_budget")):
            add(t, "예산", "광고그룹 예산 변경", cid, gid,
                f"일예산 {_fmt_won(p['budget']) if p.get('use_budget') else '제한없음'} → "
                f"{_fmt_won(g['budget']) if g.get('use_budget') else '제한없음'}")
        pt, gt = p.get("targets") or {}, g.get("targets") or {}
        if pt and gt:
            for tp in sorted(set(pt) | set(gt)):
                if tp == "RESTRICT_KEYWORD_TARGET":  # 제외키워드는 아래에서 키워드 단위로 비교
                    continue
                if pt.get(tp) != gt.get(tp):
                    add(t, "타겟팅", "타겟팅 변경", cid, gid, f"{TARGET_NAMES.get(tp, tp)} 타겟팅 변경")
        pc, gc = p.get("criteria"), g.get("criteria")
        if pc is not None and gc is not None:
            for tp in sorted(set(pc) | set(gc)):
                bi, ai = (pc.get(tp) or {}).get("items", {}), (gc.get(tp) or {}).get("items", {})
                if bi != ai:
                    ct = to_kst((gc.get(tp) or {}).get("edit"))
                    tt = ct if ct and prev_time - timedelta(hours=1) <= ct <= cur_time + timedelta(hours=1) else t
                    add(tt, "타겟팅", "타겟팅 변경", cid, gid, describe_criteria(tp, bi, ai))
        pn, gn = p.get("neg_kw"), g.get("neg_kw")
        if pn is not None and gn is not None:
            for kw in sorted(set(gn) - set(pn)):
                kt = to_kst(gn[kw])
                add(kt if kt and prev_time - timedelta(hours=1) <= kt <= cur_time + timedelta(hours=1) else t,
                    "제외키워드", "제외키워드 추가", cid, gid, kw)
            for kw in sorted(set(pn) - set(gn)):
                add(t, "제외키워드", "제외키워드 삭제", cid, gid, kw)
    for gid, p in prev["adgroups"].items():
        if gid not in cur["adgroups"]:
            add(datetime.combine(fallback_day, datetime.min.time(), KST), "구조", "광고그룹 삭제",
                p.get("campaign_id"), gid, f"광고그룹 '{p['name']}' 삭제")

    # 키워드
    for kid, k in cur["keywords"].items():
        g = groups.get(k["adgroup_id"], {})
        cid = g.get("campaign_id")
        p = prev["keywords"].get(kid)
        if not p:
            add(when(k, True), "키워드", "키워드 추가", cid, k["adgroup_id"], k["kw"], kid)
            continue
        t = when(k)
        if (p.get("bid"), p.get("use_group_bid")) != (k.get("bid"), k.get("use_group_bid")):
            before = "그룹입찰가" if p.get("use_group_bid") else _fmt_won(p.get("bid"))
            after = "그룹입찰가" if k.get("use_group_bid") else _fmt_won(k.get("bid"))
            add(t, "입찰가", "키워드 입찰가 변경", cid, k["adgroup_id"], f"{k['kw']} {before} → {after}", kid)
        if p.get("lock") != k.get("lock"):
            add(t, "ON·OFF", "키워드 ON/OFF", cid, k["adgroup_id"], f"{k['kw']} {_onoff(p['lock'])} → {_onoff(k['lock'])}", kid)
    for kid, p in prev["keywords"].items():
        if kid not in cur["keywords"]:
            g = groups.get(p["adgroup_id"], {})
            add(datetime.combine(fallback_day, datetime.min.time(), KST), "키워드", "키워드 삭제",
                g.get("campaign_id"), p["adgroup_id"], p["kw"], kid)

    # 소재
    for aid, a in cur["ads"].items():
        g = groups.get(a["adgroup_id"], {})
        p = prev["ads"].get(aid)
        if not p:
            add(when(a, True), "소재", "소재 추가", g.get("campaign_id"), a["adgroup_id"], str(a.get("title")))
        elif p.get("lock") != a.get("lock"):
            add(when(a), "ON·OFF", "소재 ON/OFF", g.get("campaign_id"), a["adgroup_id"],
                f"{a.get('title')} {_onoff(p['lock'])} → {_onoff(a['lock'])}")
        elif p.get("title") != a.get("title"):
            add(when(a), "소재", "소재 수정", g.get("campaign_id"), a["adgroup_id"], f"{p.get('title')} → {a.get('title')}")
    for aid, p in prev["ads"].items():
        if aid not in cur["ads"]:
            g = groups.get(p["adgroup_id"], {})
            add(datetime.combine(fallback_day, datetime.min.time(), KST), "소재", "소재 삭제",
                g.get("campaign_id"), p["adgroup_id"], str(p.get("title")))

    def system_bid(category, change_type, gid):
        """자동입찰이 켜진 광고그룹의 입찰가 변경은 네이버 시스템이 한 것으로 본다."""
        if category != "입찰가" or change_type == "자동입찰 설정 변경" or not gid:
            return False
        return bool(prev["adgroups"].get(gid, {}).get("auto_bid") or cur["adgroups"].get(gid, {}).get("auto_bid"))

    events = []
    for (day, category, change_type, cid, gid), b in buckets.items():
        lines = b["lines"]
        if category in ("키워드", "소재", "제외키워드") or (category in ("입찰가", "ON·OFF") and len(lines) > 1):
            detail = f"{change_type} {len(lines)}건: " + ", ".join(lines[:15]) + (" 외" if len(lines) > 15 else "")
        else:
            detail = " / ".join(lines)
        events.append({
            "uid": db.event_uid("api", customer_id, day, change_type, cid, gid, detail),
            "customer_id": customer_id, "ts": b["ts"].strftime("%Y-%m-%d %H:%M"), "date": day,
            "category": category, "change_type": change_type,
            "actor": "SYSTEM" if system_bid(category, change_type, gid) else "API 감지",
            "campaign_id": cid, "campaign_name": camps.get(cid, {}).get("name", ""),
            "adgroup_id": gid or "", "adgroup_name": groups.get(gid, {}).get("name", "") if gid else "",
            "keyword_ids": ",".join(b["kw_ids"]), "detail": detail, "source": "api",
        })
    return events


def baseline_events(customer_id, snap, since_day):
    """첫 스냅샷: 생성일(regTm)이 최근인 캠페인·광고그룹·키워드·소재를 '추가' 이벤트로 만든다."""
    fake_prev = {"taken_at": snap["taken_at"], "campaigns": {}, "adgroups": {}, "keywords": {}, "ads": {}}
    for kind in ("campaigns", "adgroups", "keywords", "ads"):
        for eid, item in snap[kind].items():
            reg = to_kst(item.get("reg"))
            if not reg or reg.date().isoformat() < since_day:
                fake_prev[kind][eid] = item  # 오래된 것은 '원래 있던 것'으로 취급
    # 생성 시각을 기준으로 날짜를 잡도록 prev 시각을 충분히 과거로
    fake_prev["taken_at"] = (datetime.fromisoformat(snap["taken_at"]) - timedelta(days=400)).isoformat()
    events = diff_snapshots(customer_id, fake_prev, snap)
    return [e for e in events if e["change_type"].endswith("추가")]


def targeting_backfill_events(customer_id, snap, since_day):
    """타겟팅 조건의 마지막 수정 시각(editTm)으로 과거 타겟팅 변경을 채운다 (조건별 가장 최근 1회)."""
    events = []
    # 한 번도 설정하지 않은 기본 타겟팅은 조회 시각이 수정 시각으로 돌아온다 → 스냅샷 직전 시각 이후 값은 무시
    taken = datetime.fromisoformat(snap["taken_at"]) - timedelta(minutes=30)
    for gid, g in snap["adgroups"].items():
        for tp, d in (g.get("criteria") or {}).items():
            t = to_kst(d.get("edit"))
            if not t or t.date().isoformat() < since_day or t >= taken:
                continue
            label = CRITERION_NAMES.get(tp, tp)
            excl = [v[0] for v in d["items"].values() if v[2] or not v[3]]
            if tp == "SD":
                on = sum(1 for v in d["items"].values() if not v[2] and v[3])
                detail = f"{label} 타겟팅 변경 (현재 노출 시간대 {on}개)"
            elif excl:
                detail = f"{label} 타겟팅 변경 (현재 제외: {', '.join(excl[:3])}{' 외' if len(excl) > 3 else ''})"
            else:
                detail = f"{label} 타겟팅 변경"
            day = t.date().isoformat()
            cid = g.get("campaign_id")
            events.append({
                "uid": db.event_uid("crit", customer_id, day, gid, tp), "customer_id": customer_id,
                "ts": t.strftime("%Y-%m-%d %H:%M"), "date": day, "category": "타겟팅", "change_type": "타겟팅 변경",
                "actor": "API 감지", "campaign_id": cid, "campaign_name": snap["campaigns"].get(cid, {}).get("name", ""),
                "adgroup_id": gid, "adgroup_name": g.get("name", ""), "keyword_ids": "", "detail": detail, "source": "api",
            })
    return events


def negkw_backfill_events(customer_id, snap, since_day):
    """제외키워드 등록 시각으로 과거 '제외키워드 추가'를 채운다 (광고그룹·날짜별로 묶음)."""
    buckets = {}
    for gid, g in snap["adgroups"].items():
        for kw, reg in (g.get("neg_kw") or {}).items():
            t = to_kst(reg)
            if not t or t.date().isoformat() < since_day:
                continue
            b = buckets.setdefault((gid, t.date().isoformat()), {"kws": [], "ts": t})
            b["kws"].append(kw)
            b["ts"] = max(b["ts"], t)
    events = []
    for (gid, day), b in buckets.items():
        g = snap["adgroups"][gid]
        cid = g.get("campaign_id")
        kws = sorted(b["kws"])
        events.append({
            "uid": db.event_uid("negkw", customer_id, day, gid), "customer_id": customer_id,
            "ts": b["ts"].strftime("%Y-%m-%d %H:%M"), "date": day, "category": "제외키워드",
            "change_type": "제외키워드 추가", "actor": "API 감지", "campaign_id": cid,
            "campaign_name": snap["campaigns"].get(cid, {}).get("name", ""), "adgroup_id": gid,
            "adgroup_name": g.get("name", ""), "keyword_ids": "",
            "detail": f"제외키워드 추가 {len(kws)}건: " + ", ".join(kws[:15]) + (" 외" if len(kws) > 15 else ""),
            "source": "api",
        })
    return events


def save_entities(conn, customer_id, snap):
    rows = []
    for cid, c in snap["campaigns"].items():
        rows.append((customer_id, "campaign", cid, c["name"], cid, ""))
    for gid, g in snap["adgroups"].items():
        rows.append((customer_id, "adgroup", gid, g["name"], g["campaign_id"], gid))
    for kid, k in snap["keywords"].items():
        g = snap["adgroups"].get(k["adgroup_id"], {})
        rows.append((customer_id, "keyword", kid, k["kw"], g.get("campaign_id", ""), k["adgroup_id"]))
    db.upsert_entities(conn, rows)


def collect_structure(conn, api, customer_id, today, log=print):
    snap = take_snapshot(api, log)
    path = snapshot_path(customer_id, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    save_entities(conn, customer_id, snap)
    prev = latest_snapshot(customer_id, today)
    since = (date.fromisoformat(today) - timedelta(days=config.BACKFILL_DAYS)).isoformat()
    if prev:
        events = diff_snapshots(customer_id, prev, snap)
    else:
        events = baseline_events(customer_id, snap, since)
    # 이전 스냅샷에 타겟팅 조건 정보가 없으면(첫 실행·업데이트 직후) 수정 시각으로 과거 타겟팅 변경을 채운다
    if not prev or any("criteria" not in g for g in prev["adgroups"].values()):
        events += targeting_backfill_events(customer_id, snap, since)
    if not prev or any("neg_kw" not in g for g in prev["adgroups"].values()):
        events += negkw_backfill_events(customer_id, snap, since)
    return db.insert_events(conn, events), snap


# ---------------------------------------------------------------- 성과

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def collect_stats_day(conn, api, customer_id, day):
    stat_date = day
    agg = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])  # imp, clk, cost, conv, conv_amt

    def keys(row):
        cid, gid, kid = row[2], row[3], row[4]
        out = [("account", customer_id), ("campaign", cid), ("adgroup", gid)]
        if kid and kid != "-":
            out.append(("keyword", kid))
        return out

    for row in api.stat_report("AD", stat_date):
        if len(row) < 12:
            continue
        for k in keys(row):
            a = agg[k]
            a[0] += int(_num(row[9])); a[1] += int(_num(row[10])); a[2] += _num(row[11])
    for row in api.stat_report("AD_CONVERSION", stat_date):
        if len(row) < 13:
            continue
        if CONV_TYPES and row[10] not in CONV_TYPES:
            continue
        for k in keys(row):
            a = agg[k]
            a[3] += _num(row[11]); a[4] += _num(row[12])
    rows = [(customer_id, lvl, eid, day, *v) for (lvl, eid), v in agg.items()]
    with db._lock:
        conn.execute("DELETE FROM daily_stats WHERE customer_id=? AND date=?", (customer_id, day))
        conn.commit()
    db.upsert_stats(conn, rows)
    db.mark_stat_day(conn, customer_id, day)


def collect_stats(conn, api, customer_id, today, log=print):
    t = date.fromisoformat(today)
    days = [(t - timedelta(days=i)).isoformat() for i in range(1, config.BACKFILL_DAYS + 1)]
    todo = [d for i, d in enumerate(days) if i < REFETCH_RECENT_DAYS or not db.has_stat_day(conn, customer_id, d)]
    for d in sorted(todo):
        try:
            collect_stats_day(conn, api, customer_id, d)
        except NaverAPIError as e:
            log(f"   성과 수집 실패 {d}: {e}")
    return len(todo)


# ---------------------------------------------------------------- 전체 실행

def list_accounts(log=print):
    """관리계정(기본: 내가 OWNER인 관리계정) 아래 광고주 목록. X-Customer 에는 customerId 를 쓴다."""
    api = NaverAPI(manager_customer_id())
    managers = api.get("/manager-accounts", {"size": 1000}).get("content", [])
    if config.MANAGER_ACCOUNT_NOS:
        managers = [m for m in managers if str(m["managerAccountNo"]) in config.MANAGER_ACCOUNT_NOS]
    else:
        managers = [m for m in managers if m.get("roleName") == "OWNER"]
    accounts = {}
    for m in managers:
        page = 0
        while True:
            r = api.get(f"/manager-accounts/{m['managerAccountNo']}/child-ad-accounts", {"size": 1000, "page": page})
            for c in r.get("content", []):
                cid = str(c.get("customerId") or "")
                if cid:
                    accounts[cid] = {"customer_id": cid, "login_id": c.get("ownerNaverId") or "",
                                     "name": c.get("adAccountName") or cid}
            if r.get("last", True):
                break
            page += 1
        log(f"관리계정 '{m['managerAccount']['name']}': 광고주 {len(r.get('content', []))}개")
    if config.INCLUDE_DIRECT_ACCOUNTS or not accounts:
        # 관리계정 없이 내 아이디에 직접 권한이 있는 검색광고 계정
        for x in api.get("/ad-accounts", {"size": 1000}).get("content", []):
            ad = x.get("adAccount") or {}
            if ad.get("adPlatformType") == "SA" and not ad.get("deleted") and not ad.get("disabled"):
                cid = str(ad.get("masterCustomerId"))
                accounts.setdefault(cid, {"customer_id": cid, "login_id": "", "name": ad.get("name") or cid})
    out = list(accounts.values())
    if config.ACCOUNTS:
        out = [a for a in out if a["customer_id"] in config.ACCOUNTS or a["login_id"] in config.ACCOUNTS or a["name"] in config.ACCOUNTS]
    return out


def run_account(conn, acc, today):
    msgs = []
    log = msgs.append
    cid = acc["customer_id"]
    conn = db.connect()  # 스레드마다 별도 연결 (sqlite 연결은 스레드 간 공유 불가)
    try:
        api = NaverAPI(cid)
        n_ev, _ = collect_structure(conn, api, cid, today, log)
        n_days = collect_stats(conn, api, cid, today, log)
        log(f"✔ {acc['name']} ({acc['login_id']}): 변경 {n_ev}건 감지, 성과 {n_days}일 수집")
    except NaverAPIError as e:
        log(f"✖ {acc['name']} ({acc['login_id']}): {e}")
    except Exception as e:  # 한 광고주 오류로 전체가 멈추지 않게
        log(f"✖ {acc['name']} ({acc['login_id']}): {type(e).__name__}: {e}")
    finally:
        conn.close()
    return msgs
