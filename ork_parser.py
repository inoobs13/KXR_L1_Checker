#!/usr/bin/env python3
"""
ork_parser.py - Read OpenRocket (.ork) design files into Python objects.

Quick start
-----------
    from ork_parser import Rocket

    rocket = Rocket("my_rocket.ork")

    rocket.bodyTube.get_param("Length")            # 0.3   (metres, first body tube)
    rocket.bodyTube.get_param("Length", "in")      # 11.81
    rocket.noseCone.get_param("Shape")             # 'ogive'
    rocket.finSet.get_param("Root chord", "mm")    # 50.8
    rocket.bodyTube.material                       # Material('Cardboard', bulk, 680 kg/m^3)
    rocket.bodyTube.position                       # distance of its front edge from the nose tip (m)

    rocket.bodyTubes                               # every body tube, in order (list)
    rocket.get("Payload bay")                      # a component by its name in OpenRocket
    rocket["Payload bay"]                          # same thing
    rocket.sustainer.bodyTube                      # lookups also work on any sub-assembly

    rocket.print_tree()                            # overview of the whole design
    rocket.parts_table()                           # list of dicts (drop straight into pandas)

Conventions
-----------
* Every value is SI, exactly as OpenRocket stores it: metres, kilograms, kg/m^3.
  Pass a unit to get_param() to convert lengths (m, cm, mm, in, ft) or masses (kg, g, lb, oz).
  Angles are left as stored in the file (OpenRocket writes component angles in degrees).
* Parameter names are matched loosely: "Root chord", "root_chord" and "rootchord" are the same.
  Any XML tag on the component can be read this way, plus derived values such as
  "Outer diameter", "Inner diameter", "Position", "Bottom", "Mass", "CG", "Fin area".
* Positions are measured along the rocket axis from the nose-cone tip (x = 0) toward the tail,
  and refer to the forward (top) edge of the part unless stated otherwise.
* "auto" dimensions (e.g. a body tube radius that follows the nose cone) are resolved.
* Mass and CG are *estimates* computed from geometry and material density the way OpenRocket
  does it (overrides in the file are honoured). Motors are not included in mass/CG.

Run it from the command line for a printed overview:
    python ork_parser.py my_rocket.ork
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterator, List, Optional

__all__ = ["Rocket", "Component", "Material", "Motor", "Simulation", "load"]

# --------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------
LENGTH_UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "inch": 0.0254,
                "inches": 0.0254, "ft": 0.3048, "feet": 0.3048}
MASS_UNITS = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "lbs": 0.45359237, "oz": 0.028349523125}

# Parameters that hold a length (used to sanity-check unit conversions).
_LENGTH_HINTS = ("length", "radius", "diameter", "thickness", "chord", "height", "span",
                 "position", "bottom", "offset", "separation", "width", "cg", "overhang",
                 "top", "x", "packed")
_MASS_HINTS = ("mass",)


def _norm(name: str) -> str:
    """'Root Chord' / 'root_chord' / 'rootChord' -> 'rootchord'."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _convert(value, unit: Optional[str], key: str):
    if unit is None or value is None or isinstance(value, (bool, str)):
        return value
    u = unit.strip().lower()
    if u in LENGTH_UNITS:
        if any(h in key for h in _MASS_HINTS):
            raise ValueError(f"'{key}' is a mass; '{unit}' is a length unit")
        factor = LENGTH_UNITS[u]
    elif u in MASS_UNITS:
        if not any(h in key for h in _MASS_HINTS):
            raise ValueError(f"'{key}' is not a mass; '{unit}' is a mass unit")
        factor = MASS_UNITS[u]
    else:
        raise ValueError(f"Unknown unit '{unit}'. Lengths: {sorted(LENGTH_UNITS)}; "
                         f"masses: {sorted(MASS_UNITS)}")
    if isinstance(value, (list, tuple)):
        return type(value)(v / factor for v in value)
    return value / factor


def _parse_scalar(text: Optional[str]):
    """Convert XML text to bool / float / int / str. 'auto' values are handled separately."""
    if text is None:
        return None
    t = text.strip()
    if t == "":
        return None
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        if re.fullmatch(r"[-+]?\d+", t):
            return int(t)
        return float(t)
    except ValueError:
        return t


# --------------------------------------------------------------------------------------
# Small data classes
# --------------------------------------------------------------------------------------
class Material:
    """A material as stored in the design.

    type is 'bulk' (density in kg/m^3), 'surface' (kg/m^2) or 'line' (kg/m).
    """
    _UNITS = {"bulk": "kg/m^3", "surface": "kg/m^2", "line": "kg/m"}

    def __init__(self, name: str, type: str, density: float, group: Optional[str] = None):
        self.name = name
        self.type = type
        self.density = density
        self.group = group

    @property
    def density_unit(self) -> str:
        return self._UNITS.get(self.type, "?")

    @classmethod
    def from_xml(cls, el: ET.Element) -> "Material":
        return cls(name=(el.text or "").strip(), type=el.get("type", "bulk"),
                   density=float(el.get("density", "0") or 0), group=el.get("group"))

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "density": self.density,
                "density_unit": self.density_unit, "group": self.group}

    def __repr__(self):
        return f"Material({self.name!r}, {self.type}, {self.density:g} {self.density_unit})"


class Motor:
    """A motor assigned to a motor mount for one flight configuration."""

    def __init__(self, el: ET.Element, mount: "Component"):
        self.mount = mount
        self.config_id = el.get("configid")
        self.params: Dict[str, Any] = {}
        for ch in el:
            self.params[_norm(ch.tag)] = _parse_scalar(ch.text)
        self.designation = self.params.get("designation")
        self.manufacturer = self.params.get("manufacturer")
        self.diameter = self.params.get("diameter")
        self.length = self.params.get("length")
        self.delay = self.params.get("delay")

    @property
    def position(self) -> Optional[float]:
        """Distance from the nose tip to the motor's forward end (m)."""
        if self.length is None or self.mount.bottom is None:
            return None
        overhang = self.mount.motor_mount.get("overhang", 0.0) or 0.0
        return self.mount.bottom + overhang - self.length

    def get_param(self, name: str, unit: Optional[str] = None):
        key = _norm(name)
        if key == "position":
            return _convert(self.position, unit, key)
        if key not in self.params:
            raise KeyError(f"Motor has no parameter '{name}'. Available: {sorted(self.params)}")
        return _convert(self.params[key], unit, key)

    def to_dict(self) -> dict:
        d = dict(self.params)
        d.update(config_id=self.config_id, mount=self.mount.name, position=self.position)
        return d

    def __repr__(self):
        return f"Motor({self.manufacturer} {self.designation}, mount={self.mount.name!r})"


