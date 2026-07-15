# 품질 평가와 availability

점수를 만들 수 있다는 사실과 정확하다는 사실을 혼동하지 않도록 각 metric은 `MetricResult`로
`value`, `available`, `warning`, `evidence_ids`를 함께 반환합니다. 필요한 구조가 없으면 0을 넣지 않고
`available=false`로 두며 aggregate는 실제로 존재하는 weight만 다시 정규화합니다.

## 구조 metric

| metric | 비교 | unavailable 조건의 예 |
| --- | --- | --- |
| `edge_agreement` | 정렬된 node 사이 edge multiset F1 | source relation 또는 node alignment 없음 |
| `arrow_agreement` | edge의 명시적 arrow endpoint multiset F1 | source에 arrowhead flag 없음 |
| `layout_similarity` | node 쌍의 좌/우·상/하 상대 순서 | 2개 미만 정렬, explicit generated position 없음 |
| `path_consistency` | 명시적 방향 root→terminal simple path multiset F1 | root/terminal 없음, cycle, path budget 초과 |

node 정렬은 같은 ID, collision-free portable emitted-ID alias 순서로 사용하고, 그다음 중복되지 않는
NFKC/casefold label만 사용합니다. normalized ID collision은 alias로 강제 정렬하지 않습니다. 방출 ID가
다른 raw source ID와 우연히 같아지는 collision cluster는 raw exact-ID provenance도 부여하지 않고 unique
label/evidence 같은 독립 근거가 있을 때만 정렬합니다. Geometry로 node를 맞추지 않으므로 layout metric이
자신의 가정을 검증하는 순환을 피합니다. edge topology는 방향을
무시하고 방향 오류는 arrow metric이 별도로 측정합니다.
Flowchart/Swimlane/BPMN/Architecture와 Sequence 계열의 generated Scene 방향은 raw
`arrow_at_start`/`arrow_at_end` hint가 아니라 serializer가 실제 방출하는 단방향 또는 `bidirectional`
connector에서 파생합니다.

typed IR은 serializer가 실제 방출하는 node/edge 구조로 다시 변환합니다. bbox가 IR에 명시되지 않으면
layout을 추측하지 않습니다. Scene IR portable fallback은 deterministic serializer 보존 여부를 평가할 수
있습니다. raw/direct Mermaid는 아직 일반 AST→Scene 변환이 없으므로 구조 점수가 unavailable일 수 있습니다.
평가 Scene adapter는 sequence/ZenUML, hierarchy/organization, planning/event,
Packet/Pie/Radar/Treemap/Ishikawa/TreeView, Wardley/Cynefin, data-lineage, Railroad, Venn까지 포함하며 typed
record의 evidence ID를 보존합니다.
Event Modeling의 generated Scene은 fallback serializer와 같은 namespaced frame/relation ID,
화면에 보이는 typed/time label, lane subgraph membership, `LR` 방향, end-arrow만 사용합니다.
ZenUML도 Sequence fallback의 namespaced participant/message ID, alias label, endpoint와 end-arrow를
공유합니다. 두 adapter는 raw bbox·direction·role·shape·style·bidirectional metadata를
방출 구조로 복사하지 않고 zero geometry를 써서 layout score를 위조하지 않습니다.
Wardley의 label 없는 component와 ZenUML의 label 없는 participant도 임의 `text`가 아니라
serializer가 실제 표시하는 safe source ID를 사용합니다.

Organization generated Scene은 shared plan의 logical `treeview_node_*` identity, 실제 visible label,
parent→child containment, `LR` 방향을 사용합니다. Pipeline은 검증된 terminal grammar를
adapter에 넘겨 native TreeView의 marker 없는 connector/shape 미지정과 Flowchart fallback의
rectangle/end-arrow를 구분합니다. Child record evidence는 child element와 해당 containment
relation에 연결하고, native/fallback이 재현하지 않는 raw bbox/group/style은 버립니다.
Data Lineage Scene은 `data_lineage_dataset_*`·
`data_lineage_process_*` node, cylinder/rectangle shape, `data_lineage_relation_*`
data-flow/end-arrow, 검증된 `TB`/`BT`/`LR`/`RL` 방향만 사용합니다.
Lineage relation evidence는 해당 relation에만 연결합니다. 두 adapter의 geometry는 0,
group은 빈 list이며 OCR projection은 화면에 보이는 node/relation label을 record당 한 번만
사용합니다.
Railroad Scene도 shared plan의 `railroad_rule_*`·`railroad_expression_N` logical element와
`railroad_relation_N` containment slot을 사용합니다. Rule과 leaf expression evidence는 각 element에,
expression evidence는 그 expression으로 들어오는 relation에도 연결합니다. Native connector에는 marker가
없으므로 relation의 양쪽 arrow flag를 모두 끄고 `LR`, zero geometry, 빈 group으로 표시합니다.
Terminal/nonterminal/special만 각 grammar-visible label을 갖고 구조 operator는 label이 없으며,
nonterminal reference를 별도 dependency edge로 세지 않습니다. OCR projection도 rule `native_name =`, leaf
label과 special `? text ?`만 한 번씩 사용하고 accessibility/title이나 operator type을 content recall에
넣지 않습니다. 여기서 rule text는 normalized safe source name 또는 scanner/preprocessor source-active와
native grammar-reserved name을 collision-safe `rrmapped_N[_suffix]`로 mapping한 실제
`native_name =`입니다. Source-active 범위에는 `style`/`classDef` substring이 포함되고, reserved 범위에는
case-folded expression-word namespace, `railroad-beta`, case-folded lowercase `title*` prefix가 포함됩니다.
Logical element
ID는 source 기반 `railroad_rule_*`를 유지합니다. Mapping은 warning으로 공개되고 raw source name은 typed
IR에, normalized name은 nonterminal label에 남습니다. Terminal/special/title/accessibility의 ASCII angle은
`〈`/`〉`, 모든 ASCII `#`는 `＃`, entity-like `&` prefix는 `＆`, NFKC quote/backslash hazard는 `″`/`∖`로
투영하고 warning으로 공개합니다. Scene/OCR은 emitted source의 zero-width separator를 제외한 이 exact
compatibility text를 공유하고 semantic 원문은 typed IR에 보존합니다. Direct Scene은 `evidence_ids`가
null/생략 또는 string list가 아니면 fail closed합니다. Raw source bbox·ID·label·role·shape·style extra는
Scene/OCR 구조로 승격하지 않습니다.
Wardley Scene은 raw record bbox 대신 native 좌표를 화면에 맞게 바꾼 `(x, 1-y)`만
`normalized` explicit position으로 씁니다. IR의 수평/수직 `x`/`y`는 native에 `[y, x]`로
방출하고 token 반올림을 Scene 값에도 적용합니다. `->` link는 실제 SVG에 marker가 없으므로
무방향 relation으로 평가해 arrow/path 점수를 만들지 않습니다.
Native rejection 뒤 선택된 Wardley Flowchart fallback은 공용 plan의 순서 기반 emitted ID와 explicit
무방향 topology를 유지하지만 좌표·축·anchor를 표현하지 않습니다. 따라서 generated Scene은 모든 node를
zero bbox rectangle로 두고 `pixels`/`LR`를 사용합니다. `relative_layout_similarity`는 동일 중심점 때문에
unavailable이며, fallback canvas에 보이지 않는 native title도 OCR generated label에서 제외합니다.
Native Cynefin Scene은 domain·item·transition과 domain group membership을 공유 plan에서 복원하고,
runtime이 항상 만드는 고정 domain/practice/response/disorder text를 무근거 template element로
추가합니다. `confusion` item은 처음 세 개와 `+N more`만 native 렌더에 맞게 투영합니다.
Native placement가 없어 zero geometry를 쓰므로 layout similarity는 unavailable이며, 고정 template의
source provenance 계약이 없는 native 결과는 aggregate와 무관하게 review를 요구합니다.

