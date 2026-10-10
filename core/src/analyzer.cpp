#include "rea/analyzer.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>

#include "rea/curves.hpp"

namespace rea {
namespace {

constexpr double kSqrt2 = 1.41421356237309504880;
constexpr char kPhase[3] = {'A', 'B', 'C'};

std::string alnum_upper(const std::string& s) {
  std::string out;
  for (char c : s) {
    if (std::isalnum(static_cast<unsigned char>(c))) {
      out.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    }
  }
  return out;
}

bool finite(cplx z) { return std::isfinite(z.real()) && std::isfinite(z.imag()); }

double median(std::vector<double> v) {
  if (v.empty()) return 0.0;
  const auto mid = v.begin() + static_cast<long>(v.size() / 2);
  std::nth_element(v.begin(), mid, v.end());
  return *mid;
}

char quantity_kind(const AnalogChannel& ch) {
  const std::string u = alnum_upper(ch.unit);
  if (u == "A" || u == "KA" || u == "AMP" || u == "AMPS") return 'I';
  if (u == "V" || u == "KV" || u == "VOLT" || u == "VOLTS") return 'V';
  const std::string id = alnum_upper(ch.id);
  if (!id.empty() && (id[0] == 'I' || id[0] == 'V')) return id[0];
  return 0;
}

char phase_letter(const AnalogChannel& ch) {
  const std::string p = alnum_upper(ch.phase);
  if (p == "A" || p == "AN" || p == "AG" || p == "L1") return 'A';
  if (p == "B" || p == "BN" || p == "BG" || p == "L2") return 'B';
  if (p == "C" || p == "CN" || p == "CG" || p == "L3") return 'C';
  const std::string id = alnum_upper(ch.id);  // IA, IAW, VAY, VAN, IL1 ...
  if (id.size() >= 2 && (id[0] == 'I' || id[0] == 'V')) {
    if (id[1] == 'A' || id[1] == 'B' || id[1] == 'C') {
      if (id.size() >= 3 && (id[2] == 'A' || id[2] == 'B' || id[2] == 'C')) return 0;  // VAB: line-line
      return id[1];
    }
    if (id.size() >= 3 && id[1] == 'L' && id[2] >= '1' && id[2] <= '3') return kPhase[id[2] - '1'];
  }
  return 0;
}

double secondary_scale(const AnalogChannel& ch) {
  const std::string u = alnum_upper(ch.unit);
  double f = (u == "KA" || u == "KV") ? 1000.0 : 1.0;
  if (ch.has_ratio && ch.ps == 'P' && ch.primary > 0.0 && ch.secondary > 0.0) {
    f *= ch.secondary / ch.primary;
  }
  return f;
}

std::optional<long> first_rising(const std::vector<std::uint8_t>& d, long from) {
  const long k_total = static_cast<long>(d.size());
  from = std::max(from, 0L);
  for (long k = std::max(from, 1L); k < k_total; ++k) {
    if (d[static_cast<std::size_t>(k)] && !d[static_cast<std::size_t>(k - 1)]) return k;
  }
  return std::nullopt;
}

std::optional<long> first_falling(const std::vector<std::uint8_t>& d, long from) {
  const long k_total = static_cast<long>(d.size());
  for (long k = std::max(from, 1L); k < k_total; ++k) {
    if (!d[static_cast<std::size_t>(k)] && d[static_cast<std::size_t>(k - 1)]) return k;
  }
  return std::nullopt;
}

// Robust noise level of |x[k] - x[k-N]| over `span` samples from `from`: 1.4826 x the
// median. Coarsely quantized records (whole amperes, as in relay event reports converted
// to COMTRADE) can have a median of exactly zero; the mean (x 1.2533, the same scale for
// Gaussian noise) is used then, so a threshold never collapses to zero.
double span_sigma(const std::vector<double>& x, std::size_t un, std::size_t from, std::size_t span) {
  std::vector<double> d;
  for (std::size_t k = std::max(from, un); k < std::min(x.size(), from + span); ++k) {
    const double v = x[k] - x[k - un];
    if (std::isfinite(v)) d.push_back(std::fabs(v));
  }
  const double med = median(d);
  if (med > 0.0 || d.empty()) return 1.4826 * med;
  double sum = 0.0;
  for (const double v : d) sum += v;
  return 1.2533 * sum / static_cast<double>(d.size());
}

// Samples used for a noise estimate: one cycle, but never fewer than 32.
std::size_t noise_span(int n) { return std::max<std::size_t>(static_cast<std::size_t>(n), 32); }

// Noise level of the cycle-to-cycle difference: the quieter of the start and the end
// of the record (a record can start inside a fault with decaying DC offset, and it
// normally ends with steady load or zero current).
double delta_sigma(const std::vector<double>& x, int n) {
  const std::size_t un = static_cast<std::size_t>(n);
  const std::size_t span = noise_span(n);
  if (x.size() < un + span) return 0.0;
  return std::min(span_sigma(x, un, un, span), span_sigma(x, un, x.size() - span, span));
}

// Noise level at the start of the record only: the prefault reference (it includes
// the drift caused by an off-nominal frequency).
double delta_sigma_first(const std::vector<double>& x, int n) {
  const std::size_t un = static_cast<std::size_t>(n);
  return span_sigma(x, un, un, noise_span(n));
}

// Walk a detected onset back to where the cycle-to-cycle difference leaves the noise
// floor. A single quiet sample is skipped: the difference of two sinusoids can cross
// zero right after the change, so stopping there would make the onset late.
long refine_back(const std::vector<double>& x, int n, long onset, long limit, double fine) {
  auto loud = [&](long j) {
    const auto u = static_cast<std::size_t>(j);
    return std::fabs(x[u] - x[u - static_cast<std::size_t>(n)]) > fine;
  };
  long best = onset;
  for (long j = onset - 1; j >= limit; --j) {
    if (loud(j)) {
      best = j;
    } else if (j - 1 < limit || !loud(j - 1)) {
      break;  // two quiet samples in a row
    }
  }
  return best;
}

// First sample of the earliest run (>= 3 samples) where |x[k] - x[k-N]| exceeds the
// threshold, refined backwards to where the difference leaves the noise floor.
std::optional<long> find_onset(const std::vector<double>& x, int n, double abs_floor) {
  const long k_total = static_cast<long>(x.size());
  if (k_total < 2L * n + 3) return std::nullopt;
  const double sigma = delta_sigma(x, n);
  const double thr = std::max(abs_floor, 8.0 * sigma);
  // Walk back only through samples that stand out from the prefault level.
  const double fine = 4.0 * std::max(sigma, delta_sigma_first(x, n)) + 1e-3 * abs_floor;
  int run = 0;
  for (long k = n; k < k_total; ++k) {
    const double d = std::fabs(x[static_cast<std::size_t>(k)] - x[static_cast<std::size_t>(k - n)]);
    if (std::isfinite(d) && d > thr) {
      if (++run >= 3) {
        return refine_back(x, n, k - 2, std::max(static_cast<long>(n), k - 2 - n / 2), fine);
      }
    } else {
      run = 0;
    }
  }
  return std::nullopt;
}

// Like find_onset, but only accepts a disturbance that follows at least half a
// cycle of quiet. Used after inception, where decaying DC can keep the
// cycle-to-cycle difference of currents high for a while.
std::optional<long> find_step_after_quiet(const std::vector<double>& x, int n, long from,
                                          double abs_floor) {
  const long k_total = static_cast<long>(x.size());
  const double sigma = delta_sigma(x, n);
  const double thr = std::max(abs_floor, 8.0 * sigma);
  const double fine = 4.0 * sigma + 1e-3 * abs_floor;
  long quiet = 0;
  int run = 0;
  bool armed = false;  // set by half a cycle of quiet; survives short blips
  for (long k = std::max(from, static_cast<long>(n)); k < k_total; ++k) {
    const double d = std::fabs(x[static_cast<std::size_t>(k)] - x[static_cast<std::size_t>(k - n)]);
    if (std::isfinite(d) && d > thr) {
      ++run;
      quiet = 0;
      if (armed && run >= 3) {
        return refine_back(x, n, k - 2, std::max({static_cast<long>(n), from, k - 2 - n / 2}), fine);
      }
    } else {
      run = 0;
      if (++quiet >= n / 2) armed = true;
    }
  }
  return std::nullopt;
}

// First sample of a full cycle of ~zero current that follows real current flow.
std::optional<long> find_interruption(const std::vector<double>& x, int n, long from, double thr) {
  const long k_total = static_cast<long>(x.size());
  long run = 0;
  for (long k = std::max(from, static_cast<long>(n)); k < k_total; ++k) {
    const double v = x[static_cast<std::size_t>(k)];
    if (std::isfinite(v) && std::fabs(v) < thr) {
      if (++run >= n) {
        const long start = k - n + 1;
        double before = 0.0;
        for (long j = std::max(0L, start - n); j < start; ++j) {
          before = std::max(before, std::fabs(x[static_cast<std::size_t>(j)]));
        }
        if (before > 2.0 * thr) return start;
        run = 0;  // no current before this quiet cycle: keep scanning
      }
    } else {
      run = 0;
    }
  }
  return std::nullopt;
}

struct Classification {
  FaultType type = FaultType::None;
  bool ground = false;
  double ground_ratio = 0.0;
  std::optional<double> i2_i0_angle_deg;
  std::string note;
};

Classification classify_abc(const std::array<cplx, 3>& d, double inom) {
  Classification c;
  const Sequence sq = to_sequence(d[0], d[1], d[2]);
  const std::array<double, 3> ph{std::abs(d[0]), std::abs(d[1]), std::abs(d[2])};
  const double max_ph = *std::max_element(ph.begin(), ph.end());
  if (!(max_ph >= 0.1 * inom)) {
    c.note = "incremental current below 0.1 x INOM";
    return c;
  }
  // Loops AB, BC, CA. Phase-to-phase differences cancel the zero-sequence component,
  // so zero-sequence infeed cannot distort this comparison.
  const std::array<double, 3> pp{std::abs(d[0] - d[1]), std::abs(d[1] - d[2]),
                                 std::abs(d[2] - d[0])};
  const double max_pp = *std::max_element(pp.begin(), pp.end());
  const double three_i0 = 3.0 * std::abs(sq.zero);
  c.ground_ratio = three_i0 / max_ph;
  c.ground = c.ground_ratio >= 0.1 && three_i0 >= 0.05 * inom;

  if (c.ground) {
    // Loop that excludes each phase: BC excludes A, CA excludes B, AB excludes C.
    auto loop_without = [&](int phase) { return phase == 0 ? pp[1] : (phase == 1 ? pp[2] : pp[0]); };
    std::array<double, 3> sorted = pp;
    std::sort(sorted.begin(), sorted.end());
    const int max_loop = static_cast<int>(std::max_element(pp.begin(), pp.end()) - pp.begin());
    const int healthy_by_loop = (max_loop + 2) % 3;  // AB->C, BC->A, CA->B

    // Sector from the angle of I2 relative to I0: AG/BCG near 0 deg, BG/CAG near -120,
    // CG/ABG near +120 (ABC rotation). The sector names the "special" phase.
    std::optional<int> special;
    if (std::abs(sq.neg) >= 0.05 * std::abs(sq.zero)) {
      const double ang = deg(std::arg(sq.neg / sq.zero));
      c.i2_i0_angle_deg = ang;
      const long idx = std::lround(ang / 120.0);  // 0 -> A, -1 or 2 -> B, 1 or -2 -> C
      special = idx == 0 ? 0 : ((idx == -1 || idx == 2) ? 1 : 2);
      // Single-line-to-ground: the loop without the faulted phase carries ~no
      // incremental current and the other two loops are equal.
      if (max_pp > 0.0 && loop_without(*special) / max_pp < 0.35 && sorted[1] / max_pp > 0.75) {
        static const FaultType slg[3] = {FaultType::AG, FaultType::BG, FaultType::CG};
        c.type = slg[*special];
        return c;
      }
    }

    // Double-line-to-ground. A dominant loop names the faulted pair directly; when all
    // loops are similar (very low zero-sequence impedance) the sector decides.
    int healthy;
    if (sorted[1] / max_pp < 0.85 || !special) {
      healthy = sorted[1] / max_pp < 0.85
                    ? healthy_by_loop
                    : static_cast<int>(std::min_element(ph.begin(), ph.end()) - ph.begin());
      if (special && *special != healthy) {
        c.note = "I2/I0 sector disagrees with the dominant loop (high ground resistance?); loop used";
      }
    } else {
      healthy = *special;
    }
    static const FaultType llg[3] = {FaultType::BCG, FaultType::CAG, FaultType::ABG};
    c.type = llg[healthy];
    return c;
  }

  std::array<double, 3> sorted = pp;
  std::sort(sorted.begin(), sorted.end());
  if (sorted[2] > 0.0 && sorted[0] / sorted[2] > 0.75) {
    c.type = FaultType::ABC;
    return c;
  }
  static const FaultType ll[3] = {FaultType::AB, FaultType::BC, FaultType::CA};
  const auto loop = static_cast<int>(std::max_element(pp.begin(), pp.end()) - pp.begin());
  c.type = ll[loop];
  if (sorted[2] > 0.0 && sorted[0] / sorted[2] < 0.2 && sorted[1] / sorted[2] > 0.8) {
    c.note = "single-phase pattern without residual current (ungrounded system?)";
  }
  return c;
}

// Swapping the B and C labels turns an ACB-rotation problem into an ABC one.
FaultType swap_bc(FaultType t) {
  switch (t) {
    case FaultType::BG: return FaultType::CG;
    case FaultType::CG: return FaultType::BG;
    case FaultType::AB: return FaultType::CA;
    case FaultType::CA: return FaultType::AB;
    case FaultType::ABG: return FaultType::CAG;
    case FaultType::CAG: return FaultType::ABG;
    default: return t;  // AG, BC, BCG, ABC and None are symmetric in B and C
  }
}

Classification classify(const std::array<cplx, 3>& d, double inom, bool abc) {
  if (abc) return classify_abc(d, inom);
  Classification c = classify_abc({d[0], d[2], d[1]}, inom);
  c.type = swap_bc(c.type);
  return c;
}

struct DirectionResult {
  Direction dir = Direction::Unknown;
  std::optional<double> z_ohm;
  std::string method;
  std::string reason;
};

DirectionResult decide_direction(const Sequence& v, const Sequence& i, const Sequence* dv,
                                 const Sequence* di, double z1ang_deg, double z2f, double z2r,
                                 double inom) {
  DirectionResult r;
  const cplx rot = std::polar(1.0, rad(z1ang_deg));
  const double i2 = std::abs(i.neg);
  const double i1 = std::abs(i.pos);
  if (i2 >= 0.05 * inom && i1 > 0.0 && i2 / i1 >= 0.1) {
    const double z2 = (v.neg * std::conj(i.neg * rot)).real() / (i2 * i2);
    r.z_ohm = z2;
    r.method = "32Q negative-sequence impedance z2 = Re[V2 * conj(I2 * 1/Z1ANG)] / |I2|^2";
    r.dir = z2 < z2f ? Direction::Forward : (z2 > z2r ? Direction::Reverse : Direction::Unknown);
    if (r.dir == Direction::Unknown) r.reason = "z2 between Z2F and Z2R";
    return r;
  }
  if (dv != nullptr && di != nullptr && std::abs(di->pos) >= 0.1 * inom) {
    const double mag = std::abs(di->pos);
    const double z1 = (dv->pos * std::conj(di->pos * rot)).real() / (mag * mag);
    r.z_ohm = z1;
    r.method = "incremental positive-sequence impedance Re[dV1 * conj(dI1 * 1/Z1ANG)] / |dI1|^2";
    r.dir = z1 < 0.0 ? Direction::Forward : Direction::Reverse;
    return r;
  }
  r.reason = "negative-sequence current too small and no prefault reference";
  return r;
}

std::string join_phases(const std::vector<int>& idx) {
  std::string s;
  for (int i : idx) s.push_back(kPhase[i]);
  return s;
}

}  // namespace

std::string to_string(FaultType t) {
  switch (t) {
    case FaultType::None: return "none";
    case FaultType::AG: return "AG";
    case FaultType::BG: return "BG";
    case FaultType::CG: return "CG";
    case FaultType::AB: return "AB";
    case FaultType::BC: return "BC";
    case FaultType::CA: return "CA";
    case FaultType::ABG: return "ABG";
    case FaultType::BCG: return "BCG";
    case FaultType::CAG: return "CAG";
    case FaultType::ABC: return "ABC";
  }
  return "none";
}

std::string describe(FaultType t) {
  switch (t) {
    case FaultType::None: return "no fault";
    case FaultType::AG: return "A-phase-to-ground";
    case FaultType::BG: return "B-phase-to-ground";
    case FaultType::CG: return "C-phase-to-ground";
    case FaultType::AB: return "A-B phase-to-phase";
    case FaultType::BC: return "B-C phase-to-phase";
    case FaultType::CA: return "C-A phase-to-phase";
    case FaultType::ABG: return "A-B-to-ground";
    case FaultType::BCG: return "B-C-to-ground";
    case FaultType::CAG: return "C-A-to-ground";
    case FaultType::ABC: return "three-phase";
  }
  return "no fault";
}

std::string to_string(Direction d) {
  switch (d) {
    case Direction::Forward: return "forward";
    case Direction::Reverse: return "reverse";
    case Direction::Unknown: return "undetermined";
  }
  return "undetermined";
}

ChannelMap map_channels(const Record& rec, const Settings& s, std::vector<std::string>* notes) {
  ChannelMap m;
  const auto& an = rec.cfg.analogs;
  const auto& dg = rec.cfg.digitals;
  auto note = [&](const std::string& n) {
    if (notes) notes->push_back(n);
  };
  auto find_analog = [&](const std::string& name) {
    for (std::size_t i = 0; i < an.size(); ++i) {
      if (alnum_upper(an[i].id) == alnum_upper(name)) return static_cast<int>(i);
    }
    return -1;
  };
  auto find_digital = [&](const std::string& name) {
    for (std::size_t i = 0; i < dg.size(); ++i) {
      if (alnum_upper(dg[i].id) == alnum_upper(name)) return static_cast<int>(i);
    }
    return -1;
  };
  struct Slot {
    const char* key;
    int* idx;
    char kind;
    char phase;
  };
  Slot slots[] = {{"IA_CH", &m.ia, 'I', 'A'}, {"IB_CH", &m.ib, 'I', 'B'},
                  {"IC_CH", &m.ic, 'I', 'C'}, {"VA_CH", &m.va, 'V', 'A'},
                  {"VB_CH", &m.vb, 'V', 'B'}, {"VC_CH", &m.vc, 'V', 'C'}};
  for (auto& sl : slots) {
    if (const auto v = s.get(sl.key)) {
      const int i = find_analog(*v);
      if (i < 0) {
        note(std::string("setting ") + sl.key + " names channel '" + *v + "' which is not in the record");
      } else {
        *sl.idx = i;
      }
    }
  }
  for (std::size_t i = 0; i < an.size(); ++i) {
    const char k = quantity_kind(an[i]);
    const char ph = phase_letter(an[i]);
    if (!k || !ph) continue;
    bool taken = false;
    for (auto& sl : slots) taken = taken || *sl.idx == static_cast<int>(i);
    if (taken) continue;
    for (auto& sl : slots) {
      if (sl.kind == k && sl.phase == ph && *sl.idx < 0) {
        *sl.idx = static_cast<int>(i);
        break;
      }
    }
  }
  if (const auto v = s.get("TRIPCH")) {
    m.trip = find_digital(*v);
    if (m.trip < 0) note("setting TRIPCH names digital '" + *v + "' which is not in the record");
  } else {
    for (const char* name : {"TRIP", "TRP", "TRIP1", "OUT101"}) {
      if ((m.trip = find_digital(name)) >= 0) break;
    }
    for (std::size_t i = 0; m.trip < 0 && i < dg.size(); ++i) {
      if (alnum_upper(dg[i].id).find("TRIP") != std::string::npos) m.trip = static_cast<int>(i);
    }
  }
  if (const auto v = s.get("BKRCH")) {
    m.breaker = find_digital(*v);
    if (m.breaker < 0) note("setting BKRCH names digital '" + *v + "' which is not in the record");
  } else {
    for (const char* name : {"52A", "52AA", "52AB", "BKRCLOSED"}) {
      if ((m.breaker = find_digital(name)) >= 0) break;
    }
    for (std::size_t i = 0; m.breaker < 0 && i < dg.size(); ++i) {
      if (alnum_upper(dg[i].id).find("52A") != std::string::npos) m.breaker = static_cast<int>(i);
    }
  }
  return m;
}

AnalysisResult analyze(const Record& rec, const Settings& s, const AnalyzerOptions& opt) {
  const auto t_begin = std::chrono::steady_clock::now();
  AnalysisResult R;
  FactSheet& F = R.facts;
  for (const auto& w : rec.warnings) F.warn(w);

  // ---------------------------------------------------------------- record
  const auto& cfg = rec.cfg;
  F.text("record.station", cfg.station, "Station name in the COMTRADE header", "CFG line 1", 3);
  F.text("record.device", cfg.device, "Recording device id", "CFG line 1", 3);
  F.number("record.rev_year", cfg.rev_year, "", 0, "COMTRADE revision year", "CFG line 1", 3);
  if (cfg.start.valid) F.text("record.start_time", cfg.start.text, "Time of first sample", "CFG", 3);
  if (cfg.trigger.valid) F.text("record.trigger_time", cfg.trigger.text, "Trigger time", "CFG", 3);
  F.text("record.data_format", to_string(cfg.format), "DAT file format", "CFG", 3);

  const double fs = rec.sample_rate_hz();
  const double f_nom = s.number("NFREQ", cfg.line_freq_hz).value_or(cfg.line_freq_hz);
  const long k_total = static_cast<long>(rec.num_samples());
  F.number("record.sample_rate_hz", fs, "Hz", 1, "Sampling rate", "CFG", 3);
  F.number("record.nominal_hz", f_nom, "Hz", std::fabs(f_nom - std::round(f_nom)) < 1e-9 ? 0 : 2,
           "Nominal system frequency", "CFG line frequency or NFREQ", 3);
  if (k_total > 0) {
    F.number("record.duration_ms", rec.time_s.back() * 1000.0, "ms", 1, "Record length", "DAT", 3);
  }
  if (!(fs > 0.0) || !(f_nom > 0.0) || k_total < 8) {
    F.missing("fault.type", "Fault type", "record has no usable sampling rate or too few samples", 1);
    return R;
  }
  const double spc_exact = fs / f_nom;
  const int n = static_cast<int>(std::lround(spc_exact));
  F.number("record.samples_per_cycle", n, "", 0, "Samples per power-system cycle", "fs / f", 3);
  if (std::fabs(spc_exact - n) > 0.01 * n) {
    F.warn("sampling rate is not an integer multiple of the nominal frequency; phasor accuracy is reduced");
  }
  if (n < 4) {
    F.missing("fault.type", "Fault type", "fewer than 4 samples per cycle", 1);
    return R;
  }
  const double cycle_s = 1.0 / f_nom;
  const double cycle_ms = 1000.0 * cycle_s;
  const double dt = 1.0 / fs;

  // ---------------------------------------------------------------- settings
  double inom = 5.0, vnom = 66.4, z1ang = 80.0;
  std::optional<double> ctr, z1mag, z2f_set, z2r_set, inom_set;
  const std::string phrot_set = alnum_upper(s.text("PHROT", ""));
  try {
    inom_set = s.number("INOM", std::nullopt);
    inom = inom_set.value_or(5.0);
    vnom = s.number("VNOM", 66.4).value_or(66.4);
    z1ang = s.number("Z1ANG", 80.0).value_or(80.0);
    ctr = s.number("CTR", std::nullopt);
    z1mag = s.number("Z1MAG", std::nullopt);
    z2f_set = s.number("Z2F", std::nullopt);
    z2r_set = s.number("Z2R", std::nullopt);
  } catch (const std::exception& e) {
    F.warn(std::string("settings: ") + e.what());
  }

  // ---------------------------------------------------------------- channels
  std::vector<std::string> notes;
  const ChannelMap cm = map_channels(rec, s, &notes);
  for (const auto& nt : notes) F.warn(nt);
  if (!cm.has_currents()) {
    F.missing("fault.type", "Fault type", "phase current channels for A, B and C were not found", 1);
    F.missing("relay.operate_ms", "Relay operate time", "phase currents not found", 1);
    return R;
  }
  bool has_v = cm.has_voltages();
  std::string no_v_reason = "no voltage channels in the record";
  // Whether the values are known to be secondary: COMTRADE 1999 states it per channel
  // (with the CT/VT ratios); the 1991 format does not.
  const bool basis_known = cfg.analogs[static_cast<std::size_t>(cm.ia)].has_ratio;
  const std::string basis = basis_known ? "secondary" : "as recorded";
  {
    std::ostringstream used;
    const auto& an = cfg.analogs;
    used << "IA=" << an[static_cast<std::size_t>(cm.ia)].id << " IB=" << an[static_cast<std::size_t>(cm.ib)].id
         << " IC=" << an[static_cast<std::size_t>(cm.ic)].id;
    if (has_v) {
      used << " VA=" << an[static_cast<std::size_t>(cm.va)].id << " VB=" << an[static_cast<std::size_t>(cm.vb)].id
           << " VC=" << an[static_cast<std::size_t>(cm.vc)].id;
    }
    F.text("record.channels_used", used.str(), "Analog channels used", "channel mapping", 3);
    if (!ctr) {
      const auto& ch = an[static_cast<std::size_t>(cm.ia)];
      if (ch.has_ratio && ch.secondary > 0.0 && ch.primary / ch.secondary > 1.0) {
        ctr = ch.primary / ch.secondary;
      }
    }
  }
  if (ctr) F.number("settings.CTR", *ctr, "", 1, "CT ratio (primary:secondary)", "settings or CFG", 3);
  if (!has_v) F.warn("no voltage channels: direction cannot be determined");

  FilterType filt = opt.filter;
  if (filt == FilterType::Cosine && n % 4 != 0) {
    filt = FilterType::Dft;
    F.warn("samples per cycle not divisible by 4; using the DFT filter instead of the cosine filter");
  }
  const int lat = filter_latency_samples(n, filt);

  auto scaled = [&](int idx) {
    std::vector<double> v = rec.analog[static_cast<std::size_t>(idx)];
    const double f = secondary_scale(cfg.analogs[static_cast<std::size_t>(idx)]);
    for (auto& x : v) x *= f;
    return v;
  };
  std::array<std::vector<double>, 3> wi{scaled(cm.ia), scaled(cm.ib), scaled(cm.ic)};
  std::array<std::vector<double>, 3> wv;
  if (has_v) {
    wv = {scaled(cm.va), scaled(cm.vb), scaled(cm.vc)};
    // Channels that exist but stay at zero (no VTs wired, common on recloser controls)
    // are treated as absent.
    for (int p = 0; p < 3 && has_v; ++p) {
      double vmax = 0.0;
      for (const double x : wv[static_cast<std::size_t>(p)]) {
        if (std::isfinite(x)) vmax = std::max(vmax, std::fabs(x));
      }
      if (vmax < 1e-3 * vnom * kSqrt2) has_v = false;
    }
    if (!has_v) {
      no_v_reason = "the voltage channels carry no signal (VTs not connected?)";
      F.warn(no_v_reason + ": direction cannot be determined");
    }
  }
  // Without an INOM setting, scale the current thresholds to the record itself: the
  // median over cycles of the largest phase RMS, which is the load in a typical record.
  if (!inom_set) {
    std::vector<double> per_cycle;
    for (long c0 = 0; c0 + n <= k_total; c0 += n) {
      double m = 0.0;
      for (int p = 0; p < 3; ++p) {
        double ss = 0.0;
        int cnt = 0;
        for (long k = c0; k < c0 + n; ++k) {
          const double v = wi[static_cast<std::size_t>(p)][static_cast<std::size_t>(k)];
          if (std::isfinite(v)) {
            ss += v * v;
            ++cnt;
          }
        }
        if (cnt > 0) m = std::max(m, std::sqrt(ss / cnt));
      }
      per_cycle.push_back(m);
    }
    const double typical = per_cycle.empty() ? 0.0 : median(per_cycle);
    if (typical > 0.0) {
      inom = typical;
      std::ostringstream w;
      w << "INOM not set: current thresholds are scaled to the record's typical current (" << std::setprecision(3)
        << typical << " A " << basis << "); set INOM to the relay's rating for fixed thresholds";
      F.warn(w.str());
    }
  }
  std::array<std::vector<cplx>, 3> pi, pv;
  for (int p = 0; p < 3; ++p) {
    pi[static_cast<std::size_t>(p)] = estimate_phasors(wi[static_cast<std::size_t>(p)], n, filt);
    if (has_v) pv[static_cast<std::size_t>(p)] = estimate_phasors(wv[static_cast<std::size_t>(p)], n, filt);
  }
  auto I = [&](int p, long k) { return pi[static_cast<std::size_t>(p)][static_cast<std::size_t>(k)]; };
  auto V = [&](int p, long k) { return pv[static_cast<std::size_t>(p)][static_cast<std::size_t>(k)]; };
  auto t_at = [&](long k) { return rec.time_s[static_cast<std::size_t>(std::clamp(k, 0L, k_total - 1))]; };
  auto max_i = [&](long k) {
    double m = 0.0;
    for (int p = 0; p < 3; ++p) m = std::max(m, std::abs(I(p, k)));
    return m;
  };

  // ---------------------------------------------------------------- phase rotation
  // The dominant sequence component of the most balanced phasor set (voltages if
  // present, else currents) shows the rotation the record really has. The analysis
  // follows the measurement; a PHROT setting that disagrees is reported, because a
  // relay set for the wrong rotation swaps positive and negative sequence.
  std::optional<bool> rot_measured;  // true = ABC
  {
    auto X = [&](int p, long k) { return has_v ? V(p, k) : I(p, k); };
    const double floor_mag = has_v ? 0.1 * vnom : 0.05 * inom;
    double best = 0.0;
    long k_best = -1;
    for (long k = lat; k < k_total; k += std::max(1, n / 4)) {
      const Sequence q = to_sequence(X(0, k), X(1, k), X(2, k), true);
      const double p1 = std::abs(q.pos), p2 = std::abs(q.neg), p0 = std::abs(q.zero);
      const double tot = p1 + p2 + p0;
      if (!std::isfinite(tot) || std::max(p1, p2) < floor_mag) continue;
      const double dominance = std::max(p1, p2) / tot;
      if (dominance > best) {
        best = dominance;
        k_best = k;
      }
    }
    if (k_best >= 0 && best >= 0.8) {
      const Sequence q = to_sequence(X(0, k_best), X(1, k_best), X(2, k_best), true);
      rot_measured = std::abs(q.pos) >= std::abs(q.neg);
    }
  }
  const bool abc = rot_measured ? *rot_measured : phrot_set != "ACB";
  F.text("record.rotation", abc ? "ABC" : "ACB", "Phase rotation used for sequence components",
         rot_measured ? std::string("dominant sequence of the most balanced ") + (has_v ? "voltage" : "current") + " phasors"
                      : std::string(phrot_set.empty() ? "default (not measurable)" : "PHROT setting (not measurable)"),
         2);
  if (phrot_set == "ABC" || phrot_set == "ACB") {
    F.text("settings.PHROT", phrot_set, "Phase rotation setting", "settings PHROT", 2);
    if (rot_measured) {
      const std::string meas = *rot_measured ? "ABC" : "ACB";
      if (meas == phrot_set) {
        F.text("assessment.rotation", "matches the PHROT setting", "Phase rotation check", "measured vs. PHROT", 2);
      } else {
        F.text("assessment.rotation",
               "the record shows " + meas + " rotation but the relay is set PHROT := " + phrot_set,
               "Phase rotation check", "measured vs. PHROT", 1);
        F.warn("phase rotation: the record shows " + meas + " but PHROT := " + phrot_set +
               "; a relay using that setting computes positive and negative sequence swapped");
      }
    }
  }

  // ---------------------------------------------------------------- inception
  std::optional<long> onset;
  for (int p = 0; p < 3; ++p) {
    if (auto k = find_onset(wi[static_cast<std::size_t>(p)], n, 0.05 * inom * kSqrt2)) {
      onset = onset ? std::min(*onset, *k) : *k;
    }
    if (has_v) {
      if (auto k = find_onset(wv[static_cast<std::size_t>(p)], n, 0.05 * vnom * kSqrt2)) {
        onset = onset ? std::min(*onset, *k) : *k;
      }
    }
  }
  // A disturbance where current falls is a clearing, not an inception: the record
  // started with the fault already present.
  bool inception_in_record = false;
  if (onset && *onset <= n + 2) {
    // The cycle-to-cycle difference is already large at the first sample where it can
    // be computed: the disturbance (typically decaying DC) began before the record.
    F.warn("record starts during a disturbance; treating the fault as present before the record");
  } else if (onset) {
    const long before = *onset - 1;
    const long after = std::min(*onset + lat, k_total - 1);
    if (before >= lat) {
      // Healthy load is balanced, so unbalance (I0 + I2) appearing marks an inception
      // and unbalance disappearing marks a clearing, even for high-resistance faults
      // that barely change the phase-current magnitudes. Balanced faults fall back
      // to I1 and V1.
      const Sequence sb = to_sequence(I(0, before), I(1, before), I(2, before), abc);
      const Sequence sa = to_sequence(I(0, after), I(1, after), I(2, after), abc);
      const double ub = std::abs(sb.zero) + std::abs(sb.neg);
      const double ua = std::abs(sa.zero) + std::abs(sa.neg);
      const bool unbalance_rise = ua > ub + std::max(0.05 * inom, 0.25 * ub);
      const bool unbalance_fall = ub > ua + std::max(0.05 * inom, 0.25 * ua);
      if (unbalance_rise) {
        inception_in_record = true;
      } else if (unbalance_fall) {
        inception_in_record = false;
      } else {
        const double pb = std::abs(sb.pos);
        const double pa = std::abs(sa.pos);
        bool v1_drop = false;
        if (has_v) {
          const double vb1 = std::abs(to_sequence(V(0, before), V(1, before), V(2, before), abc).pos);
          const double va1 = std::abs(to_sequence(V(0, after), V(1, after), V(2, after), abc).pos);
          v1_drop = va1 < 0.95 * vb1;
        }
        const bool i1_rise = pa > 1.1 * pb && pa > pb + 0.1 * inom;
        inception_in_record = !(pa < 0.8 * pb) && (i1_rise || v1_drop);
      }
    } else {
      inception_in_record = true;  // too early to compare; treat as inception
      F.warn("disturbance within the first cycle of the record; prefault reference is incomplete");
    }
  }

  const double max_start = max_i(std::min(static_cast<long>(lat), k_total - 1));
  if (!onset && !(max_start > 1.5 * inom)) {
    F.flag("fault.detected", false, "Fault detected", "cycle-to-cycle difference", 1);
    F.missing("fault.type", "Fault type", "no disturbance detected in the record", 1);
    F.missing("relay.operate_ms", "Relay operate time", "no fault detected", 1);
    R.facts.meta("analyze_ms", 0.0);
    return R;
  }

  // A disturbance that starts soon after the breaker closes may be an energization
  // (motor start, transformer inrush, line pickup) rather than a fault. A switch onto
  // a fault looks the same to this analysis, so the record is still analyzed and the
  // coincidence is reported.
  if (onset && cm.breaker >= 0) {
    const auto& d52 = rec.digital[static_cast<std::size_t>(cm.breaker)];
    const auto k_close = first_rising(d52, std::max(0L, *onset - 10L * n));
    if (k_close && *k_close <= *onset + n) {
      const double after = (t_at(*onset) - t_at(*k_close)) * 1000.0;
      F.number("breaker.close_ms", t_at(*k_close) * 1000.0, "ms", 2, "Breaker closed (52A rose), from the first sample",
               "52A 0->1 transition", 2);
      F.number("fault.after_breaker_close_ms", after, "ms", 2, "Disturbance began after the breaker closed",
               "onset - 52A rising edge", 1);
      F.warn("the disturbance began just after the breaker closed: it may be an energization (motor start, inrush) "
             "rather than a fault; a switch onto a fault would look the same");
    }
  }

  const long k_on = inception_in_record ? *onset : 0;
  R.inception_in_record = inception_in_record;
  if (inception_in_record) {
    R.inception_s = t_at(k_on);
    F.number("fault.inception_ms", t_at(k_on) * 1000.0, "ms", 2,
             "Fault inception, measured from the first sample",
             "first sample where |x[k] - x[k-N]| leaves the noise floor", 2);
    double trig = 0.0;
    if (rec.trigger_offset_s(trig)) {
      F.number("fault.inception_from_trigger_ms", (t_at(k_on) - trig) * 1000.0, "ms", 2,
               "Fault inception relative to the record trigger", "inception - trigger", 2);
    }
  } else {
    F.missing("fault.inception_ms", "Fault inception", "the fault was already present when the record started", 1);
    F.warn("fault inception is not in the record; operate time cannot be measured");
  }

  // ---------------------------------------------------------------- trip & breaker digitals
  std::optional<long> k_trip;
  bool trip_at_start = false;
  if (cm.trip >= 0) {
    const auto& d = rec.digital[static_cast<std::size_t>(cm.trip)];
    const long from = inception_in_record ? std::max(0L, k_on - n / 2) : 0L;
    k_trip = first_rising(d, from);
    if (!k_trip && !d.empty() && d.front()) {
      k_trip = 0;
      trip_at_start = true;
      F.warn("trip output was already asserted at the start of the record");
    }
  }

  // ---------------------------------------------------------------- breaker interruption per pole
  double noise_i = 0.0;
  for (int p = 0; p < 3; ++p) noise_i = std::max(noise_i, delta_sigma(wi[static_cast<std::size_t>(p)], n));
  const double thr_zero = std::max(0.02 * inom * kSqrt2, 6.0 * noise_i);
  std::array<std::optional<long>, 3> k_clear{};
  std::array<bool, 3> carried{};
  const long scan_from = k_on + 1;
  // A pole only counts for breaker timing if it carried meaningful current: a lightly
  // loaded healthy phase (RMS below 0.1 x INOM) never produces a clean interruption.
  const double carry_min_rms = std::max(0.1 * inom, 2.5 * thr_zero / kSqrt2);
  for (int p = 0; p < 3; ++p) {
    const auto& x = wi[static_cast<std::size_t>(p)];
    double rms = 0.0;
    for (long k = k_on + lat; k < std::min(k_total, k_on + lat + 2L * n); ++k) {
      const cplx z = I(p, k);
      if (finite(z)) rms = std::max(rms, std::abs(z));
    }
    carried[static_cast<std::size_t>(p)] = rms > carry_min_rms;
    if (carried[static_cast<std::size_t>(p)]) {
      k_clear[static_cast<std::size_t>(p)] = find_interruption(x, n, scan_from, thr_zero);
    }
  }
  bool breaker_opened = true;
  bool any_carried = false;
  std::optional<long> k_first_clear, k_last_clear;
  for (int p = 0; p < 3; ++p) {
    if (!carried[static_cast<std::size_t>(p)]) continue;
    any_carried = true;
    const auto& kc = k_clear[static_cast<std::size_t>(p)];
    if (!kc) {
      breaker_opened = false;
      continue;
    }
    k_first_clear = k_first_clear ? std::min(*k_first_clear, *kc) : *kc;
    k_last_clear = k_last_clear ? std::max(*k_last_clear, *kc) : *kc;
  }
  breaker_opened = breaker_opened && any_carried && k_last_clear.has_value();

  // ---------------------------------------------------------------- phasor snapshots
  const long k_pre = k_on - 1;
  const bool have_pre = inception_in_record && k_pre >= lat;
  long k_snap = inception_in_record ? k_on + lat + n / 4 : std::min(static_cast<long>(lat + n / 4), k_total - 1);
  if (breaker_opened && k_first_clear && k_snap >= *k_first_clear) {
    k_snap = std::max(static_cast<long>(lat), *k_first_clear - 1);
    F.warn("fault shorter than the phasor window; fault magnitudes are underestimated");
  }
  k_snap = std::min(k_snap, k_total - 1);
  if (k_snap < lat) {
    F.missing("fault.type", "Fault type", "record too short for a full phasor window", 1);
    return R;
  }

  std::array<cplx, 3> i_pre{}, i_flt{}, v_pre{}, v_flt{};
  for (int p = 0; p < 3; ++p) {
    i_flt[static_cast<std::size_t>(p)] = I(p, k_snap);
    if (have_pre) i_pre[static_cast<std::size_t>(p)] = I(p, k_pre);
    if (has_v) {
      v_flt[static_cast<std::size_t>(p)] = V(p, k_snap);
      if (have_pre) v_pre[static_cast<std::size_t>(p)] = V(p, k_pre);
    }
  }
  // ---------------------------------------------------------------- fault end (if the local breaker did not open)
  std::optional<long> k_end;
  std::string end_cause;
  bool short_fault = false;
  auto unbalance_at = [&](long k) {
    const Sequence q = to_sequence(I(0, k), I(1, k), I(2, k), abc);
    return std::abs(q.zero) + std::abs(q.neg);
  };
  if (breaker_opened) {
    k_end = k_last_clear;
    end_cause = "local breaker interrupted the current";
  } else {
    // The fault was not interrupted locally. Look for the step where it is removed
    // elsewhere: voltages recover in a clean step, while currents may carry a
    // decaying DC transient, so voltages are tried first. A candidate counts only if
    // the currents then settle back near their prefault values.
    std::array<double, 3> lim{};
    for (int p = 0; p < 3; ++p) {
      const double pre = std::abs(i_pre[static_cast<std::size_t>(p)]);
      const double flt = std::abs(i_flt[static_cast<std::size_t>(p)]);
      lim[static_cast<std::size_t>(p)] = pre + 0.2 * std::max(flt - pre, 0.0) + 0.05 * inom;
    }
    // Without a prefault reference (the record starts inside the fault), an unbalanced
    // fault has ended when the unbalance (I0 + I2) collapses; balanced faults cannot be
    // judged this way.
    const double u_flt = unbalance_at(k_snap);
    double i_flt_max = 0.0;
    for (int p = 0; p < 3; ++p) i_flt_max = std::max(i_flt_max, std::abs(i_flt[static_cast<std::size_t>(p)]));
    const bool can_use_unbalance = std::isfinite(u_flt) && u_flt >= 0.2 * i_flt_max && u_flt > 0.0;
    auto settles_after = [&](long k_step) {
      long hold = 0;
      for (long k = k_step + lat; k < std::min(k_total, k_step + lat + 4L * n); ++k) {
        bool ok = true;
        if (have_pre) {
          for (int p = 0; p < 3 && ok; ++p) {
            const cplx z = I(p, k);
            ok = finite(z) && std::abs(z) <= lim[static_cast<std::size_t>(p)];
          }
        } else {
          const double u = unbalance_at(k);
          ok = can_use_unbalance && std::isfinite(u) && u <= std::max(0.3 * u_flt, 0.05 * inom);
        }
        hold = ok ? hold + 1 : 0;
        if (hold >= n / 2) return true;
      }
      return false;
    };
    auto earliest_step = [&](const std::array<std::vector<double>, 3>& w, double floor, long from) {
      std::optional<long> best;
      for (int p = 0; p < 3; ++p) {
        if (auto k = find_step_after_quiet(w[static_cast<std::size_t>(p)], n, from, floor)) {
          best = best ? std::min(*best, *k) : *k;
        }
      }
      return best;
    };
    // A fault shorter than the phasor window leaves no quiet stretch between its start
    // and its end, so a step search cannot see the end. Compare each sample with the
    // same point of the last prefault cycle instead (superimposed current): the fault
    // has ended once a whole cycle looks like the prefault cycle again.
    std::optional<long> k_super;
    if (have_pre && k_on >= n) {
      auto superimposed = [&](long k) {
        double e = 0.0;
        for (int p = 0; p < 3; ++p) {
          const auto& x = wi[static_cast<std::size_t>(p)];
          const long ref = k_on - n + ((k - k_on) % n);
          const double v = x[static_cast<std::size_t>(k)] - x[static_cast<std::size_t>(ref)];
          if (std::isfinite(v)) e = std::max(e, std::fabs(v));
        }
        return e;
      };
      double e_peak = 0.0;
      for (long k = k_on; k < std::min(k_total, k_on + 4L * n); ++k) e_peak = std::max(e_peak, superimposed(k));
      const double thr_off = std::max(0.25 * e_peak, 6.0 * noise_i);
      for (long k = k_on + n / 4; k + n <= k_total; ++k) {
        bool quiet = true;
        for (long j = k; j < k + n && quiet; ++j) quiet = superimposed(j) < thr_off;
        if (quiet) {
          k_super = k;
          break;
        }
      }
    }
    if (k_super && *k_super <= k_snap + n / 4) {
      // Short fault (it ends before a full phasor window plus a quarter cycle fits
      // inside it): re-take the fault snapshot where the partial-window phasor
      // deviates most from prefault, and say that the magnitudes are underestimated.
      k_end = k_super;
      short_fault = true;
      long k_best = k_snap;
      double best = -1.0;
      for (long k = k_on + n / 2; k <= std::min(*k_end + lat, k_total - 1); ++k) {
        double dev = 0.0;
        for (int p = 0; p < 3; ++p) {
          const cplx z = I(p, k) - i_pre[static_cast<std::size_t>(p)];
          if (finite(z)) dev = std::max(dev, std::abs(z));
        }
        if (dev > best) {
          best = dev;
          k_best = k;
        }
      }
      k_snap = k_best;
      for (int p = 0; p < 3; ++p) {
        i_flt[static_cast<std::size_t>(p)] = I(p, k_snap);
        if (has_v) v_flt[static_cast<std::size_t>(p)] = V(p, k_snap);
      }
      std::ostringstream w;
      w << "fault lasted " << std::setprecision(2) << (t_at(*k_end) - t_at(k_on)) / cycle_s
        << " cycles, shorter than the phasor window: magnitudes come from a partial window and are underestimated";
      F.warn(w.str());
    }
    for (int pass = 0; pass < 2 && !k_end; ++pass) {
      const bool use_v = pass == 0;
      if (use_v && !has_v) continue;
      long from = k_on + lat;
      for (int attempt = 0; attempt < 8 && !k_end; ++attempt) {
        const auto k_step = use_v ? earliest_step(wv, 0.05 * vnom * kSqrt2, from)
                                  : earliest_step(wi, 0.05 * inom * kSqrt2, from);
        if (!k_step) break;
        if (settles_after(*k_step)) {
          k_end = *k_step;
        } else {
          from = *k_step + n;  // e.g. an evolving fault; keep looking
        }
      }
    }
    if (!k_end && k_super) k_end = k_super;
    if (k_end) {
      // Tripped first and the local breaker never interrupted: other protection cleared it.
      // Otherwise the fault may also have cleared itself.
      end_cause = (k_trip && *k_trip <= *k_end)
                      ? "current returned to load without a local interruption (cleared by other protection)"
                      : "current returned to load before any local trip (self-cleared or cleared by other protection)";
    } else {
      end_cause = "fault current still present at the end of the record";
    }
  }
  if (k_end) R.fault_end_s = t_at(*k_end);

  // Incremental quantities need a healthy reference. Prefault is best; when the record
  // starts inside the fault and the fault ends within it, the post-fault load is used.
  bool have_ref = have_pre;
  if (!have_pre && k_end && !breaker_opened) {
    const long k_ref = *k_end + lat + n / 2;
    if (k_ref < k_total) {
      for (int p = 0; p < 3; ++p) {
        i_pre[static_cast<std::size_t>(p)] = I(p, k_ref);
        if (has_v) v_pre[static_cast<std::size_t>(p)] = V(p, k_ref);
      }
      have_ref = true;
      F.warn("record starts inside the fault: incremental quantities use the post-fault load as the reference");
    }
  }
  if (!have_ref) F.warn("no prefault phasors: classification uses total (not incremental) currents");
  F.text("fault.end_cause", end_cause, "How the fault current ended", "per-pole zero-current detection", 1);

  // ---------------------------------------------------------------- classification
  std::array<cplx, 3> d_i{}, d_v{};
  for (int p = 0; p < 3; ++p) {
    d_i[static_cast<std::size_t>(p)] = i_flt[static_cast<std::size_t>(p)] - i_pre[static_cast<std::size_t>(p)];
    d_v[static_cast<std::size_t>(p)] = v_flt[static_cast<std::size_t>(p)] - v_pre[static_cast<std::size_t>(p)];
  }
  const Classification cls = classify(d_i, inom, abc);
  if (!cls.note.empty()) F.warn("classification: " + cls.note);
  R.fault_type = cls.type;
  R.fault_detected = cls.type != FaultType::None;
  F.flag("fault.detected", R.fault_detected, "Fault detected", "incremental current above 0.1 x INOM", 1);
  if (!R.fault_detected) {
    F.missing("fault.type", "Fault type", "disturbance found but incremental current is below the fault threshold", 1);
  } else {
    F.text("fault.type", to_string(cls.type), "Fault type", "incremental phase-to-phase currents and angle of I2/I0", 1);
    F.text("fault.type_description", describe(cls.type), "Fault type in words", "", 1);
    F.flag("fault.ground_involved", cls.ground, "Ground involved", "3I0 relative to the largest incremental phase current", 2);
    if (cls.i2_i0_angle_deg) {
      F.number("fault.i2_i0_angle_deg", *cls.i2_i0_angle_deg, "deg", 1, "Angle of I2 relative to I0",
               "incremental sequence currents", 2);
    }
  }

  // ---------------------------------------------------------------- magnitudes
  // Steady-state snapshot: the last full phasor window before the fault ends, after
  // the DC offset has decayed. Classification and direction use the early snapshot.
  long k_steady = k_snap;
  {
    long last_fault_sample = k_total - 1;
    if (breaker_opened && k_first_clear) {
      last_fault_sample = *k_first_clear - 1;
    } else if (k_end) {
      last_fault_sample = *k_end - std::max(2, n / 8);  // margin for step-detection error
    }
    if (last_fault_sample > k_snap && !short_fault) k_steady = last_fault_sample;
  }
  std::array<cplx, 3> i_ss{}, v_ss{};
  for (int p = 0; p < 3; ++p) {
    i_ss[static_cast<std::size_t>(p)] = I(p, k_steady);
    if (has_v) v_ss[static_cast<std::size_t>(p)] = V(p, k_steady);
  }
  const Sequence sq_i = to_sequence(i_ss[0], i_ss[1], i_ss[2], abc);
  const Sequence sq_i_snap = to_sequence(i_flt[0], i_flt[1], i_flt[2], abc);
  int max_phase = 0;
  for (int p = 1; p < 3; ++p) {
    if (std::abs(i_ss[static_cast<std::size_t>(p)]) > std::abs(i_ss[static_cast<std::size_t>(max_phase)])) max_phase = p;
  }
  const double i_max = std::abs(i_ss[static_cast<std::size_t>(max_phase)]);
  const std::string snap_method = std::string(to_string(filt)) + " filter phasor, last full window before the fault ended";
  F.number("fault.steady_at_ms", t_at(k_steady) * 1000.0, "ms", 2, "Time of the steady-state magnitude snapshot",
           "end of the last full phasor window inside the fault", 3);
  for (int p = 0; p < 3; ++p) {
    const std::string ph(1, static_cast<char>(std::tolower(kPhase[p])));
    F.number("fault.i" + ph + "_rms_a", std::abs(i_ss[static_cast<std::size_t>(p)]), "A", 2,
             std::string("Phase ") + kPhase[p] + " fault current (" + basis + " RMS)", snap_method, 2);
    if (have_pre) {
      F.number("prefault.i" + ph + "_rms_a", std::abs(i_pre[static_cast<std::size_t>(p)]), "A", 2,
               std::string("Phase ") + kPhase[p] + " prefault current (" + basis + " RMS)", "phasor before inception", 2);
    }
    if (has_v) {
      F.number("fault.v" + ph + "_rms_v", std::abs(v_ss[static_cast<std::size_t>(p)]), "V", 1,
               std::string("Phase ") + kPhase[p] + " fault voltage (" + basis + " RMS)", snap_method, 2);
      if (have_pre) {
        F.number("prefault.v" + ph + "_rms_v", std::abs(v_pre[static_cast<std::size_t>(p)]), "V", 1,
                 std::string("Phase ") + kPhase[p] + " prefault voltage (" + basis + " RMS)", "phasor before inception", 3);
      }
    }
  }
  F.text("fault.max_phase", std::string(1, kPhase[max_phase]), "Phase with the largest fault current", snap_method, 1);
  F.number("fault.max_phase_rms_a", i_max, "A", 2, "Largest phase fault current (" + basis + " RMS)", snap_method, 1);
  if (ctr) {
    F.number("fault.max_phase_rms_primary_a", i_max * *ctr, "A", 0,
             "Largest phase fault current (primary RMS)", "secondary x CTR", 1);
  }
  F.number("fault.3i0_a", 3.0 * std::abs(sq_i.zero), "A", 2, "Residual current 3I0 (" + basis + " RMS)", snap_method, 2);
  F.number("fault.i1_a", std::abs(sq_i.pos), "A", 2, "Positive-sequence current I1 (" + basis + ")", snap_method, 2);
  F.number("fault.i2_a", std::abs(sq_i.neg), "A", 2, "Negative-sequence current I2 (" + basis + ")", snap_method, 2);

  // Instantaneous peak (includes DC offset).
  {
    const long k_stop = k_end ? *k_end : k_total;
    double peak = 0.0;
    int peak_phase = 0;
    for (int p = 0; p < 3; ++p) {
      const auto& x = wi[static_cast<std::size_t>(p)];
      for (long k = k_on; k < k_stop; ++k) {
        const double v = std::fabs(x[static_cast<std::size_t>(k)]);
        if (std::isfinite(v) && v > peak) {
          peak = v;
          peak_phase = p;
        }
      }
    }
    F.number("fault.peak_a", peak, "A", 2, "Largest instantaneous fault current (" + basis + ", includes DC offset)",
             "max |i(t)| during the fault", 2);
    F.text("fault.peak_phase", std::string(1, kPhase[peak_phase]), "Phase with the largest instantaneous current",
           "max |i(t)|", 2);
    const double rms_peak_phase = std::abs(i_ss[static_cast<std::size_t>(peak_phase)]);
    if (rms_peak_phase > 0.0) {
      F.number("fault.asymmetry", peak / (kSqrt2 * rms_peak_phase), "", 2,
               "Peak divided by symmetrical peak (DC offset indicator)", "peak / (sqrt(2) x RMS)", 3);
    }
  }

  // ---------------------------------------------------------------- direction
  if (R.fault_detected && has_v) {
    const Sequence sq_v = to_sequence(v_flt[0], v_flt[1], v_flt[2], abc);
    const Sequence sq_di = to_sequence(d_i[0], d_i[1], d_i[2], abc);
    const Sequence sq_dv = to_sequence(d_v[0], d_v[1], d_v[2], abc);
    const double z2f = z2f_set.value_or(z1mag ? 0.5 * *z1mag : 0.0);
    const double z2r = z2r_set.value_or(z2f + 0.1);
    const DirectionResult dr = decide_direction(sq_v, sq_i_snap, have_ref ? &sq_dv : nullptr,
                                                have_ref ? &sq_di : nullptr, z1ang, z2f, z2r, inom);
    R.direction = dr.dir;
    F.text("fault.direction", to_string(dr.dir), "Fault direction relative to the relay", dr.method, 1);
    if (dr.z_ohm) {
      const bool is_32q = dr.method.rfind("32Q", 0) == 0;
      F.number(is_32q ? "fault.z2_ohm" : "fault.dz1_ohm", *dr.z_ohm, "ohm", 2,
               is_32q ? "Negative-sequence directional impedance z2 (secondary ohms)"
                      : "Incremental positive-sequence impedance (secondary ohms)",
               dr.method, 2);
      if (is_32q) {
        F.number("settings.Z2F", z2f, "ohm", 2, "Forward threshold Z2F", "settings (default 0.5 x Z1MAG)", 2);
        F.number("settings.Z2R", z2r, "ohm", 2, "Reverse threshold Z2R", "settings (default Z2F + 0.1)", 2);
      }
    }
    if (!dr.reason.empty()) F.warn("direction: " + dr.reason);
  } else if (R.fault_detected) {
    F.missing("fault.direction", "Fault direction", no_v_reason, 1);
  }

  // ---------------------------------------------------------------- relay trip timing
  if (k_trip) {
    R.trip_s = t_at(*k_trip);
    F.text("relay.trip_channel", cfg.digitals[static_cast<std::size_t>(cm.trip)].id, "Trip output channel", "digital channel", 3);
    F.number("relay.trip_ms", t_at(*k_trip) * 1000.0, "ms", 2, "Trip output asserted, from the first sample",
             "first 0->1 transition of the trip channel", 3);
    if (inception_in_record && !trip_at_start) {
      const double op = t_at(*k_trip) - t_at(k_on);
      F.number("relay.operate_ms", op * 1000.0, "ms", 2, "Relay operate time (inception to trip output)",
               "trip edge - inception", 1);
      F.number("relay.operate_cycles", op * 1000.0 / cycle_ms, "cycles", 2, "Relay operate time in cycles",
               "operate time x frequency", 1);
      if (op < 0.0) F.warn("trip asserted before the detected fault inception");
    } else {
      F.missing("relay.operate_ms", "Relay operate time",
                trip_at_start ? "trip was already asserted when the record started"
                              : "fault inception is not in the record",
                1);
    }
  } else if (cm.trip < 0) {
    F.missing("relay.operate_ms", "Relay operate time", "no trip output channel (TRIP/OUT101) in the record", 1);
  } else {
    F.flag("relay.tripped", false, "Relay trip output asserted", "trip channel never rose", 1);
  }
  // A trip that comes more than half a cycle after the fault current has gone found
  // no fault to clear, whatever element issued it.
  const bool trip_after_end = R.fault_detected && k_trip && !trip_at_start && k_end && !breaker_opened &&
                              *k_trip > *k_end + n / 2;
  if (trip_after_end) {
    const double late = t_at(*k_trip) - t_at(*k_end);
    F.number("relay.trip_after_fault_end_ms", late * 1000.0, "ms", 2, "Trip asserted after the fault had ended",
             "trip edge - fault end", 1);
    F.number("relay.trip_after_fault_end_cycles", late * 1000.0 / cycle_ms, "cycles", 2,
             "Trip asserted after the fault had ended, in cycles", "delay x frequency", 1);
  }

  // ---------------------------------------------------------------- breaker timing
  std::optional<bool> a52_opened;  // nullopt when the record has no 52A channel
  if (cm.breaker >= 0) {
    F.text("breaker.status_channel", cfg.digitals[static_cast<std::size_t>(cm.breaker)].id,
           "Breaker status channel", "digital channel", 3);
    const auto& d = rec.digital[static_cast<std::size_t>(cm.breaker)];
    const long from = k_trip ? *k_trip : k_on;
    const auto k52 = first_falling(d, from);
    a52_opened = k52.has_value();
    F.flag("breaker.52a_opened", *a52_opened, "52A contact opened during the record", "52A 1->0 transition", 2);
    if (k52 && k_trip) {
      F.number("breaker.52a_open_after_trip_ms", (t_at(*k52) - t_at(*k_trip)) * 1000.0, "ms", 2,
               "52A contact opened, after the trip", "52A falling edge - trip edge", 2);
    }
  }
  // Current stopping while 52A stays closed means other breakers removed the source.
  const bool other_breakers = breaker_opened && a52_opened.has_value() && !*a52_opened;
  if (other_breakers) {
    end_cause = "current interrupted by other breakers while the local 52A stayed closed";
    F.text("fault.end_cause", end_cause, "How the fault current ended", "per-pole zero detection and 52A", 1);
  }
  if (breaker_opened && !other_breakers) {
    R.breaker_clear_s = t_at(*k_last_clear);
    std::vector<int> order;
    for (int p = 0; p < 3; ++p) {
      if (k_clear[static_cast<std::size_t>(p)]) order.push_back(p);
    }
    std::sort(order.begin(), order.end(), [&](int x, int y) {
      return *k_clear[static_cast<std::size_t>(x)] < *k_clear[static_cast<std::size_t>(y)];
    });
    F.text("breaker.pole_clearing_order", join_phases(order), "Order in which poles interrupted",
           "first sample of a zero-current cycle per phase", 2);
    F.text("breaker.first_pole", std::string(1, kPhase[order.front()]), "First pole to clear", "per-phase zero detection", 2);
    if (k_trip) {
      for (int p : order) {
        const std::string ph(1, static_cast<char>(std::tolower(kPhase[p])));
        F.number("breaker.clear_" + ph + "_after_trip_ms",
                 (t_at(*k_clear[static_cast<std::size_t>(p)]) - t_at(*k_trip)) * 1000.0, "ms", 2,
                 std::string("Phase ") + kPhase[p] + " current interrupted, after the trip", "per-phase zero detection", 2);
      }
      const double interrupt = t_at(*k_last_clear) - t_at(*k_trip);
      F.number("breaker.interrupt_ms", interrupt * 1000.0, "ms", 2,
               "Breaker interrupting time (trip to last pole cleared)", "last pole zero - trip edge", 1);
      F.number("breaker.interrupt_cycles", interrupt * 1000.0 / cycle_ms, "cycles", 2,
               "Breaker interrupting time in cycles", "interrupting time x frequency", 1);
    }
  } else {
    F.missing("breaker.interrupt_ms", "Breaker interrupting time",
              other_breakers ? "the local breaker did not open (52A stayed closed)"
              : k_trip       ? "current was not interrupted by the local breaker within the record"
                             : "no local trip and no local interruption",
              1);
  }
  if (inception_in_record && k_end) {
    const double dur = t_at(*k_end) - t_at(k_on);
    F.number("fault.duration_ms", dur * 1000.0, "ms", 2, "Fault duration (inception to current removed)",
             breaker_opened ? "last pole zero - inception" : "forward cycle difference", 1);
    F.number("fault.duration_cycles", dur * 1000.0 / cycle_ms, "cycles", 2, "Fault duration in cycles",
             "duration x frequency", 1);
  } else {
    F.missing("fault.duration_ms", "Fault duration",
              !inception_in_record ? "fault inception is not in the record" : "fault current did not end within the record", 1);
  }

  // ---------------------------------------------------------------- expected relay behaviour
  struct ToC {
    std::string name;
    InverseTimeElement el;
    bool ground;
    std::optional<double> t_op = std::nullopt;  // seconds from the first sample
  };
  std::optional<double> p50, d50;
  std::vector<ToC> tocs;
  std::string dir_mode = "N";
  double tol_cyc = 1.5, tol_pct = 5.0, bkr_max = 5.0;
  bool any_element = false;
  try {
    p50 = s.number("50P1P", std::nullopt);
    d50 = s.number("50P1D", 0.0);
    for (const char* prefix : {"51P1", "51G1"}) {
      const std::string pre(prefix);
      const auto pk = s.number(pre + "P", std::nullopt);
      const auto td = s.number(pre + "TD", std::nullopt);
      const auto cv = parse_curve(s.text(pre + "C", "U3"));
      if (pk && td && cv) {
        tocs.push_back(ToC{pre, InverseTimeElement(*cv, *pk, *td, s.flag(pre + "RS", false)), pre == "51G1"});
        F.number("settings." + pre + "P", *pk, "A", 2, pre + " pickup (secondary)", "settings", 2);
        F.text("settings." + pre + "C", curve_name(*cv), pre + " curve", curve_def(*cv).description, 2);
        F.number("settings." + pre + "TD", *td, "", 2, pre + " time dial", "settings", 2);
      } else if (pk && !cv) {
        F.warn("unknown curve for " + pre + ": " + s.text(pre + "C", ""));
      }
    }
    if (p50) {
      F.number("settings.50P1P", *p50, "A", 2, "50P1 pickup (secondary)", "settings", 2);
      F.number("settings.50P1D", d50.value_or(0.0), "cycles", 2, "50P1 definite-time delay", "settings", 3);
    }
    dir_mode = alnum_upper(s.text("DIR", "N"));
    tol_cyc = s.number("TOLCYC", 1.5).value_or(1.5);
    tol_pct = s.number("TOLPCT", 5.0).value_or(5.0);
    bkr_max = s.number("BKRMAX", 5.0).value_or(5.0);
  } catch (const std::exception& e) {
    F.warn(std::string("settings: ") + e.what());
  }
  any_element = p50.has_value() || !tocs.empty();
  F.text("settings.DIR", dir_mode == "F" ? "forward" : (dir_mode == "R" ? "reverse" : "non-directional"),
         "Directional supervision setting (DIR) of the overcurrent elements", "settings DIR", 2);

  bool blocked = false;
  if (dir_mode == "F" || dir_mode == "R") {
    const Direction want = dir_mode == "F" ? Direction::Forward : Direction::Reverse;
    if (R.direction == Direction::Unknown) {
      F.warn("direction undetermined: directional supervision not applied to the prediction");
    } else {
      blocked = R.direction != want;
    }
  }

  std::optional<ElementPrediction> expected;
  if (R.fault_detected && inception_in_record && any_element && !blocked) {
    const long k_stop = breaker_opened ? *k_first_clear : (k_end ? *k_end : k_total);
    std::optional<double> t50;
    for (long k = k_on; k < k_stop; ++k) {
      double mag_ph = 0.0;
      bool ok = true;
      for (int p = 0; p < 3; ++p) {
        const cplx z = I(p, k);
        ok = ok && finite(z);
        mag_ph = std::max(mag_ph, std::abs(z));
      }
      if (!ok) continue;
      const double mag_g = std::abs(I(0, k) + I(1, k) + I(2, k));
      if (p50 && !t50 && mag_ph >= *p50) t50 = t_at(k) + d50.value_or(0.0) * cycle_s;
      for (auto& toc : tocs) {
        if (toc.t_op) continue;
        // The magnitude at sample k is held for one sample period [t_k, t_k + dt).
        if (auto frac = toc.el.step(toc.ground ? mag_g : mag_ph, dt)) toc.t_op = t_at(k) + *frac * dt;
      }
    }
    auto consider = [&](const std::string& name, double t_from_on, bool extrapolated) {
      if (!expected || t_from_on < expected->time_s) expected = ElementPrediction{name, t_from_on, extrapolated};
    };
    if (t50) consider("50P1", *t50 - t_at(k_on), false);
    const double m_ph_steady = i_max;
    const double m_g_steady = 3.0 * std::abs(sq_i.zero);
    for (auto& toc : tocs) {
      const std::string tag = toc.ground ? "51g" : "51p";
      const double steady = toc.ground ? m_g_steady : m_ph_steady;
      const double mult = steady / toc.el.pickup();
      F.number("expected.m_" + tag, mult, "", 2, toc.name + " multiple of pickup at the fault current",
               "fault magnitude / pickup", 2);
      if (mult > 1.0) {
        F.number("expected.curve_time_" + tag + "_ms",
                 operate_time_s(toc.el.curve(), toc.el.time_dial(), mult) * 1000.0, "ms", 2,
                 toc.name + " curve time at constant current", "TD x (A + B / (M^p - 1))", 2);
      }
      if (toc.t_op) {
        consider(toc.name, *toc.t_op - t_at(k_on), false);
      } else if (mult > 1.0) {
        const double remaining =
            (1.0 - toc.el.travel()) * operate_time_s(toc.el.curve(), toc.el.time_dial(), mult);
        consider(toc.name, t_at(k_stop) - t_at(k_on) + remaining, true);
      }
    }
  }

  if (expected) {
    R.expected = expected;
    F.text("expected.element", expected->element, "Element expected to trip first", "replay of measured currents", 1);
    F.number("expected.operate_ms", expected->time_s * 1000.0, "ms", 2,
             "Expected operate time from the settings (from inception)",
             expected->extrapolated ? "IEEE C37.112 integral, extrapolated at the steady fault current"
                                    : "IEEE C37.112 integral over the measured phasor magnitudes",
             1);
    F.number("expected.operate_cycles", expected->time_s * 1000.0 / cycle_ms, "cycles", 2,
             "Expected operate time in cycles", "expected time x frequency", 2);
    F.flag("expected.extrapolated", expected->extrapolated, "Prediction extrapolated past the end of the fault",
           "integral had not reached 1 when the current stopped", 2);
  } else {
    // "No element should have operated" is a finding; "cannot tell" is a missing fact.
    std::string why;
    bool known = false;
    if (!R.fault_detected) why = "no fault detected";
    else if (!inception_in_record) why = "fault inception is not in the record";
    else if (!any_element) why = "no 50P1/51P1/51G1 settings were provided";
    else {
      known = true;
      why = blocked ? "fault is " + to_string(R.direction) + " and the elements are directional (" + dir_mode + ")"
                    : "fault current is below every element pickup";
    }
    if (known) {
      F.text("expected.element", "none", "Element expected to trip first", why, 1);
      F.text("expected.reason", why, "Why no trip was expected", "", 1);
    } else {
      F.missing("expected.element", "Element expected to trip first", why, 1);
    }
  }

  // ---------------------------------------------------------------- verdicts
  std::string relay_verdict = "undetermined";
  if (trip_after_end) {
    relay_verdict = "tripped after the fault had ended";
  } else if (R.fault_detected && inception_in_record && any_element) {
    const bool tripped = k_trip.has_value() && !trip_at_start;
    if (expected && tripped) {
      const double actual = t_at(*k_trip) - t_at(k_on);
      const double tol = tol_cyc * cycle_s + tol_pct / 100.0 * expected->time_s;
      const double dev = actual - expected->time_s;
      F.number("assessment.deviation_ms", dev * 1000.0, "ms", 2, "Actual minus expected operate time",
               "relay.operate_ms - expected.operate_ms", 1);
      F.number("assessment.tolerance_ms", tol * 1000.0, "ms", 2, "Allowed deviation",
               "TOLCYC cycles + TOLPCT % of expected", 2);
      relay_verdict = std::fabs(dev) <= tol ? "operated as expected" : (dev > 0 ? "slower than expected" : "faster than expected");
    } else if (expected && !tripped) {
      relay_verdict = cm.trip < 0 ? "undetermined" : "failed to trip";
    } else if (!expected && tripped) {
      relay_verdict = blocked ? "tripped for a fault outside its directional zone" : "tripped although no trip was expected";
    } else if (!expected && !tripped) {
      relay_verdict = cm.trip < 0 ? "undetermined" : "correctly did not trip";
    }
  }
  R.relay_assessment = relay_verdict;
  F.text("assessment.relay", relay_verdict, "Relay performance verdict", "actual trip vs. replayed settings", 1);

  std::string bkr_verdict = "not applicable";
  if (cm.trip < 0) {
    bkr_verdict = "undetermined";
  } else if (trip_after_end) {
    bkr_verdict = a52_opened.value_or(false) ? "opened on the late trip, with no fault current left to interrupt"
                                             : "did not open within the record";
  } else if (k_trip && !trip_at_start) {
    if (other_breakers) {
      bkr_verdict = "failed: current interrupted by other breakers while 52A stayed closed";
    } else if (breaker_opened && *k_last_clear >= *k_trip) {
      const double cyc = (t_at(*k_last_clear) - t_at(*k_trip)) / cycle_s;
      bkr_verdict = cyc <= bkr_max ? "normal" : "slow";
    } else if (k_end) {
      bkr_verdict = "failed: fault removed by other protection";
    } else {
      bkr_verdict = "did not interrupt within the record";
    }
    F.number("assessment.breaker_limit_cycles", bkr_max, "cycles", 1, "Maximum acceptable interrupting time", "settings BKRMAX", 2);
  } else if (!k_trip && a52_opened.value_or(false)) {
    bkr_verdict = "opened without a trip from this relay";
  }
  R.breaker_assessment = bkr_verdict;
  F.text("assessment.breaker", bkr_verdict, "Breaker performance verdict", "interrupting time vs. BKRMAX", 1);

  const auto t_end = std::chrono::steady_clock::now();
  F.meta("analyze_ms", std::chrono::duration<double, std::milli>(t_end - t_begin).count());
  F.meta("samples", static_cast<double>(k_total));
  return R;
}

}  // namespace rea
