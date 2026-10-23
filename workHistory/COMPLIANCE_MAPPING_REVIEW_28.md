# 28개 매핑 원문 대조 결과

AI 검토 결과이며 사람 확인 상태로 변경하지 않았습니다.

|유형|기존 조항|결정|이유|
|---|---|---|---|
|INFO_EXPOSURE|FSC-AI-GL:7-CHK-08|hold|현재 규칙은 일반 스택트레이스 노출도 탐지하므로 AI 모델 정보 노출이라고 가정할 수 없음.|
|JAILBREAK|FSC-AI-GL:7-CHK-09|keep|문제 유형과 원문 통제의 기술적 관련성을 확인. 적용 조건과 통제 효과는 별도 검토 필요.|
|TOOL_NO_APPROVAL|KR-AIBA:34:EVIDENCE-HUMAN-OVERSIGHT|keep|사람 승인 누락은 감독 체계 검토의 단서이며, 특정 승인 절차의 법정 의무나 감독 체계 전체의 이행 여부를 확정하지 않습니다. 코드 Findings 및 보고서 생성만으로 조직 의무 이행을 확인하지 않습니다.|
|TOOL_EXCESSIVE_SCOPE|FSC-AI-GL:7-CHK-24|keep|문제 유형과 원문 통제의 기술적 관련성을 확인. 적용 조건과 통제 효과는 별도 검토 필요.|
|INDIRECT_INJECTION|FSC-AI-GL:7-CHK-09|keep|문제 유형과 원문 통제의 기술적 관련성을 확인. 적용 조건과 통제 효과는 별도 검토 필요.|
|PROMPT_NO_INSTRUCTION_HIERARCHY|FSC-AI-GL:7-CHK-09|keep|지시 계층 부재는 인젝션 방어 검토의 단서이며, 특정 프롬프트 문구 유무만으로 방어 전체의 이행을 판정하지 않습니다.|
|TOOL_NO_APPROVAL|FSC-AI-SEC:PAGE-057-01:EVIDENCE-TECH-5|revise|고객 영향도가 큰 금융 의사결정에 한정된 통제임을 명시하며 모든 도구 호출에 승인을 요구하지 않음.|
|SYSTEM_PROMPT_LEAK|FSC-AI-GL:7-CHK-13|revise|정기 모의훈련·운영 검증은 조직 운영 증적이 필요하며 이번 단발 감사로 이행 여부를 확인할 수 없음.|
|TOOL_MISUSE|FSC-AI-GL:7-CHK-24|hold|도구 오남용이 항상 권한 과다를 의미하지 않아 최소 권한 항목과의 일괄 연결을 보류함.|
|TOOL_MISUSE|FSC-AI-SEC:PAGE-052-01:EVIDENCE-TECH-4|revise|사전 도입 승인 문구 대신 연결된 외부 도구의 오남용·의도하지 않은 행동에 대한 위험 완화 문구로 교체.|
|HARDCODED_SECRET|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-1|hold|비밀 변수의 고엔트로피 문자열만으로 API 키인지 AI 시스템 자격증명인지 확인할 수 없음.|
|TOOL_EXCESSIVE_SCOPE|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-3|revise|개발자·운영자 역할 권한 문구 대신 외부 AI 서비스 연결 범위·권한 제한 문구로 교체.|
|TOOL_NO_APPROVAL|KR-AIBA-HIGH-GL:PAGE-055-01:EVIDENCE-HUMAN-INTERVENTION|keep|고영향 AI에서 사람의 개입을 지원하는 제어 절차의 구현 예에 해당합니다. 모든 도구 호출에 같은 승인 방식을 요구한다는 의미는 아닙니다. 고영향 AI 적용 여부와 개입이 필요한 상황인지 확인해야 합니다.|
|DATA_EXFILTRATION|FSC-AI-GL:7-CHK-07|revise|대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완.|
|INDIRECT_INJECTION_SURFACE|FSC-AI-GL:7-CHK-05|revise|대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완.|
|INDIRECT_INJECTION|FSC-AI-GL:7-CHK-05|revise|대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완.|
|SYSTEM_PROMPT_LEAK|FSC-AI-GL:7-CHK-08|revise|대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완.|
|audit_execution|FSC-AI-GL:7-CHK-29|revise|정기 모의훈련·운영 검증은 조직 운영 증적이 필요하며 이번 단발 감사로 이행 여부를 확인할 수 없음.|
|PROMPT_SECRET_EMBEDDED|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-1|hold|프롬프트 안의 임의 기밀 문자열이 API 키라는 전제가 확인되지 않음.|
|PRIVILEGE_ESCALATION|FSC-AI-GL:7-CHK-24|keep|AI 에이전트의 권한 상승 시나리오와 최소 권한 통제의 관련성을 검토하는 후보. 일반 웹 권한 문제로 확대 적용하지 않습니다.|
|report_document|KR-AIBA-DECREE:27:EVIDENCE-RETENTION|keep|보고서 작성은 근거 보관 체계 검토의 시작점이며, 5년 보관과 전체 조치 이행은 별도 조직 증적이 필요합니다. 코드 Findings 및 보고서 생성만으로 조직 의무 이행을 확인하지 않습니다.|
|AGENT_SHARED_SECRET|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-2|hold|코드와 에이전트에서 같은 환경변수 이름을 쓴다는 사실만으로 서비스별 키 분리 위반을 확정할 수 없음.|
|TOOL_EXFIL_CHANNEL|FSC-AI-SEC:PAGE-052-01:EVIDENCE-TECH-4|hold|도구의 사전 도입 승인과 수신자 도메인 제한은 다른 통제이므로 직접 연결을 보류함.|
|JAILBREAK|FSC-AI-GL:7-CHK-06|revise|대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완.|
|report_document|KR-AIBA:34:EVIDENCE-DOCUMENTATION|keep|보고서는 증적 자료의 일부가 될 수 있으나 전체 조치 이행과 보관 의무를 입증하지 않습니다. 코드 Findings 및 보고서 생성만으로 조직 의무 이행을 확인하지 않습니다.|
|probe_execution|FSC-AI-GL:7-CHK-10|revise|정기 모의훈련·운영 검증은 조직 운영 증적이 필요하며 이번 단발 감사로 이행 여부를 확인할 수 없음.|
|HARDCODED_CREDENTIAL|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-1|hold|현재 규칙은 일반 DB 비밀번호도 탐지하므로 AI API 키 전용 문구와 일괄 연결하지 않음.|
|SECRET_LEAK|FSC-AI-SEC:PAGE-040-01:EVIDENCE-TECH-1|hold|현재 규칙은 AWS 키·JWT·개인키도 탐지하므로 AI API 키 보관 항목과 일괄 연결할 수 없음.|