Terminal runtime type이 Flowchart인 Cynefin fallback은 별도 projection을 사용합니다. Source에 있는 domain만
같은 ID의 conceptual element와 group으로 만들고, 모든 explicit item과 explicit directed transition을
축약 없이 투영합니다. Fixed template와 membership relation은 추가하지 않으며 domain label도 element/group
때문에 두 번 세지 않습니다. 모든 bbox는 0이고 direction은 실제 fallback의 `LR`이므로 layout similarity는
unavailable이며 quadrant/Cynefin 공간 의미 손실을 warning에 남깁니다. Domain/item/transition의 record-local
provenance와 다른 semantic hard gate가 충분하면 이 fallback에는 native 전용 review hold를 적용하지 않습니다.
Event Modeling·ZenUML·Wardley·Cynefin의 grammar/entity-like 원문을 Mermaid 11.16 호환
glyph로 표시하는 경우 OCR projection도
해당 호환 label이 실제 SVG에 보이는 text를 사용합니다. 원문을 projection에 넣어 렌더러 손실을
숨기지 않습니다. Wardley 축·evolution stage처럼 grammar 고정 chrome을 전체 source label로
간주하지는 않습니다.

Packet Scene은 serializer의 field plan에서 나온 reserved-safe emitted ID, label,
bbox/evidence를 그대로 사용하고 입력에 없는 field 간 edge를 추가하지 않습니다.
Bit range는 같은 plan에서 검증되지만 Scene element로 승격하지 않고 별도 numeric
projection/source gate에서 비교합니다. Pipeline은 검증된 terminal grammar도 semantic projection에
전달합니다. Native Packet일 때만 실제 canvas의 normalized title을 OCR text에 포함하고, disconnected
Flowchart fallback에서는 native-only title을 제외합니다. Entity-like title은 serializer와 같은 visible
fullwidth glyph를 사용하지만 source security용 invisible separator는 OCR token을 쪼개지 않도록 제거합니다.
Pie Scene은 serializer·semantic OCR과 같은 bounded `PiePlan`을 사용합니다. Native terminal은 각
`pie_slice_N`을 `sector` element로 만들고 positive slice를 Mermaid percentage-label radius의 normalized
centroid에 놓습니다. Zero slice는 legend-only이므로 zero bbox이며, relation/group 없이 `radial` 방향을
사용합니다. Element text는 `showData` 표시를 포함한 실제 legend text입니다. Native OCR은 visible title,
모든 legend와 positive slice percentage만 세고 접근성 metadata는 제외합니다. Flowchart terminal은
`TB` 방향의 zero-geometry `label: exact-value` rectangle만 만들며 relation/group과 native-only title을
추가하지 않습니다. Slice evidence는 두 terminal의 element에 record-local로 유지되고, malformed evidence
list는 구조를 바꾸지 않은 채 해당 slice provenance를 비웁니다. Terminal-visible compatibility glyph과
source-only separator 제거도 serializer/OCR/Scene이 같은 plan에서 공유합니다.

