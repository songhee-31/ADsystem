"""변경 이벤트 + 일별 성과 → 변경 전/후 비교 결과(JSON)."""
from collections import defaultdict
from datetime import date, timedelta

import config

METRICS = ("imp", "clk", "cost", "conv", "conv_amt")


def _d(s):
    return date.fromisoformat(s)


def _sum(rows):
    out = dict.fromkeys(METRICS, 0.0)
    for r in rows:
        for m in METRICS:
            out[m] += r.get(m, 0) or 0
    return out


def _avg(rows, n):
    s = _sum(rows)
    if n <= 0:
        return None
    a = {m: s[m] / n for m in METRICS}
    a["ctr"] = s["clk"] / s["imp"] * 100 if s["imp"] else 0
    a["cpc"] = s["cost"] / s["clk"] if s["clk"] else 0
    a["roas"] = s["conv_amt"] / s["cost"] * 100 if s["cost"] else 0
    return a


def _pct(a, b):
    if a is None or b is None:
        return None
    if a == 0:
        return None if b == 0 else 999.0
    return (b - a) / a * 100


class Analyzer:
    def __init__(self, conn, base_day):
        self.conn = conn
        self.base = _d(base_day)          # 마지막 성과 데이터 날짜(어제)
        self.W = config.WINDOW_DAYS
        self.T = config.THRESHOLD_PCT
        self._cache = {}

    def series(self, cid, level, eid):
        key = (cid, level, eid)
        if key not in self._cache:
            rows = self.conn.execute(
                "SELECT date, imp, clk, cost, conv, conv_amt FROM daily_stats WHERE customer_id=? AND level=? AND entity_id=?",
                (cid, level, eid)).fetchall()
            self._cache[key] = {r["date"]: dict(r) for r in rows}
        return self._cache[key]

    def stat_days(self, cid):
        return {r[0] for r in self.conn.execute("SELECT date FROM stat_days WHERE customer_id=?", (cid,))}

    def window(self, ser, days):
        return [ser.get(d.isoformat(), {}) for d in days]

    def compare(self, cid, level, eid, change_day, have_days):
        pre_days = [change_day - timedelta(days=i) for i in range(self.W, 0, -1)]
        post_days = [change_day + timedelta(days=i) for i in range(1, self.W + 1) if change_day + timedelta(days=i) <= self.base]
        pre_days = [d for d in pre_days if d.isoformat() in have_days]
        post_days = [d for d in post_days if d.isoformat() in have_days]
        ser = self.series(cid, level, eid)
        pre = _avg(self.window(ser, pre_days), len(pre_days))
        post = _avg(self.window(ser, post_days), len(post_days))
        return pre, post, len(pre_days), len(post_days)

    def chart(self, cid, level, eid, change_day, span=14):
        ser = self.series(cid, level, eid)
        out = []
        for i in range(-span, span + 1):
            d = change_day + timedelta(days=i)
            if d > self.base:
                break
            r = ser.get(d.isoformat(), {})
            out.append([d.isoformat()[5:], i, int(r.get("imp", 0) or 0), int(r.get("clk", 0) or 0),
                        round(r.get("cost", 0) or 0), round(r.get("conv", 0) or 0, 1), round(r.get("conv_amt", 0) or 0)])
        return out

    def verdict(self, pre, post, n_post, acct_pct):
        if n_post == 0:
            return "관찰중", None, "변경 후 데이터가 아직 없습니다."
        if not pre or (pre["cost"] == 0 and pre["clk"] == 0):
            if post and (post["clk"] > 0 or post["cost"] > 0):
                return "신규", None, "변경 전 성과가 없어 변경 후 성과만 봅니다."
            return "데이터없음", None, "변경 전후 모두 성과가 없습니다."
        total_clk = pre["clk"] * self.W + post["clk"] * n_post
        if total_clk < config.MIN_CLICKS:
            return "데이터없음", None, f"변경 전후 클릭이 {total_clk:.0f}회뿐이라 판정하지 않습니다 (기준 {config.MIN_CLICKS}회)."
        use_rev = (pre["conv_amt"] + post["conv_amt"]) > 0
        metric = "conv_amt" if use_rev else "clk"
        pct = _pct(pre[metric], post[metric])
        label = "전환매출" if use_rev else "클릭"
        if n_post < 3:
            v = "관찰중"
        elif pct is not None and pct >= self.T:
            v = "성장"
        elif pct is not None and pct <= -self.T:
            v = "하락"
        else:
            v = "보합"
        note = f"기준 지표: {label} (변경 후 {n_post}일 반영)"
        if acct_pct is not None and v in ("성장", "하락") and pct is not None:
            lift = pct - acct_pct
            if abs(lift) < self.T:
                note += f" · 계정 전체도 {acct_pct:+.0f}% 움직여 변경 효과라고 단정하기 어렵습니다"
            else:
                note += f" · 계정 전체({acct_pct:+.0f}%)보다 {lift:+.0f}%p 차이"
        return v, metric, note

    def run(self):
        since = (self.base - timedelta(days=config.REPORT_DAYS)).isoformat()
        accounts = {r["customer_id"]: dict(r) for r in self.conn.execute("SELECT * FROM accounts")}
        ent = {}
        name_idx = {}
        for r in self.conn.execute("SELECT * FROM entities"):
            ent[(r["customer_id"], r["entity_id"])] = dict(r)
            name_idx.setdefault((r["customer_id"], r["level"], r["name"]), r["entity_id"])

        evs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM change_events WHERE date>=? ORDER BY ts", (since,))]
        groups = defaultdict(list)
        for e in evs:
            cid = e["customer_id"]
            if not e["adgroup_id"] and e["adgroup_name"]:
                e["adgroup_id"] = name_idx.get((cid, "adgroup", e["adgroup_name"]), "")
            if not e["campaign_id"] and e["campaign_name"]:
                e["campaign_id"] = name_idx.get((cid, "campaign", e["campaign_name"]), "")
            if e["adgroup_id"]:
                key = (cid, e["date"], "adgroup", e["adgroup_id"])
            elif e["adgroup_name"]:
                key = (cid, e["date"], "adgroup?", e["adgroup_name"])
            elif e["campaign_id"]:
                key = (cid, e["date"], "campaign", e["campaign_id"])
            elif e["campaign_name"]:
                key = (cid, e["date"], "campaign?", e["campaign_name"])
            else:
                key = (cid, e["date"], "account", cid)
            groups[key].append(e)

        # 같은 대상의 변경일 목록 (겹침 경고용)
        by_target = defaultdict(list)
        for (cid, day, lvl, eid) in groups:
            by_target[(cid, lvl, eid)].append(day)

        stat_days_cache = {}
        actions = []
        for (cid, day, lvl, eid), items in groups.items():
            have = stat_days_cache.setdefault(cid, self.stat_days(cid))
            # API 감지와 엑셀 이력이 같은 분류로 겹치면 엑셀(변경자·원문 포함) 쪽만 남긴다
            imported_cats = {i["category"] for i in items if i["source"] == "import"}
            items = [i for i in items if not (i["source"] == "api" and i["category"] in imported_cats)]
            change_day = _d(day)
            level = lvl.rstrip("?")
            measurable = not lvl.endswith("?")
            first = items[0]
            if measurable:
                pre, post, n_pre, n_post = self.compare(cid, level, eid, change_day, have)
                chart = self.chart(cid, level, eid, change_day)
            else:
                pre = post = None
                n_pre = n_post = 0
                chart = []
            a_pre, a_post, _, _ = self.compare(cid, "account", cid, change_day, have)
            acct_metric = "conv_amt" if a_pre and a_post and (a_pre["conv_amt"] + a_post["conv_amt"]) > 0 else "clk"
            acct_pct = _pct(a_pre[acct_metric], a_post[acct_metric]) if a_pre and a_post else None
            v, metric, note = self.verdict(pre, post, n_post, acct_pct) if measurable else ("데이터없음", None, "광고그룹/캠페인을 찾지 못해 성과를 연결하지 못했습니다(삭제됐거나 이름이 바뀜).")

            # 추가된 키워드 자체 성과
            kw_ids = sorted({k for i in items for k in (i.get("keyword_ids") or "").split(",") if k})
            kw_perf = None
            if kw_ids and any(i["change_type"] == "키워드 추가" for i in items):
                post_days = [(change_day + timedelta(days=i)).isoformat() for i in range(0, config.REPORT_DAYS)
                             if change_day + timedelta(days=i) <= self.base]
                per_kw = []
                for k in kw_ids:
                    s = _sum([self.series(cid, "keyword", k).get(d, {}) for d in post_days])
                    per_kw.append({"kw": ent.get((cid, k), {}).get("name", k), **{m: round(s[m]) for m in METRICS}})
                tot = _sum(per_kw)
                kw_perf = {"count": len(kw_ids), "days": len(post_days), "total": {m: round(tot[m]) for m in METRICS},
                           "active": sum(1 for p in per_kw if p["imp"] > 0),
                           "top": sorted(per_kw, key=lambda p: (-p["conv_amt"], -p["clk"], -p["imp"]))[:8]}

            other = [d for d in by_target[(cid, lvl, eid)] if d != day and 0 < (_d(d) - change_day).days <= self.W]
            acc = accounts.get(cid, {})
            camp_name = first["campaign_name"] or ent.get((cid, first["campaign_id"]), {}).get("name", "")
            grp_name = first["adgroup_name"] or ent.get((cid, first["adgroup_id"]), {}).get("name", "")
            actions.append({
                "id": f"{cid}-{day}-{eid}"[:80],
                "customer_id": cid, "account": acc.get("name") or cid, "login": acc.get("login_id", ""),
                "date": day, "ts": max(i["ts"] for i in items), "level": level,
                "campaign": camp_name, "adgroup": grp_name if level == "adgroup" else "",
                "categories": sorted({i["category"] for i in items}),
                "actors": sorted({i["actor"] for i in items if i["actor"]}),
                "system_only": all((i["actor"] or "").upper() == "SYSTEM" for i in items),
                "items": [{"ts": i["ts"][11:16], "category": i["category"], "type": i["change_type"],
                           "actor": i["actor"], "detail": i["detail"], "source": i["source"]} for i in items],
                "verdict": v, "metric": metric, "note": note,
                "n_pre": n_pre, "n_post": n_post,
                "pre": _round(pre), "post": _round(post),
                "pct": {m: _r1(_pct(pre[m], post[m])) for m in ("imp", "clk", "ctr", "cpc", "cost", "conv", "conv_amt", "roas")} if pre and post else {},
                "acct_pct": _r1(acct_pct),
                "overlap": len(other),
                "chart": chart,
                "kw_perf": kw_perf,
            })
        actions.sort(key=lambda a: a["ts"], reverse=True)

        acc_out = []
        start = self.base - timedelta(days=config.REPORT_DAYS + config.WINDOW_DAYS)
        for cid, acc in accounts.items():
            ser = self.series(cid, "account", cid)
            pts = []
            d = start
            while d <= self.base:
                r = ser.get(d.isoformat(), {})
                pts.append([d.isoformat(), round(r.get("cost", 0) or 0), int(r.get("clk", 0) or 0), round(r.get("conv_amt", 0) or 0)])
                d += timedelta(days=1)
            acc_out.append({"customer_id": cid, "name": acc.get("name") or cid, "login": acc.get("login_id", ""),
                            "series": pts, "n_actions": sum(1 for a in actions if a["customer_id"] == cid)})
        acc_out.sort(key=lambda a: -sum(p[1] for p in a["series"][-7:]))
        return {"base_date": self.base.isoformat(), "window": self.W, "threshold": self.T,
                "report_days": config.REPORT_DAYS, "accounts": acc_out, "actions": actions}


def _r1(v):
    return None if v is None else round(v, 1)


def _round(a):
    if not a:
        return None
    return {k: (round(v, 2) if k in ("ctr", "conv") else round(v)) for k, v in a.items()}
