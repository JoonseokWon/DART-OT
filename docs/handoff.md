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

## v0.2.0 시각 디자인 개선

- 사용자 피드백: 토스와 시각적으로 다르므로 실제 화면 색상·형태·배치를 변경한다.
- 결정: TDS 공개 색상표의 blue500과 중립 회색, 흰색 둥근 카드, 주요/보조 버튼, 통합 검색과 빈 상태를 Windows Tk로 구현. DART-OT 이름·창 아이콘은 유지하며 공식 TDS 라이브러리 사용으로 표현하지 않는다.
- 관련 파일: desktop_ui.py(공통 시각 컴포넌트와 화면), app.py(연결·한글 입력·검색), scripts/ui_smoke.py, tests/test_dart_reliability.py, docs/design.md.
- 검증: 기존 회귀 검사에 6자리 종목코드 분기 검사 1개를 더해 19개 통과. 실제 Tk에서 오류 복귀·최소 창 필수 입력·회사 선택 후 버튼 노출·Excel 생성·결과 표 노출 통과. Computer Use로 한글 입력과 Enter 검색을 실제 확인.
- 실패·해결: 네이티브 EDIT에 Python WNDPROC 콜백을 붙이는 Enter 처리 방식은 실제 키 입력에서 GIL 오류로 종료됐다. 해당 콜백을 완전히 제거하고 기존 Tk 입력 폴링 루프에서 현재 포커스와 Enter 상태를 확인하도록 수정한 뒤 같은 실제 키 입력을 재검증했다. 입력 Canvas의 기본 요청 폭이 2열 배치를 밀어내는 문제는 명시적 최소 폭과 카드 폭 제한으로 수정했다. 검색 목록이 하단 버튼을 밀어내는 문제는 버튼 공간을 먼저 확보하고 테스트 조건을 추가했다.
- 최종 결과 화면: 실제 1180×760 창에서 큰 제목·회색 배경·둥근 카드·파란 주요 버튼·표와 스크롤 동시 노출 확인. 사용자 보고용 캡처는 outputs/ui-v0.2.0-result.png.
- GitHub 배포: v0.2.0 릴리스 게시 완료. Windows release 40818725100과 Regression tests 40818722430 모두 성공. 두 작업에서 19개 단위 검사와 Tk 화면 흐름 검증 통과.
- 배포 ZIP: 35,731,353바이트. release/verify-v0.2.0/에 다운로드하고 SHA256 ad6a386e2ecaa293e1d4e9b78fe80fdba599977ed37b7bc0480891f49d3d6f1e 일치, 3개 EXE 및 문서 포함 확인.
- 배포 EXE: 다운로드한 DART-OT.exe가 새 밝은 화면으로 정상 시작하는 것을 직접 확인했다. 캡처는 outputs/ui-v0.2.0-home.png이며 검증 창은 닫았다. 인계 기록을 제외한 코드 작업 트리는 깨끗하다.
- 현재 상태: v0.2.0 시각 개선·기능 회귀 검사·릴리스·체크섬·실제 EXE 시작 검증 완료. 계산 정확도 재측정과 전체 기업 원문 정답 대조는 이번 변경 범위 밖이다.
