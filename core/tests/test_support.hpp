// Builds three-phase records in memory for unit tests (no files involved).
#pragma once

#include <cmath>
#include <complex>
#include <optional>
#include <random>
#include <string>
#include <vector>

#include "rea/analyzer.hpp"
#include "rea/comtrade.hpp"
#include "rea/dsp.hpp"

namespace rea::test {

constexpr double kPi = 3.14159265358979323846;
inline const cplx kA = std::polar(1.0, 2.0 * kPi / 3.0);

inline cplx pd(double mag, double ang_deg) { return std::polar(mag, ang_deg * kPi / 180.0); }

struct ThreePhase {
  cplx a, b, c;
  cplx operator[](int p) const { return p == 0 ? a : (p == 1 ? b : c); }
};

inline ThreePhase balanced(double mag, double ang_deg) {
  return {pd(mag, ang_deg), pd(mag, ang_deg - 120.0), pd(mag, ang_deg + 120.0)};
}

inline ThreePhase add(const ThreePhase& x, const ThreePhase& y) { return {x.a + y.a, x.b + y.b, x.c + y.c}; }

inline ThreePhase from_sequence(cplx z0, cplx z1, cplx z2) {
  const cplx a2 = kA * kA;
  return {z0 + z1 + z2, z0 + a2 * z1 + kA * z2, z0 + kA * z1 + a2 * z2};
}

// Re-reference a fault computed with phase A as the special phase onto phase B or C.
inline ThreePhase rereference(const ThreePhase& x, char ref) {
  if (ref == 'B') return {kA * kA * x.c, kA * kA * x.a, kA * kA * x.b};
  if (ref == 'C') return {kA * x.b, kA * x.c, kA * x.a};
  return x;
}

struct FaultPhasors {
  ThreePhase di;  // incremental currents at the relay
  ThreePhase dv;  // incremental voltages at the relay
};

// Incremental relay quantities for a fault type. Forward faults see the source
// behind the relay (dV = -Zs * dI); reverse faults see the line and remote source
// (dV = +Zrev * dI). Impedances are secondary ohms.
inline FaultPhasors make_fault(const std::string& type, double i1_mag, bool forward = true,
                               double three_rg = 0.0) {
  const cplx zs1 = pd(1.0, 85.0), zs0 = pd(2.0, 80.0);
  const cplx zr1 = pd(6.0, 82.0), zr0 = pd(15.0, 75.0);
  const cplx i1 = pd(i1_mag, -80.0);
  cplx d0, d1, d2;
  std::string base;
  char ref = 'A';
  if (type == "AG" || type == "BG" || type == "CG") {
    d0 = d1 = d2 = i1;
    base = "SLG";
    ref = type[0];
  } else if (type == "BC" || type == "CA" || type == "AB") {
    d1 = i1;
    d2 = -i1;
    d0 = 0.0;
    ref = type == "BC" ? 'A' : (type == "CA" ? 'B' : 'C');
  } else if (type == "BCG" || type == "CAG" || type == "ABG") {
    // Ground path impedance includes 3 x Rg in the zero-sequence branch.
    const cplx z2 = pd(1.0, 85.0), z0 = pd(3.0, 78.0) + three_rg;
    d1 = i1;
    d2 = -i1 * z0 / (z2 + z0);
    d0 = -i1 * z2 / (z2 + z0);
    ref = type == "BCG" ? 'A' : (type == "CAG" ? 'B' : 'C');
  } else {  // ABC
    d1 = i1;
    d0 = d2 = 0.0;
  }
  const cplx z1 = forward ? -zs1 : zr1;
  const cplx z0v = forward ? -zs0 : zr0;
  FaultPhasors f;
  f.di = rereference(from_sequence(d0, d1, d2), ref);
  f.dv = rereference(from_sequence(z0v * d0, z1 * d1, z1 * d2), ref);
  return f;
}

struct EventSpec {
  double f = 60.0;
  double f_nom = 60.0;
  int spc = 32;
  double duration_s = 0.6;
  double t_fault = 0.1;
  ThreePhase v_pre = balanced(66.4, 0.0);
  ThreePhase i_pre = balanced(1.0, -20.0);
  ThreePhase v_flt = balanced(66.4, 0.0);
  ThreePhase i_flt = balanced(1.0, -20.0);
  double tau_dc = 0.03;
  bool fault_from_start = false;
  std::optional<double> t_trip;
  std::optional<double> t_open;    // contacts part; each pole clears at its next current zero
  std::optional<double> t_return;  // fault removed elsewhere; prefault waveform resumes
  double noise = 0.0;
  unsigned seed = 7;
  bool with_voltage = true;
};

inline Record build_record(const EventSpec& e) {
  const double fs = e.spc * e.f_nom;
  const auto k_total = static_cast<std::size_t>(std::lround(e.duration_s * fs));
  const double w = 2.0 * kPi * e.f;
  std::mt19937 rng(e.seed);
  std::normal_distribution<double> noise(0.0, 1.0);
  auto wave = [&](cplx p, double t) { return std::sqrt(2.0) * std::abs(p) * std::cos(w * t + std::arg(p)); };

  Record rec;
  Config& c = rec.cfg;
  c.station = "TEST";
  c.device = "UNIT";
  c.rev_year = 1999;
  c.line_freq_hz = e.f_nom;
  c.rates = {{fs, static_cast<long long>(k_total)}};
  const char* ids[] = {"IA", "IB", "IC", "VA", "VB", "VC"};
  const int n_analog = e.with_voltage ? 6 : 3;
  for (int i = 0; i < n_analog; ++i) {
    AnalogChannel ch;
    ch.index = i + 1;
    ch.id = ids[i];
    ch.phase = std::string(1, "ABC"[i % 3]);
    ch.unit = i < 3 ? "A" : "V";
    c.analogs.push_back(ch);
  }
  for (const char* id : {"TRIP", "52A"}) {
    DigitalChannel d;
    d.id = id;
    c.digitals.push_back(d);
  }
  rec.time_s.resize(k_total);
  rec.analog.assign(static_cast<std::size_t>(n_analog), std::vector<double>(k_total));
  rec.digital.assign(2, std::vector<std::uint8_t>(k_total, 0));

  const double tf = e.fault_from_start ? -1.0 : e.t_fault;
  for (int p = 0; p < 3; ++p) {
    bool cleared = false;
    double prev = 0.0;
    for (std::size_t k = 0; k < k_total; ++k) {
      const double t = static_cast<double>(k) / fs;
      rec.time_s[k] = t;
      double i_val;
      double v_val;
      const bool faulted = t >= tf && !(e.t_return && t >= *e.t_return);
      if (!faulted) {
        i_val = wave(e.i_pre[p], t);
        v_val = wave(e.v_pre[p], t);
        if (e.t_return && t >= *e.t_return) {
          const double tr = *e.t_return;
          i_val += (wave(e.i_flt[p], tr) - wave(e.i_pre[p], tr)) * std::exp(-(t - tr) / e.tau_dc);
        }
      } else {
        i_val = wave(e.i_flt[p], t);
        v_val = wave(e.v_flt[p], t);
        if (!e.fault_from_start) {
          i_val += (wave(e.i_pre[p], tf) - wave(e.i_flt[p], tf)) * std::exp(-(t - tf) / e.tau_dc);
        }
      }
      if (e.t_open && t >= *e.t_open && !cleared && k > 0 && ((prev >= 0.0) != (i_val >= 0.0))) {
        cleared = true;
      }
      prev = i_val;
      if (cleared) {
        i_val = 0.0;
        v_val = wave(e.v_pre[p], t);
      }
      rec.analog[static_cast<std::size_t>(p)][k] = i_val + e.noise * noise(rng);
      if (e.with_voltage) rec.analog[static_cast<std::size_t>(p + 3)][k] = v_val + 10.0 * e.noise * noise(rng);
    }
  }
  for (std::size_t k = 0; k < k_total; ++k) {
    const double t = rec.time_s[k];
    rec.digital[0][k] = (e.t_trip && t >= *e.t_trip) ? 1 : 0;
    rec.digital[1][k] = (e.t_open && t >= *e.t_open) ? 0 : 1;
  }
  return rec;
}

// Event whose fault quantities come from make_fault().
inline EventSpec fault_event(const std::string& type, double i1_mag, bool forward = true,
                             double three_rg = 0.0) {
  EventSpec e;
  const FaultPhasors f = make_fault(type, i1_mag, forward, three_rg);
  e.i_flt = add(e.i_pre, f.di);
  e.v_flt = add(e.v_pre, f.dv);
  return e;
}

}  // namespace rea::test
