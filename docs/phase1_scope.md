# Phase 1: Scope note

## Question

How does a fault in the farm's water network (a pipe leak or a pump failure) change soil moisture and crop water stress on each plot, and which irrigation schedule limits the damage?

## Study area, crop and season

| Item | Choice | Why |
|---|---|---|
| Location | Anantapur, Andhra Pradesh, India (14.68° N, 77.60° E) | One of India's driest districts (about 550 mm of rain a year, mostly June to October), so dry-season farming depends entirely on irrigation |
| Season | Rabi 2023-24: sowing 1-22 Nov 2023, last harvest 19 Apr 2024 | The dry season: almost no rain, rising evaporative demand. Irrigation, and therefore the network, decides the outcome |
| Crop | Maize (grain) | FAO-56 has a stage-length set for exactly this case, "India (dry, cool), October sowing": 30/40/50/30 days. Maize is sensitive to water stress (Ky = 1.25), so faults show up in yield |
| Farm | Four 0.5 ha plots (2 ha) | Fits the workflow's 3-5 plots. Sowing is staggered a week apart (common practice), and soils vary |
| Water source | A well/sump with one pump, an elevated tank and buried PVC pipes | The Phase 2 network |

| Plot | Junction | Soil (FAO-56 Table 19, mid-range) | Sown |
|---|---|---|---|
| A | J3 | Sandy loam (θFC 0.23, θWP 0.11) | 1 Nov 2023 |
| B | J4 | Sandy loam | 8 Nov 2023 |
| C | J6 | Loam (θFC 0.25, θWP 0.12) | 15 Nov 2023 |
| D | J7 | Loamy sand (θFC 0.15, θWP 0.065) | 22 Nov 2023 |

## Methods

- **Network:** WNTR (EPANET engine), pressure-driven demand, 1-minute hydraulic steps. See [phase2_water_network.md](phase2_water_network.md).
- **Reference ET:** FAO-56 Penman-Monteith (eq. 6) from daily Tmax, Tmin, RHmax, RHmin, wind and solar radiation.
- **Crop water use:** single crop coefficient, ETc = Kc × ET0 (FAO-56 Tables 11-12). Kc_mid is adjusted for the local climate (eq. 62).
- **Soil water:** FAO-56 chapter 8 root-zone depletion balance, with stress coefficient Ks (eq. 84) and p adjusted for ETc.
- **Yield effect:** FAO-33, 1 − Ya/Ym = Ky (1 − ΣETa/ΣETc).
- **Weather:** Open-Meteo historical archive, downloaded once and stored as CSV.

## Deliverables by phase

| Phase | Output |
|---|---|
| 2 | Network model, baseline pressures and flows, leak injection |
| 3 | Farm model, soil-moisture curves for each plot without the network |
| 4 | Coupled daily loop; one run where a leak visibly lowers a plot's soil moisture |
| 5 | Baseline, leak and pump-failure (plus drought) comparison table |
| 6 | Streamlit dashboard |
| 7 | Validation and final report |
| Stretch | Leak detection, schedule optimiser, drought and water-saving metrics |

## Main simplifications (all repeated in the report)

- Rain fully infiltrates (no runoff). Capillary rise is zero because the water table is deep.
- The irrigation system's efficiency is 80%; the remaining 20% is lost to drift and evaporation.
- The soil starts at field capacity on each sowing date (pre-sowing irrigation).
- Roots grow linearly from 0.3 m at sowing to 1.0 m at the end of the development stage.
- Leak size, location and timing are scenario assumptions, not observations.