Pie는 Extended generated-node provenance gate에 포함됩니다. Slice는 실제 Mermaid element이므로 native와
fallback 모두 injective attribution 분모에 남고, 여러 slice가 같은 eligible evidence를 주장하면 기존
collision 취소 규칙을 적용합니다. Percentage와 title은 별도의 generated node가 아니며 provenance credit을
만들지 않습니다. 따라서 slice-local numeric binding이 정확해도 generated slice attribution이 80% 미만이거나
계산 불가능하면 자동 게시하지 않습니다.
Explicit Pie title/accessibility text는 별도 content node가 아니므로 전용 gate가 candidate-authorized 독립
OCR/vector exact observation 또는 reconstruction 초기 입력의 exact `user_edit` evidence를 요구합니다.
Engine-emitted `user_edit`는 이 신뢰 경계를 만들 수 없습니다. Slice-owned observation과 겹치거나 ID만 바꾼
동일 text+bbox는 독립 근거가 아닙니다. 결정적으로 파생한 기본 접근성 문구와 experimental notice만 별도
source attribution 없이 허용합니다.
Radar Scene은 serializer·semantic OCR과 같은 `RadarPlan`을 사용합니다. Native terminal은 axis와 data point를
Mermaid의 radial scale에 맞춘 `[0,1]` normalized 위치에 놓고, series element와 마지막 point→첫 point까지 닫힌
marker/label 없는 `series_curve` association을 만듭니다. Series bbox는 point들의 normalized curve envelope이고
source bbox나 임의 원점이 아닙니다. Direction은 `radial`, group은 비어 있으며 source
bbox를 generated position으로 복사하지 않습니다. Series text는 `showLegend=true`일 때만 visible합니다.
Fallback terminal은 실제 `flowchart TB`처럼 visible title을 isolated zero-geometry node로 보존하고,
series별 zero-geometry group을 만들되 `showLegend=true`일 때만 label을 표시하며 rectangle
`dimension: exact-value` cell과 빈 relation list를 사용합니다. Dimension/series evidence는 각 axis와
series에, bounded union은 point/cell에, series evidence는 native curve relation에 연결합니다. Malformed
evidence list는 그 record에서만 전부 비우며 terminal-visible compatibility glyph과 warning을 Scene/OCR에서도
공유합니다.
Native data point는 독립 Mermaid node가 아니라 series curve에서 파생된 geometry이므로 generated-node
provenance 분모에서는 제외하고, 직접 귀속 가능한 axis와 series만 injective하게 평가합니다. Flowchart cell은
두 source record의 terminal projection이므로 record-local association을 통과한 dimension/series evidence를
공유할 수 있지만, 알려진 evidence가 전혀 없는 cell은 provenance credit을 받지 못합니다.
Treemap Scene은 serializer·semantic OCR과 같은 DFS preorder `TreemapPlan`을 소비합니다.
Native terminal은 source에서 고유하고 bounded한 ID 또는 collision-safe
`treemap_node_N[_suffix]`를 section/leaf identity로 쓰고 parent/child를 arrow가 없는 logical
containment로 표시합니다. 실제 SVG에 connector path가 없고 영역 중첩 배치이므로
`reading_direction`은 `unknown`입니다. Source bbox는 typed IR/source provenance에 남지만
native/fallback generated Scene은 모두 zero bbox를 써서 source 위치를 rendered layout으로
오인한 거짓 layout score를 만들지 않습니다. Flowchart terminal은 같은 plan의
DFS preorder `N1..Nn`, rectangle, `TB`, parent→child end-arrow와 explicit ` (value: x)` label을
사용합니다. Child record evidence는 element와 containment relation에 공유하며 malformed/oversized
`evidence_ids`는 해당 record의 목록 전체만 비워 partial provenance를 만들지 않습니다.
Quote와 Flowchart angle/backslash/hash, native title angle의 visible compatibility glyph는 Scene/OCR에
같이 투영하고 candidate warning으로 공개합니다. Scanner용 zero-width separator는 content token에
남기지 않습니다. Unicode whitespace run은 terminal과 semantic projection에서 한 ASCII space로
정규화하고, resolved accessibility metadata의 visible 치환도 candidate warning에서 누락하지 않습니다.
Venn Scene도 serializer·semantic OCR과 같은 `VennPlan`을 소비합니다. Set의 portable emitted ID와
collision-safe intersection ID를 공유하고 source bbox는 typed IR/review provenance에만 보존하며 두
terminal의 generated bbox는 모두 zero입니다. Native는 set circle, shape 없는 intersection,
label/marker 없는 logical membership, `unknown` direction을 사용합니다. Native OCR은 visible title과
실제 area label만 세고 geometry input value는 canvas text로 세지 않습니다. Flowchart는 set circle,
intersection round, exact value-suffix label, `intersects` relation label, end-arrow, `LR`을 사용합니다.
Set/intersection evidence는 element에, intersection evidence는 그 membership relation에도 연결하고 malformed
evidence tuple은 record-local로 비웁니다. Terminal-visible compatibility text와 warning도 같은 plan에서
공유합니다.
Ishikawa/TreeView는 serializer와 공유하는 DFS plan의 정확한 parent/emitted ID로 containment를
만듭니다. Duplicate/normalized collision, missing-ID ambiguity, alias conflict, cycle, object reuse 또는
resource 한도로 planner가 거부하면 Scene adapter는 충돌 node를 조용히 제거해 attribution
분모를 줄이지 않고 전체 metric을 unavailable로 둡니다.

C4 자동 후보의 generated Scene은 진단용 native C4 macro를 재구성하지 않습니다. 자동 serializer가 실제
게시 대상으로 만드는 Architecture와 필요 시 nested Flowchart fallback을 따라, C4 element·boundary·relation을
공용 bounded Architecture service/group/edge plan에 넣습니다. 따라서 collision-safe emitted ID, boundary
membership, 표시 label, endpoint와 arrow semantics는 두 fallback grammar 및 OCR projection에서
동일합니다. element bbox/evidence, relation evidence, boundary bbox는 원 record에서 보존하지만,
relation polyline, technology, description, relation label, native boundary notation과 기타 fallback이
표시하지 않는 raw metadata는 구조나 OCR label로 승격하지 않습니다. `reading_direction`은 runtime의
Architecture→Flowchart 선택을 generated Scene이 미리 알 수 없으므로 IR 값 또는 `unknown`을 유지합니다.
형식이 잘못됐거나 reference 예산을 넘은 C4 `evidence_ids`는 기존 Mermaid 게시를 막지 않고 해당
generated Scene attribution에서 제외합니다.

## 기존 metric과 결합

- syntax/render는 게시 hard gate이면서 score input입니다. CandidateValidator의 SVG inspection은 Mermaid가
  render 성공을 보고해도 geometry attribute에 `NaN` 또는 `Infinity`가 있으면 render-invalid로 바꿉니다.