class Simulation:
    """Name, launch conditions and stored summary results of one OpenRocket simulation."""

    def __init__(self, el: ET.Element):
        self.name = (el.findtext("name") or "").strip()
        self.status = el.get("status")
        cond = el.find("conditions")
        self.conditions: Dict[str, Any] = {}
        if cond is not None:
            for ch in cond:
                if len(ch) == 0 and ch.text and ch.text.strip():
                    self.conditions[_norm(ch.tag)] = _parse_scalar(ch.text)
        self.config_id = self.conditions.get("configid")
        fd = el.find("flightdata")
        self.results: Dict[str, Any] = {}
        self._branches: List[ET.Element] = []
        if fd is not None:
            self.results = {k: _parse_scalar(v) for k, v in fd.attrib.items()}
            self._branches = fd.findall("databranch")

    def data(self, branch: int = 0) -> Dict[str, List[float]]:
        """Stored time series (if the file was saved with simulation data), as {column: values}."""
        if not self._branches:
            return {}
        b = self._branches[branch]
        cols = [c.strip() for c in b.get("types", "").split(",")]
        out: Dict[str, List[float]] = {c: [] for c in cols}
        for dp in b.findall("datapoint"):
            for c, v in zip(cols, (dp.text or "").split(",")):
                try:
                    out[c].append(float(v))
                except ValueError:
                    out[c].append(float("nan"))
        return out

    def __repr__(self):
        alt = self.results.get("maxaltitude")
        return f"Simulation({self.name!r}, max altitude={alt} m)"


# --------------------------------------------------------------------------------------
# Nose cone / transition profile math (same formulas OpenRocket uses)
# --------------------------------------------------------------------------------------
def _shape_radius(shape: str, x: float, radius: float, length: float, param: float) -> float:
    if length <= 0:
        return radius
    x = min(max(x, 0.0), length)
    shape = (shape or "conical").lower()
    if shape == "ogive":
        if length < radius:
            x = x * radius / length
            length = radius
        if param < 1e-6:
            return radius * x / length
        R = math.sqrt(max((length ** 2 + radius ** 2) *
                          (((2 - param) * length) ** 2 + (param * radius) ** 2) /
                          (4 * (param * radius) ** 2), 0))
        L = length / param
        y0 = math.sqrt(max(R * R - L * L, 0))
        return math.sqrt(max(R * R - (L - x) ** 2, 0)) - y0
    if shape == "ellipsoid":
        x = x * radius / length
        return math.sqrt(max(2 * radius * x - x * x, 0))
    if shape == "power":
        if param <= 1e-5:
            return 0.0 if x <= 1e-5 else radius
        return radius * (x / length) ** param
    if shape == "parabolic":
        return radius * ((2 * x / length - param * (x / length) ** 2) / (2 - param))
    if shape == "haack":
        theta = math.acos(max(-1.0, min(1.0, 1 - 2 * x / length)))
        return radius * math.sqrt(max((theta - math.sin(2 * theta) / 2 +
                                       param * math.sin(theta) ** 3) / math.pi, 0))
    return radius * x / length  # conical


_CLIPPABLE = {"ogive", "ellipsoid", "power", "haack"}


def _transition_radius_fn(shape, r1, r2, length, param, clipped):
    """Returns r(x) for 0 <= x <= length (x measured from the fore end)."""
    if abs(r1 - r2) < 1e-12:
        return lambda x: r1
    flipped = r1 > r2
    lo, hi = (r2, r1) if flipped else (r1, r2)
    clip_len = 0.0
    if clipped and shape in _CLIPPABLE and lo > 0 and length > 0:
        mn, mx = 0.0, length
        n = 0
        while _shape_radius(shape, mx, hi, mx + length, param) - lo < 0 and n <= 10:
            mn, mx, n = mx, mx * 2, n + 1
        while mx - mn > 1e-7:
            clip_len = (mn + mx) / 2
            if _shape_radius(shape, clip_len, hi, clip_len + length, param) - lo > 0:
                mx = clip_len
            else:
                mn = clip_len
        clip_len = (mn + mx) / 2

    def r(x):
        if flipped:
            x = length - x
        if clipped and shape in _CLIPPABLE and lo > 0:
            return _shape_radius(shape, clip_len + x, hi, clip_len + length, param)
        return lo + _shape_radius(shape, x, hi - lo, length, param)

    return r


def _frustum(l, r1, r2):
    """(volume*3/pi, cg) of a solid frustum of length l."""
    v = l * (r1 * r1 + r1 * r2 + r2 * r2)
    if v <= 0:
        return 0.0, l / 2
    cg = l * (r1 * r1 + 2 * r1 * r2 + 3 * r2 * r2) / (4 * (r1 * r1 + r1 * r2 + r2 * r2))
    return v, cg


def _polygon_area_centroid(pts):
    """Area and x-centroid of a closed polygon given as [(x, y), ...]."""
    a = cx = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
    a /= 2.0
    if abs(a) < 1e-15:
        return 0.0, 0.0
    return abs(a), cx / (6.0 * a)


