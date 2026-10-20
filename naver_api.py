"""네이버 검색광고 API 클라이언트 (표준 라이브러리만 사용)."""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config
import secret_store

BASE_URL = "https://api.searchad.naver.com"


class NaverAPIError(Exception):
    pass


_CREDS = None


def credentials():
    """(api_key, secret_key, manager_customer_id) — 암호화 저장소에서 한 번만 읽는다."""
    global _CREDS
    if _CREDS is None:
        _CREDS = secret_store.load()
        if not all(_CREDS):
            raise NaverAPIError("API 키가 등록되지 않았습니다. python main.py setkey 로 등록하세요.")
    return _CREDS


def manager_customer_id():
    return credentials()[2]


def _scrub(text, *secrets):
    """오류 메시지에 키가 섞여 로그에 남지 않도록 가린다."""
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, secret_store.mask(s))
            text = text.replace(s[:20], secret_store.mask(s))
    return text


class NaverAPI:
    def __init__(self, customer_id):
        self.customer_id = str(customer_id)
        self.api_key, self.secret_key, _ = credentials()

    def __repr__(self):  # 디버깅 출력에 키가 찍히지 않게
        return f"NaverAPI(customer={self.customer_id})"

    def _headers(self, method, uri):
        ts = str(int(time.time() * 1000))
        msg = f"{ts}.{method}.{uri}"
        sig = base64.b64encode(hmac.new(self.secret_key.encode(), msg.encode(), hashlib.sha256).digest()).decode()
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": ts,
            "X-API-KEY": self.api_key,
            "X-Customer": self.customer_id,
            "X-Signature": sig,
        }

    def request(self, method, uri, params=None, body=None, raw=False, retries=4):
        url = BASE_URL + uri
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers(method, uri))
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    content = resp.read()
                    if raw:
                        return content
                    return json.loads(content) if content else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                # 429 / 1016(too many requests) 는 잠시 쉬고 재시도
                if (e.code == 429 or "1016" in detail or e.code >= 500) and attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise NaverAPIError(_scrub(f"{method} {uri} → HTTP {e.code}: {detail[:300]}", self.api_key, self.secret_key))
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise NaverAPIError(_scrub(f"{method} {uri} → {e}", self.api_key, self.secret_key))

    def get(self, uri, params=None):
        return self.request("GET", uri, params=params)

    # ---- 구조 ----
    def campaigns(self):
        return self.get("/ncc/campaigns") or []

    def adgroups(self):
        return self.get("/ncc/adgroups") or []

    def keywords(self, adgroup_id):
        return self.get("/ncc/keywords", {"nccAdgroupId": adgroup_id}) or []

    def ads(self, adgroup_id):
        return self.get("/ncc/ads", {"nccAdgroupId": adgroup_id}) or []

    def targets(self, adgroup_id):
        """요일/시간·지역·매체 등 타겟팅. 계정/버전에 따라 실패할 수 있어 호출부에서 무시 처리."""
        return self.get("/ncc/targets", {"ownerId": adgroup_id}) or []

    # ---- 대용량 보고서 (일별 성과) ----
    def stat_report(self, report_tp, stat_date, poll_sec=2, timeout_sec=300):
        """reportTp(AD, AD_CONVERSION)를 statDt 하루치로 만들어 TSV 행 리스트로 반환."""
        job = self.request("POST", "/stat-reports", body={"reportTp": report_tp, "statDt": f"{stat_date}T00:00:00.000Z"})
        job_id = job["reportJobId"]
        waited = 0
        try:
            while True:
                info = self.get(f"/stat-reports/{job_id}")
                status = info.get("status")
                if status == "BUILT":
                    break
                if status == "NONE":  # 그날 데이터 없음
                    return []
                if status in ("ERROR", "AGGREGATING_ERROR"):
                    raise NaverAPIError(f"보고서 생성 실패: {report_tp} {stat_date}")
                time.sleep(poll_sec)
                waited += poll_sec
                if waited > timeout_sec:
                    raise NaverAPIError(f"보고서 대기 시간 초과: {report_tp} {stat_date}")
            dl = urllib.parse.urlparse(info["downloadUrl"])
            content = self.request("GET", dl.path, params=dict(urllib.parse.parse_qsl(dl.query)), raw=True)
            text = content.decode("utf-8-sig", "replace")
            return [line.split("\t") for line in text.splitlines() if line.strip()]
        finally:
            try:
                self.request("DELETE", f"/stat-reports/{job_id}", retries=0)
            except NaverAPIError:
                pass