- pipeline은 최종 source, 사후 보안 검사를 통과한 비어 있지 않은 SVG, 선택적 runtime PNG의 SHA-256,
  security profile, emitted/runtime type을 validation receipt로 함께 봉인합니다. Receipt 설치에는
  `CandidateValidator`가 exact source/SVG/PNG 검사를 끝낸 뒤 발급한 process-local certificate가 필요하며,
  단순히 candidate의 valid flag를 설정해서는 발급되지 않습니다. 별도의 publication receipt는 freshly
  recomputed publish policy, status, 자동 `review_required` routing과 선택 후보 receipt digest를
  고정합니다. 사용자 승인·거절은 generation receipt를 바꾸지 않고 review state/revision/history에
  기록합니다. Markdown renderer는 boolean flag만 신뢰하지 않고 두 receipt와 process-private seal이 현재 상태에 모두
  일치할 때만 fence를 삽입합니다. 객체를 JSON으로 왕복하면 공개 digest는 audit용으로 남지만 private
  trust는 복원되지 않으므로 다시 검증하지 않은 역직렬화 결과는 자동 게시할 수 없습니다.
  Publication receipt의 quality digest는 표시되는 aggregate score와 grade, metric map, generation
  warning을 함께 고정합니다. Markdown에 전달하는 봉인 snapshot은 serializer stability도 함께 고정하며,
  `experimental` candidate는 grade A여도 `Experimental reconstruction` 경고를 표시합니다. Pipeline은 선택 후보 warning을 중복 제거하고 최대 256개·항목당 4,096자로
  제한한 뒤 결정하므로, 점수나 `scores.json`만 바꿔 신뢰도가 높은 것처럼 표시할 수 없습니다. 이때 게시
  보류·정책 제한을 설명하는 evaluation warning과 pinned renderer compatibility warning을 engine 진단보다
  먼저 보존하고 남은 예산만 일반 warning에 사용하므로, noisy engine output이 best-effort 결과의 필수
  experimental 경고를 밀어낼 수 없습니다. Digest의
  확률 값은 exponent 없는 decimal string으로 encode하고 negative zero를 `"0"`으로 정규화하므로 Python과
  JavaScript verifier가 같은 bytes를 재현할 수 있습니다.
- Source/generated Scene은 nested record를 포함한 현재 payload가 Pydantic resource 계약을 다시 통과한
  경우에만 semantic scoring에 들어갑니다. Fusion overflow는 winner record fallback으로 canonical화되고,
  pipeline의 내부-fusion backstop은 evidence collection의 exact-list/20,000-item 계약도 확인합니다. 이
  backstop이 실패하면 fused 후보만 격리한 채 원 engine 후보를 유지합니다.
- OCR recall은 NFKC/casefold한 원 OCR token multiset의 occurrence recall입니다. 같은 text라도 다른 bbox에서
  관찰되면 별도 occurrence로 유지하고, context OCR과 OCR/vector evidence가 겹치면 token별 최대 count를
  사용합니다. bbox가 없는 동일 text evidence는 공간적으로 다른 occurrence임을 입증하지 못하므로 하나로
  합칩니다. Typed/Scene 후보는 generated Scene의 node, relation, group label을 비교하며 Gantt task와
  section도 Scene 의미 label로 복원합니다. 따라서 Mermaid ID, schedule field, header,
  `accTitle`/`accDescr`가 recall을 올릴 수 없습니다. Scene adapter가 없는 direct 후보는 quoted label과
  문법별 보수적 label fallback을 적용합니다.
- OCR/vector reference와 생성 semantic label은 각각 최대 50,000개 observation, 1,000,000자,
  100,000 token의 평가 예산을 적용합니다. 어느 한도를 넘으면 일부 입력을 잘라 점수를 만들지 않고
  semantic evaluation을 unavailable로 표시하여 자동 게시를 막습니다. Token occurrence는 `Counter`로
  유지하며 반복 횟수만큼 list를 확장하지 않습니다. Parse/render에 실패한 후보는 구조 변환과 OCR 같은
  고비용 semantic scoring을 건너뛰고, typed Scene 변환 오류는 후보 단위 warning으로 격리합니다.
- 구조 Scene은 topology를 위해 class member나 ER attribute를 node로 만들지 않습니다. 별도의 지연형
  typed semantic projection이 실제 serializer가 표시하는 Class field/method/parameter/cardinality,
  ER attribute type/name/key/comment, Timeline period/title/모든 event label을 OCR 비교에 추가합니다.
  이 projection도 생성 label 예산 안에서 소비되므로 큰 typed IR이 제한을 우회하지 못합니다.
  Core Scene도 serializer-visible default를 그대로 사용합니다. Block은 collision-safe emitted ID와
  `[unreadable]` fallback을 공유하고, 일반 State는 serializer가 쓰는 label/ID만 세며 choice/fork/join은
  topology element를 유지하되 실제 canvas에 없는 source label을 OCR text로 세지 않습니다. Sequence
  무라벨 message와 Gantt 무라벨 task는 각각 `[unreadable]`, section-local `Task N`으로 투영하며 hidden
  `text`/task ID에 OCR credit을 주지 않습니다. State의 normalized ID와 transition endpoint는 serializer와
  Scene이 하나의 plan을 공유하고 malformed 또는 unknown endpoint transition은 전체 Scene을 fail closed합니다.
  `[*]` boundary marker는 structural relation으로 만들지 않지만 화면에 표시되는 transition label은 OCR
  projection에 유지합니다.
  Gantt section/task는 source ID가 중복되어도 collision-free Scene identity를 배정해 렌더링된 record와
  provenance를 모두 보존합니다.
