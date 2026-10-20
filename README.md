# 네이버 SA 변경 효과 추적판

광고센터에서 바꾼 것(키워드 추가, 입찰가·예산·타겟팅 변경, ON/OFF, 소재 등)이 **그 뒤에 성과를 어떻게 바꿨는지** 한 화면에서 보여 줍니다.
기존 점검판 시스템과는 별개로, 대행사(MCC) API 키 하나로 연결된 광고주를 모두 돌립니다.

## 처음 설정
1. Python 3.10 이상 설치. 변경이력을 엑셀(.xlsx)로 넣을 거면 `pip install openpyxl`
2. `python main.py setkey` → 엑세스라이선스·비밀키·CUSTOMER_ID 입력 (화면에 안 보이고, Windows DPAPI로 암호화돼 `data\credentials.bin` 에 저장. 이 PC의 이 Windows 사용자만 풀 수 있음)
   - 설정을 바꾸려면 `.env.example` 을 `.env` 로 복사해 수정 (키는 넣지 않음)
3. `python main.py accounts` → 광고주 목록이 나오면 연결 성공
4. `run_daily.bat` 실행 (처음엔 과거 35일 성과를 받느라 오래 걸립니다)
5. `output\index.html` 을 브라우저로 엽니다.
6. 매일 자동 갱신: PowerShell에서 `.\register_task.ps1` (매일 07:30)

## 변경 기록이 들어오는 두 경로
| 경로 | 방법 | 특징 |
|---|---|---|
| API 감지 (자동) | 매일 계정 상태를 저장하고 전날과 비교 | 입찰가·예산·ON/OFF·키워드/소재 추가·삭제·타겟팅. 변경자는 알 수 없음. 설치한 날 이후부터 (키워드·그룹 '추가'는 등록일로 과거 35일까지) |
| 광고센터 변경이력 (수동) | 광고센터 > 도구 > 변경 이력 관리 > 다운로드 → `data\inbox\<광고주 로그인ID>\` 폴더에 넣기 | 변경자·원문 포함, 과거 이력도 채울 수 있음. 다음 실행 때 자동 반영 |

같은 날 같은 광고그룹의 같은 종류 변경이 두 경로로 다 들어오면 엑셀 쪽만 남깁니다.

## 판정 방식
- 변경일 기준 **전 7일 vs 후 7일**의 하루 평균 비교 (변경 당일 제외)
- 기준 지표: 전환매출(구매완료). 전환이 없는 대상은 클릭
- ±15% 이상이면 성장/하락, 그 사이는 보합, 변경 후 3일 미만은 관찰중, 전에 성과가 없던 대상은 신규
- 같은 기간 계정 전체 변화도 함께 계산해, 계정 전체가 같이 움직였으면 카드에 적어 둡니다
- 기간·기준은 `.env` 에서 바꿀 수 있습니다

## 명령
```
python main.py run       # 수집 + 가져오기 + 화면 생성 (매일)
python main.py import    # inbox 엑셀만 반영하고 화면 다시 생성
python main.py build     # 화면만 다시 생성
python main.py demo      # API 없이 예시 화면 (output\demo.html)
```

## 파일
- `data\history.db` 수집한 성과·변경 기록 (지우면 처음부터 다시 수집)
- `data\snapshots\` 매일 계정 구조 스냅샷 (전날과 비교용)
- `data\run.log` 실행 기록
- `output\index.html` 최신 화면, `output\change_impact_YYYY-MM-DD.html` 날짜별 보관본

## 보안
- API 키는 코드·`.env`·로그에 평문으로 남지 않습니다. 오류 메시지에 키가 섞여도 앞 4자리만 남기고 가립니다.
- `data\`(키·수집 데이터)와 `output\`(광고주 실적 화면)은 `.gitignore` 로 저장소에 올라가지 않습니다.
- 결과 화면은 외부 스크립트·외부 전송이 막혀 있습니다(Content-Security-Policy). 광고주 실적이 들어 있으니 공유할 때 주의하세요.
- 키가 유출됐다고 의심되면 광고센터 > 도구 > API 사용 관리에서 재발급 후 `python main.py setkey` 로 다시 등록하세요.
- 서버/자동화 환경에서는 환경변수 `NAVER_API_KEY`, `NAVER_SECRET_KEY`, `NAVER_CUSTOMER_ID` 로도 넣을 수 있습니다.
