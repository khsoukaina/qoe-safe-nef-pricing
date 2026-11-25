Measured overload frequency (%) vs target ε — mean ± 95 % CI. Surge: first 2n days after the shift.

| scenario | ε | Conformal quantile (ours) | Adaptive conformal (ours) | Gaussian quantile | Mean-demand price | Rule-based tiers (reactive) |
|---|---|---|---|---|---|---|
| Normal demand | 1% | 1.03 ± 0.31 | 1.10 ± 0.32 | 1.18 ± 0.33 | 50.68 ± 1.55 | 45.07 ± 1.54 |
| Normal demand | 5% | 4.55 ± 0.65 | 4.95 ± 0.67 | 5.25 ± 0.69 | 50.20 ± 1.55 | 45.95 ± 1.54 |
| Normal demand | 10% | 9.45 ± 0.91 | 9.93 ± 0.93 | 9.55 ± 0.91 | 50.00 ± 1.55 | 44.88 ± 1.54 |
| Normal demand | 20% | 19.68 ± 1.23 | 19.93 ± 1.24 | 20.28 ± 1.25 | 49.93 ± 1.55 | 45.67 ± 1.54 |
| Heavy tail (Student-t, 3 dof) | 1% | 0.88 ± 0.29 | 0.97 ± 0.30 | 1.93 ± 0.43 | 49.65 ± 1.55 | 46.50 ± 1.55 |
| Heavy tail (Student-t, 3 dof) | 5% | 5.05 ± 0.68 | 4.95 ± 0.67 | 4.38 ± 0.63 | 50.70 ± 1.55 | 46.83 ± 1.55 |
| Heavy tail (Student-t, 3 dof) | 10% | 9.68 ± 0.92 | 9.98 ± 0.93 | 7.72 ± 0.83 | 48.33 ± 1.55 | 46.85 ± 1.55 |
| Heavy tail (Student-t, 3 dof) | 20% | 19.62 ± 1.23 | 19.90 ± 1.24 | 14.90 ± 1.10 | 51.30 ± 1.55 | 47.33 ± 1.55 |
| Skewed (log-normal) | 1% | 1.05 ± 0.32 | 1.05 ± 0.32 | 1.95 ± 0.43 | 47.30 ± 1.55 | 45.27 ± 1.54 |
| Skewed (log-normal) | 5% | 5.00 ± 0.68 | 4.95 ± 0.67 | 6.45 ± 0.76 | 48.10 ± 1.55 | 44.45 ± 1.54 |
| Skewed (log-normal) | 10% | 9.98 ± 0.93 | 9.95 ± 0.93 | 11.20 ± 0.98 | 48.40 ± 1.55 | 44.42 ± 1.54 |
| Skewed (log-normal) | 20% | 20.00 ± 1.24 | 19.98 ± 1.24 | 20.18 ± 1.24 | 47.75 ± 1.55 | 44.80 ± 1.54 |
| Unforecast surge (+30 %) | 1% | 2.00 ± 1.94 | 2.00 ± 1.94 | 4.50 ± 2.87 | 67.50 ± 6.49 | 89.00 ± 4.34 |
| Unforecast surge (+30 %) | 5% | 11.67 ± 5.74 | 7.50 ± 4.71 | 15.83 ± 6.53 | 68.33 ± 8.32 | 90.00 ± 5.37 |
| Unforecast surge (+30 %) | 10% | 19.17 ± 7.04 | 16.67 ± 6.67 | 21.67 ± 7.37 | 66.67 ± 8.43 | 82.50 ± 6.80 |
| Unforecast surge (+30 %) | 20% | 32.50 ± 8.38 | 30.00 ± 8.20 | 36.67 ± 8.62 | 66.67 ± 8.43 | 91.67 ± 4.95 |

Cost of safety at ε = 5 % (peak hour, random admission)

| scenario | rule | profit | rejected QoD (%) | best-effort MOS loss (1e-3) | Δ mean MOS |
|---|---|---|---|---|---|
| normal | Conformal quantile (ours) | 2.723 | 0.95 | 2.02 | 0.3030 |
| normal | Adaptive conformal (ours) | 2.694 | 1.06 | 2.10 | 0.3008 |
| normal | Gaussian quantile | 2.978 | 1.00 | 2.27 | 0.3035 |
| normal | Mean-demand price | 0.047 | 12.91 | 16.83 | 0.2733 |
| normal | Rule-based tiers (reactive) | 0.765 | 10.33 | 13.30 | 0.2816 |
| heavy | Conformal quantile (ours) | 2.742 | 1.76 | 2.35 | 0.3045 |
| heavy | Adaptive conformal (ours) | 2.589 | 1.72 | 2.23 | 0.2977 |
| heavy | Gaussian quantile | 2.888 | 1.83 | 2.53 | 0.3021 |
| heavy | Mean-demand price | 0.011 | 9.09 | 11.96 | 0.2974 |
| heavy | Rule-based tiers (reactive) | 0.505 | 7.76 | 9.85 | 0.3006 |
| skewed | Conformal quantile (ours) | 2.814 | 2.32 | 2.27 | 0.3057 |
| skewed | Adaptive conformal (ours) | 2.688 | 2.46 | 2.27 | 0.3000 |
| skewed | Gaussian quantile | 3.277 | 3.09 | 3.11 | 0.3061 |
| skewed | Mean-demand price | 0.012 | 16.06 | 18.63 | 0.2683 |
| skewed | Rule-based tiers (reactive) | 0.692 | 14.12 | 15.85 | 0.2734 |
| surge | Conformal quantile (ours) | 0.051 | 3.00 | 0.03 | 0.0123 |
| surge | Adaptive conformal (ours) | 0.000 | 1.00 | 0.00 | 0.0000 |
| surge | Gaussian quantile | 0.396 | 5.61 | 0.30 | 0.0261 |
| surge | Mean-demand price | 0.727 | 47.74 | 7.11 | 0.0926 |
| surge | Rule-based tiers (reactive) | 0.892 | 70.14 | 10.64 | 0.1168 |
