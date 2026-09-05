# RELEASE — PyPI 배포 런북 (Trusted Publishing)

확정(2026-09-05, 사용자): 라이선스 **MIT**, 배포 인증은 **GitHub Actions Trusted Publishing(OIDC)**. 장기 API 토큰은 만들지도 저장하지도 않는다. 설계 근거는 DESIGN §10, 정식 배포의 사람 승인 게이트는 WORKFLOW §4.

## 0. 배포가 개발자의 자원을 쓰지 않는 이유 (사실)

| 항목 | 부담 주체 | 근거 |
|---|---|---|
| 패키지 파일 저장·다운로드 | PyPI(PSF 운영, CDN) | `pip install`은 PyPI에서 받는다. 개발자의 서버·트래픽이 아니며 무료. |
| 설치자의 실행·모델 비용 | 설치자 본인 | 설치자 PC에서 돌고, 모델 호출은 `lang-ai-agent init`으로 넣은 **설치자 자신의 API 키**로 과금된다. |
| 개발자의 API 키 | 배포물에 없음 | sdist·wheel에는 `src/` 소스, `pyproject.toml`, README만 들어간다(2026-09-05 빌드로 확인: `.env`·`data/`·`mcp_servers.json`·키 문자열 없음). |
| GitHub Actions 실행 시간 | GitHub | 공개 레포는 표준 러너 무료·무제한. 워크플로는 이 레포의 이벤트에만 반응하고, 포크 PR은 첫 기여자의 경우 소유자 승인 없이는 실행되지 않으며, 배포 job은 쓰기 권한자의 태그 푸시에만 돈다. |

남는 것은 PyPI의 프로젝트 이름 하나와 이슈 대응 여부뿐이다.

## 1. 사람이 1회 설정하는 것 (브라우저)

### 1.1 계정

- https://test.pypi.org 와 https://pypi.org 는 **별개 계정**이다. 둘 다 가입 → 이메일 인증 → 2FA(인증 앱 또는 보안 키) 설정. 2FA 없이는 퍼블리셔 등록이 되지 않는다.
- 프로젝트 이름 `lang-ai-agent`는 2026-09-05 기준 두 인덱스 모두 비어 있다(확인 완료). **프로젝트를 미리 만들지 않는다** — pending publisher를 등록해 두면 첫 업로드가 프로젝트를 생성하고 퍼블리셔가 그 프로젝트에 붙는다.

### 1.2 TestPyPI pending publisher

https://test.pypi.org/manage/account/publishing/ → "Add a new pending publisher" → GitHub 탭

| 필드 | 값 |
|---|---|
| PyPI Project Name | `lang-ai-agent` |
| Owner | `Trapa-Eureka` |
| Repository name | `lang-ai-agent` |
| Workflow name | `publish.yml` |
| Environment name | `testpypi` |

다섯 값이 워크플로와 **정확히** 일치해야 한다. Workflow name은 `.github/workflows/publish.yml`의 파일명만(경로 없이), 대소문자 그대로.

### 1.3 PyPI pending publisher

https://pypi.org/manage/account/publishing/ → 같은 값, Environment name만 `pypi`.

### 1.4 GitHub Environments

레포 Settings → Environments → New environment

- `testpypi`: 보호 규칙 없음. 시험 인덱스라 에이전트가 자율 배포하는 범위(WORKFLOW §4).
- `pypi`: **Required reviewers**에 본인 추가. "Deployment branches and tags" → "Selected branches and tags" → Tag rule `v*` 추가. 정식 배포 때 Actions 화면에서 "Review deployments → Approve"를 누르는 것이 곧 WORKFLOW §4의 사람 승인이다.

공개 레포이므로 무료 플랜에서도 환경 보호 규칙을 쓸 수 있다.

### 1.5 하지 않는 것

- API 토큰을 발급·저장하지 않는다. 로컬 수동 업로드가 꼭 필요한 예외 상황에서만 **프로젝트 범위** 토큰을 환경변수로 쓰고(레포 밖), 끝나면 폐기한다. TestPyPI와 PyPI 토큰은 별개다.
- GitHub Secrets에 아무것도 넣지 않는다. 워크플로에 필요한 권한은 job의 `id-token: write`뿐이다.

## 2. 에이전트가 하는 것 (T13 · T14)

