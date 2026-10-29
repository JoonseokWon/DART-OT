# DART-OT 인계 기록

- 목적: DART 수집 오류 구분, 원문 근거 보존, 오류 복귀 개선.
- UI·UX 적용 여부: 적용 / 화면 있음.
- 상태: v0.1.2 구현·검증·GitHub 릴리스 게시 완료. 시작 시 작업 트리 깨끗함.
- 결정: 기존 화면·판정 기준 유지, 공통 API 계층과 원문추적 시트 추가. 별도 정답 대조 없이 정확도 개선율을 주장하지 않음.
- 관련 파일: app.py, disclosure_viewer.py, dart_api.py, tests/test_dart_reliability.py, scripts/ui_smoke.py, docs/spec.md, docs/design.md, docs/plan.md, docs/todo.md.
- 실행: 소스에서는 `_start.bat` 또는 루트 DART-OT.exe. 배포 ZIP을 새 폴더에 풀고 DART-OT.exe 실행. 릴리스: https://github.com/JoonseokWon/DART-OT/releases/tag/v0.1.2
- 완료: 공통 요청 계층, XML/JSON 오류 검사, 오류 캐시 방지, 3개 근거 시트, GUI 지연 콜백, 오류 복귀, 결과 표 스크롤, 하단 버튼 공간 확보.
- 실제 검증 결과 (2026-10-29): unittest 18개 통과. 실제 DART 회사 목록 119,816건(삼성전자 정확 일치 1건) 확인. 삼성전자 2026.03 보고서 1건에서 123개 추출 행과 매출 기준을 수집하고 `outputs/validation_v0_1_1.xlsx` 생성. API 키는 출력·산출물에 기록하지 않음.
- 화면 검증: Computer Use로 입력 화면 확인. 실제 Tk `scripts/ui_smoke.py --automated`에서 오류→입력 보존→재검색→검토→Excel 생성→최소 1180×760 창 버튼 노출 확인. 검증 실행은 설정 저장을 막고 모의 키·모의 데이터를 사용한다.
- 발견·수정: 최소 창에서 표가 하단 버튼을 밀어내던 실패를 재현하여 footer 공간을 먼저 확보. 결과 화면의 API 입력 패널을 접고 작은 브랜드 표시로 대체하여 표도 확보. 동일 검증 재실행 통과. 회사 목록 XML도 result 태그를 쓰지만 status 없이 list를 담는 정상 응답이므로 오류 봉투와 구분하는 테스트 추가. Windows 캡처 도구의 승인 시간 초과·다른 전경 창 캡처는 대상 창 활성화 후 재시도로 해결.
- GitHub 검증: Regression tests 40816942587과 Windows release 40816947987 모두 성공. 두 작업 모두 자동 테스트 18개와 실제 Tk 검증 통과. Windows ZIP(35,713,325바이트)과 SHA256 파일 게시 확인.
- 배포 검증: `release/verify-v0.1.2/`에 게시 ZIP을 다운로드하고 SHA256 `9f4b8edbee00b32782a0249fa63b67d23efc3c1792e6cb384facdc24bc821fd9` 일치 확인. DART-OT.exe, DART-Disclosure-Viewer.exe, DART-DEP.exe 및 docs/·README·프로세스·VERSION 포함 확인. 다운로드한 배포판 DART-OT.exe를 실제 실행해 시작 화면을 확인하고 검증 창을 닫았다.
- 미확인: 전 기업 원문 정답 대조, 배포판 뷰어·감가상각 도구의 전체 사용자 흐름. 이번 작업으로 정확도 상승률은 산출하지 않았다.
- 실패한 접근: 제한된 쉘의 외부 연결은 차단되어 승인된 외부 연결로 재실행했다. 로컬 Python에 PyInstaller가 없으므로 기존 GitHub Windows 빌드 경로를 사용한다.
- GitHub 첫 실행: v0.1.1의 자동 테스트 18개와 GUI 검증 assertions가 통과했지만 최종 한국어 JSON 로그 출력이 영문 cp1252 콘솔에서 UnicodeEncodeError로 실패했다. 로그를 ASCII JSON escape로 출력해 해결하며 검사 기준은 그대로 유지한다. v0.1.1 태그는 이력을 위해 유지하고 배포 파일은 v0.1.2로 게시한다.
- 백그라운드 후속 확인: 사용자가 요청했으나 반복 커밋·배포 자동화와 읽기 전용 반복 스케줄은 자동 승인 검토에서 거부됐다. 자동화는 생성하지 않았다. 현재 작업에서 릴리스 완료·첨부·체크섬·배포판 실행 검증까지 끝냈으므로 예약된 후속 실행은 없다.
- 다음 권장 작업: 고정된 접수번호·조회기간·중요성 프리셋과 원문 정답 표본으로 기업별 정확도 대조. 현재 릴리스 작업의 남은 필수 작업은 없다.
