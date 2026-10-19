# 외부 구성 요소

이 저장소는 아래 외부 구성 요소를 사용합니다. 각 구성 요소의 라이선스는 해당 파일에 보존했습니다. 이 문서는 개별 라이선스를 대체하지 않습니다.

| 구성 요소 | 사용 위치 | 출처 / 라이선스 파일 |
| --- | --- | --- |
| MarkovJunior | `Content/Python/mj/MarkovJunior.*`, 팔레트와 설정 리소스 | [원본 저장소](https://github.com/mxgmn/MarkovJunior), [MIT 라이선스](third_party/licenses/MarkovJunior-LICENSE.txt) |
| SixLabors.ImageSharp 3.1.12 | `Content/Python/mj/SixLabors.ImageSharp.dll` | 기존 로컬 NuGet 패키지에 동봉된 [Six Labors Split License](third_party/licenses/ImageSharp-LICENSE.txt) |
| Tamzen 글꼴 | `Content/Python/mj/res/fonts/Tamzen8x16*.png` | [원본 저장소](https://github.com/sunaku/tamzen-font), [라이선스](third_party/licenses/Tamzen-LICENSE.txt) |
| Dear PyGui 2.3.1 | `requirements.txt`로 설치하는 GUI 의존성 | [원본 저장소](https://github.com/hoffstadt/DearPyGui), 설치 패키지의 [라이선스](third_party/licenses/DearPyGui-LICENSE.txt) |

MarkovJunior는 기존 도구에서 텍스트 격자 출력을 위해 수정·빌드한 바이너리입니다. 원본 엔진을 그대로 빌드한 파일과 동일하다고 간주하면 안 됩니다. Unreal Engine과 Unreal 에디터 플러그인 바이너리는 포함하지 않습니다.