- Requested type이 fallback으로 방출되는 경우 projection은 요청 문법이 아니라 실제 emitted serializer를
  따릅니다. C4의 `architecture` 또는 nested Flowchart 결과는 위 공용 plan의 emitted boundary group과
  service label만 세고 technology, relation label, description은 제외합니다. Architecture도 native와
  nested Flowchart에서 service `label`/`name` alias, group label과 label 없는 topology를 동일하게
  평가합니다. label 없는 Architecture group은 두 serializer가 같은 portable emitted ID를 표시합니다.
  Deployment와 Component fallback에서 보존만 되는 relation label은 세지 않으며, Use-case Flowchart
  relation은 serializer와 같은 `type` 우선, `label` fallback 순서로 셉니다. 이 세 software fallback의
  Scene node도 serializer의 record planner를 공유해 missing/colliding ID, `label`/`name` alias와 endpoint를
  실제 방출 결과와 같은 공간으로 정규화합니다. Use-case planner는 Actor와 UseCase의 최종 namespace를
  함께 할당해 prefix 뒤의 2차 collision도 suffix로 분리합니다. serializer가 소비하지 않는 raw
  `text`/`role`/`shape`/style/semantic metadata와 relation ID는 의미 구조로 승격하지 않으며, node와 relation
  수가 Scene budget을 넘으면 serializer와 projection이 같은 경계에서 거부합니다.
  Requirement는 serializer와 같은 normalized/collision-safe output ID,
  requirement type·ID·text·risk·verification, element type·docref, relation type을 셉니다. 접근성 metadata와
  serializer가 무시한 대체 label은 포함하지 않습니다. Event Modeling은 lane label과 실제 fallback의
  time·frame type·label 조합 및 relation label, Wardley는 native title·component·link label을 셉니다.
  Cynefin native는 고정 template·실제 visible item(`confusion`은 세 개+`+N more`)·transition label을,
  Flowchart fallback은 supplied domain label을 한 번씩, 모든 explicit item과 transition label을 셉니다. ZenUML은
  Sequence fallback의 participant alias·message label만 셉니다. Packet은 terminal이 native일 때만 canvas
  title을 field label 앞에 세고 Flowchart fallback에서는 제외합니다. Pie native terminal은 visible title,
  모든 legend와 positive slice의 percentage를 세며 `showData` value는 legend text에 포함합니다. Pie
  Flowchart는 exact `label: value` cell만 세고 native-only title과 접근성 metadata는 제외합니다. Quadrant
  native terminal은 visible title, axis endpoint 네 개, supplied quadrant label과 point label만 세고 좌표와
  접근성 metadata는 제외합니다. Quadrant Flowchart는 title·axis·supplied slot·exact `label · x X, y Y`
  cell을 셉니다. Sankey native
  terminal은 node label과 renderer가 표시하는 `max(incoming, outgoing)` 합계를 세되 개별 flow weight는 세지 않고, Flowchart terminal은
  node label과 exact edge-weight label을 셉니다. 두 Sankey 경로 모두 title/description을 canvas text로 세지
  않습니다. Radar native terminal은 visible title·axis와 `showLegend=true`인 series legend만 세고, value,
  bounds, ticks, graticule과 `accTitle`/`accDescr`는 geometry/metadata이므로 제외합니다. Radar Flowchart는 visible
  title, `showLegend=true`인 series subgraph label과 각 `dimension: exact-value` cell을 세며 hidden option은
  제외합니다.
  Treemap native terminal은 visible `title`, 각 section/leaf label, d3-hierarchy의
  reverse-order binary64 합산을 d3 `format(",")`으로 표시한 값을 세고, Flowchart terminal은
  preorder node의 exact value-suffix label만 셉니다. `accTitle`/`accDescr`는 SVG metadata일 뿐 content
  label이 아닙니다. Native renderer는 작은 cell text를 `display:none`으로 숨길 수 있으므로
  실제 render review에서는 이 제한을 같이 봅니다. Venn native terminal은 visible title과 set/intersection
  label만 세고 area value와 marker-less membership은 OCR text로 만들지 않습니다. Venn Flowchart는 exact
  value-suffix node label과 membership마다 보이는 `intersects`를 세며 accessibility metadata와 native-only
  title은 제외합니다. 내부 endpoint ID, 좌표, anchor 같은 문법 구조와
  접근성 text는 OCR 의미 증거로 세지 않습니다.
  각 유형의 record planning은 serializer와 projection이 같은 deterministic helper를 공유합니다.
- Typed semantic projection이 malformed data나 adapter defect로 예외를 내면 해당 candidate의 OCR을
  direct-code fallback으로 바꾸지 않습니다. 예외를 candidate warning으로 격리하고 aggregate를
  unavailable로 유지하여 다른 candidate 선택과 문서 변환은 계속합니다.
- 일반 numeric consistency는 source/generated 숫자 occurrence multiset의 precision·recall F1입니다. Pie·XY·
  Quadrant·Radar와 Packet의 record-local 결합 검증은 아래 예외를 사용합니다. Bounded
  evidence 안의 동일 normalized text+bbox는 한 관측으로 합치고, OCR context와 evidence 채널의 numeric
  Counter는 token별 최대 occurrence로 병합합니다. 따라서 위치가 다른 반복값은 보존하면서 채널 간 중복
  보고는 다시 세지 않습니다. 생성한 숫자가 source에 없거나 occurrence 수가 다르면 precision/recall을
  낮춥니다. Generated projection은 Mermaid `%%` comment를 제외하고, detected grammar가 지원할 때만
  `title ...`/`title: ...`, `accTitle: ...`, 한 줄 `accDescr: ...`와 block `accDescr { ... }`를 chart
  metadata로 제외합니다. Sankey의 metadata-like CSV label과 weight는 실제 data로 보존합니다. Quadrant의
  `quadrant-1`~`quadrant-4` slot index도 문법 토큰으로 제외하지만 directive label과 point 좌표 안의 실제
  숫자는 보존합니다. Block metadata 뒤 같은 줄의 statement는 다시 평가하며 bounded suffix budget이
  소진되면 부분 점수 대신 `0.0`으로 fail closed합니다.
- Sankey 구조 metric도 검증된 terminal grammar를 따릅니다. Native는 source node identity와 marker-less
  `data_flow` topology, 고정 `LR`, 무라벨 relation을 사용하고 node Scene text에는 파생 합계를 섞지 않습니다.
  Flowchart fallback은 공용 planner의 collision-safe emitted ID, exact weight relation label, end-arrow와
  정규화된 requested direction을 사용합니다. Node/flow record의 evidence만 attribution에 연결하며 입력의
  미방출 role/shape/style/arrow/semantic hint와 title/description은 구조·OCR 점수를 높일 수 없습니다.
  Malformed/oversized evidence list는 문자 단위 ID로 coercion하지 않고 해당 record에서만 빈 provenance로
  격리하며, relation count와 relation ID도 Scene resource 경계 안에서 serializer와 함께 검증합니다.
  Flowchart projection이 pinned runtime의 500-edge cap을 넘으면 partial Scene을 만들지 않고 unavailable입니다.
- Sankey numeric consistency는 plan의 각 flow가 가진 exact `value_text`를 candidate-authorized OCR/vector
  observation에 flow-local로 결합하고, 전역 source/generated 숫자 occurrence exactness도 요구합니다. Flow와
  evidence bbox는 source image 안의 양의 면적이고 flow끼리 양의 면적으로 겹치지 않아야 하며 cited evidence는
  해당 flow bbox 안에 완전히 포함되어야 합니다. Evidence ID나 normalized text+bbox의 cross-flow 재사용,
  같은 bbox의 상충 관측, weight swap,
  invalid/missing geometry·authority와 bounded reference/text/token/spatial budget 초과는 metric 전체를
  unavailable 또는 mismatch로 두어 review합니다. Native, same-slot Flowchart와 semantic repair는 새 typed
  IR/scoped evidence로 같은 gate를 다시 계산하며 direct 또는 untyped Sankey는 owner binding이 없어
  review-only입니다.
