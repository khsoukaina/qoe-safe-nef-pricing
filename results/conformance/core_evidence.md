# Core signalling evidence
capture: `results/core_logs/core_signalling.pcap`

## SBI (HTTP/2) requests
| request | count |
|---|---|
| `PATCH nnrf-nfm/v1/nf-instances/{id}` | 36 |
| `nnrf-nfm/v1/nf-instances/{id}` | 34 |
| `POST nsmf-callback/v1/sm-policy-notify/{id}/update` | 6 |
| `POST namf-comm/v1/ue-contexts/{id}` | 4 |
| `GET nnrf-disc/v1/nf-instances` | 2 |
| `GET nbsf-management/v1/pcfBindings` | 1 |
| `POST npcf-policyauthorization/v1/app-sessions` | 1 |
| `PATCH npcf-policyauthorization/v1/app-sessions/{id}` | 1 |
| `POST npcf-policyauthorization/v1/app-sessions/{id}/delete` | 1 |

## PFCP (SMF -> UPF)
| message | count |
|---|---|
| Heartbeat Request | 6 |
| Heartbeat Response | 6 |
| Session Modification Request | 2 |
| Session Modification Response | 2 |

IEs inside Session Modification Requests:
| IE | messages containing it |
|---|---|
| Create PDR | 1 |
| Create QER | 1 |
| Create FAR | 1 |
| Remove PDR | 1 |
| Remove QER | 1 |

## NGAP (AMF <-> gNB)
| procedure | messages |
|---|---|
| PDUSessionResourceModify | 2 |
