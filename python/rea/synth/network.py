"""Short-circuit model of a line fed from both ends, solved with symmetrical components.

The relay sits at bus S and measures current flowing from bus S into the line
S-R. A forward fault is on the line at per-unit distance ``location`` from S. A
reverse fault is just behind the relay (on bus S or an adjacent circuit), so the
relay only sees the remote source feeding back through the line.

Faults are solved in the phase domain with a fault admittance matrix, which
handles all ten shunt fault types (and fault resistance) with one formula:

    I_f = (I + Y_f Z_th) ^ -1  Y_f  V_prefault

The relay's share of each sequence component of I_f follows from the current
divider of that sequence network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

A_OP = np.exp(2j * np.pi / 3)
# phase = A_MAT @ [zero, positive, negative]
A_MAT = np.array([[1, 1, 1], [1, A_OP**2, A_OP], [1, A_OP, A_OP**2]], dtype=complex)
A_INV = np.linalg.inv(A_MAT)

FAULT_TYPES = ("AG", "BG", "CG", "AB", "BC", "CA", "ABG", "BCG", "CAG", "ABC")
_PHASE_INDEX = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True)
class SystemModel:
    """Primary-side impedances in ohms. Negative-sequence equals positive-sequence."""

    zs1: complex  # local source
    zs0: complex
    zr1: complex  # remote source
    zr0: complex
    zl1: complex  # whole line
    zl0: complex
    v_ll_kv: float = 115.0
    load_angle_deg: float = 10.0  # local source angle relative to remote source
    f_hz: float = 60.0
    ctr: float = 240.0  # 1200:5
    ptr: float = 1000.0  # 115 kV : 115 V

    @property
    def e_ln(self) -> float:
        return self.v_ll_kv * 1e3 / math.sqrt(3.0)

    def seq(self, z1: complex, z0: complex) -> np.ndarray:
        return np.array([z0, z1, z1], dtype=complex)


@dataclass(frozen=True)
class FaultSpec:
    kind: str  # one of FAULT_TYPES
    forward: bool = True
    location: float = 0.5  # per unit of line length from the relay (forward faults)
    r_phase: float = 0.0  # ohms in each faulted phase leg
    r_ground: float = 0.0  # ohms from the faulted phases' common point to ground

    def __post_init__(self) -> None:
        if self.kind not in FAULT_TYPES:
            raise ValueError(f"unknown fault type {self.kind!r}")
        if not 0.0 <= self.location <= 1.0:
            raise ValueError("location must be within 0..1")


@dataclass(frozen=True)
class FaultSolution:
    """Relay-side phasors (primary units; current direction bus -> line)."""

    i_pre: np.ndarray
    v_pre: np.ndarray
    i_flt: np.ndarray
    v_flt: np.ndarray
    i_fault: np.ndarray  # phase currents flowing into the fault
    zth_seq: np.ndarray  # Thevenin impedance at the fault [Z0, Z1, Z2]
    tau_s: float  # DC offset time constant of the fault loop


def fault_admittance(spec: FaultSpec) -> np.ndarray:
    """Admittance matrix of a star of resistances from the faulted phases.

    Each faulted phase connects through ``r_phase`` to a common node; the node
    connects to ground through ``r_ground`` for ground faults and floats
    otherwise. Eliminating the node gives Y = diag(g) - g g^T / (sum(g) + g_ground).
    """
    phases = spec.kind[:-1] if spec.kind.endswith("G") else spec.kind
    grounded = spec.kind.endswith("G")
    g = np.zeros(3)
    for ph in phases:
        g[_PHASE_INDEX[ph]] = 1.0 / max(spec.r_phase, 1e-4)
    g_ground = 1.0 / max(spec.r_ground, 1e-4) if grounded else 0.0
    return np.diag(g) - np.outer(g, g) / (g.sum() + g_ground)


def solve(system: SystemModel, spec: FaultSpec) -> FaultSolution:
    e = system.e_ln
    es = e * np.exp(1j * math.radians(system.load_angle_deg))
    er = complex(e, 0.0)
    zs = system.seq(system.zs1, system.zs0)
    zr = system.seq(system.zr1, system.zr0)
    zl = system.seq(system.zl1, system.zl0)

    i_load = (es - er) / (system.zs1 + system.zl1 + system.zr1)
    v_bus_pre = es - system.zs1 * i_load

    if spec.forward:
        m = spec.location
        z_left = zs + m * zl
        z_right = zr + (1.0 - m) * zl
        zth = z_left * z_right / (z_left + z_right)
        share = z_right / (z_left + z_right)  # part of the fault current coming through the relay
        v_fault_pre = v_bus_pre - m * system.zl1 * i_load
    else:
        zth = zs * (zl + zr) / (zs + zl + zr)
        share = -zs / (zs + zl + zr)  # remote infeed flows line -> bus through the relay
        v_fault_pre = v_bus_pre

    zth_phase = A_MAT @ np.diag(zth) @ A_INV
    v_pre_phase = A_MAT @ np.array([0.0, v_fault_pre, 0.0], dtype=complex)
    yf = fault_admittance(spec)
    i_fault = np.linalg.solve(np.eye(3) + yf @ zth_phase, yf @ v_pre_phase)
    i_fault_seq = A_INV @ i_fault

    di_seq = share * i_fault_seq
    dv_seq = -zs * di_seq if spec.forward else -zth * i_fault_seq

    i_pre_seq = np.array([0.0, i_load, 0.0], dtype=complex)
    v_pre_seq = np.array([0.0, v_bus_pre, 0.0], dtype=complex)
    omega = 2.0 * math.pi * system.f_hz
    z1 = zth[1]
    tau = z1.imag / (omega * z1.real) if z1.real > 0 else 0.05
    return FaultSolution(
        i_pre=A_MAT @ i_pre_seq,
        v_pre=A_MAT @ v_pre_seq,
        i_flt=A_MAT @ (i_pre_seq + di_seq),
        v_flt=A_MAT @ (v_pre_seq + dv_seq),
        i_fault=i_fault,
        zth_seq=zth,
        tau_s=float(min(max(tau, 0.005), 0.12)),
    )


def sequence(phasors: np.ndarray) -> np.ndarray:
    """[zero, positive, negative] sequence components of a phase vector."""
    return A_INV @ np.asarray(phasors, dtype=complex)


def default_system(**overrides: float) -> SystemModel:
    """A 115 kV, roughly 40 km line between a strong and a weaker source."""
    zl1 = complex(2.0, 16.0)
    params = dict(
        zs1=3.3 * np.exp(1j * math.radians(85.0)),
        zs0=5.0 * np.exp(1j * math.radians(80.0)),
        zr1=8.0 * np.exp(1j * math.radians(83.0)),
        zr0=14.0 * np.exp(1j * math.radians(78.0)),
        zl1=zl1,
        zl0=3.0 * abs(zl1) * np.exp(1j * math.radians(75.0)),
    )
    params.update(overrides)
    return SystemModel(**params)  # type: ignore[arg-type]