- Pie 구조 metric은 `PiePlan`이 확정한 terminal을 따릅니다. Native는 최대 12 slice, zero-or-normal binary64
  round-trip value와 left-to-right finite positive total, positive slice별 1% visibility, finite normalized
  centroid, `showData`의 exact JavaScript string을 요구합니다. Positive slice는 normalized `sector`, zero slice는
  legend-only zero bbox이고 relation/group은 없습니다. 조건 밖의 valid input과 native runtime rejection은 같은
  candidate slot에서 최대 256개의 zero-geometry `TB` exact-value cell로 재검증합니다. Fallback도 relation을
  만들지 않으며 두 terminal 모두 50,000 UTF-16 code-unit·5,000줄 source preflight를 공유합니다.
- Quadrant 구조 metric은 `QuadrantPlan`이 고정한 terminal을 따릅니다. Native는 최대 256 point의
  zero-or-normal binary64 coordinate와 pinned 500×500 renderer의 finite·distinct point/text visibility를
  요구하고, `(x, 1-y)` point circle·네 axis endpoint·네 quadrant group을 평가합니다. Axis line, connector와
  quadrant membership은 source에서 증명되지 않았으므로 만들지 않습니다. Native-lossy input과 runtime
  rejection은 같은 slot의 zero-geometry `TB` title/axis/slot/exact-point cell로 재검증하며 edge/group은
  비웁니다. Pairwise collision/association은 각각 candidate당 100,000회, source는 50,000 UTF-16
  code-unit·5,000줄로 제한하고 초과 시 partial score를 만들지 않습니다.
- Radar 구조 metric은 `RadarPlan`이 확정한 terminal을 따릅니다. Native는 최대 12 series와 zero-or-normal binary64
  round-trip value/bound, positive finite effective span과 finite renderer radius를 요구하고 normalized radial
  point 및 closed marker-less curve relation을 평가합니다. Flowchart는 최대 256 point의 zero-geometry `TB`
  group/cell과 빈 relation list를 사용하므로 radial layout이나 edge를 가장하지 않습니다. Native runtime
  rejection은 같은 candidate slot의 fallback을 한 번 재검증하며 fallback budget을 넘으면 partial Scene 대신
  unavailable입니다. Native provenance gate는 derived point 대신 axis/series를 평가합니다. Flowchart의 실제
  cell은 dimension과 series evidence를 공유하므로 Radar-local owner binding으로 추적하고, 알려진 record evidence가
  없는 cell은 provenance credit을 받지 못합니다. 두 terminal은 reserved-safe ID namespace와
  50,000 UTF-16 code-unit·5,000줄 source preflight를 공유합니다.
- Radar numeric consistency는 dimension별 exact label record와 series별 `label + ordered values` record를
  candidate-authorized OCR/vector evidence에 결합한 뒤 전역 숫자 occurrence exactness를 추가로 요구합니다. 모든
  owner bbox는 source image 안의 양의 면적이고 서로 겹치지 않아야 하며, cited evidence bbox는 owner 안에 완전히
  포함되어야 합니다. Evidence ID와 normalized text+bbox는 owner 사이에서 재사용할 수 없고, 같은 bbox의 uncited
  contradictory text도 cherry-pick할 수 없습니다. Missing typed plan, invalid geometry/authority, 비어 있는 owner
  observation, reference/text/token 또는 100,000회 spatial comparison budget 소진은 metric 전체를 unavailable로
  두며, 결합된 label/value 순서가 다르면 `0.0`입니다. Native와 same-slot Flowchart, semantic repair proposal은
  모두 새 typed IR과 같은 scoped evidence로 이 gate를 다시 계산합니다.
- Radar visible title과 non-derived explicit accessibility title/description은 record-owned observation과 겹치지
  않는 candidate-authorized spatial OCR/vector exact text 또는 reconstruction 초기 입력의 approved exact
  `user_edit`를 별도로 요구합니다. 같은 evidence/normalized text+bbox를 metadata owner 사이에서 재사용하거나,
  engine-emitted edit로 스스로 승인하거나, bounded metadata-to-record/matching comparison을 초과하면 native와
  fallback 모두 review입니다. 구조에서 결정적으로 파생한 기본 accessibility와 experimental notice는 제외합니다.
- Treemap 구조 metric은 공용 preorder plan이 고정한 terminal을 따릅니다. Native는 section/leaf
  identity와 arrow 없는 logical containment, `unknown` direction을 쓰고, Flowchart는 `N1..Nn`,
  `TB`, rectangle, end-arrow를 씁니다. Internal explicit value·binary64/renderer 표시 비호환은
  exact-value Flowchart를 선택하고 native runtime rejection도 같은 candidate slot에서 그 fallback을
  한 번 재검증합니다. Flowchart projection이 500 relation을 넘으면 unavailable이지만 같은
  계층이 native resource 계약을 만족하면 native까지 금지하지 않습니다.
- Venn 구조 metric도 공용 plan이 고정한 terminal을 따릅니다. Native는 positive normal
  binary64-safe area, `200:1` visibility gate, exact-containment 제외, higher-order union별 complete explicit
  pair를 요구하고 누락 area/pair를 합성하지 않습니다. Native Scene은 marker-less logical membership과
  `unknown` direction, fallback은 labeled end-arrow membership과 `LR`을 사용합니다. Runtime native rejection은
  같은 candidate slot의 exact-value Flowchart를 한 번 재검증합니다. Flowchart projection만 500-edge hard
  cap을 적용하며 near-limit render 성능은 별도 runtime timeout에 계속 의존합니다.
