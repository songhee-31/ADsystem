"""API 키를 Windows DPAPI로 암호화해 저장한다.

- 암호화 파일(data/credentials.bin)은 '이 PC의 이 Windows 사용자'만 풀 수 있다.
  파일이 유출되거나 다른 PC/계정으로 복사돼도 키를 읽을 수 없다.
- 키는 코드·.env·로그 어디에도 평문으로 남기지 않는다.
- 등록:  python main.py setkey   (입력값은 화면에 표시되지 않음)
"""
import ctypes
import ctypes.wintypes as wt
import getpass
import json
import os

import config

CRED_PATH = config.DATA_DIR / "credentials.bin"
_ENTROPY = b"naver-sa-change-tracker/v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _protect(data: bytes) -> bytes:
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "naver-sa", ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):
        raise OSError("키 암호화 실패")
    return _take(out)


def _unprotect(data: bytes) -> bytes:
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):
        raise OSError("키 복호화 실패 (다른 PC/계정에서 만든 파일이면 python main.py setkey 로 다시 등록하세요)")
    return _take(out)


def save(api_key, secret_key, customer_id):
    payload = json.dumps({"api_key": api_key, "secret_key": secret_key, "customer_id": str(customer_id)}).encode()
    CRED_PATH.write_bytes(_protect(payload))


def load():
    """(api_key, secret_key, customer_id). 환경변수가 있으면 우선(서버/CI용), 없으면 암호화 파일."""
    env = (os.environ.get("NAVER_API_KEY"), os.environ.get("NAVER_SECRET_KEY"), os.environ.get("NAVER_CUSTOMER_ID"))
    if all(env):
        return env
    if CRED_PATH.exists():
        d = json.loads(_unprotect(CRED_PATH.read_bytes()))
        return d["api_key"], d["secret_key"], d["customer_id"]
    return None, None, None


def mask(s, keep=4):
    s = str(s or "")
    return s[:keep] + "…" if len(s) > keep else "…"


def setup_interactive():
    print("네이버 검색광고 API 키를 등록합니다. (입력 내용은 화면에 보이지 않습니다)")
    api_key = getpass.getpass("엑세스라이선스: ").strip()
    secret_key = getpass.getpass("비밀키: ").strip()
    customer_id = input("CUSTOMER_ID: ").strip()
    if not (api_key and secret_key and customer_id.isdigit()):
        raise SystemExit("입력값이 비어 있거나 CUSTOMER_ID가 숫자가 아닙니다.")
    save(api_key, secret_key, customer_id)
    print(f"저장 완료: {CRED_PATH.name} (이 PC의 현재 Windows 사용자만 읽을 수 있음) · 키 {mask(api_key)}")
