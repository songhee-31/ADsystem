"""처음 설치 / 키 변경 마법사.  setup.bat 을 더블클릭하면 실행된다.

1) API 키 입력 (화면에 안 보임, 이 PC·이 Windows 사용자만 풀 수 있게 암호화)
2) 키 확인 + 내 관리계정·광고주 목록 불러오기
3) 매일 자동 갱신 등록 (작업 스케줄러)
4) 첫 수집 → 결과 화면 열기
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    print("Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.")
    input("Enter 를 누르면 닫힙니다.")
    sys.exit(1)

for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))

# 다른 사람의 키가 환경변수로 섞여 들어오지 않게, 마법사에서는 저장소 값만 쓴다
for k in ("NAVER_API_KEY", "NAVER_SECRET_KEY", "NAVER_CUSTOMER_ID"):
    os.environ.pop(k, None)

import config  # noqa: E402
import naver_api  # noqa: E402
import secret_store  # noqa: E402

TASK_NAME = "NaverSA_ChangeImpact"


def line(t=""):
    print(t, flush=True)


def ask(q, default=""):
    v = input(f"{q}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def yes(q, default=True):
    v = input(f"{q} ({'Y/n' if default else 'y/N'}): ").strip().lower()
    return default if not v else v in ("y", "yes", "ㅛ", "예", "네")


def secret_input(q):
    # 콘솔에서는 입력이 화면에 안 보이게. (파이프 입력일 때만 일반 입력)
    return (getpass.getpass(q) if sys.stdin.isatty() else input(q)).strip()


def update_env(values: dict):
    """.env 의 설정값만 갱신한다. (키는 절대 .env 에 쓰지 않음)"""
    env = BASE / ".env"
    lines = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else \
        (BASE / ".env.example").read_text(encoding="utf-8-sig").splitlines()
    done = set()
    out = []
    for l in lines:
        k = l.split("=", 1)[0].strip()
        if k in values and not l.lstrip().startswith("#"):
            out.append(f"{k}={values[k]}")
            done.add(k)
        else:
            out.append(l)
    out += [f"{k}={v}" for k, v in values.items() if k not in done]
    env.write_text("\n".join(out) + "\n", encoding="utf-8")


def step_keys():
    line("━━ 1단계: 네이버 검색광고 API 키 ━━")
    line("광고센터(searchad.naver.com) → 도구 → API 사용 관리 에서 확인할 수 있습니다.")
    if secret_store.CRED_PATH.exists():
        try:
            k, _, c = secret_store.load()
            line(f"이미 등록된 키가 있습니다. (키 {secret_store.mask(k)}, CUSTOMER_ID {secret_store.mask(c, 3)})")
            if not yes("새 키로 바꿀까요?", default=False):
                return
        except OSError:
            line("저장된 키를 이 PC/계정에서 열 수 없어 새로 등록합니다.")
    for attempt in range(3):
        api_key = secret_input("엑세스라이선스 (입력해도 화면에 안 보입니다): ")
        secret_key = secret_input("비밀키 (입력해도 화면에 안 보입니다): ")
        customer_id = input("CUSTOMER_ID (숫자): ").strip()
        if not (api_key and secret_key and customer_id.isdigit()):
            line("✖ 비어 있거나 CUSTOMER_ID 가 숫자가 아닙니다. 다시 입력해 주세요.\n")
            continue
        naver_api._CREDS = (api_key, secret_key, customer_id)
        try:
            naver_api.NaverAPI(customer_id).get("/billing/bizmoney")
        except naver_api.NaverAPIError as e:
            line(f"✖ 키 확인 실패: {e}\n   세 값이 같은 계정의 것인지 확인하고 다시 입력해 주세요.\n")
            naver_api._CREDS = None
            continue
        secret_store.save(api_key, secret_key, customer_id)
        line(f"✔ 키 확인 완료, 암호화해 저장했습니다. (키 {secret_store.mask(api_key)})\n")
        return
    line("키 확인에 3번 실패해 설치를 멈춥니다. 키를 확인한 뒤 setup.bat 을 다시 실행하세요.")
    input("Enter 를 누르면 닫힙니다.")
    sys.exit(1)


def step_accounts():
    line("━━ 2단계: 광고주 불러오기 ━━")
    naver_api._CREDS = None
    api = naver_api.NaverAPI(naver_api.manager_customer_id())
    try:
        managers = api.get("/manager-accounts", {"size": 1000}).get("content", [])
    except naver_api.NaverAPIError:
        managers = []
    values = {}
    if managers:
        line("내 관리계정:")
        for i, m in enumerate(managers, 1):
            line(f"  {i}. {m['managerAccount']['name']}  ({m.get('roleName')})")
        owners = [str(i) for i, m in enumerate(managers, 1) if m.get("roleName") == "OWNER"]
        pick = ask("가져올 관리계정 번호 (여러 개는 쉼표, Enter = 내가 책임자인 계정)", ",".join(owners) or "1")
        nos = []
        for p in pick.replace(" ", "").split(","):
            if p.isdigit() and 1 <= int(p) <= len(managers):
                nos.append(str(managers[int(p) - 1]["managerAccountNo"]))
        values["MANAGER_ACCOUNT_NOS"] = ",".join(nos)
        values["INCLUDE_DIRECT_ACCOUNTS"] = "1" if yes("관리계정 밖에서 직접 권한 받은 검색광고 계정도 넣을까요?", False) else "0"
    else:
        line("관리계정이 없어 내 아이디로 권한 받은 검색광고 계정을 사용합니다.")
        values["MANAGER_ACCOUNT_NOS"] = ""
        values["INCLUDE_DIRECT_ACCOUNTS"] = "1"
    values["OWNER_NAME"] = ask("화면에 표시할 담당자 이름 (비워도 됨)", "")
    update_env(values)
    # 바뀐 설정으로 다시 읽어 광고주 수 확인
    os.environ.update(values)
    config.MANAGER_ACCOUNT_NOS = [a for a in values["MANAGER_ACCOUNT_NOS"].split(",") if a]
    config.INCLUDE_DIRECT_ACCOUNTS = values["INCLUDE_DIRECT_ACCOUNTS"] == "1"
    import collector
    accs = collector.list_accounts(log=lambda m: None)
    line(f"✔ 광고주 {len(accs)}개를 불러옵니다. 예: {', '.join(a['name'] for a in accs[:5])}{' …' if len(accs) > 5 else ''}\n")
    if not accs:
        line("! 광고주가 0개입니다. 관리계정 선택을 바꾸려면 setup.bat 을 다시 실행하세요.")
    return len(accs)


def pythonw():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def step_schedule():
    line("━━ 3단계: 매일 자동 갱신 ━━")
    if not yes("매일 아침 자동으로 수집하도록 등록할까요?", True):
        line("건너뜀. 나중에 setup.bat 을 다시 실행하면 등록할 수 있습니다.\n")
        return
    at = ask("실행 시각 (24시간제)", "07:30")
    if len(at) != 5 or at[2] != ":" or not (at[:2] + at[3:]).isdigit():
        at = "07:30"
    # 경로는 환경변수로 넘겨 따옴표·한글 경로 문제를 피한다. PC가 꺼져 있었으면 켜진 뒤 바로 실행(StartWhenAvailable)
    ps = ("$a = New-ScheduledTaskAction -Execute $env:SA_PYW -Argument ('\"' + $env:SA_MAIN + '\" run') -WorkingDirectory $env:SA_BASE;"
          "$t = New-ScheduledTaskTrigger -Daily -At $env:SA_AT;"
          "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3);"
          f"Register-ScheduledTask -TaskName {TASK_NAME} -Action $a -Trigger $t -Settings $s -Force | Out-Null")
    env = dict(os.environ, SA_PYW=pythonw(), SA_MAIN=str(BASE / "main.py"), SA_BASE=str(BASE), SA_AT=at)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, encoding="cp949", errors="replace", env=env)
    if r.returncode == 0:
        line(f"✔ 매일 {at} 에 자동 수집합니다. (그 시간에 PC가 꺼져 있었으면 켜진 뒤 바로 수집)\n")
    else:
        line(f"✖ 자동 갱신 등록 실패: {r.stderr.strip() or r.stdout.strip()}\n")


def step_shortcut():
    """바탕화면에 결과 화면 바로가기."""
    try:
        desktop = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Desktop"
        if desktop.exists():
            target = (BASE / "output" / "index.html").as_uri()
            (desktop / "SA 변경 효과.url").write_text(f"[InternetShortcut]\nURL={target}\n", encoding="utf-8")
            line("✔ 바탕화면에 'SA 변경 효과' 바로가기를 만들었습니다.")
    except OSError:
        pass


def step_first_run(n_accounts):
    line("━━ 4단계: 첫 수집 ━━")
    mins = max(3, round(n_accounts * 0.4))
    if not yes(f"지금 첫 수집을 할까요? (광고주 {n_accounts}개 기준 약 {mins}분 이상 걸립니다)", True):
        line("건너뜀. 내일 아침 자동 수집 후 결과 화면이 생깁니다.")
        return
    r = subprocess.run([sys.executable, str(BASE / "main.py"), "run"])
    out = BASE / "output" / "index.html"
    if r.returncode == 0 and out.exists():
        line("✔ 첫 수집 완료. 결과 화면을 엽니다.")
        os.startfile(out)  # noqa: S606 (로컬 파일 열기)
    else:
        line("✖ 수집 중 오류가 있었습니다. data\\run.log 를 확인해 주세요.")


def main():
    line("네이버 SA 변경 효과 추적판 설치\n")
    step_keys()
    n = step_accounts()
    step_schedule()
    step_shortcut()
    if n:
        step_first_run(n)
    line("\n설치가 끝났습니다. 키를 바꾸거나 설정을 다시 하려면 setup.bat 을 다시 실행하세요.")
    if sys.stdin.isatty():
        input("Enter 를 누르면 닫힙니다.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        line("\n설치를 취소했습니다.")