- Pie는 slice-local association과 전역 occurrence completeness를 함께 사용합니다. Native Pie, 같은-slot exact-value
  Flowchart, semantic repair 모두 각 typed slice가 candidate publication authority의 `ocr_token` 또는
  `vector_text`를 직접 참조해야 합니다. Slice/evidence bbox는 양의 면적이고 source image 안에 있어야 하며,
  evidence bbox 전체가 해당 non-overlapping slice bbox 안에 들어가야 합니다. Source-wide `ocr_texts`는 label이나
  value를 어떤 slice에도 귀속할 수 없습니다.
- 모든 slice에서 bbox reading order의 cited observation이 punctuation-preserving 전체 label과 허용 separator,
  하나의 value record에 정확히 결합되고 `(label 안의 숫자 + exact value)` numeric multiset도 같아야 합니다.
  이 local 결과와 전체 source/generated numeric occurrence multiset이 모두 exact일 때만 Pie numeric
  consistency는 `1.0`입니다. 정상적으로 결합된 record의 value가 swap되거나 source-wide 숫자가 더 있으면
  `0.0`입니다. Label suffix omission이나 malformed/cited-extra record로 full-record 결합 자체가 성립하지 않으면
  metric은 unavailable입니다. 두 경우 모두 threshold와 관계없이 review입니다. 동일 slice의 같은 normalized
  text+bbox 중복은 한 관측으로 세고 공간적으로 다른 반복은 보존합니다.
- Slice bbox가 겹치거나, broad/shared evidence, 같은 evidence ID 또는 같은 normalized text+bbox의 cross-slice
  claim, 같은 bbox의 상충 text, invalid authority/geometry/image bounds, association work budget 소진이 있으면
  일부 slice 점수를 내지 않고 Pie metric 전체를 unavailable/review로 둡니다. Typed slice slot이 없는 direct
  Pie도 이 결합을 증명할 수 없습니다.
- Packet은 위 전역 숫자 occurrence multiset을 사용하지 않고 field-local association으로 대체합니다.
  Native Packet과 같은 candidate slot의 Flowchart fallback, semantic repair proposal은 모두 동일한
  field plan과 평가 경로를 사용합니다. 각 field가 candidate publication authority 안의 `ocr_token` 또는
  `vector_text` evidence를 명시적으로 참조하고, field/evidence bbox가 양의 면적이며 실제 source image
  안에 있고 evidence bbox 전체가 해당 field bbox 안에 들어갈 때만 label과 `start`/`end`를 결합합니다.
  Source 전체의 `ocr_texts`는 어떤 field에도 숫자나 label을 귀속할 권한이 없습니다.
- 모든 field의 label과 range 숫자가 field-local evidence와 정확히 결합되면 Packet numeric consistency는
  `1.0`입니다. Label은 결합됐지만 range 숫자가 다르거나 관계없는 숫자가 더 있으면 `0.0`으로 두고
  threshold와 관계없이 review로 보냅니다. `start == end`인 single-bit field는 range endpoint 숫자 한 번을
  요구합니다. 동일 field에서 OCR/vector가 같은 normalized text+bbox를 중복 보고하면 한 관측으로 세지만,
  공간적으로 다른 반복 관측은 합치지 않습니다.
- Field bbox가 겹치거나, evidence bbox가 여러 field에 걸치는 broad box이거나, 같은 evidence ID 또는 같은
  위치의 모호한 관측을 여러 field가 주장하거나, candidate authority·bbox·image bounds·association work
  budget을 확인할 수 없으면 일부 field만 평가하지 않고 Packet metric 전체를 unavailable로 두어 review를
  요구합니다. Candidate authority 안의 같은 bbox에 서로 다른 normalized OCR/vector text가 있으면 field가
  유리한 관측 하나만 인용했더라도 상충 관측을 숨길 수 없도록 unavailable로 처리합니다. Packet binding은
  전역 multiset을 대체하고, Pie·XY·Quadrant·Radar binding은 전역 exactness에 추가됩니다. 다른 numeric type
  계산은 바뀌지 않습니다.
- visual entailment precision은 생성된 node를 source node ID, collision-free portable ID alias 또는
  유일한 정규화 label로 정렬한 collision-free evidence coverage proxy입니다. Node 근거로
  인정하는 kind는 `ocr_token`, `vector_text`, `contour`, `vlm_observation`, `user_edit`로
  제한합니다. `source_crop`, `line_segment`, `arrowhead`는 registry에 존재해도 node credit을
  만들지 않습니다. 둘 이상의 generated node가 같은 eligible evidence ID를 직접 참조하거나
  source alignment에서 상속하면 그 ID는 모든 claimant에서 모호한 근거로 취소합니다.
  한 node 내부의 동일 ID 반복은 한 claim으로 세고, 충돌하지 않는 eligible ID가 하나라도
  남으면 그 node는 지지된 것으로 셉니다. Relation/group의 근거 참조는 node claim
  충돌에 포함하지 않아 정상적인 connector·containment 근거 공유를 손상하지 않습니다.
  Source scene 자체를 후보 precision으로 재사용하지 않습니다. model scorer는 후속입니다.
- 구조 edge를 평가할 수 없고 render PNG가 있으면 raster edge IoU를 fallback으로 사용합니다.

path enumeration은 기본 10,000개 completed path와 100,000개 탐색 state/stack에서 중단합니다. Terminal로
이어지지 않는 cyclic dead branch도 state budget을 소비하므로 simple-path 조합 폭발이 완료 path 수를 우회할
수 없습니다. Source 또는 generated graph가 path/state/depth budget을 넘으면 부분 결과로 점수를 만들지 않고
metric 전체를 unavailable로 둡니다.
표시용 total score와 별도로 non-runtime semantic score를 계산합니다. syntax/render는 hard gate와 total
score에는 참여하지만 0인 의미 점수를 게시 가능 등급으로 희석할 수 없습니다. `extended`/`maximal`의
구조 후보는 위 eligible-kind·conservative-revocation 규칙을 적용한 collision-free 생성 node
provenance가 80% 미만이거나 계산 불가능하면 review 대상으로 둡니다.
Packet과 Pie도 이 구조 provenance gate에 포함되며, bit range 또는 slice value가 일치하는 것만으로
unattributed field/slice를 자동 게시하지 않습니다.