# --------------------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------------------
# Friendly names -> OpenRocket XML tags. Used for rocket.<name> lookups.
TYPE_ALIASES: Dict[str, List[str]] = {
    "nosecone": ["nosecone"],
    "bodytube": ["bodytube"],
    "transition": ["transition"],
    "finset": ["trapezoidfinset", "ellipticalfinset", "freeformfinset", "tubefinset"],
    "fins": ["trapezoidfinset", "ellipticalfinset", "freeformfinset", "tubefinset"],
    "fin": ["trapezoidfinset", "ellipticalfinset", "freeformfinset", "tubefinset"],
    "trapezoidfinset": ["trapezoidfinset"],
    "ellipticalfinset": ["ellipticalfinset"],
    "freeformfinset": ["freeformfinset"],
    "tubefinset": ["tubefinset"],
    "innertube": ["innertube"],
    "motortube": ["innertube"],
    "tubecoupler": ["tubecoupler"],
    "coupler": ["tubecoupler"],
    "centeringring": ["centeringring"],
    "bulkhead": ["bulkhead"],
    "engineblock": ["engineblock"],
    "launchlug": ["launchlug"],
    "railbutton": ["railbutton"],
    "parachute": ["parachute"],
    "chute": ["parachute"],
    "streamer": ["streamer"],
    "shockcord": ["shockcord"],
    "masscomponent": ["masscomponent"],
    "mass": ["masscomponent"],
    "stage": ["stage"],
    "podset": ["podset"],
    "pod": ["podset"],
    "parallelstage": ["parallelstage"],
    "booster": ["parallelstage"],
}
_PLURALS = {k + "s": k for k in TYPE_ALIASES}
_PLURALS.update({"finsets": "finset", "fins": "finset", "pods": "podset", "masses": "masscomponent",
                 "boosters": "parallelstage", "chutes": "parachute"})

_EXTERNAL_SYMMETRIC = {"nosecone", "transition", "bodytube"}
_ASSEMBLIES = {"rocket", "stage", "podset", "parallelstage"}
_FINSETS = {"trapezoidfinset", "ellipticalfinset", "freeformfinset", "tubefinset"}
_MATERIAL_TAGS = {"material", "linematerial", "filletmaterial"}
_SKIP_TAGS = {"subcomponents", "appearance", "insideappearance", "motormount", "finpoints",
              "deploymentconfiguration", "separationconfiguration", "motorconfiguration",
              "flightconfiguration", "preset", "color", "linestyle", "decal"}
_CLUSTER_COUNT = {"single": 1, "double": 2, "3-row": 3, "4-row": 4, "3-ring": 3, "4-ring": 4,
                  "5-ring": 5, "6-ring": 6, "3-star": 4, "4-star": 5, "5-star": 6, "6-star": 7,
                  "9-grid": 9, "9-star": 9}
_CROSS_SECTION_FACTOR = {"square": 1.0, "rounded": 0.99, "airfoil": 0.85}