- **T13**: `pyproject.toml` 메타데이터(`license = "MIT"`, classifiers, urls, keywords) + `LICENSE`(MIT) + `.github/workflows/publish.yml`(build → `testpypi` job) + `uv build` 검증 + 태그 `v0.1.0rcN`으로 TestPyPI 업로드·설치 확인. 업로드 단계는 §1이 끝난 뒤에만 가능하다.
- **T14**: `publish.yml`에 `pypi` job 추가(`testpypi` 성공 + `pypi` 환경 승인 후 실행) + 정식 배포 1회(사람 승인).

워크플로의 형태(구현은 T13, 값은 §1.2·1.3과 일치):

```yaml
name: Publish
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: test "v$(uv version --short)" = "$GITHUB_REF_NAME"   # 태그 = pyproject 버전
      - run: uv build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }
  testpypi:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: testpypi, url: https://test.pypi.org/p/lang-ai-agent }
    permissions:
      id-token: write            # OIDC — 토큰·시크릿 없음
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/
  pypi:                          # T14에서 추가
    needs: [build, testpypi]
    if: needs.build.outputs.prerelease == 'false'   # 정식 태그만
    runs-on: ubuntu-latest
    environment: { name: pypi, url: https://pypi.org/p/lang-ai-agent }   # Required reviewers
    permissions:
      id-token: write
    steps:                       # testpypi와 동일, repository-url만 없음
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

정식/프리릴리스 판정(T14 구현): build job의 태그 검사 단계가 `packaging.version.Version(version).is_prerelease`를 계산해 job 출력 `prerelease`로 넘기고, `pypi` job은 그 값이 `false`일 때만 실행된다. 태그 문자열에 `rc`가 있는지 보는 방식은 `a1`·`b1`·`.dev1` 프리릴리스를 정식으로 오판하므로 PEP 440 파싱으로 판정한다. 프리릴리스 태그에서는 `pypi` job이 skipped로 표시되고 워크플로는 성공이다.

## 3. 태그·버전 규칙

- 버전의 단일 소스는 `pyproject.toml`의 `version`. 태그는 `v` + 그 값(`v0.1.0rc1`, `v0.1.0`). build job이 둘의 불일치를 실패로 만든다.
- `vX.Y.ZrcN`(프리릴리스): TestPyPI까지만. `vX.Y.Z`(정식): TestPyPI → 사람 승인 → PyPI.
- PyPI·TestPyPI 모두 **같은 버전(파일)은 두 번 올릴 수 없다**(삭제 후에도). 시험은 rc 번호를 올려 가며 하고, 정식 `0.1.0`은 한 번뿐이다.

## 4. 배포 절차 (매번)

1. `pyproject.toml`의 version 올리기 → PR → `make check` → main 머지.
2. main에서 태그: `git tag v0.1.0rc1 && git push origin v0.1.0rc1`.
3. Actions에서 build → testpypi 성공 확인 후 설치 검증(TestPyPI에는 의존성이 없어 PyPI를 보조 인덱스로 붙인다):

```bash
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ "lang-ai-agent==0.1.0rc1"
lang-ai-agent --help
```

4. 정식: version `0.1.0` → 머지 → `v0.1.0` 태그 → testpypi 성공 후 Actions의 `pypi` job에서 "Review deployments" → Approve. 이 클릭이 정식 배포다. 태그를 올리면 TestPyPI에 그 정식 버전이 먼저 소비되므로(재업로드 불가), 릴리스에 포함될 README 등 배포물 내용이 확정된 뒤에 태그한다.
5. 확인: `pip install lang-ai-agent` → `lang-ai-agent init` → `lang-ai-agent serve`.

## 5. 실행 기록

| 날짜 | 태그 | 대상 | 결과 |
|---|---|---|---|
| 2026-09-05 | `v0.1.0rc1` (main `e21d000`) | TestPyPI | Publish run 33956057465 성공(build → testpypi). 격리 venv에 `--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/`로 설치, `lang-ai-agent --help`·버전·MIT 확인. pending publisher가 첫 업로드로 프로젝트 `lang-ai-agent`를 생성함 (T13) |

## 6. 문제 해결

- `invalid-publisher` 류 에러: §1.2/1.3의 다섯 값 중 하나가 워크플로와 다르다(대소문자·파일명·환경명).
- `File already exists`: 그 버전은 이미 올라갔다. 버전을 올려 다시 태그한다.
- `pypi` job이 승인 대기에서 멈춤: 환경 `pypi`의 Required reviewers가 본인인지, 태그 규칙 `v*`가 맞는지 확인.
- TestPyPI 설치가 의존성을 못 찾음: `--extra-index-url https://pypi.org/simple/`이 빠졌다.