`best_effort_validated`와 `strict_validated`에서 여러 parse/render 후보가 있으면 각 후보에 같은
aggregate·semantic threshold와 provenance/numeric hold를 적용한 뒤, publish 가능한 class를 먼저
선택합니다. 같은 class 안에서는 aggregate, OCR recall, generation method, candidate ID 순서를 유지합니다.
따라서 metric availability가 적은 높은 total 후보가 실제 게시 가능한 evidence-rich 대안을 가리고 문서
전체를 review 상태로 내리지 않습니다. 강제 review/sidecar 정책은 이 class 우선순위를 사용하지 않습니다.
Typed/Scene 후보의 numeric hold는 fallback grammar와 무관하게 semantic type을 유지합니다. Direct 후보는
typed semantic contract가 없으므로 prediction/requested type 대신 parse/render validation으로 확인한
emitted/runtime grammar type을 기준으로 결정합니다.

Semantic repair 후보도 초기 후보와 같은 reference text 집합과 평가 함수를 사용합니다. OCR/vector,
provenance, edge, arrow, layout, path, numeric gate를 새 typed IR에서 다시 계산하며 aggregate 엄격 개선과
semantic score 비감소를 동시에 요구합니다. Held aggregate를 repair가 임의로 해제하지 않습니다. 방향
반전과 무라벨 누락 edge proposal은 source relation confidence 0.6, 내장 Geometry engine이 생성한 exact
endpoint/relation ownership, ID 충돌이 없는 bbox/score 0.6 이상의 line/arrow evidence, 동일 source block
attribution을 모두 요구합니다. 이 threshold는 기본 detector의 line 0.6/arrow 0.65 범위를 포함하되 engine
identity와 geometry relation 일치를 별도 gate로 둡니다. VLM이 새로 선언한 connector evidence와 약한 것을
포함한 engine 간 방향 충돌, 상충·병렬·라벨·conditional relation과 decision/gateway/diamond source의
outgoing edge는 자동 topology repair에서 제외됩니다.
Label repair도 trusted Marker OCR/built-in Vector origin, source block, bbox containment, ID collision gate를
통과해야 합니다. Proposal typed IR은 입력과 같은 resource budget을 다시 통과하고 code가 deterministic
재직렬화 결과와 정확히 일치해야 평가 단계로 진입합니다.

Typed/Scene semantic type 또는 direct 후보의 validated emitted/runtime type이
Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn이면 OCR/vector numeric evidence가 하나도 없거나
numeric consistency가 게시 threshold보다 낮을 때 aggregate를 `None`으로 두어 자동 게시하지 않습니다.
Pie는 candidate-authorized slice-local association, 전역 numeric completeness, explicit accessibility
attribution 중 하나라도 unavailable/mismatch이면 설정된 게시 threshold로 우회하지 않고 aggregate를
`None`으로 둡니다.
XY는 typed plan이 지정한 axis·series·explicit point별로 candidate-authorized OCR/vector
observation을 바인딩합니다. 각 source record의 finite in-image bbox 안에 있는 cited text가 axis
label+category/bounds, series kind+ordered values, 또는 point x+y의 허용된 전체 표현과 일치해야 합니다.
Evidence ID 또는 normalized text+bbox observation을 두 record가 공유하거나 category/value/x를 바꾸고 전역
숫자 multiset만 맞추는 후보, invalid/missing bbox, budget 초과는 unavailable/mismatch로 review에 남습니다.
Explicit metadata가 없으면 별도 bbox overlap scan을 실행하지 않고, 있는 경우에도 spatial
evidence×record bbox 검사를 candidate당 100,000회로 제한해 초과 입력을 fail closed로 처리합니다.
Explicit `title`/`acc_title`·`description`/`acc_description`은 data-owned observation·bbox와 분리된 exact
OCR/vector evidence 또는 reconstruction 초기 exact `user_edit`를 요구하며, engine-emitted edit와 direct
Mermaid-only XY는 typed record association을 스스로 만들 수 없습니다. Native가 same-slot Flowchart로 낮아가도
게시 gate의 semantic type은 XY로 유지되어 동일한 규칙을 적용합니다.
Quadrant도 typed plan의 axis/point record마다 complete low/high 또는 label/x/y를 candidate-authorized
OCR/vector와 결합하고 global numeric multiset을 함께 검사합니다. Evidence ID, normalized text+bbox와 source
record를 재사용하거나 axis/point·좌표를 바꾸는 후보는 숫자 multiset이 같아도 mismatch입니다. X axis는
horizontal·아래쪽, y axis는 vertical·왼쪽인 bbox 관계도 만족해야 하므로 entire-record swap과 nonstandard
axis geometry는 review입니다. Supplied
quadrant label은 전체 source canvas의 해당 사분면 안에 있는 독립 exact observation 또는 reconstruction
초기의 exact `user_edit` 중 유효한 source-quadrant bbox가 있는 근거를 요구하며 schema에 없는 slot evidence를
axis/point에서 상속하지 않습니다.
Explicit metadata도 data-owned observation과 분리해 검사하고, direct Mermaid-only Quadrant와 invalid/missing
bbox, engine-emitted edit, 모든 spatial/matching phase가 공유하는 100,000회 budget 초과는 review-only입니다. Same-slot Flowchart에서도
semantic type을 Quadrant로 유지해 이 gate를 우회하지 않습니다. Source quadrant는 detected plot bbox가 아닌
전체 crop midpoint로 판정하므로 inset/off-center plot은 false-review될 수 있으며 향후 axis/vector plot bbox를
도입하기 전에는 자동으로 위치를 보정하지 않습니다.
Explicit title/description/접근성 metadata의 독립 관측은 현재 evidence schema에 immutable target role이 없어
content existence만 증명합니다. 따라서 best-effort 정책은 role-attribution limitation warning을 남기고
experimental candidate로만 게시하며, `strict_validated`는 aggregate를 unavailable로 두어 review를 요구합니다.
Semantic repair가 새 explicit metadata를 제안하면 같은 제한을 다시 계산합니다. Strict 정책에서 제한된
proposal은 점수 개선으로 채택하지 않되, 이미 검증된 이전 candidate의 code·IR·score·게시 가능성은 그대로
보존합니다.
Packet은 candidate-authorized field-local association이 unavailable이거나 `0.0`이면 전역 숫자 multiset이나
설정된 게시 threshold로 우회하지 않고 aggregate를 `None`으로 둡니다.