class Component:
    """One part of the rocket (nose cone, body tube, fin set, parachute, stage, ...).

    Useful attributes
        type        OpenRocket component type (XML tag), e.g. 'bodytube'
        name        name given in OpenRocket
        parent      the component this one is attached to
        children    sub-components, in file order
        material    main Material (or None); materials holds all of them
        position    distance from nose tip to the part's forward edge (m)
        bottom      distance from nose tip to the part's aft edge (m)
        length      axial length (m)
    """

    def __init__(self, el: ET.Element, parent: Optional["Component"], rocket: "Rocket"):
        self.type: str = el.tag
        self.parent = parent
        self.rocket = rocket
        self.children: List[Component] = []
        self.params: Dict[str, Any] = {}       # raw values from the file (auto -> resolved)
        self.attributes: Dict[str, dict] = {}  # XML attributes, e.g. axialoffset method
        self.auto: set = set()                 # params that were 'auto' in OpenRocket
        self.materials: Dict[str, Material] = {}
        self.motor_mount: Optional[Dict[str, Any]] = None
        self.motors: List[Motor] = []
        self.fin_points: List[tuple] = []
        self.comment: Optional[str] = None

        for ch in el:
            tag = ch.tag
            key = _norm(tag)
            if tag in _MATERIAL_TAGS:
                self.materials[key] = Material.from_xml(ch)
            elif tag == "finpoints":
                self.fin_points = [(float(p.get("x")), float(p.get("y"))) for p in ch.findall("point")]
            elif tag == "motormount":
                self._parse_motor_mount(ch)
            elif tag == "subcomponents":
                for sub in ch:
                    self.children.append(Component(sub, self, rocket))
            elif tag in _SKIP_TAGS or len(ch) > 0:
                continue
            elif tag == "comment":
                self.comment = (ch.text or "").strip()
            else:
                text = (ch.text or "").strip()
                if ch.attrib:
                    self.attributes[key] = dict(ch.attrib)
                if text.lower().startswith("auto") and key not in ("ignitionevent",):
                    self.auto.add(key)
                    rest = text.split()[1:]
                    self.params[key] = float(rest[0]) if rest else None
                else:
                    if key in self.params and key == "position":
                        continue
                    if key == "thickness" and text.lower() == "filled":
                        self.params["filled"] = True
                        self.params[key] = None
                        continue
                    self.params[key] = _parse_scalar(text)

        self.name: str = str(self.params.get("name", self.type))
        self.id: Optional[str] = self.params.get("id")

        # axial placement
        if "axialoffset" in self.params:
            self.axial_method = self.attributes.get("axialoffset", {}).get("method", "top")
            self.axial_offset = float(self.params["axialoffset"] or 0.0)
        elif "position" in self.params:           # older files
            self.axial_method = self.attributes.get("position", {}).get("type", "top")
            self.axial_offset = float(self.params["position"] or 0.0)
        else:
            self.axial_method = "after"
            self.axial_offset = 0.0
        self.params.pop("position", None)          # 'position' is reserved for the absolute value
        if self.type == "rocket" or (self.type == "stage" and self.axial_method != "after"
                                     and parent is not None and parent.type == "rocket"):
            self.axial_method, self.axial_offset = ("absolute" if self.type == "rocket" else "after"), 0.0

        # computed during Rocket._layout()
        self.position: Optional[float] = None   # forward edge, from nose tip
        self._rel: float = 0.0                   # forward edge, relative to parent's forward edge
        self._length: Optional[float] = None

    # ---- parsing helpers ----------------------------------------------------------------
    def _parse_motor_mount(self, el):
        self.motor_mount = {}
        for ch in el:
            if ch.tag == "motor":
                self.motors.append(Motor(ch, self))
            elif len(ch) == 0:
                self.motor_mount[_norm(ch.tag)] = _parse_scalar(ch.text)

    # ---- geometry -----------------------------------------------------------------------
    @property
    def length(self) -> float:
        if self._length is not None:
            return self._length
        p = self.params
        if self.type in _ASSEMBLIES:
            return sum(c.length for c in self.children if c.axial_method == "after")
        if self.type == "freeformfinset" and self.fin_points:
            xs = [x for x, _ in self.fin_points]
            return max(xs) - min(xs)
        if self.type in ("trapezoidfinset", "ellipticalfinset"):
            return float(p.get("rootchord") or 0.0)
        if self.type == "railbutton":
            return float(p.get("outerdiameter") or 0.0)
        for k in ("length", "packedlength"):
            if p.get(k) is not None:
                return float(p[k])
        return 0.0

    @property
    def bottom(self) -> Optional[float]:
        return None if self.position is None else self.position + self.length

    @property
    def material(self) -> Optional[Material]:
        return self.materials.get("material")

    @property
    def fore_radius(self) -> Optional[float]:
        if self.type == "bodytube":
            return self.params.get("radius")
        if self.type in ("nosecone", "transition"):
            return self.params.get("foreradius", 0.0 if self.type == "nosecone" else None)
        return self.outer_radius

    @property
    def aft_radius(self) -> Optional[float]:
        if self.type == "bodytube":
            return self.params.get("radius")
        if self.type in ("nosecone", "transition"):
            return self.params.get("aftradius", 0.0 if self.type == "nosecone" else None)
        return self.outer_radius

    @property
    def outer_radius(self) -> Optional[float]:
        p = self.params
        if self.type == "bodytube":
            return p.get("radius")
        if self.type in ("nosecone", "transition"):
            vals = [v for v in (p.get("foreradius"), p.get("aftradius")) if v is not None]
            return max(vals) if vals else None
        if self.type in ("launchlug", "tubefinset"):
            return p.get("radius")
        if self.type == "railbutton":
            return None if p.get("outerdiameter") is None else p["outerdiameter"] / 2
        if self.type in ("parachute", "streamer", "shockcord", "masscomponent"):
            return p.get("packedradius")
        return p.get("outerradius")

    @property
    def inner_radius(self) -> Optional[float]:
        p = self.params
        if self.type in ("centeringring",):
            return p.get("innerradius")
        if self.type == "bulkhead":
            return 0.0
        if self.type == "railbutton":
            return None if p.get("innerdiameter") is None else p["innerdiameter"] / 2
        r, t = self.outer_radius, p.get("thickness")
        if r is None:
            return None
        if self.type in ("nosecone", "transition") and p.get("filled"):
            return 0.0
        if t is None:
            return None
        return max(r - t, 0.0)

    def radius_at(self, x_abs: float) -> Optional[float]:
        """Outer radius of a nose cone / transition / tube at absolute axial position x."""
        if self.type in ("nosecone", "transition"):
            fn = self._profile()
            if fn is None or self.position is None:
                return None
            return fn(min(max(x_abs - self.position, 0.0), self.length))
        return self.outer_radius

    def _profile(self):
        p = self.params
        r1, r2 = self.fore_radius, self.aft_radius
        if r1 is None or r2 is None:
            return None
        default_clip = self.type == "transition"
        return _transition_radius_fn(str(p.get("shape", "conical")), float(r1), float(r2),
                                     self.length, float(p.get("shapeparameter", 1.0) or 0.0),
                                     bool(p.get("shapeclipped", default_clip)))

    def _cavity_radius(self, x_abs: Optional[float] = None) -> Optional[float]:
        """Inside radius available to components mounted in this one."""
        if self.type in ("nosecone", "transition"):
            if x_abs is None:
                return self.inner_radius
            r = self.radius_at(x_abs)
            t = self.params.get("thickness") or 0.0
            return None if r is None else max(r - t, 0.0)
        return self.inner_radius

    @property
    def instance_count(self) -> int:
        p = self.params
        if self.type in _FINSETS:
            return int(p.get("fincount") or p.get("instancecount") or 1)
        if self.type == "innertube":
            cfg = str(p.get("clusterconfiguration", "single"))
            return _CLUSTER_COUNT.get(cfg, int(re.match(r"\d+", cfg).group()) if re.match(r"\d+", cfg) else 1)
        return int(p.get("instancecount") or 1)

    # ---- mass & CG ----------------------------------------------------------------------
    def _own_mass_cg(self):
        """(mass, cg relative to forward edge) of this component alone, without children."""
        p = self.params
        rho = self.material.density if self.material else 0.0
        L = self.length
        n = self.instance_count
        sep = float(p.get("instanceseparation") or 0.0)

        def annulus(ro, ri, length):
            ro = ro or 0.0
            ri = ri or 0.0
            return math.pi * max(ro * ro - ri * ri, 0.0) * length

        mass, cg = 0.0, L / 2
        t = self.type
        if t in ("bodytube", "innertube", "tubecoupler", "launchlug", "engineblock",
                 "centeringring", "bulkhead"):
            mass = annulus(self.outer_radius, self.inner_radius, L) * rho * n
            cg = L / 2 + sep * (n - 1) / 2 if t not in ("innertube",) else L / 2
        elif t in ("nosecone", "transition"):
            mass, cg = self._symmetric_mass_cg(rho)
        elif t in ("trapezoidfinset", "ellipticalfinset", "freeformfinset"):
            mass, cg = self._fin_mass_cg(rho)
        elif t == "tubefinset":
            mass = annulus(self.outer_radius, self.inner_radius, L) * rho * n
        elif t == "railbutton":
            od, idia = p.get("outerdiameter") or 0.0, p.get("innerdiameter") or 0.0
            h, bh, fh = p.get("height") or 0.0, p.get("baseheight") or 0.0, p.get("flangeheight") or 0.0
            sh = p.get("screwheight") or 0.0
            vol = (math.pi * (od / 2) ** 2 * (fh + bh) + math.pi * (idia / 2) ** 2 * max(h - bh - fh, 0)
                   + 2.0 / 3 * math.pi * (od / 2) ** 2 * sh)
            mass = vol * rho * n
            cg = L / 2 + sep * (n - 1) / 2
        elif t == "parachute":
            d = p.get("diameter") or 0.0
            mass = math.pi * d * d / 4 * rho
            line = self.materials.get("linematerial")
            if line:
                mass += (p.get("linecount") or 0) * (p.get("linelength") or 0.0) * line.density
        elif t == "streamer":
            mass = (p.get("striplength") or 0.0) * (p.get("stripwidth") or 0.0) * rho
        elif t == "shockcord":
            mass = (p.get("cordlength") or 0.0) * rho
        elif t == "masscomponent":
            mass = float(p.get("mass") or 0.0)
        return mass, cg

    def _symmetric_mass_cg(self, rho):
        p = self.params
        fn = self._profile()
        L = self.length
        if fn is None or L <= 0:
            return 0.0, 0.0
        t = float(p.get("thickness") or 0.0)
        filled = bool(p.get("filled"))
        N = 128
        vol = mom = 0.0
        for i in range(N):
            x1, x2 = i * L / N, (i + 1) * L / N
            l = x2 - x1
            r1o, r2o = fn(x1), fn(x2)
            h = t * math.hypot(r2o - r1o, l) / l
            r1i = 0.0 if filled else max(r1o - h, 0.0)
            r2i = 0.0 if filled else max(r2o - h, 0.0)
            vo, co = _frustum(l, r1o, r2o)
            vi, ci = _frustum(l, r1i, r2i)
            dv = vo - vi
            dcg = l / 2 if dv == 0 else (co * vo - ci * vi) / dv
            vol += dv
            mom += dv * (x1 + dcg)
        vol *= math.pi / 3
        mom *= math.pi / 3
        # shoulders and caps
        for side in ("fore", "aft"):
            sl = float(p.get(side + "shoulderlength") or 0.0)
            sr = float(p.get(side + "shoulderradius") or 0.0)
            st = float(p.get(side + "shoulderthickness") or 0.0)
            ir = max(sr - st, 0.0)
            if sl > 1e-6:
                v = math.pi * (sr * sr - ir * ir) * sl
                x0 = -sl if side == "fore" else L
                vol += v
                mom += v * (x0 + sl / 2)
            if p.get(side + "shouldercapped"):
                v = math.pi * ir * ir * st
                xc = (-sl + st / 2) if side == "fore" else (L + sl - st / 2)
                vol += v
                mom += v * xc
        return vol * rho, (mom / vol if vol > 0 else L / 2)

    def fin_area(self) -> Optional[float]:
        """Planform area of a single fin (m^2), excluding the tab."""
        p = self.params
        if self.type == "trapezoidfinset":
            return (p.get("rootchord", 0) + p.get("tipchord", 0)) / 2 * p.get("height", 0)
        if self.type == "ellipticalfinset":
            return math.pi / 4 * p.get("rootchord", 0) * p.get("height", 0)
        if self.type == "freeformfinset" and self.fin_points:
            return _polygon_area_centroid(self.fin_points)[0]
        return None

    def _fin_outline(self):
        p = self.params
        if self.type == "trapezoidfinset":
            cr, ct, s, h = (p.get("rootchord", 0), p.get("tipchord", 0),
                            p.get("sweeplength", 0), p.get("height", 0))
            return [(0, 0), (s, h), (s + ct, h), (cr, 0)]
        if self.type == "ellipticalfinset":
            cr, h = p.get("rootchord", 0), p.get("height", 0)
            return [(cr / 2 - cr / 2 * math.cos(a), h * math.sin(a))
                    for a in (i * math.pi / 64 for i in range(65))]
        return list(self.fin_points)

    def _fin_mass_cg(self, rho):
        p = self.params
        n = self.instance_count
        t = float(p.get("thickness") or 0.0)
        area, cx = _polygon_area_centroid(self._fin_outline())
        factor = _CROSS_SECTION_FACTOR.get(str(p.get("crosssection", "square")).lower(), 1.0)
        mass = area * t * factor * rho
        mom = mass * cx
        tab_h, tab_l = float(p.get("tabheight") or 0.0), float(p.get("tablength") or 0.0)
        if tab_h > 0 and tab_l > 0:
            m_tab = tab_h * tab_l * t * rho
            tab_x = self._tab_front() + tab_l / 2
            mass += m_tab
            mom += m_tab * tab_x
        fr = float(p.get("filletradius") or 0.0)
        fm = self.materials.get("filletmaterial")
        if fr > 0 and fm and self.parent is not None and self.parent.type in _EXTERNAL_SYMMETRIC:
            # OpenRocket's concave-fillet cross-section, both sides of the fin
            mid = None if self.position is None else self.position + self.length / 2
            rb = self.parent.radius_at(mid) if mid is not None else self.parent.outer_radius
            if rb:
                hyp = fr + rb
                inner, outer = math.asin(fr / hyp), math.acos(fr / hyp)
                xs = 2 * (math.tan(outer) * fr * fr / 2 - outer * fr * fr / 2 - inner * rb * rb / 2)
                m_fil = max(xs, 0.0) * self.length * fm.density
                mass += m_fil
                mom += m_fil * self.length / 2
        return mass * n, (mom / mass if mass > 0 else self.length / 2)

    def _tab_front(self) -> float:
        p = self.params
        off = float(p.get("tabposition") or 0.0)
        method = self.attributes.get("tabposition", {}).get("relativeto", "front")
        L, tl = self.length, float(p.get("tablength") or 0.0)
        if method in ("center", "middle"):
            return (L - tl) / 2 + off
        if method in ("end", "bottom"):
            return L - tl + off
        return off

    def _mass_props(self):
        """(mass, absolute cg) of this component and everything attached to it."""
        p = self.params
        pos = self.position or 0.0
        own_m, own_cg = self._own_mass_cg()
        legacy = bool(p.get("overridesubcomponents"))
        sub_mass = bool(p.get("overridesubcomponentsmass")) or legacy
        sub_cg = bool(p.get("overridesubcomponentscg")) or legacy
        m_ovr = p.get("overridemass")
        cg_ovr = p.get("overridecg")
        if m_ovr is not None and not sub_mass:
            own_m = float(m_ovr)
        if cg_ovr is not None and not sub_cg:
            own_cg = float(cg_ovr)
        m, mom = own_m, own_m * (pos + own_cg)
        k = self.instance_count if self.type == "innertube" else 1  # clustered tubes repeat children
        for c in self.children:
            cm, ccg = c._mass_props()
            m += cm * k
            mom += cm * k * ccg
        cg = mom / m if m > 0 else pos + self.length / 2
        if m_ovr is not None and sub_mass:
            m = float(m_ovr)
        if cg_ovr is not None and sub_cg:
            cg = pos + float(cg_ovr)
        if self.type in ("podset", "parallelstage"):
            m *= self.instance_count
        return m, cg

    @property
    def mass(self) -> float:
        """Estimated mass of this part alone (kg), honouring any override in the file."""
        if self.params.get("overridemass") is not None:
            return float(self.params["overridemass"])
        return self._own_mass_cg()[0]

    @property
    def total_mass(self) -> float:
        """Estimated mass including everything attached to this component (kg)."""
        return self._mass_props()[0]

    @property
    def cg(self) -> float:
        """Estimated CG of this component and its sub-components, from the nose tip (m)."""
        return self._mass_props()[1]

    # ---- parameter access -----------------------------------------------------------------
    def _derived(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "length": self.length,
            "position": self.position,
            "top": self.position,
            "bottom": self.bottom,
            "axialmethod": self.axial_method,
            "axialoffset": self.axial_offset,
            "mass": self.mass,
            "totalmass": self.total_mass,
            "cg": self.cg,
            "instancecount": self.instance_count,
        }
        if self.material:
            d["materialname"] = self.material.name
            d["density"] = self.material.density
        for k, v in (("outerradius", self.outer_radius), ("innerradius", self.inner_radius)):
            if v is not None:
                d[k] = v
                d[k.replace("radius", "diameter")] = 2 * v
        if self.type in ("nosecone", "transition"):
            for k, v in (("foreradius", self.fore_radius), ("aftradius", self.aft_radius)):
                if v is not None:
                    d[k] = v
                    d[k.replace("radius", "diameter")] = 2 * v
        if self.type in _FINSETS:
            d["fincount"] = self.instance_count
            a = self.fin_area()
            if a is not None:
                d["finarea"] = a
                d["totalfinarea"] = a * self.instance_count
            if "height" in self.params:
                d["span"] = self.params["height"]
        if self.type == "parachute" and self.params.get("diameter"):
            d["canopyarea"] = math.pi * self.params["diameter"] ** 2 / 4
        if self.motor_mount is not None:
            d["overhang"] = self.motor_mount.get("overhang")
        return d

    def all_params(self) -> Dict[str, Any]:
        """Every parameter (raw from the file + derived), keyed by normalised name."""
        out = dict(self.params)
        out.update(self._derived())
        return out

    _ALIASES = {"od": "outerdiameter", "diameter": "outerdiameter", "radius": "outerradius",
                "location": "position", "start": "position", "end": "bottom",
                "count": "instancecount", "material": "materialname", "span": "height",
                "semispan": "height", "sweep": "sweeplength"}

    def get_param(self, name: str, unit: Optional[str] = None, default: Any = KeyError):
        """Return a parameter by (loosely matched) name, optionally converted to `unit`.

        Lengths are metres and masses kilograms unless `unit` is given.
        Raises KeyError (listing what is available) unless `default` is supplied.
        """
        key = _norm(name)
        params = self.all_params()
        if key not in params:
            alias = self._ALIASES.get(key)
            if alias in params:
                key = alias
            elif key.endswith("s") and key[:-1] in params:
                key = key[:-1]
            else:
                if default is not KeyError:
                    return default
                raise KeyError(f"{self.type} '{self.name}' has no parameter '{name}'. "
                               f"Available: {', '.join(sorted(params))}")
        return _convert(params[key], unit, key)

    def has_param(self, name: str) -> bool:
        return self.get_param(name, default=None) is not None

    def is_auto(self, name: str) -> bool:
        """True if OpenRocket sizes this parameter automatically (e.g. radius 'auto')."""
        return _norm(name) in self.auto

    def to_dict(self, recursive: bool = True) -> dict:
        d = {k: v for k, v in self.all_params().items()}
        d["materials"] = {k: m.to_dict() for k, m in self.materials.items()}
        if self.fin_points:
            d["finpoints"] = self.fin_points
        if self.motors:
            d["motors"] = [m.to_dict() for m in self.motors]
        if recursive and self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    # ---- navigation ---------------------------------------------------------------------
    def walk(self) -> Iterator["Component"]:
        """This component and every descendant, depth first, in file order."""
        yield self
        for c in self.children:
            yield from c.walk()

    def components(self, type: Optional[str] = None) -> List["Component"]:
        """All descendants (not including self), optionally filtered by type or alias."""
        allc = list(self.walk())[1:]
        if type is None:
            return allc
        tags = TYPE_ALIASES.get(_norm(type), [_norm(type)])
        return [c for c in allc if c.type in tags]

    def find(self, name: str) -> List["Component"]:
        """All descendants whose OpenRocket name matches (case/spacing-insensitive)."""
        key = _norm(name)
        return [c for c in list(self.walk())[1:] if _norm(c.name) == key]

    def get(self, name: str) -> "Component":
        """First descendant with this name, else first descendant of this type."""
        hits = self.find(name)
        if hits:
            return hits[0]
        hits = self.components(name)
        if hits:
            return hits[0]
        raise KeyError(f"No component named or of type '{name}'. Names: "
                       f"{[c.name for c in list(self.walk())[1:]]}")

    def __getitem__(self, name: str) -> "Component":
        return self.get(name)

    def __getattr__(self, attr: str):
        # Only called when normal attribute lookup fails: rocket.bodyTube, rocket.finSets ...
        if attr.startswith("_") or attr in ("children", "params", "type", "rocket"):
            raise AttributeError(attr)
        key = _norm(attr)
        hits = self.find(attr)
        if hits:
            return hits[0]
        if key in TYPE_ALIASES:
            hits = self.components(key)
            if hits:
                return hits[0]
            raise AttributeError(f"'{self.name}' contains no {attr}")
        if key in _PLURALS:
            return self.components(_PLURALS[key])
        raise AttributeError(f"'{type(self).__name__}' has no attribute or component '{attr}'")

    def __dir__(self):
        base = list(super().__dir__())
        return base + [re.sub(r"\W", "_", c.name) for c in list(self.walk())[1:]]

    def __repr__(self):
        pos = "" if self.position is None else f", x={self.position:.4f} m"
        return f"<{self.type} {self.name!r}{pos}, L={self.length:.4f} m>"


