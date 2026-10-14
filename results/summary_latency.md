# Branch predictor results

_Every number below comes from an RTL simulation that matched the golden model bit-exactly._

## Resolve-latency sweep (tournament, 10-bit tables, depth 32)

| GHR | latency | mean acc | sort | bsearch | matmul | interp | hash | strsearch | cfg_large |
|---|---|---|---|---|---|---|---|---|---|
| non-spec | 1 | 88.84% | 94.5 | 81.3 | 92.5 | 97.8 | 86.4 | 90.9 | 78.5 |
| non-spec | 2 | 88.17% | 94.4 | 80.8 | 92.6 | 93.6 | 86.5 | 90.9 | 78.3 |
| non-spec | 4 | 88.96% | 94.4 | 81.3 | 92.0 | 99.8 | 86.4 | 90.7 | 78.0 |
| non-spec | 8 | 88.42% | 94.4 | 81.2 | 97.0 | 91.5 | 86.4 | 90.6 | 78.0 |
| non-spec | 16 | 86.49% | 94.4 | 81.7 | 91.1 | 83.3 | 86.4 | 90.6 | 78.0 |
| spec | 1 | 88.82% | 94.5 | 80.8 | 92.2 | 97.8 | 86.7 | 91.0 | 78.7 |
| spec | 2 | 88.81% | 94.4 | 80.8 | 92.2 | 97.8 | 86.7 | 91.0 | 78.7 |
| spec | 4 | 88.81% | 94.5 | 80.8 | 92.2 | 97.8 | 86.7 | 91.0 | 78.7 |
| spec | 8 | 88.81% | 94.5 | 80.9 | 92.2 | 97.8 | 86.7 | 91.0 | 78.7 |
| spec | 16 | 88.81% | 94.5 | 80.8 | 92.2 | 97.8 | 86.7 | 91.0 | 78.7 |

