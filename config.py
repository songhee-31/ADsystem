"""설정 로드. 같은 폴더의 .env 파일(KEY=VALUE)을 읽는다."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INBOX_DIR = DATA_DIR / "inbox"          # 광고센터 변경이력 엑셀/CSV를 넣는 곳
IMPORTED_DIR = DATA_DIR / "imported"    # 가져오기 끝난 파일 보관
SNAPSHOT_DIR = DATA_DIR / "snapshots"   # 매일 계정 구조 스냅샷
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "history.db"


def _load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# API 키는 여기서 읽지 않는다. secret_store(암호화 파일)에서만 읽는다.
# 특정 광고주만 돌리고 싶으면 로그인 ID 또는 CUSTOMER_ID를 쉼표로 (비우면 전체)
ACCOUNTS = [a.strip() for a in os.environ.get("ACCOUNTS", "").split(",") if a.strip()]
# 광고주를 가져올 관리계정 번호(쉼표). 비우면 내가 OWNER인 관리계정 전체
MANAGER_ACCOUNT_NOS = [a.strip() for a in os.environ.get("MANAGER_ACCOUNT_NOS", "").split(",") if a.strip()]
# 관리계정 밖에서 내 아이디로 직접 권한 받은 검색광고 계정도 넣을지
INCLUDE_DIRECT_ACCOUNTS = os.environ.get("INCLUDE_DIRECT_ACCOUNTS", "0") == "1"
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "35"))     # 처음 실행 때 과거 성과를 받아올 일수
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "7"))          # 변경 전/후 비교 기간
REPORT_DAYS = int(os.environ.get("REPORT_DAYS", "60"))         # 화면에 보여줄 변경 기간
THRESHOLD_PCT = float(os.environ.get("THRESHOLD_PCT", "15"))   # 성장/하락 판정 기준(%)
# '내가 바꾼 것'으로 볼 네이버 아이디 (쉼표). 비우면 API 키 주인 아이디를 자동으로 사용
MY_NAVER_IDS = [a.strip() for a in os.environ.get("MY_NAVER_IDS", "").split(",") if a.strip()]
MIN_CLICKS = int(os.environ.get("MIN_CLICKS", "30"))           # 변경 전후 클릭 합이 이보다 적으면 판정 보류
WORKERS =int(os.environ.get("WORKERS", "3"))                  # 동시에 처리할 광고주 수
OWNER_NAME = os.environ.get("OWNER_NAME", "")

for d in (DATA_DIR, INBOX_DIR, IMPORTED_DIR, SNAPSHOT_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