# --------------------------------------------------------------------------------------
# Rocket
# --------------------------------------------------------------------------------------
class Rocket(Component):
    """The whole design. Construct with a path to an .ork file (zip, gzip or plain XML)."""

    def __init__(self, path_or_file):
        root = self._read_xml(path_or_file)
        self.source = str(path_or_file)
        self.file_version = root.get("version")
        self.creator = root.get("creator")
        rel = root.find("rocket")
        if rel is None:
            raise ValueError("Not an OpenRocket design: no <rocket> element found")
        super().__init__(rel, None, self)
        self.designer = self.params.get("designer")
        self.revision = self.params.get("revision")

        self.motor_configurations: Dict[str, dict] = {}
        for mc in rel.findall("motorconfiguration"):
            cid = mc.get("configid")
            self.motor_configurations[cid] = {
                "default": mc.get("default") == "true",
                "name": (mc.findtext("name") or "").strip() or None,
            }
        self.simulations = [Simulation(s) for s in root.iter("simulation")]

        self._resolve_auto_radii()
        self._layout(self, None)
        self._resolve_inner_autos()

    # ---- loading ------------------------------------------------------------------------
    @staticmethod
    def _read_xml(src) -> ET.Element:
        data = src.read() if hasattr(src, "read") else open(src, "rb").read()
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                inner = next((n for n in names if n.lower().endswith((".ork", ".xml"))
                              and "/" not in n), None) or names[0]
                data = z.read(inner)
        elif data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return ET.fromstring(data)

    # ---- resolution of 'auto' sizes & placement -----------------------------------------
    def _chains(self) -> List[List[Component]]:
        """Runs of external nose cones/tubes/transitions that sit end to end."""
        chains = []
        main = [c for s in self.children for c in s.children if c.type in _EXTERNAL_SYMMETRIC]
        chains.append(main)
        for c in self.walk():
            if c.type in ("podset", "parallelstage"):
                chains.append([k for k in c.children if k.type in _EXTERNAL_SYMMETRIC])
        return chains

    def _resolve_auto_radii(self):
        for chain in self._chains():
            for _ in range(len(chain) + 1):
                for i, c in enumerate(chain):
                    prev = chain[i - 1] if i > 0 else None
                    nxt = chain[i + 1] if i + 1 < len(chain) else None
                    if c.type == "bodytube" and c.params.get("radius") is None:
                        c.params["radius"] = ((prev and prev.aft_radius) or (nxt and nxt.fore_radius))
                    if c.type in ("nosecone", "transition"):
                        if "foreradius" in c.auto and c.params.get("foreradius") is None:
                            c.params["foreradius"] = ((prev and prev.aft_radius) or (nxt and nxt.fore_radius))
                        if "aftradius" in c.auto and c.params.get("aftradius") is None:
                            c.params["aftradius"] = ((nxt and nxt.fore_radius) or (prev and prev.aft_radius))
        # nose cone flipped and stored with aft* tags only -> treat as tail cone
        for c in self.walk():
            if c.type == "nosecone" and c.params.get("isflipped") and "foreradius" not in c.params:
                c.params["foreradius"], c.params["aftradius"] = c.params.get("aftradius"), 0.0

    def _layout(self, comp: Component, prev: Optional[Component]):
        parent = comp.parent
        if parent is None:
            comp._rel, comp.position = 0.0, 0.0
        else:
            m, o = comp.axial_method, comp.axial_offset
            P, L = parent.length, comp.length
            if m == "after":
                comp._rel = (prev._rel + prev.length if prev is not None else 0.0) + o
            elif m == "middle":
                comp._rel = o + (P - L) / 2
            elif m == "bottom":
                comp._rel = o + (P - L)
            elif m == "absolute":
                comp._rel = o - (parent.position or 0.0)
            else:  # top
                comp._rel = o
            comp.position = (parent.position or 0.0) + comp._rel
        p = None
        for child in comp.children:
            self._layout(child, p)
            p = child

    def _resolve_inner_autos(self):
        for c in self.walk():
            parent = c.parent
            if parent is None:
                continue
            mid = None if c.position is None else c.position + c.length / 2
            if "outerradius" in c.auto and c.params.get("outerradius") is None:
                c.params["outerradius"] = parent._cavity_radius(mid)
            if "packedradius" in c.auto and c.params.get("packedradius") is None:
                c.params["packedradius"] = parent._cavity_radius(mid)
        for c in self.walk():
            if c.type == "tubefinset" and c.params.get("radius") is None and c.parent is not None:
                rb = c.parent.outer_radius or 0.0
                n = c.instance_count
                c.params["radius"] = rb if n < 3 else rb * math.sin(math.pi / n) / (1 - math.sin(math.pi / n))
            if c.type == "centeringring" and "innerradius" in c.auto and c.params.get("innerradius") is None:
                best = 0.0
                for sib in c.parent.children if c.parent else []:
                    if sib.type == "innertube" and sib.position is not None and c.position is not None:
                        if sib.position <= c.bottom and sib.bottom >= c.position:
                            best = max(best, sib.outer_radius or 0.0)
                c.params["innerradius"] = min(best, c.params.get("outerradius") or best)

    # ---- rocket-level information ---------------------------------------------------------
    @property
    def stages(self) -> List[Component]:
        return [c for c in self.children if c.type == "stage"]

    @property
    def length(self) -> float:
        ends = [c.bottom for c in self.walk() if c.type in _EXTERNAL_SYMMETRIC and c.bottom is not None]
        return max(ends) if ends else 0.0

    @property
    def max_diameter(self) -> float:
        r = [c.outer_radius for c in self.walk() if c.type in _EXTERNAL_SYMMETRIC and c.outer_radius]
        return 2 * max(r) if r else 0.0

    @property
    def material(self):
        return None

    @property
    def motors(self) -> List[Motor]:
        """Every motor in every flight configuration (see motors_for() for one configuration)."""
        return [m for c in list(self.walk())[1:] for m in c.motors]

    @motors.setter
    def motors(self, value):
        pass  # the <rocket> element itself never holds motors

    def motors_for(self, config_id: Optional[str] = None) -> List[Motor]:
        """Motors loaded in a flight configuration (default configuration if none given)."""
        if config_id is None:
            config_id = next((k for k, v in self.motor_configurations.items() if v["default"]),
                             next(iter(self.motor_configurations), None))
        return [m for m in self.motors if m.config_id == config_id]

    def parts_table(self, unit: str = "m") -> List[dict]:
        """One row per component: placement, size, material, estimated mass."""
        rows = []
        f = LENGTH_UNITS[unit]
        for c in list(self.walk())[1:]:
            def cv(v):
                return None if v is None else v / f
            rows.append({
                "name": c.name,
                "type": c.type,
                "parent": c.parent.name if c.parent else None,
                f"position_{unit}": cv(c.position),
                f"bottom_{unit}": cv(c.bottom),
                f"length_{unit}": cv(c.length),
                f"outer_diameter_{unit}": cv(None if c.outer_radius is None else 2 * c.outer_radius),
                f"inner_diameter_{unit}": cv(None if c.inner_radius is None else 2 * c.inner_radius),
                f"thickness_{unit}": cv(c.params.get("thickness")),
                "material": c.material.name if c.material else None,
                "density": c.material.density if c.material else None,
                "density_unit": c.material.density_unit if c.material else None,
                "count": c.instance_count,
                "mass_kg": c.mass,
            })
        return rows

    def to_dict(self, recursive: bool = True) -> dict:
        d = super().to_dict(recursive)
        d.update(file_version=self.file_version, creator=self.creator,
                 motor_configurations=self.motor_configurations,
                 simulations=[{"name": s.name, "config_id": s.config_id, **s.results}
                              for s in self.simulations])
        return d

    def to_json(self, path: Optional[str] = None, **kw) -> str:
        s = json.dumps(self.to_dict(), indent=2, default=str, **kw)
        if path:
            with open(path, "w") as fh:
                fh.write(s)
        return s

    def print_tree(self, unit: str = "mm", file=None):
        """Print the design as an indented tree with placement, size and material."""
        f = LENGTH_UNITS[unit]
        out = file or sys.stdout
        print(f"{self.name}  (OpenRocket file v{self.file_version}, {self.creator})", file=out)
        print(f"  length {self.length / f:.1f} {unit}, max diameter {self.max_diameter / f:.1f} {unit}, "
              f"estimated dry mass {self.total_mass * 1000:.1f} g, CG {self.cg / f:.1f} {unit} "
              f"from tip (no motor)", file=out)

        def rec(c: Component, depth: int):
            size = []
            if c.outer_radius:
                size.append(f"OD {2 * c.outer_radius / f:.1f}")
            if c.inner_radius:
                size.append(f"ID {2 * c.inner_radius / f:.1f}")
            if c.type in _FINSETS:
                size.append(f"{c.instance_count} fins")
                if "height" in c.params:
                    size.append(f"span {c.params['height'] / f:.1f}")
            if c.type == "parachute" and c.params.get("diameter"):
                size.append(f"canopy {c.params['diameter'] / f:.0f}")
            mat = f" [{c.material.name}]" if c.material else ""
            pos = "" if c.position is None else f"x {c.position / f:8.1f} .. {c.bottom / f:8.1f}"
            print(f"{'  ' * depth}- {c.name} ({c.type}) {pos} {unit}  L {c.length / f:.1f}"
                  f"{'  ' + ', '.join(size) if size else ''}{mat}  ~{c.mass * 1000:.1f} g", file=out)
            for m in c.motors:
                print(f"{'  ' * (depth + 1)}* motor {m.manufacturer} {m.designation} "
                      f"(config {m.config_id[:8] if m.config_id else '?'})", file=out)
            for k in c.children:
                rec(k, depth + 1)

        for s in self.children:
            rec(s, 1)
        if self.simulations:
            print("  simulations:", file=out)
            for s in self.simulations:
                print(f"    - {s.name}: apogee {s.results.get('maxaltitude')} m, "
                      f"max v {s.results.get('maxvelocity')} m/s", file=out)

    def __repr__(self):
        return (f"<Rocket {self.name!r}: {len(self.stages)} stage(s), "
                f"{len(list(self.walk())) - 1} components, L={self.length:.4f} m>")


def load(path) -> Rocket:
    """Shorthand for Rocket(path)."""
    return Rocket(path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    r = Rocket(sys.argv[1])
    r.print_tree()
    if len(sys.argv) > 2 and sys.argv[2] == "--json":
        print(r.to_json())