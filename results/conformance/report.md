# API conformance report

Specifications: 3GPP Rel-18 OpenAPI (jdegre/5GC_APIs, commit `117674b61c`)  
Messages: 15 northbound, 12 southbound

| interface | operation | direction | pass | fail |
|---|---|---|---|---|
| northbound TS29122 | `DELETE /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | AF request accepted by NEF | 1 | 0 |
| northbound TS29122 | `DELETE /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | NEF response | 2 | 0 |
| northbound TS29122 | `DELETE /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | rejected request (404): valid per OpenAPI (rejected by NEF semantics) | 1 | 0 |
| northbound TS29122 | `GET /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | AF request accepted by NEF | 1 | 0 |
| northbound TS29122 | `GET /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | NEF response | 1 | 0 |
| northbound TS29122 | `GET /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | AF request accepted by NEF | 1 | 0 |
| northbound TS29122 | `GET /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | NEF response | 2 | 0 |
| northbound TS29122 | `GET /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | rejected request (404): valid per OpenAPI (rejected by NEF semantics) | 1 | 0 |
| northbound TS29122 | `PATCH /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | AF request accepted by NEF | 1 | 0 |
| northbound TS29122 | `PATCH /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{id}` | NEF response | 1 | 0 |
| northbound TS29122 | `POST /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | AF request accepted by NEF | 4 | 0 |
| northbound TS29122 | `POST /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | NEF response | 9 | 0 |
| northbound TS29122 | `POST /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | rejected request (400): invalid per OpenAPI | 2 | 0 |
| northbound TS29122 | `POST /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions` | rejected request (400): valid per OpenAPI (rejected by NEF semantics) | 3 | 0 |
| southbound TS29510 | `GET /nnrf-disc/v1/nf-instances` | core response (Open5GS, informative) | 2 | 0 |
| southbound TS29510 | `GET /nnrf-disc/v1/nf-instances` | request sent by NEF | 2 | 0 |
| southbound TS29514 | `PATCH /npcf-policyauthorization/v1/app-sessions/{id}` | core response (Open5GS, informative) | 0 | 1 |
| southbound TS29514 | `PATCH /npcf-policyauthorization/v1/app-sessions/{id}` | request sent by NEF | 1 | 0 |
| southbound TS29514 | `POST /npcf-policyauthorization/v1/app-sessions` | core response (Open5GS, informative) | 4 | 0 |
| southbound TS29514 | `POST /npcf-policyauthorization/v1/app-sessions` | request sent by NEF | 4 | 0 |
| southbound TS29514 | `POST /npcf-policyauthorization/v1/app-sessions/{id}/delete` | core response (Open5GS, informative) | 1 | 0 |
| southbound TS29514 | `POST /npcf-policyauthorization/v1/app-sessions/{id}/delete` | request sent by NEF | 1 | 0 |
| southbound TS29521 | `GET /nbsf-management/v1/pcfBindings` | core response (Open5GS, informative) | 4 | 0 |
| southbound TS29521 | `GET /nbsf-management/v1/pcfBindings` | request sent by NEF | 4 | 0 |

**southbound TS29514 | PATCH /npcf-policyauthorization/v1/app-sessions/{id} | core response (Open5GS, informative)**
- `InvalidData: InvalidData: Value {'ascReqData': {'medComponents': {'1': {'marBwDl': '3 Mbps', 'marBwUl': '200 Kbps', 'medCompN': 1, 'medType': 'VIDEO', 'mirBwDl': '3 Mbps', 'mirBwUl': '200 Kbps'}}}} not valid for schema of type object: (<ValidationError: "'notifUri' is a required property">, <ValidationError: "'suppFeat' is a required property">, <ValidationError: "{'medComponents': {'1': {'marBwDl': '3 Mbps', 'marBwUl': '200 Kbps', 'medCompN': 1, 'medType': 'VIDEO', 'mirBwDl': '3 Mbps', 'mirBwUl': '200 Kbps'}}} is not valid under any of the given schemas">) | 'notifUri' is a required property;`

**NEF-side failures (requests sent + responses produced by our NEF): 0**
