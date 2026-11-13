# PJe-JT (Justica do Trabalho) coverage

Validated with an A1 certificate + TOTP through the national SSO (`flavor="kz"`, same flow as TRT7/TRT18). Read-only: login, counters, Meus Expedientes, process read and document read (documents of an expediente without ciencia stay behind the `PendingNotice` guard).

| Portal | Validated | Reads that work | Notes |
| --- | --- | --- | --- |
| trt12-1g | 2026-11-13 | counters (Acervo Geral/Meus Expedientes/Arquivados), Meus Expedientes (10 caixas), process read (timeline + 36 expedientes), document read (PDF + text) | 207 intimacoes/30d |
| trt12-2g | 2026-11-13 | counters, Meus Expedientes, process read, document read (PDF + text) | |
| trt15-1g | 2026-11-13 | counters (36/4/5), Meus Expedientes (4 caixas), process read (5 expedientes completos), document read (PDF + text) | 30 intimacoes/30d |
| trt15-2g | 2026-11-13 | counters (2/1/2), Meus Expedientes, process read, document read (PDF + text) | |
