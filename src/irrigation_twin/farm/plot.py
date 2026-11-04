"""Daily root-zone water balance for one farm plot (FAO-56 chapter 8).

Root-zone depletion Dr (mm) is the water missing below field capacity
(FAO-56 eq. 85)::

    Dr_i = Dr_{i-1} - P_i - I_i + ETc,adj_i + DP_i

with ETc,adj = Ks * Kc * ET0 (eq. 80). Ks < 1 once Dr exceeds the readily
available water RAW = p * TAW (eq. 84). Capillary rise is taken as zero
because the water table is deep. Runoff is also taken as zero: all rain
infiltrates, and anything that would push the soil above field capacity
drains as deep percolation DP.

A day is split in two to match the twin's simulation loop:

1. :meth:`Plot.start_day` applies rain and crop water use and returns the
   irrigation request.
2. The network decides how much of that request it can deliver.
3. :meth:`Plot.finish_day` applies the delivered irrigation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .crop import Crop, Soil, depletion_fraction, growth_stage, kc_on_day, root_depth_on_day


@dataclass(frozen=True)
class IrrigationPolicy:
    """When to irrigate, and how much to ask for.

    trigger_fraction:
        Irrigate when depletion reaches this fraction of RAW. Values below 1
        irrigate before the crop is stressed.
    max_application_mm:
        Most net water one plot can receive in a day. This is the irrigation
        system's capacity, sized at about 1.4 x peak crop water use (maize
        ETc peaks near 8.5 mm/day here), a common design margin. A larger
        deficit is refilled over several days.
    efficiency:
        Fraction of delivered water that reaches the root zone. Sprinkler
        losses such as wind drift and evaporation take the rest.
    """

    trigger_fraction: float = 0.8
    max_application_mm: float = 12.0
    efficiency: float = 0.8


@dataclass
class DayRecord:
    date: dt.date
    plot: str
    stage: str
    day_after_sowing: int
    et0_mm: float
    rain_mm: float
    kc: float
    etc_mm: float          # potential crop ET (no stress)
    ks: float
    eta_mm: float          # actual crop ET
    root_depth_m: float
    taw_mm: float
    raw_mm: float
    depletion_start_mm: float
    depletion_before_irrigation_mm: float
    request_m3: float = 0.0
    delivered_m3: float = 0.0
    irrigation_net_mm: float = 0.0
    deep_percolation_mm: float = 0.0
    depletion_end_mm: float = 0.0
    soil_moisture: float = 0.0   # volumetric water content at the end of the day (m3/m3)
    stressed: bool = False


@dataclass
class Plot:
    name: str
    junction: str
    area_ha: float
    crop: Crop
    soil: Soil
    sowing_date: dt.date
    policy: IrrigationPolicy = field(default_factory=IrrigationPolicy)
    kc_mid: float | None = None  # climate-adjusted Kc_mid (None: table value)

    depletion_mm: float = 0.0    # pre-sowing irrigation leaves the soil at field capacity
    _today: DayRecord | None = field(default=None, repr=False)

    @property
    def area_m2(self) -> float:
        return self.area_ha * 10_000.0

    def mm_to_m3(self, mm: float) -> float:
        return mm / 1000.0 * self.area_m2

    def m3_to_mm(self, m3: float) -> float:
        return m3 / self.area_m2 * 1000.0

    def day_after_sowing(self, date: dt.date) -> int:
        return (date - self.sowing_date).days

    def in_season(self, date: dt.date) -> bool:
        return 0 <= self.day_after_sowing(date) < self.crop.season_length

    # ------------------------------------------------------------------
    def start_day(self, date: dt.date, et0_mm: float, rain_mm: float) -> float:
        """Apply rain and crop water use for ``date``. Return the irrigation request (m^3)."""
        if self._today is not None:
            raise RuntimeError(f"{self.name}: finish_day() was not called for {self._today.date}")
        das = self.day_after_sowing(date)
        active = self.in_season(date)
        zr = root_depth_on_day(self.crop, das)  # minimum before sowing, maximum after harvest
        taw = self.soil.taw_mm(zr)

        if not active:
            # Fallow (before sowing or after harvest): no crop water use and
            # no irrigation. The soil state is held and these days are left
            # out of the seasonal balance.
            rec = DayRecord(date, self.name, growth_stage(self.crop, das), das, et0_mm, rain_mm,
                            0.0, 0.0, 1.0, 0.0, zr, taw, 0.0, self.depletion_mm, self.depletion_mm)
            self._today = rec
            return 0.0

        kc = kc_on_day(self.crop, das, self.kc_mid)
        etc = kc * et0_mm
        p = depletion_fraction(self.crop, etc)
        raw = p * taw
        dr_prev = min(self.depletion_mm, taw)
        ks = 1.0 if dr_prev <= raw else max((taw - dr_prev) / ((1.0 - p) * taw), 0.0)  # eq. 84
        # The crop cannot extract water past wilting point (Dr = TAW).
        eta = min(ks * etc, max(taw - dr_prev + rain_mm, 0.0))

        dr = dr_prev - rain_mm + eta
        dp = 0.0
        if dr < 0.0:
            dp = -dr
            dr = 0.0

        request_m3 = 0.0
        if dr >= self.policy.trigger_fraction * raw and dr > 0.0:
            net_mm = min(dr, self.policy.max_application_mm)
            request_m3 = self.mm_to_m3(net_mm / self.policy.efficiency)

        self._today = DayRecord(
            date=date, plot=self.name, stage=growth_stage(self.crop, das), day_after_sowing=das,
            et0_mm=et0_mm, rain_mm=rain_mm, kc=kc, etc_mm=etc, ks=ks, eta_mm=eta,
            root_depth_m=zr, taw_mm=taw, raw_mm=raw,
            depletion_start_mm=dr_prev, depletion_before_irrigation_mm=dr,
            request_m3=request_m3, deep_percolation_mm=dp, stressed=ks < 1.0,
        )
        self.depletion_mm = dr
        return request_m3

    def finish_day(self, delivered_m3: float) -> DayRecord:
        """Apply the water the network actually delivered and close the day."""
        rec = self._today
        if rec is None:
            raise RuntimeError(f"{self.name}: start_day() must be called first")
        if delivered_m3 < -1e-9:
            raise ValueError("delivered volume cannot be negative")
        delivered_m3 = max(delivered_m3, 0.0)
        net_mm = self.m3_to_mm(delivered_m3) * self.policy.efficiency
        dr = self.depletion_mm - net_mm
        if dr < 0.0:
            rec.deep_percolation_mm += -dr
            dr = 0.0
        self.depletion_mm = dr

        rec.delivered_m3 = delivered_m3
        rec.irrigation_net_mm = net_mm
        rec.depletion_end_mm = dr
        zr = rec.root_depth_m
        rec.soil_moisture = self.soil.theta_fc - dr / (1000.0 * zr)
        self._today = None
        return rec
